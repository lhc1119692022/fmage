// Original-image sidecar for the Desktop composer.
//
// The conversation stores a normalized copy of every attached picture (at most
// 2048x2048 pixels and 4 MiB, re-encoded when it is larger or carries
// metadata) and hands that copy to tools. The composer still holds the file the
// user picked, so the browser uploads those bytes here as soon as the picture
// appears. `edit_image` then recognizes the copy and sends the original
// instead (mcp/server.mjs, `restoreOriginalInputs`). Nothing here changes what
// the conversation stores or shows.
//
// Location, resolved the same way by mcp/server.mjs:
//   1. <cache_dir>/originals/        when providers.json sets a usable cache_dir
//   2. <os temp>/fmage/originals/    otherwise, or when (1) cannot be written
// Each entry is <sha256>/original.<ext> plus <sha256>/meta.json
//   { version, sha256, status, width, height, fingerprint, bytes, format, file,
//     startedAt, createdAt, seenAt, usedAt }.
// `status` is "uploading" while the bytes arrive, then "ready".
import { createHash } from 'node:crypto';
import { createWriteStream } from 'node:fs';
import { mkdir, readFile, readdir, rename, rm, stat, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';

export const ORIGINAL_ROUTE = '/api/fmage.original';
export const ORIGINAL_DIR_NAME = 'originals';
export const TEMP_ORIGINAL_DIR = ['fmage', 'originals'];
// Above the host's 50 MiB per-image admission limit, so every picture the
// composer accepts fits.
export const MAX_ORIGINAL_BYTES = 64 * 1024 * 1024;
// An original not seen in a composer or used by an edit for a week is dropped.
export const UNUSED_RETENTION_MS = 7 * 24 * 60 * 60 * 1000;
// Past this total the least recently active originals go first.
export const MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024;
// An original an edit used within the hour is never evicted, so a retry or a
// follow-up edit of the same picture still finds it.
export const USED_PROTECTION_MS = 60 * 60 * 1000;
// An upload that has not finished by then was interrupted.
export const STALE_UPLOAD_MS = 10 * 60 * 1000;
const NO_STORE = { 'cache-control': 'no-store' };
const META_NAME = 'meta.json';
const PART_NAME = 'original.part';
const SHA256_PATTERN = /^[a-f0-9]{64}$/;
const FINGERPRINT_CELLS = 16 * 16;

class OriginalError extends Error {
  constructor(message, status = 400) {
    super(message);
    this.status = status;
  }
}

async function respond(task) {
  try {
    return Response.json(await task(), { headers: NO_STORE });
  } catch (error) {
    const status = error instanceof OriginalError ? error.status : 500;
    return Response.json(
      { error: status === 500 ? 'Fmage original upload failed.' : error.message },
      { status, headers: NO_STORE },
    );
  }
}

function sha256Of(value) {
  const digest = typeof value === 'string' ? value.trim().toLowerCase() : '';
  if (!SHA256_PATTERN.test(digest)) throw new OriginalError('sha256 must be a hex SHA-256 digest.');
  return digest;
}

function dimensionOf(value, label) {
  const number = Number(value);
  if (!Number.isInteger(number) || number < 1 || number > 65535) {
    throw new OriginalError(`${label} must be an integer from 1 to 65535.`);
  }
  return number;
}

function fingerprintOf(value) {
  const cells = typeof value === 'string' ? value.split(',') : [];
  if (cells.length !== FINGERPRINT_CELLS) {
    throw new OriginalError(`fingerprint must list ${FINGERPRINT_CELLS} numbers.`);
  }
  return cells.map((cell, index) => {
    const number = Number(cell);
    if (cell.trim() === '' || !Number.isFinite(number) || number < 0 || number > 255) {
      throw new OriginalError(`fingerprint[${index}] must be a number from 0 to 255.`);
    }
    return Math.round(number * 10) / 10;
  });
}

/** The container format from the leading bytes; the extension the copy keeps. */
export function detectFormat(head) {
  const ascii = (start, end) => head.subarray(start, end).toString('latin1');
  if (head.length >= 8 && ascii(0, 8) === '\x89PNG\r\n\x1a\n') return { format: 'png', extension: 'png' };
  if (head.length >= 3 && head[0] === 0xff && head[1] === 0xd8 && head[2] === 0xff) return { format: 'jpeg', extension: 'jpg' };
  if (head.length >= 12 && ascii(0, 4) === 'RIFF' && ascii(8, 12) === 'WEBP') return { format: 'webp', extension: 'webp' };
  if (head.length >= 6 && /^GIF8[79]a$/.test(ascii(0, 6))) return { format: 'gif', extension: 'gif' };
  if (head.length >= 2 && ascii(0, 2) === 'BM') return { format: 'bmp', extension: 'bmp' };
  if (head.length >= 4 && (ascii(0, 4) === 'II*\x00' || ascii(0, 4) === 'MM\x00*')) return { format: 'tiff', extension: 'tif' };
  if (head.length >= 12 && ascii(4, 8) === 'ftyp') {
    const brand = ascii(8, 12);
    if (brand === 'avif' || brand === 'avis') return { format: 'avif', extension: 'avif' };
    if (/^(heic|heix|hevc|hevx|mif1|msf1)$/.test(brand)) return { format: 'heif', extension: 'heic' };
  }
  return null;
}

/** The user's `cache_dir` from providers.json, resolved, or null when unset. */
async function configuredCacheDir(configPath) {
  try {
    const config = JSON.parse((await readFile(configPath, 'utf8')).replace(/^\uFEFF/, ''));
    const value = typeof config?.cache_dir === 'string' ? config.cache_dir.trim() : '';
    return value ? resolve(dirname(configPath), value) : null;
  } catch {
    return null;
  }
}

/** Candidate directories in preference order; the last one is the temp fallback. */
export async function originalDirectories(configPath, temp = tmpdir()) {
  const fallback = join(temp, ...TEMP_ORIGINAL_DIR);
  const configured = await configuredCacheDir(configPath);
  const preferred = configured ? join(configured, ORIGINAL_DIR_NAME) : null;
  return preferred && resolve(preferred) !== resolve(fallback) ? [preferred, fallback] : [fallback];
}

/** Replace a meta file in one step, so a reader never sees half of it. */
async function writeMeta(target, meta) {
  const path = join(target, META_NAME);
  const temporary = `${path}.${process.pid}.${Date.now()}.tmp`;
  await writeFile(temporary, JSON.stringify(meta, null, 2) + '\n', 'utf8');
  try {
    await rename(temporary, path);
  } catch (error) {
    await rm(temporary, { force: true }).catch(() => {});
    throw error;
  }
}

async function readMeta(target) {
  return JSON.parse(await readFile(join(target, META_NAME), 'utf8'));
}

const timeOf = value => {
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : 0;
};

/** The last moment a composer or an edit touched the entry. */
export function lastActive(meta) {
  return Math.max(timeOf(meta?.createdAt), timeOf(meta?.seenAt), timeOf(meta?.usedAt), timeOf(meta?.startedAt));
}

/** Every entry we own across the directories: sha-named folders holding our meta file. */
async function entriesIn(directories) {
  const found = [];
  for (const directory of directories) {
    let entries;
    try {
      entries = await readdir(directory, { withFileTypes: true });
    } catch {
      continue;
    }
    for (const entry of entries) {
      if (!entry.isDirectory() || !SHA256_PATTERN.test(entry.name)) continue;
      const target = join(directory, entry.name);
      try {
        const meta = await readMeta(target);
        if (meta?.sha256 === entry.name) found.push({ target, meta });
      } catch { /* not an original entry */ }
    }
  }
  return found;
}

/**
 * Drop originals past their retention, then the least recently active ones
 * while the total is over the budget. Best effort; `keep` is never touched,
 * nor is an upload in progress or an original an edit used within the hour.
 */
export async function evictOriginals(directories, { keep = null, now = Date.now(), maxTotalBytes = MAX_TOTAL_BYTES } = {}) {
  const ready = [];
  for (const entry of await entriesIn(directories)) {
    if (entry.target === keep) continue;
    const meta = entry.meta;
    try {
      if (meta.status !== 'ready') {
        if (now - lastActive(meta) > STALE_UPLOAD_MS) await rm(entry.target, { recursive: true, force: true });
        continue;
      }
      if (now - timeOf(meta.usedAt) <= USED_PROTECTION_MS) continue;
      if (now - lastActive(meta) > UNUSED_RETENTION_MS) {
        await rm(entry.target, { recursive: true, force: true });
        continue;
      }
      ready.push(entry);
    } catch { /* already gone */ }
  }
  const sized = await Promise.all((await entriesIn(directories)).map(async entry => {
    if (entry.meta?.status !== 'ready' || !entry.meta.file) return 0;
    try {
      return (await stat(join(entry.target, entry.meta.file))).size;
    } catch {
      return 0;
    }
  }));
  let total = sized.reduce((sum, size) => sum + size, 0);
  if (total <= maxTotalBytes) return;
  ready.sort((left, right) => lastActive(left.meta) - lastActive(right.meta));
  for (const entry of ready) {
    if (total <= maxTotalBytes) break;
    try {
      const size = (await stat(join(entry.target, entry.meta.file))).size;
      await rm(entry.target, { recursive: true, force: true });
      total -= size;
    } catch { /* already gone */ }
  }
}

/** Stream a request body to `path`, hashing it and enforcing the size cap. */
async function receive(body, path) {
  const hash = createHash('sha256');
  const out = createWriteStream(path, { flags: 'wx' });
  const done = new Promise((resolvePromise, rejectPromise) => {
    out.once('finish', resolvePromise);
    out.once('error', rejectPromise);
    // A destroyed stream emits neither of those, only close.
    out.once('close', resolvePromise);
  });
  let bytes = 0;
  let head = Buffer.alloc(0);
  try {
    const reader = body.getReader();
    for (;;) {
      const { value, done: ended } = await reader.read();
      if (ended) break;
      const chunk = Buffer.from(value.buffer, value.byteOffset, value.byteLength);
      bytes += chunk.byteLength;
      if (bytes > MAX_ORIGINAL_BYTES) {
        await reader.cancel().catch(() => {});
        throw new OriginalError(`The original exceeds ${MAX_ORIGINAL_BYTES} bytes.`, 413);
      }
      if (head.length < 16) head = Buffer.concat([head, chunk.subarray(0, 16 - head.length)]);
      hash.update(chunk);
      if (!out.write(chunk)) await new Promise(resolvePromise => out.once('drain', resolvePromise));
    }
  } catch (error) {
    out.destroy();
    await done.catch(() => {});
    throw error;
  }
  out.end();
  await done;
  return { bytes, head, sha256: hash.digest('hex') };
}

export function createOriginalsApi(root, { temp } = {}) {
  const configPath = join(root, 'providers.json');
  const directories = () => originalDirectories(configPath, temp);

  /** The stored entry for a digest, or null. */
  async function find(sha256) {
    for (const directory of await directories()) {
      const target = join(directory, sha256);
      try {
        const meta = await readMeta(target);
        if (meta?.sha256 === sha256) return { target, meta };
      } catch { /* try the next location */ }
    }
    return null;
  }

  /**
   * Whether the picture is already stored. A hit counts as the picture
   * being seen in a composer again, which keeps it from eviction.
   */
  async function status(query) {
    const sha256 = sha256Of(query.get('sha256'));
    const found = await find(sha256);
    if (!found) return { sha256, status: 'missing' };
    const { target, meta } = found;
    if (meta.status === 'ready') {
      try {
        await stat(join(target, meta.file));
        await writeMeta(target, { ...meta, seenAt: new Date().toISOString() });
        return { sha256, status: 'ready' };
      } catch {
        return { sha256, status: 'missing' };
      }
    }
    return { sha256, status: Date.now() - lastActive(meta) > STALE_UPLOAD_MS ? 'missing' : 'uploading' };
  }

  return {
    directories,
    status,

    /** Store the bytes of one picture under its SHA-256. */
    async upload(query, body, declaredLength) {
      const sha256 = sha256Of(query.get('sha256'));
      const width = dimensionOf(query.get('width'), 'width');
      const height = dimensionOf(query.get('height'), 'height');
      const fingerprint = fingerprintOf(query.get('fingerprint'));
      if (declaredLength != null && Number(declaredLength) > MAX_ORIGINAL_BYTES) {
        throw new OriginalError(`The original exceeds ${MAX_ORIGINAL_BYTES} bytes.`, 413);
      }
      if (!body) throw new OriginalError('The request carries no image bytes.');
      const existing = await find(sha256);
      if (existing) {
        const stored = await status(query);
        if (stored.status !== 'missing') return { ...stored, existing: true };
        await rm(existing.target, { recursive: true, force: true }).catch(() => {});
      }

      const startedAt = new Date().toISOString();
      const pending = { version: 1, sha256, status: 'uploading', width, height, fingerprint, startedAt };
      const candidates = await directories();
      let target = null;
      // An unusable cache_dir (missing drive, read-only, a file) falls through
      // to the temp directory. The body can be read only once, so the place is
      // settled before any byte is read.
      for (const directory of candidates) {
        const candidate = join(directory, sha256);
        try {
          await mkdir(candidate, { recursive: true });
          await rm(join(candidate, PART_NAME), { force: true });
          // The marker tells an edit that starts meanwhile to wait for the bytes.
          await writeMeta(candidate, pending);
          target = candidate;
          break;
        } catch {
          await rm(candidate, { recursive: true, force: true }).catch(() => {});
        }
      }
      if (!target) throw new OriginalError('No writable original cache directory.', 500);

      const part = join(target, PART_NAME);
      try {
        const received = await receive(body, part);
        if (received.bytes === 0) throw new OriginalError('The original is empty.');
        if (received.sha256 !== sha256) throw new OriginalError('The bytes do not match sha256.');
        const detected = detectFormat(received.head);
        if (!detected) throw new OriginalError('The original is not a recognized image format.', 415);
        const file = `original.${detected.extension}`;
        await rename(part, join(target, file));
        const now = new Date().toISOString();
        const record = {
          ...pending, status: 'ready', bytes: received.bytes, format: detected.format, file,
          createdAt: now, seenAt: now, usedAt: null,
        };
        await writeMeta(target, record);
        await evictOriginals(candidates, { keep: target }).catch(() => {});
        return { sha256, status: 'ready', bytes: received.bytes, format: detected.format };
      } catch (error) {
        await rm(target, { recursive: true, force: true }).catch(() => {});
        throw error;
      }
    },
  };
}

/**
 * Exact owner-only route inside the Connection authentication fence. The body
 * streams, because the host buffers at most 16 MiB and an original can be
 * larger.
 */
export function registerOriginalRoutes(ctx, api) {
  ctx.connection.fetch.register({
    path: ORIGINAL_ROUTE,
    methods: ['GET', 'POST'],
    requestBody: 'streaming',
    fetch: request => respond(async () => {
      const query = new URL(request.url, 'http://localhost').searchParams;
      if (request.method === 'GET') return api.status(query);
      const type = (request.headers.get('content-type') || '').split(';')[0].trim().toLowerCase();
      if (type !== 'application/octet-stream') {
        throw new OriginalError('The original must be sent as application/octet-stream.', 415);
      }
      return api.upload(query, request.body, request.headers.get('content-length'));
    }),
  });
}
