import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { mkdir, mkdtemp, readFile, readdir, rm, stat, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import {
  createOriginalsApi, detectFormat, evictOriginals, registerOriginalRoutes,
  MAX_ORIGINAL_BYTES, ORIGINAL_ROUTE,
} from '../adapters/deepseek/originals-api.mjs';

const PNG_HEAD = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
const imageBytes = (size = 4096, seed = 1) => {
  const bytes = Buffer.alloc(size, seed);
  PNG_HEAD.copy(bytes);
  return bytes;
};
const sha = bytes => createHash('sha256').update(bytes).digest('hex');
const grid = (value = 12.5) => Array.from({ length: 256 }, () => value).join(',');
const query = (bytes, extra = {}) => new URLSearchParams({
  sha256: sha(bytes), width: '640', height: '480', fingerprint: grid(), ...extra,
});
/** A web stream yielding `bytes` in small chunks, like a streamed request body. */
const streamOf = (bytes, chunk = 1000) => new ReadableStream({
  start(controller) {
    for (let at = 0; at < bytes.length; at += chunk) controller.enqueue(new Uint8Array(bytes.subarray(at, at + chunk)));
    controller.close();
  },
});
const daysAgo = days => new Date(Date.now() - days * 24 * 60 * 60 * 1000).toISOString();
const minutesAgo = minutes => new Date(Date.now() - minutes * 60 * 1000).toISOString();

async function fixture(t, config) {
  const root = await mkdtemp(join(tmpdir(), 'fmage-originals-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  if (config !== undefined) await writeFile(join(root, 'providers.json'), JSON.stringify(config), 'utf8');
  const temp = join(root, 'tmp');
  return { root, temp, store: join(temp, 'fmage', 'originals'), api: createOriginalsApi(root, { temp }) };
}

async function rewriteMeta(directory, patch) {
  const path = join(directory, 'meta.json');
  const meta = JSON.parse(await readFile(path, 'utf8'));
  await writeFile(path, JSON.stringify({ ...meta, ...patch }), 'utf8');
}

test('an upload is stored under its digest with its picture binding', async t => {
  const { api, store } = await fixture(t);
  const bytes = imageBytes();
  const saved = await api.upload(query(bytes), streamOf(bytes), String(bytes.length));
  assert.deepEqual(saved, { sha256: sha(bytes), status: 'ready', bytes: bytes.length, format: 'png' });
  const directory = join(store, sha(bytes));
  assert.deepEqual(await readFile(join(directory, 'original.png')), bytes);
  const meta = JSON.parse(await readFile(join(directory, 'meta.json'), 'utf8'));
  assert.equal(meta.status, 'ready');
  assert.equal(meta.width, 640);
  assert.equal(meta.height, 480);
  assert.equal(meta.fingerprint.length, 256);
  assert.equal(meta.file, 'original.png');
  assert.deepEqual(await readdir(directory), ['meta.json', 'original.png'], 'no partial file is left behind');
  assert.equal((await api.status(query(bytes))).status, 'ready');
});

test('a second upload of the same picture keeps the first copy', async t => {
  const { api } = await fixture(t);
  const bytes = imageBytes();
  await api.upload(query(bytes), streamOf(bytes), null);
  const again = await api.upload(query(bytes), streamOf(bytes), null);
  assert.equal(again.existing, true);
  assert.equal(again.status, 'ready');
});

test('bad uploads are refused and leave nothing behind', async t => {
  const { api, store } = await fixture(t);
  const bytes = imageBytes();
  const refuse = async (promise, status) => {
    await assert.rejects(promise, error => error.status === status);
  };
  await refuse(api.upload(query(bytes, { sha256: 'a'.repeat(64) }), streamOf(bytes), null), 400);
  await refuse(api.upload(query(bytes, { sha256: 'nope' }), streamOf(bytes), null), 400);
  await refuse(api.upload(query(bytes, { width: '0' }), streamOf(bytes), null), 400);
  await refuse(api.upload(query(bytes, { fingerprint: '1,2,3' }), streamOf(bytes), null), 400);
  const text = Buffer.from('not an image at all, just text');
  await refuse(api.upload(query(text), streamOf(text), null), 415);
  await refuse(api.upload(query(bytes), streamOf(bytes), String(MAX_ORIGINAL_BYTES + 1)), 413);
  let names = [];
  try { names = await readdir(store); } catch { /* never created */ }
  assert.deepEqual(names, []);
});

test('a body over the cap is cut off while streaming', async t => {
  const { api, store } = await fixture(t);
  const chunk = imageBytes(1024 * 1024);
  let sent = 0;
  const body = new ReadableStream({
    pull(controller) {
      if (sent > MAX_ORIGINAL_BYTES + 2 * chunk.length) { controller.close(); return; }
      controller.enqueue(new Uint8Array(chunk));
      sent += chunk.length;
    },
  });
  await assert.rejects(api.upload(query(chunk), body, null), error => error.status === 413);
  assert.ok(sent <= MAX_ORIGINAL_BYTES + 2 * chunk.length);
  assert.deepEqual(await readdir(store), []);
});

test('formats are sniffed from the leading bytes', () => {
  assert.equal(detectFormat(Buffer.from([0xff, 0xd8, 0xff, 0xe0])).format, 'jpeg');
  assert.equal(detectFormat(Buffer.from('RIFF\0\0\0\0WEBPVP8 ', 'latin1')).format, 'webp');
  assert.equal(detectFormat(Buffer.from('GIF89a', 'latin1')).format, 'gif');
  assert.equal(detectFormat(Buffer.from('\0\0\0\x1cftypavif', 'latin1')).format, 'avif');
  assert.equal(detectFormat(Buffer.from('\0\0\0\x18ftypheic', 'latin1')).format, 'heif');
  assert.equal(detectFormat(PNG_HEAD).format, 'png');
  assert.equal(detectFormat(Buffer.from('<svg>', 'latin1')), null);
});

test('a configured cache_dir is used, and an unwritable one falls back to temp', async t => {
  const usable = await fixture(t, { cache_dir: 'cache' });
  const bytes = imageBytes();
  await usable.api.upload(query(bytes), streamOf(bytes), null);
  await stat(join(usable.root, 'cache', 'originals', sha(bytes), 'meta.json'));

  const blocked = await fixture(t, { cache_dir: 'blocked' });
  await writeFile(join(blocked.root, 'blocked'), 'a file where the directory should be');
  await blocked.api.upload(query(bytes), streamOf(bytes), null);
  await stat(join(blocked.store, sha(bytes), 'meta.json'));
  assert.equal((await blocked.api.status(query(bytes))).status, 'ready');
});

test('eviction: a week unused, then oldest first over the budget; recent use and live uploads are kept', async t => {
  const { api, store } = await fixture(t);
  const entries = {};
  for (const [name, seed] of [['fresh', 1], ['weekOld', 2], ['weekOldUsedNow', 3], ['older', 4], ['newer', 5], ['uploading', 6], ['staleUpload', 7]]) {
    const bytes = imageBytes(4096, seed);
    await api.upload(query(bytes), streamOf(bytes), null);
    entries[name] = join(store, sha(bytes));
  }
  await rewriteMeta(entries.weekOld, { createdAt: daysAgo(8), seenAt: daysAgo(8), startedAt: daysAgo(8) });
  await rewriteMeta(entries.weekOldUsedNow, { createdAt: daysAgo(8), seenAt: daysAgo(8), startedAt: daysAgo(8), usedAt: minutesAgo(30) });
  await rewriteMeta(entries.older, { createdAt: daysAgo(3), seenAt: daysAgo(3), startedAt: daysAgo(3) });
  await rewriteMeta(entries.newer, { createdAt: daysAgo(1), seenAt: daysAgo(1), startedAt: daysAgo(1) });
  await rewriteMeta(entries.uploading, { status: 'uploading', startedAt: minutesAgo(1) });
  await rewriteMeta(entries.staleUpload, { status: 'uploading', startedAt: minutesAgo(30), createdAt: null, seenAt: null });
  const stranger = join(store, 'c'.repeat(64));
  await mkdir(stranger, { recursive: true });
  await writeFile(join(stranger, 'notes.txt'), 'keep me');

  // Room for three originals: fresh, weekOldUsedNow (protected) and one more.
  await evictOriginals([store], { maxTotalBytes: 3 * 4096 + 100 });
  const names = new Set(await readdir(store));
  const kept = name => names.has(entries[name].split(/[\\/]/).pop());
  assert.ok(kept('fresh'));
  assert.ok(!kept('weekOld'), 'a week without use removes it');
  assert.ok(kept('weekOldUsedNow'), 'an edit within the hour protects it');
  assert.ok(!kept('older'), 'over the budget the least recently active goes first');
  assert.ok(kept('newer'));
  assert.ok(kept('uploading'), 'an upload in progress is never touched');
  assert.ok(!kept('staleUpload'), 'an interrupted upload is cleared');
  assert.ok(names.has('c'.repeat(64)), 'a directory without our meta file is untouched');
});

test('the route streams octet-stream bodies and answers status queries', async t => {
  const { api } = await fixture(t);
  let route = null;
  registerOriginalRoutes({ connection: { fetch: { register: value => { route = value; } } } }, api);
  assert.equal(route.path, ORIGINAL_ROUTE);
  assert.equal(route.requestBody, 'streaming');
  const bytes = imageBytes(200000);
  const url = `http://localhost${ORIGINAL_ROUTE}?${query(bytes)}`;

  const missing = await route.fetch(new Request(url));
  assert.equal((await missing.json()).status, 'missing');
  const wrongType = await route.fetch(new Request(url, { method: 'POST', body: bytes, headers: { 'content-type': 'application/json' } }));
  assert.equal(wrongType.status, 415);
  const posted = await route.fetch(new Request(url, {
    method: 'POST', body: streamOf(bytes, 65536), duplex: 'half', headers: { 'content-type': 'application/octet-stream' },
  }));
  assert.equal(posted.status, 200);
  assert.equal((await posted.json()).status, 'ready');
  assert.equal(posted.headers.get('cache-control'), 'no-store');
  const ready = await route.fetch(new Request(url));
  assert.equal((await ready.json()).status, 'ready');
  const bad = await route.fetch(new Request(`http://localhost${ORIGINAL_ROUTE}?sha256=x`));
  assert.equal(bad.status, 400);
});
