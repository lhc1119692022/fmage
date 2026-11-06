// Annotation workspace for the Desktop sidebar: the browser draws a layer,
// this module owns the file on disk.
//
// The model never sees this file's path. `edit_image` looks for the newest
// unused layer drawn on its first input image and attaches it itself, so
// nothing has to be pasted into the conversation. That is why the directory is
// a cache: every entry is disposable, and an unused one expires.
//
// A layer is bound to its picture by `sha256` (the bytes the sidebar read) and
// `fingerprint` (a 16x16 luma grid, see scripts/image_fingerprint.py), and to
// the composer upload it came from by `upload` (that upload's object URL).
//
// Location, resolved the same way by mcp/server.mjs:
//   1. <cache_dir>/annotations/   when providers.json sets a usable cache_dir
//                                 (relative paths resolve against that file's folder)
//   2. <os temp>/fmage/annotations/  otherwise, or when (1) cannot be written
// Each entry is <id>/layer.png plus <id>/meta.json
//   { id, mode, width, height, sha256, fingerprint, upload, createdAt, consumedAt, comments }.
import { mkdir, readFile, readdir, rm, stat, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { randomBytes } from 'node:crypto';

export const ANNOTATION_ROUTE = '/api/fmage.annotation';
export const ANNOTATION_DIR_NAME = 'annotations';
export const TEMP_ANNOTATION_DIR = ['fmage', 'annotations'];
const NO_STORE = { 'cache-control': 'no-store' };
const MAX_LAYER_BYTES = 32 * 1024 * 1024;
const MAX_REQUEST_BYTES = 40 * 1024 * 1024;
const META_NAME = 'meta.json';
const LAYER_NAME = 'layer.png';
// A layer waits a day for its edit, so a slow upload, a long prompt or a
// break does not lose the marks. It binds to its picture by sha256 and
// fingerprint, so a waiting layer cannot attach itself to another picture.
export const UNUSED_TTL_MS = 24 * 60 * 60 * 1000;
// A used layer stays a day so the run that sent it can still be inspected.
export const USED_RETENTION_MS = 24 * 60 * 60 * 1000;

class AnnotationError extends Error {
  constructor(message, status = 400) {
    super(message);
    this.status = status;
  }
}

async function respond(task) {
  try {
    return Response.json(await task(), { headers: NO_STORE });
  } catch (error) {
    const status = error instanceof AnnotationError ? error.status : 500;
    return Response.json(
      { error: status === 500 ? 'Fmage annotation request failed.' : error.message },
      { status, headers: NO_STORE },
    );
  }
}

async function jsonBody(request) {
  try {
    return await request.json();
  } catch {
    throw new AnnotationError('Request body must be JSON.');
  }
}

const ID_PATTERN = /^[a-z0-9]{8,40}$/;

function annotationId(value) {
  const id = typeof value === 'string' ? value.trim() : '';
  if (!ID_PATTERN.test(id)) throw new AnnotationError('Unknown annotation id.', 404);
  return id;
}

/** Decode one `data:image/png;base64,...` payload or a bare base64 string. */
function decodePng(value, label) {
  if (typeof value !== 'string' || value.length === 0) {
    throw new AnnotationError(`${label} must be a base64 PNG string.`);
  }
  const comma = value.indexOf(',');
  const payload = value.startsWith('data:') && comma >= 0 ? value.slice(comma + 1) : value;
  if (value.startsWith('data:') && !/^data:image\/png;base64,/i.test(value)) {
    throw new AnnotationError(`${label} must be a PNG data URL.`);
  }
  const bytes = Buffer.from(payload, 'base64');
  if (bytes.byteLength === 0) throw new AnnotationError(`${label} is empty.`);
  if (bytes.byteLength > MAX_LAYER_BYTES) {
    throw new AnnotationError(`${label} exceeds ${MAX_LAYER_BYTES} bytes.`, 413);
  }
  // The layer must stay a transparent PNG: the provider checks its alpha, and a
  // mislabeled JPEG would fail there instead of here.
  if (bytes.subarray(0, 8).toString('binary') !== '\x89PNG\r\n\x1a\n') {
    throw new AnnotationError(`${label} must be a PNG.`);
  }
  return bytes;
}

function normalizeComments(value) {
  if (value === undefined) return [];
  if (!Array.isArray(value)) throw new AnnotationError('comments must be an array.');
  return value.slice(0, 100).map((entry, index) => {
    if (entry === null || typeof entry !== 'object') {
      throw new AnnotationError(`comments[${index}] must be an object.`);
    }
    const text = typeof entry.text === 'string' ? entry.text.trim().slice(0, 2000) : '';
    if (!text) throw new AnnotationError(`comments[${index}].text is empty.`);
    const x = Number(entry.x);
    const y = Number(entry.y);
    return {
      id: Number.isInteger(entry.id) ? entry.id : index + 1,
      text,
      ...(Number.isFinite(x) && Number.isFinite(y) ? { x: Number(x.toFixed(4)), y: Number(y.toFixed(4)) } : {}),
    };
  });
}

const SHA256_PATTERN = /^[a-f0-9]{64}$/;
const FINGERPRINT_CELLS = 16 * 16;

function sha256Of(value) {
  const digest = typeof value === 'string' ? value.trim().toLowerCase() : '';
  return SHA256_PATTERN.test(digest) ? digest : null;
}

function fingerprintOf(value) {
  if (value === undefined || value === null) return null;
  if (!Array.isArray(value) || value.length !== FINGERPRINT_CELLS) {
    throw new AnnotationError(`fingerprint must be an array of ${FINGERPRINT_CELLS} numbers.`);
  }
  return value.map((cell, index) => {
    const number = Number(cell);
    if (!Number.isFinite(number) || number < 0 || number > 255) {
      throw new AnnotationError(`fingerprint[${index}] must be a number from 0 to 255.`);
    }
    return Math.round(number * 10) / 10;
  });
}

function uploadOf(value) {
  return typeof value === 'string' && value.length > 0 && value.length <= 300 ? value : null;
}

function metaOf(body) {
  const mode = body.mode === 'flatten' ? 'flatten' : body.mode === 'markup' ? 'markup' : '';
  if (!mode) throw new AnnotationError('mode must be "markup" or "flatten".');
  const width = Number(body.width);
  const height = Number(body.height);
  if (!Number.isInteger(width) || !Number.isInteger(height) || width < 1 || height < 1) {
    throw new AnnotationError('width and height must be positive integers.');
  }
  return {
    version: 2,
    mode,
    width,
    height,
    sha256: sha256Of(body.sha256),
    fingerprint: fingerprintOf(body.fingerprint),
    upload: uploadOf(body.upload),
    comments: normalizeComments(body.comments),
    sourceName: typeof body.sourceName === 'string' ? body.sourceName.slice(0, 200) : '',
  };
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
export async function annotationDirectories(configPath, temp = tmpdir()) {
  const fallback = join(temp, ...TEMP_ANNOTATION_DIR);
  const configured = await configuredCacheDir(configPath);
  const preferred = configured ? join(configured, ANNOTATION_DIR_NAME) : null;
  return preferred && resolve(preferred) !== resolve(fallback) ? [preferred, fallback] : [fallback];
}

/**
 * Whether an entry is past its retention: an unused layer a day after it
 * was saved, a used one a day after its request. Timestamps come from the
 * meta file; one that cannot be read falls back to the file's age.
 */
async function expired(target, now) {
  const metaPath = join(target, META_NAME);
  let meta;
  try {
    meta = JSON.parse(await readFile(metaPath, 'utf8'));
  } catch {
    const info = await stat(metaPath);
    return now - info.mtimeMs > USED_RETENTION_MS;
  }
  const consumed = Date.parse(meta?.consumedAt);
  if (Number.isFinite(consumed)) return now - consumed > USED_RETENTION_MS;
  const created = Date.parse(meta?.createdAt);
  if (Number.isFinite(created)) return now - created > UNUSED_TTL_MS;
  return now - (await stat(metaPath)).mtimeMs > UNUSED_TTL_MS;
}

/**
 * Drop entries past their retention.
 *
 * Best effort: the entry just written is always kept, and only directories
 * named like our ids that hold our meta file are ever touched.
 */
async function pruneExpired(directory, keep) {
  let entries;
  try {
    entries = await readdir(directory, { withFileTypes: true });
  } catch {
    return;
  }
  const now = Date.now();
  await Promise.all(entries.filter(entry => entry.isDirectory() && ID_PATTERN.test(entry.name)).map(async entry => {
    const target = join(directory, entry.name);
    if (target === keep) return;
    try {
      if (await expired(target, now)) await rm(target, { recursive: true, force: true });
    } catch { /* not an annotation entry, or already gone */ }
  }));
}

/** Every readable entry across the candidate directories. */
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
      if (!entry.isDirectory() || !ID_PATTERN.test(entry.name)) continue;
      const target = join(directory, entry.name);
      try {
        found.push({ id: entry.name, target, meta: JSON.parse(await readFile(join(target, META_NAME), 'utf8')) });
      } catch { /* not an annotation entry */ }
    }
  }
  return found;
}

/** Delete the unused entries `matches` selects; used ones stay for inspection. */
async function retireUnused(directories, matches) {
  let retired = 0;
  for (const entry of await entriesIn(directories)) {
    if (entry.meta?.consumedAt || !matches(entry)) continue;
    try {
      await rm(entry.target, { recursive: true, force: true });
      retired += 1;
    } catch { /* already gone */ }
  }
  return retired;
}

/**
 * Persist one annotation layer.
 *
 * Only the marks are written. The image being annotated stays the one the user
 * attached, so there is no second copy to drift from it.
 */
export function createAnnotationApi(root, { temp } = {}) {
  const configPath = join(root, 'providers.json');
  const directories = () => annotationDirectories(configPath, temp);

  return {
    directories,

    async read(body) {
      const id = annotationId(body?.id);
      for (const directory of await directories()) {
        const target = join(directory, id);
        try {
          const meta = JSON.parse(await readFile(join(target, META_NAME), 'utf8'));
          return { ...meta, id, directory: target, layer: join(target, LAYER_NAME) };
        } catch { /* try the next location */ }
      }
      throw new AnnotationError('Annotation not found.', 404);
    },

    async save(body) {
      if (body === null || typeof body !== 'object') {
        throw new AnnotationError('Annotation request must be an object.');
      }
      const meta = metaOf(body);
      const layer = decodePng(body.layer, 'layer');

      const id = randomBytes(8).toString('hex');
      const record = { ...meta, id, createdAt: new Date().toISOString(), consumedAt: null };
      const candidates = await directories();
      // An unusable cache_dir (missing drive, read-only, a file) falls through
      // to the temp directory instead of failing the save.
      for (const directory of candidates) {
        const target = join(directory, id);
        try {
          await mkdir(target, { recursive: true });
          await writeFile(join(target, LAYER_NAME), layer);
          await writeFile(join(target, META_NAME), JSON.stringify(record, null, 2) + '\n', 'utf8');
        } catch {
          await rm(target, { recursive: true, force: true }).catch(() => {});
          continue;
        }
        await Promise.all(candidates.map(candidate => pruneExpired(candidate, target)));
        // Saving again replaces the earlier unsent save of the same picture or
        // upload, so only the marks on screen can go with the next edit.
        await retireUnused(candidates, entry => entry.target !== target && (
          (record.sha256 && entry.meta?.sha256 === record.sha256)
          || (record.upload && entry.meta?.upload === record.upload)));
        return { ...record, directory: target, layer: join(target, LAYER_NAME) };
      }
      throw new AnnotationError('No writable annotation cache directory.', 500);
    },

    /** Take back an unsent save. A layer a request already used is kept. */
    async withdraw(body) {
      const id = annotationId(body?.id);
      const entry = (await entriesIn(await directories())).find(item => item.id === id);
      if (!entry) return { id, withdrawn: false, missing: true };
      if (entry.meta?.consumedAt) return { id, withdrawn: false, consumedAt: entry.meta.consumedAt };
      await rm(entry.target, { recursive: true, force: true });
      return { id, withdrawn: true };
    },

    /**
     * A new upload of a picture starts a new task: unsent saves of the same
     * picture made for uploads no longer in the composer are dropped.
     */
    async release(body) {
      const sha256 = sha256Of(body?.sha256);
      if (!sha256) throw new AnnotationError('sha256 must be a hex SHA-256 digest.');
      const keep = new Set(Array.isArray(body?.keep) ? body.keep.filter(item => typeof item === 'string') : []);
      const released = await retireUnused(await directories(), entry => (
        entry.meta?.sha256 === sha256 && !keep.has(entry.meta?.upload)));
      return { released };
    },
  };
}

/** Exact owner-only routes inside the Connection authentication fence. */
export function registerAnnotationRoutes(ctx, api) {
  ctx.connection.fetch.register({
    path: ANNOTATION_ROUTE,
    methods: ['GET', 'POST'],
    requestBody: 'buffered',
    fetch: request => respond(async () => {
      if (request.method === 'GET') {
        const id = new URL(request.url, 'http://localhost').searchParams.get('id');
        return api.read({ id });
      }
      const body = await jsonBody(request);
      const size = body === null || typeof body !== 'object' ? 0 : JSON.stringify(body).length;
      if (size > MAX_REQUEST_BYTES) throw new AnnotationError('Annotation payload is too large.', 413);
      if (body?.action === 'withdraw') return api.withdraw(body);
      if (body?.action === 'release') return api.release(body);
      return api.save(body);
    }),
  });
}
