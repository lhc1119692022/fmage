import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, readFile, readdir, rm, writeFile } from 'node:fs/promises';
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

test('saving a markup annotation writes only the layer and a manifest', async t => {
  const { api, root } = await fixture(t);
  const saved = await api.save({
    mode: 'markup',
    markup: dataUrl(),
    comments: [{ id: 1, text: 'make this vase cobalt', marker: { x: 0.4, y: 0.5 } }],
    sourceName: 'kitchen.png',
  });
  assert.match(saved.id, /^[a-z0-9]{16}$/);
  assert.equal(saved.mode, 'markup');
  assert.ok(saved.markup.endsWith('markup.png'));
  assert.equal(saved.image, null);
  assert.deepEqual(await readdir(join(root, 'annotations', saved.id)), ['annotation.json', 'markup.png']);
  const manifest = JSON.parse(await readFile(join(root, 'annotations', saved.id, 'annotation.json'), 'utf8'));
  assert.equal(manifest.comments[0].text, 'make this vase cobalt');
  assert.equal(manifest.comments[0].marker.x, 0.4);
});

test('a flattened annotation stores one image and no layer', async t => {
  const { api } = await fixture(t);
  const saved = await api.save({ mode: 'flatten', image: dataUrl() });
  assert.equal(saved.markup, null);
  assert.ok(saved.image.endsWith('image.png'));
  assert.equal(saved.mode, 'flatten');
});

test('reading returns the stored manifest by id', async t => {
  const { api } = await fixture(t);
  const saved = await api.save({ mode: 'flatten', image: dataUrl() });
  const read = await api.read({ id: saved.id });
  assert.equal(read.id, saved.id);
  assert.equal(read.image, saved.image);
});

test('unknown or malformed ids are refused', async t => {
  const { api } = await fixture(t);
  await assert.rejects(api.read({ id: '../../etc/passwd' }), error => error.status === 404);
  await assert.rejects(api.read({ id: 'nope' }), error => error.status === 404);
  await assert.rejects(api.read({ id: 'a'.repeat(16) }), error => error.status === 404);
});

test('markup mode without a layer, or with a non-PNG layer, is refused', async t => {
  const { api } = await fixture(t);
  await assert.rejects(
    api.save({ mode: 'markup' }),
    /needs the transparent layer/,
  );
  await assert.rejects(
    api.save({ mode: 'markup', markup: 'data:image/jpeg;base64,/9j/4AAQ' }),
    /must be a PNG/,
  );
});

test('a non-PNG image and an unknown mode are refused', async t => {
  const { api } = await fixture(t);
  await assert.rejects(api.save({ mode: 'flatten', image: 'data:image/png;base64,AAAA' }), /must be a PNG/);
  await assert.rejects(api.save({ mode: 'sketch', image: dataUrl() }), /mode must be/);
});

test('a flattened annotation must not carry a separate layer', async t => {
  const { api } = await fixture(t);
  await assert.rejects(
    api.save({ mode: 'flatten', image: dataUrl(), markup: dataUrl() }),
    /must not carry a separate layer/,
  );
});

test('empty comment text is refused before anything is written', async t => {
  const { api } = await fixture(t);
  await assert.rejects(
    api.save({ mode: 'flatten', image: dataUrl(), comments: [{ text: '   ' }] }),
    /text is empty/,
  );
});

test('the route answers JSON, maps errors to status codes and keeps no-store', async t => {
  const { api, root } = await fixture(t);
  const routes = routesFor(api);
  const route = routes.get(ANNOTATION_ROUTE);
  assert.ok(route);
  const created = await route.fetch(
    new Request('http://h' + ANNOTATION_ROUTE, {
      method: 'POST',
      body: JSON.stringify({ mode: 'flatten', image: dataUrl() }),
    }),
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
  assert.deepEqual(await readdir(root), ['annotations']);
});

test('two saves never share a directory', async t => {
  const { api } = await fixture(t);
  const first = await api.save({ mode: 'flatten', image: dataUrl() });
  const second = await api.save({ mode: 'flatten', image: dataUrl() });
  assert.notEqual(first.id, second.id);
  assert.notEqual(first.directory, second.directory);
});

test('a second revision keeps the earlier files intact', async t => {
  const { api } = await fixture(t);
  const first = await api.save({ mode: 'markup', markup: dataUrl() });
  const second = await api.save({ mode: 'markup', markup: dataUrl() });
  assert.notEqual(first.id, second.id);
  const original = await readFile(join(first.directory, 'markup.png'));
  assert.deepEqual(original, pngBytes());
});
