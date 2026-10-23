import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, readFile, readdir, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import {
  createConfigApi, maskSecret, ConfigError, registerConfigRoutes,
  CONFIG_ROUTE, REVEAL_ROUTE, MODELS_ROUTE,
  DEFAULT_RETRY_COUNT, MAX_RETRY_COUNT,
} from '../adapters/deepseek/config-api.mjs';
import { DEFAULT_RETRY_COUNT as POLICY_DEFAULT_RETRY_COUNT, MAX_RETRY_COUNT as POLICY_MAX_RETRY_COUNT } from '../mcp/retry-policy.mjs';

const SECRET = 'sk-abcdefghijklmnop1234';

async function fixture(t, config) {
  const dir = await mkdtemp(join(tmpdir(), 'fmage-config-api-'));
  t.after(() => rm(dir, { recursive: true, force: true }));
  const path = join(dir, 'providers.json');
  if (config) await writeFile(path, JSON.stringify(config));
  return { dir, path, api: createConfigApi(path) };
}

const base = () => ({
  active_providers: ['b', 'a'],
  retry_on_failure: true, retry_count: 3,
  output_dir: '', cache_dir: '',
  providers: {
    a: { transport: 'openai-images', base_url: 'https://a.example/v1', model: 'gpt-image-2', api_key: SECRET, async_mode: true, image_field: 'image[]' },
    b: { transport: 'gemini-generate-content', base_url: 'https://b.example', model: 'gemini-3-pro-image', api_key: '' },
    c: { transport: 'midjourney', base_url: 'https://c.example', model: 'mj', api_key: 'xyz' },
  },
  workflow_connections: { runninghub: { base_url: 'https://www.runninghub.cn', api_key: 'rh-secret-value-1' } },
  active_workflow: 'up',
  workflows: { up: { connection: 'runninghub', workflow_id: '123', inputs: {}, output_node_ids: ['9'], instance_type: 'default', extra: 1 } },
  unrelated: { keep: true },
});

test('masks secrets with edges and stars only', () => {
  assert.equal(maskSecret(''), '');
  assert.equal(maskSecret('abc'), '***');
  assert.equal(maskSecret(SECRET), 'sk-a********1234');
  assert.ok(!maskSecret(SECRET).includes('efgh'));
});

test('read orders enabled providers by priority and never returns raw keys', async t => {
  const { api } = await fixture(t, base());
  const view = await api.read();
  assert.deepEqual(view.providers.map(p => [p.name, p.enabled]), [['b', true], ['a', true], ['c', false]]);
  assert.ok(!JSON.stringify(view).includes(SECRET));
  assert.ok(!JSON.stringify(view).includes('rh-secret-value-1'));
  assert.equal(view.providers[1].api_key_set, true);
  assert.equal(view.providers[0].api_key_set, false);
  assert.equal(view.active_workflow, 'up');
});

test('update reorders, toggles, renames, deletes and preserves unshown fields', async t => {
  const { api, path, dir } = await fixture(t, base());
  const { revision } = await api.read();
  await api.update({ revision, changes: {
    providers: [
      { name: 'a2', from: 'a', enabled: true, transport: 'openai-images', base_url: 'https://a.example/v1/', model: 'gpt-image-2' },
      { name: 'b', from: 'b', enabled: false, transport: 'gemini-generate-content', base_url: 'https://b.example', model: 'gemini-3-pro-image', api_key: 'new-key' },
      { name: 'n', from: null, enabled: true, transport: 'midjourney', base_url: 'https://n.example', model: 'm' },
    ],
  } });
  const saved = JSON.parse(await readFile(path, 'utf8'));
  assert.deepEqual(saved.active_providers, ['a2', 'n']);
  assert.deepEqual(Object.keys(saved.providers), ['a2', 'b', 'n']);
  assert.equal(saved.providers.a2.api_key, SECRET, 'omitted api_key keeps stored value');
  assert.equal(saved.providers.a2.async_mode, true);
  assert.equal(saved.providers.a2.base_url, 'https://a.example/v1');
  assert.equal(saved.providers.b.api_key, 'new-key');
  assert.equal(saved.providers.n.api_key, '');
  assert.deepEqual(saved.unrelated, { keep: true });
  assert.equal(saved.retry_on_failure, true, 'top-level retry settings survive provider edits');
  assert.equal(saved.retry_count, 3);
  assert.equal(saved.workflows.up.extra, 1, 'sections not submitted stay untouched');
  assert.equal((await readdir(join(dir, 'backups'))).length, 1);
});

test('retry settings read, validate and round-trip', async t => {
  const { api, path } = await fixture(t, base());
  const view = await api.read();
  assert.equal(view.retry_on_failure, true);
  assert.equal(view.retry_count, 3);

  await api.update({ revision: view.revision, changes: { retry_on_failure: false, retry_count: 0 } });
  const saved = JSON.parse(await readFile(path, 'utf8'));
  assert.equal(saved.retry_on_failure, false);
  assert.equal(saved.retry_count, 0);

  const next = await api.read();
  for (const bad of [-1, 11, 1.5, '3', null]) {
    await assert.rejects(
      api.update({ revision: next.revision, changes: { retry_count: bad } }),
      error => error instanceof ConfigError && /integer from 0 to 10/.test(error.message),
    );
  }
  await assert.rejects(api.update({ revision: next.revision, changes: { retry_on_failure: 'yes' } }), /must be a boolean/);
});

test('retry defaults apply to missing and invalid stored values', async t => {
  const missing = await fixture(t, { providers: {} });
  const missingView = await missing.api.read();
  assert.equal(missingView.retry_on_failure, false);
  assert.equal(missingView.retry_count, DEFAULT_RETRY_COUNT);

  const invalid = await fixture(t, { providers: {}, retry_on_failure: 'yes', retry_count: 12 });
  const invalidView = await invalid.api.read();
  assert.equal(invalidView.retry_on_failure, false);
  assert.equal(invalidView.retry_count, DEFAULT_RETRY_COUNT);
});

test('retry bounds match the generation retry policy', () => {
  assert.equal(DEFAULT_RETRY_COUNT, POLICY_DEFAULT_RETRY_COUNT);
  assert.equal(MAX_RETRY_COUNT, POLICY_MAX_RETRY_COUNT);
});

test('update rejects stale revisions, bad transports and duplicate names', async t => {
  const { api } = await fixture(t, base());
  const { revision } = await api.read();
  await assert.rejects(api.update({ revision: 'old', changes: {} }), e => e instanceof ConfigError && e.status === 409);
  const row = { enabled: true, transport: 'openai-images', base_url: 'https://x.example', model: 'm' };
  await assert.rejects(api.update({ revision, changes: { providers: [{ ...row, name: 'x', transport: 'bogus' }] } }), /unsupported transport/);
  await assert.rejects(api.update({ revision, changes: { providers: [{ ...row, name: 'x' }, { ...row, name: 'x' }] } }), /Duplicate/);
  await assert.rejects(api.update({ revision, changes: { providers: [{ ...row, name: 'x', base_url: 'ftp://x' }] } }), /http or https/);
});

test('workflows, connections and folders round-trip with validation', async t => {
  const { api, path } = await fixture(t, base());
  const { revision } = await api.read();
  await api.update({ revision, changes: {
    workflow_connections: [{ name: 'rh', from: 'runninghub', base_url: 'https://www.runninghub.ai' }],
    workflows: [{ name: 'up2', from: 'up', connection: 'rh', workflow_id: '456', output_node_ids: ['1', '2'], instance_type: 'plus', description: 'd', inputs: {} }],
    active_workflow: 'up2', output_dir: 'D:/out', cache_dir: '',
  } });
  const saved = JSON.parse(await readFile(path, 'utf8'));
  assert.equal(saved.workflow_connections.rh.api_key, 'rh-secret-value-1');
  assert.equal(saved.workflows.up2.extra, 1);
  assert.equal(saved.workflows.up2.connection, 'rh');
  assert.equal(saved.active_workflow, 'up2');
  assert.equal(saved.output_dir, 'D:/out');
  const next = await api.read();
  await assert.rejects(api.update({ revision: next.revision, changes: { workflows: [{ name: 'w', connection: 'missing', workflow_id: '1', output_node_ids: ['1'] }] } }), /unknown connection/);
  await assert.rejects(api.update({ revision: next.revision, changes: { workflow_connections: [{ name: 'r', base_url: 'https://evil.example' }] } }), /base_url must be one of/);
});

test('reveal returns one stored key on explicit request', async t => {
  const { api } = await fixture(t, base());
  assert.deepEqual(await api.reveal({ kind: 'provider', name: 'a' }), { api_key: SECRET });
  assert.deepEqual(await api.reveal({ kind: 'connection', name: 'runninghub' }), { api_key: 'rh-secret-value-1' });
  await assert.rejects(api.reveal({ kind: 'provider', name: 'missing' }), e => e.status === 404);
});

test('missing config file reads as empty and save creates it', async t => {
  const { api, path } = await fixture(t);
  const view = await api.read();
  assert.equal(view.exists, false);
  await api.update({ revision: null, changes: { providers: [{ name: 'p', enabled: true, transport: 'openai-images', base_url: 'https://p.example', model: 'm', api_key: 'k' }] } });
  assert.deepEqual(JSON.parse(await readFile(path, 'utf8')).active_providers, ['p']);
});

test('fetch routes answer JSON and map errors to status codes', async t => {
  const { api } = await fixture(t, base());
  const routes = new Map();
  registerConfigRoutes({ connection: { fetch: { register: r => { routes.set(r.path, r); return async () => {}; } } } }, api);
  assert.deepEqual([...routes.keys()], [CONFIG_ROUTE, REVEAL_ROUTE, MODELS_ROUTE]);
  const get = await routes.get(CONFIG_ROUTE).fetch(new Request('http://h' + CONFIG_ROUTE));
  assert.equal(get.status, 200);
  assert.equal(get.headers.get('cache-control'), 'no-store');
  const stale = await routes.get(CONFIG_ROUTE).fetch(new Request('http://h' + CONFIG_ROUTE, { method: 'PUT', body: JSON.stringify({ revision: 'x', changes: {} }) }));
  assert.equal(stale.status, 409);
  const bad = await routes.get(REVEAL_ROUTE).fetch(new Request('http://h' + REVEAL_ROUTE, { method: 'POST', body: 'not json' }));
  assert.equal(bad.status, 400);
  const models = await routes.get(MODELS_ROUTE).fetch(new Request('http://h' + MODELS_ROUTE, { method: 'POST', body: JSON.stringify({}) }));
  assert.equal(models.status, 400);
});

test('model list uses the stored key and normalizes OpenAI-compatible payloads', async t => {
  const { path } = await fixture(t, base());
  const calls = [];
  const api = createConfigApi(path, { fetch: async (url, init) => {
    calls.push({ url, init });
    return new Response(JSON.stringify({ data: [{ id: 'gpt-image-2.5' }, { id: 'gpt-image-2.5-sunburst' }] }), { status: 200 });
  } });
  const result = await api.listModels({ provider: 'a', transport: 'openai-images', base_url: 'https://a.example/v1' });
  assert.deepEqual(result.models, ['gpt-image-2.5', 'gpt-image-2.5-sunburst']);
  assert.equal(calls[0].url, 'https://a.example/v1/models');
  assert.equal(calls[0].init.headers.authorization, `Bearer ${SECRET}`);
  assert.ok(!JSON.stringify(result).includes(SECRET));
});

test('model list follows Gemini naming and keeps generateContent models only', async t => {
  const { path } = await fixture(t, base());
  const calls = [];
  const api = createConfigApi(path, { fetch: async (url, init) => {
    calls.push({ url, init });
    return new Response(JSON.stringify({ models: [
      { name: 'models/gemini-3-pro-image', supportedGenerationMethods: ['generateContent'] },
      { name: 'models/text-embedding-004', supportedGenerationMethods: ['embedContent'] },
    ] }), { status: 200 });
  } });
  const result = await api.listModels({ provider: 'b', transport: 'gemini-generate-content', base_url: 'https://b.example', api_key: 'fresh-key' });
  assert.deepEqual(result.models, ['gemini-3-pro-image']);
  assert.equal(calls[0].url, 'https://b.example/v1beta/models');
  assert.equal(calls[0].init.headers['x-goog-api-key'], 'fresh-key');
  assert.equal(calls[0].init.headers.authorization, undefined);
});

test('model list retries another path on 404 and reports upstream failures', async t => {
  const { path } = await fixture(t, base());
  const urls = [];
  const api = createConfigApi(path, { fetch: async url => {
    urls.push(url);
    if (url.endsWith('/v1/models')) return new Response('not found', { status: 404 });
    return new Response(JSON.stringify([{ id: 'mj-v8.2' }]), { status: 200 });
  } });
  const result = await api.listModels({ provider: 'c', transport: 'midjourney', base_url: 'https://c.example' });
  assert.deepEqual(result.models, ['mj-v8.2']);
  assert.deepEqual(urls, ['https://c.example/v1/models', 'https://c.example/models']);

  const failing = createConfigApi(path, { fetch: async () => new Response('busy', { status: 429 }) });
  await assert.rejects(
    failing.listModels({ provider: 'c', transport: 'midjourney', base_url: 'https://c.example' }),
    error => error instanceof ConfigError && error.status === 502 && /HTTP 429/.test(error.message),
  );
  await assert.rejects(
    failing.listModels({ provider: 'c', transport: 'midjourney', base_url: 'https://c.example', api_key: '' }),
    /Fill in an API key/,
  );
});
