import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdir, mkdtemp, readFile, readdir, rm, utimes, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { deflateSync } from 'node:zlib';
import {
  createAnnotationApi, registerAnnotationRoutes, ANNOTATION_ROUTE,
} from '../adapters/deepseek/annotation-api.mjs';

/** Minimal valid PNG; a signature plus IHDR is enough for the route's sniff. */
function pngBytes() {
  const signature = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
  const chunk = (type, data) => {
    const length = Buffer.alloc(4);
    length.writeUInt32BE(data.length);
    const body = Buffer.concat([Buffer.from(type, 'ascii'), data]);
    const crc = Buffer.alloc(4);
    crc.writeUInt32BE(crc32(body));
    return Buffer.concat([length, body, crc]);
  };
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(1, 0);
  ihdr.writeUInt32BE(1, 4);
  ihdr[8] = 8;
  ihdr[9] = 6;
  return Buffer.concat([
    signature,
    chunk('IHDR', ihdr),
    chunk('IDAT', deflateSync(Buffer.from([0, 0, 0, 0, 0]))),
    chunk('IEND', Buffer.alloc(0)),
  ]);
}

function crc32(buffer) {
  let crc = 0xffffffff;
  for (const byte of buffer) {
    crc ^= byte;
    for (let bit = 0; bit < 8; bit += 1) crc = (crc >>> 1) ^ (0xedb88320 & -(crc & 1));
  }
  return (crc ^ 0xffffffff) >>> 0;
}

const dataUrl = () => `data:image/png;base64,${pngBytes().toString('base64')}`;
const base = () => ({ mode: 'markup', layer: dataUrl(), width: 640, height: 480 });

async function fixture(t) {
  const root = await mkdtemp(join(tmpdir(), 'fmage-annotation-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  return { root, api: createAnnotationApi(root) };
}

function routesFor(api) {
  const routes = new Map();
  registerAnnotationRoutes(
    { connection: { fetch: { register: route => { routes.set(route.path, route); return async () => {}; } } } },
    api,
  );
  return routes;
}

test('a save writes only the layer plus its metadata', async t => {
  const { api, root } = await fixture(t);
  const saved = await api.save({
    ...base(),
    comments: [{ id: 1, text: 'make this ear fold', x: 0.221, y: 0.26 }],
    sourceName: 'cat.png',
  });
  assert.match(saved.id, /^[a-z0-9]{16}$/);
  assert.equal(saved.consumedAt, null);
  assert.equal(saved.mode, 'markup');
  assert.ok(saved.layer.endsWith('layer.png'));
  assert.deepEqual(await readdir(join(root, 'cache', 'annotations', saved.id)), ['layer.png', 'meta.json']);
  const meta = JSON.parse(await readFile(join(root, 'cache', 'annotations', saved.id, 'meta.json'), 'utf8'));
  assert.equal(meta.comments[0].text, 'make this ear fold');
  assert.equal(meta.comments[0].x, 0.221);
  assert.equal(meta.width, 640);
});

test('reading returns the stored metadata by id', async t => {
  const { api } = await fixture(t);
  const saved = await api.save(base());
  const read = await api.read({ id: saved.id });
  assert.equal(read.id, saved.id);
  assert.equal(read.mode, 'markup');
  assert.equal(read.layer, saved.layer);
});

test('unknown or malformed ids are refused', async t => {
  const { api } = await fixture(t);
  await assert.rejects(api.read({ id: '../../etc/passwd' }), error => error.status === 404);
  await assert.rejects(api.read({ id: 'nope' }), error => error.status === 404);
  await assert.rejects(api.read({ id: 'a'.repeat(16) }), error => error.status === 404);
});

test('a layer that is not a transparent PNG is refused', async t => {
  const { api } = await fixture(t);
  await assert.rejects(api.save({ ...base(), layer: 'data:image/jpeg;base64,/9j/4AAQ' }), /must be a PNG/);
  await assert.rejects(api.save({ ...base(), layer: 'data:image/png;base64,AAAA' }), /must be a PNG/);
  await assert.rejects(api.save({ mode: 'markup', width: 640, height: 480 }), /layer must be/);
});

test('an unknown mode or missing canvas is refused', async t => {
  const { api } = await fixture(t);
  await assert.rejects(api.save({ ...base(), mode: 'sketch' }), /mode must be/);
  await assert.rejects(api.save({ mode: 'markup', layer: dataUrl() }), /width and height/);
});

test('empty comment text is refused before anything is written', async t => {
  const { api } = await fixture(t);
  await assert.rejects(api.save({ ...base(), comments: [{ text: '   ' }] }), /text is empty/);
});

test('the route answers JSON, maps errors to status codes and keeps no-store', async t => {
  const { api, root } = await fixture(t);
  const routes = routesFor(api);
  const route = routes.get(ANNOTATION_ROUTE);
  assert.ok(route);
  const created = await route.fetch(
    new Request('http://h' + ANNOTATION_ROUTE, { method: 'POST', body: JSON.stringify(base()) }),
  );
  assert.equal(created.status, 200);
  assert.equal(created.headers.get('cache-control'), 'no-store');
  const body = await created.json();
  assert.ok(body.id);
  const fetched = await route.fetch(new Request(`http://h${ANNOTATION_ROUTE}?id=${body.id}`));
  assert.equal(fetched.status, 200);
  assert.equal((await fetched.json()).id, body.id);
  const missing = await route.fetch(new Request(`http://h${ANNOTATION_ROUTE}?id=${'b'.repeat(16)}`));
  assert.equal(missing.status, 404);
  const badJson = await route.fetch(new Request('http://h' + ANNOTATION_ROUTE, { method: 'POST', body: 'nope' }));
  assert.equal(badJson.status, 400);
  assert.deepEqual(await readdir(root), ['cache']);
});

test('two saves never share a directory', async t => {
  const { api } = await fixture(t);
  const first = await api.save(base());
  const second = await api.save(base());
  assert.notEqual(first.id, second.id);
  assert.notEqual(first.directory, second.directory);
});

test('a second save keeps the earlier layer intact', async t => {
  const { api } = await fixture(t);
  const first = await api.save(base());
  await api.save(base());
  assert.deepEqual(await readFile(join(first.directory, 'layer.png')), pngBytes());
});

test('an entry older than the retention window is swept; fresh ones and strangers are not', async t => {
  const { api, root } = await fixture(t);
  const fresh = await api.save(base());
  const stale = await api.save(base());
  const stranger = join(root, 'cache', 'annotations', 'c'.repeat(16));
  await mkdir(stranger, { recursive: true });
  await writeFile(join(stranger, 'notes.txt'), 'keep me');

  const old = new Date(Date.now() - 3 * 60 * 60 * 1000);
  await utimes(join(stale.directory, 'meta.json'), old, old);

  await api.save(base());

  const names = await readdir(join(root, 'cache', 'annotations'));
  assert.ok(names.includes(fresh.id), 'the older-but-fresh entry survives');
  assert.ok(!names.includes(stale.id), 'the expired entry is removed');
  assert.ok(names.includes('c'.repeat(16)), 'a directory without our meta file is untouched');
});
