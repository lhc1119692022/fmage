// Fmage settings page data layer: a narrow view of providers.json for the
// DeepSeek Desktop settings section. Unknown fields are preserved on write;
// secrets leave the Host masked unless one value is revealed explicitly.
import { createHash } from 'node:crypto';
import { copyFile, mkdir, readFile, rename, writeFile } from 'node:fs/promises';
import { homedir } from 'node:os';
import { dirname, join } from 'node:path';

export const TRANSPORTS = ['openai-images', 'gemini-generate-content', 'midjourney'];
export const RUNNINGHUB_ORIGINS = ['https://www.runninghub.cn', 'https://www.runninghub.ai'];
export const INSTANCE_TYPES = ['default', 'plus', 'ultra'];
const NAME = /^[^\u0000-\u001f]{1,100}$/;
const DIGITS = /^\d{1,40}$/;
const MODEL_LIST_TIMEOUT_MS = 15000;
const MODEL_LIST_LIMIT = 500;
const MODEL_AUTH_SCHEMES = ['bearer', 'x-goog-api-key'];
const MODEL_LIST_USER_AGENT = 'fmage-model-list/1.0';

export class ConfigError extends Error {
  constructor(message, status = 400) { super(message); this.status = status; }
}

/** Same resolution order as mcp/server.mjs. */
export function resolveConfigPath(env = process.env) {
  return env.FMAGE_CONFIG || join(env.CODEX_HOME || join(homedir(), '.codex'), 'fmage', 'providers.json');
}

/** Mask a secret: keep a few edge characters, never the middle. */
export function maskSecret(value) {
  if (typeof value !== 'string' || value === '') return '';
  if (value.length <= 4) return '*'.repeat(value.length);
  const edge = value.length >= 12 ? 4 : value.length >= 8 ? 2 : 1;
  return value.slice(0, edge) + '*'.repeat(Math.min(8, value.length - edge * 2)) + value.slice(-edge);
}

const isObject = value => value !== null && typeof value === 'object' && !Array.isArray(value);

function modelEndpoints(transport, base) {
  const versioned = /\/v\d+(?:beta)?$/i.test(base);
  if (transport === 'gemini-generate-content') {
    return versioned ? [`${base}/models`] : [`${base}/v1beta/models`, `${base}/v1/models`];
  }
  return versioned ? [`${base}/models`] : [`${base}/v1/models`, `${base}/models`];
}

function modelHeaders(transport, apiKey, authScheme) {
  const headers = { accept: 'application/json', 'user-agent': MODEL_LIST_USER_AGENT };
  if (transport === 'gemini-generate-content' && authScheme !== 'bearer') {
    headers['x-goog-api-key'] = apiKey;
  } else {
    headers.authorization = `Bearer ${apiKey}`;
  }
  return headers;
}

function modelIds(payload, transport) {
  const rows = Array.isArray(payload) ? payload
    : Array.isArray(payload?.data) ? payload.data
      : Array.isArray(payload?.models) ? payload.models
        : [];
  const ids = [];
  for (const row of rows) {
    if (typeof row === 'string') {
      ids.push(row);
      continue;
    }
    if (!isObject(row)) continue;
    const methods = row.supportedGenerationMethods ?? row.supported_generation_methods;
    if (
      transport === 'gemini-generate-content' &&
      Array.isArray(methods) &&
      !methods.includes('generateContent')
    ) continue;
    const id = row.id ?? row.name ?? row.model;
    if (typeof id === 'string') ids.push(id);
  }
  const seen = new Set();
  return ids
    .map(id => id.trim())
    .map(id => transport === 'gemini-generate-content' ? id.replace(/^models\//i, '') : id)
    .filter(id => id && !seen.has(id) && seen.add(id))
    .slice(0, MODEL_LIST_LIMIT);
}

async function fetchModelPayload(url, headers, fetchImpl, timeoutMs = MODEL_LIST_TIMEOUT_MS) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  timer.unref?.();
  try {
    const response = await fetchImpl(url, {
      method: 'GET', headers, cache: 'no-store', signal: controller.signal,
    });
    const body = await response.text();
    if (!response.ok) {
      const detail = body.trim().slice(0, 300);
      const error = new Error(`HTTP ${response.status}${detail ? `: ${detail}` : ''}`);
      error.status = response.status;
      throw error;
    }
    if (!body.trim()) return {};
    try { return JSON.parse(body); }
    catch { throw new ConfigError('Provider model list did not return JSON.', 502); }
  } catch (error) {
    if (error instanceof ConfigError) throw error;
    if (controller.signal.aborted) throw new ConfigError('Provider model list request timed out.', 502);
    throw error;
  } finally {
    clearTimeout(timer);
  }
}

/** Resolve the key for a model-list request without returning it to the browser. */
function modelApiKey(body, stored) {
  if (typeof body.api_key === 'string') {
    const provided = body.api_key.trim();
    if (!provided) throw new ConfigError('Fill in an API key before fetching models.');
    return provided;
  }
  if (!isObject(stored)) throw new ConfigError('Fill in an API key before fetching models.');
  const storedKey = typeof stored.api_key === 'string' ? stored.api_key.trim() : '';
  if (storedKey) return storedKey;
  const envName = typeof stored.api_key_env === 'string' ? stored.api_key_env.trim() : '';
  const envKey = envName ? String(process.env[envName] ?? '').trim() : '';
  if (envKey) return envKey;
  throw new ConfigError('Fill in an API key before fetching models.');
}

/** Fetch the upstream model list for the current (possibly unsaved) provider form. */
export async function listProviderModels(path, body, options = {}) {
  if (!isObject(body)) throw new ConfigError('Model list request must be a JSON object.');
  const transport = text(body.transport, 'transport', { required: true });
  if (!TRANSPORTS.includes(transport)) throw new ConfigError(`Unsupported transport: ${transport}.`);
  const base = httpUrl(body.base_url, 'base_url');
  const { config } = await load(path);
  const storedName = typeof body.provider === 'string' ? body.provider : '';
  const stored = storedName && isObject(config.providers?.[storedName]) ? config.providers[storedName] : null;
  const apiKey = modelApiKey(body, stored);
  const authScheme = body.auth_scheme === undefined
    ? (typeof stored?.auth_scheme === 'string' ? stored.auth_scheme : 'x-goog-api-key')
    : body.auth_scheme;
  if (!MODEL_AUTH_SCHEMES.includes(authScheme)) throw new ConfigError(`Unsupported auth_scheme: ${authScheme}.`);
  const fetchImpl = options.fetch ?? globalThis.fetch;
  if (typeof fetchImpl !== 'function') throw new ConfigError('Model list fetching is unavailable.', 500);

  const headers = modelHeaders(transport, apiKey, authScheme);
  let lastError = null;
  for (const url of modelEndpoints(transport, base)) {
    try {
      const payload = await fetchModelPayload(url, headers, fetchImpl, options.timeoutMs);
      return { models: modelIds(payload, transport), endpoint: url };
    } catch (error) {
      lastError = error;
      if ([404, 405].includes(error.status)) continue;
      throw new ConfigError(`Could not fetch models: ${error.message}`, 502);
    }
  }
  throw new ConfigError(`Could not fetch models: ${lastError?.message ?? 'no endpoint answered'}.`, 502);
}

async function load(path) {
  let bytes;
  try { bytes = await readFile(path); }
  catch (error) { if (error.code === 'ENOENT') return { exists: false, revision: null, config: {} }; throw error; }
  const revision = createHash('sha256').update(bytes).digest('hex');
  let config;
  try { config = JSON.parse(bytes.toString('utf8').replace(/^\uFEFF/, '')); }
  catch { throw new ConfigError(`providers.json is not valid JSON: ${path}`, 422); }
  if (!isObject(config)) throw new ConfigError('providers.json must contain a JSON object.', 422);
  return { exists: true, revision, config };
}

/** Build the page view; secrets are masked. */
export function view(path, { exists, revision, config }) {
  const providers = isObject(config.providers) ? config.providers : {};
  const active = Array.isArray(config.active_providers) ? config.active_providers.filter(n => Object.hasOwn(providers, n)) : [];
  const names = Object.keys(providers);
  // Enabled providers first in their priority order, then the disabled ones in file order.
  const ordered = [...active, ...names.filter(n => !active.includes(n))];
  const connections = isObject(config.workflow_connections) ? config.workflow_connections : {};
  const workflows = isObject(config.workflows) ? config.workflows : {};
  return {
    path, exists, revision,
    transports: TRANSPORTS, runninghubOrigins: RUNNINGHUB_ORIGINS, instanceTypes: INSTANCE_TYPES,
    providers: ordered.map(name => {
      const p = isObject(providers[name]) ? providers[name] : {};
      return {
        name, enabled: active.includes(name), transport: String(p.transport ?? ''),
        base_url: String(p.base_url ?? ''), model: String(p.model ?? ''),
        api_key_set: typeof p.api_key === 'string' && p.api_key !== '', api_key_masked: maskSecret(p.api_key),
        api_key_env: typeof p.api_key_env === 'string' ? p.api_key_env : '',
      };
    }),
    workflow_connections: Object.entries(connections).map(([name, c]) => ({
      name, base_url: String(c?.base_url ?? ''),
      api_key_set: typeof c?.api_key === 'string' && c.api_key !== '', api_key_masked: maskSecret(c?.api_key),
    })),
    active_workflow: typeof config.active_workflow === 'string' && Object.hasOwn(workflows, config.active_workflow) ? config.active_workflow : null,
    workflows: Object.entries(workflows).map(([name, w]) => ({
      name, connection: String(w?.connection ?? ''), workflow_id: String(w?.workflow_id ?? ''),
      description: String(w?.description ?? ''), instance_type: String(w?.instance_type ?? 'default'),
      output_node_ids: Array.isArray(w?.output_node_ids) ? w.output_node_ids.map(String) : [],
      inputs: isObject(w?.inputs) ? w.inputs : {},
    })),
    output_dir: typeof config.output_dir === 'string' ? config.output_dir : '',
    cache_dir: typeof config.cache_dir === 'string' ? config.cache_dir : '',
  };
}
function text(value, field, { required = false, max = 2000 } = {}) {
  if (value === undefined || value === null) value = '';
  if (typeof value !== 'string') throw new ConfigError(`${field} must be a string.`);
  const trimmed = value.trim();
  if (required && !trimmed) throw new ConfigError(`${field} is required.`);
  if (trimmed.length > max) throw new ConfigError(`${field} is too long.`);
  return trimmed;
}

function name(value, field, seen) {
  const result = text(value, field, { required: true, max: 100 });
  if (!NAME.test(result)) throw new ConfigError(`${field} contains invalid characters.`);
  if (seen.has(result)) throw new ConfigError(`Duplicate name: ${result}`);
  seen.add(result);
  return result;
}

function httpUrl(value, field) {
  const result = text(value, field, { required: true });
  let url;
  try { url = new URL(result); } catch { throw new ConfigError(`${field} must be a valid URL.`); }
  if (url.protocol !== 'https:' && url.protocol !== 'http:') throw new ConfigError(`${field} must use http or https.`);
  if (url.username || url.password) throw new ConfigError(`${field} must not embed credentials.`);
  return result.replace(/\/+$/, '');
}

/** api_key: undefined keeps the stored value, a string replaces it ('' clears). */
function applySecret(target, value, field) {
  if (value === undefined) return;
  if (typeof value !== 'string' || value.length > 4000 || /[\r\n]/.test(value)) throw new ConfigError(`${field} is invalid.`);
  target.api_key = value.trim();
}

function validInputs(inputs, field) {
  if (inputs === undefined) return {};
  if (!isObject(inputs)) throw new ConfigError(`${field} must be a JSON object.`);
  for (const [key, spec] of Object.entries(inputs)) {
    if (!isObject(spec) || !spec.node_id || !spec.field_name) throw new ConfigError(`${field}.${key} needs node_id and field_name.`);
    if (!['image', 'string', 'number', 'boolean'].includes(spec.type)) throw new ConfigError(`${field}.${key} has an unsupported type.`);
  }
  return inputs;
}

/**
 * Apply one submitted page state onto the stored config. Each row may carry
 * `from` (its stored name) so renames keep unshown fields.
 */
export function applyUpdate(config, update) {
  if (!isObject(update)) throw new ConfigError('Update must be a JSON object.');
  const next = structuredClone(config);
  const oldProviders = isObject(config.providers) ? config.providers : {};
  if (update.providers !== undefined) {
    if (!Array.isArray(update.providers)) throw new ConfigError('providers must be an array.');
    const seen = new Set();
    const providers = {};
    const active = [];
    for (const [index, row] of update.providers.entries()) {
      if (!isObject(row)) throw new ConfigError(`providers[${index}] must be an object.`);
      const key = name(row.name, `providers[${index}].name`, seen);
      const from = typeof row.from === 'string' && Object.hasOwn(oldProviders, row.from) ? row.from : null;
      const target = from && isObject(oldProviders[from]) ? structuredClone(oldProviders[from]) : {};
      const transport = text(row.transport, `${key}.transport`, { required: true });
      if (!TRANSPORTS.includes(transport)) throw new ConfigError(`${key}: unsupported transport ${transport}.`);
      target.transport = transport;
      target.base_url = httpUrl(row.base_url, `${key}.base_url`);
      target.model = text(row.model, `${key}.model`, { required: true, max: 200 });
      if (!from && row.api_key === undefined) target.api_key = '';
      applySecret(target, row.api_key, `${key}.api_key`);
      providers[key] = target;
      if (row.enabled === true) active.push(key);
    }
    next.providers = providers;
    next.active_providers = active;
  }
  const oldConnections = isObject(config.workflow_connections) ? config.workflow_connections : {};
  if (update.workflow_connections !== undefined) {
    if (!Array.isArray(update.workflow_connections)) throw new ConfigError('workflow_connections must be an array.');
    const seen = new Set();
    const connections = {};
    for (const [index, row] of update.workflow_connections.entries()) {
      if (!isObject(row)) throw new ConfigError(`workflow_connections[${index}] must be an object.`);
      const key = name(row.name, `workflow_connections[${index}].name`, seen);
      const from = typeof row.from === 'string' && Object.hasOwn(oldConnections, row.from) ? row.from : null;
      const target = from && isObject(oldConnections[from]) ? structuredClone(oldConnections[from]) : { api_key: '' };
      const base = text(row.base_url, `${key}.base_url`, { required: true }).replace(/\/+$/, '');
      if (!RUNNINGHUB_ORIGINS.includes(base)) throw new ConfigError(`${key}: base_url must be one of ${RUNNINGHUB_ORIGINS.join(', ')}.`);
      target.base_url = base;
      applySecret(target, row.api_key, `${key}.api_key`);
      connections[key] = target;
    }
    next.workflow_connections = connections;
  }
  const connectionNames = new Set(Object.keys(isObject(next.workflow_connections) ? next.workflow_connections : {}));
  const oldWorkflows = isObject(config.workflows) ? config.workflows : {};
  if (update.workflows !== undefined) {
    if (!Array.isArray(update.workflows)) throw new ConfigError('workflows must be an array.');
    const seen = new Set();
    const workflows = {};
    for (const [index, row] of update.workflows.entries()) {
      if (!isObject(row)) throw new ConfigError(`workflows[${index}] must be an object.`);
      const key = name(row.name, `workflows[${index}].name`, seen);
      const from = typeof row.from === 'string' && Object.hasOwn(oldWorkflows, row.from) ? row.from : null;
      const target = from && isObject(oldWorkflows[from]) ? structuredClone(oldWorkflows[from]) : {};
      const connection = text(row.connection, `${key}.connection`, { required: true, max: 100 });
      if (!connectionNames.has(connection)) throw new ConfigError(`${key}: unknown connection ${connection}.`);
      const workflowId = text(row.workflow_id, `${key}.workflow_id`, { required: true, max: 40 });
      if (!DIGITS.test(workflowId)) throw new ConfigError(`${key}: workflow_id must contain digits only.`);
      const outputs = Array.isArray(row.output_node_ids) ? row.output_node_ids.map(id => String(id).trim()).filter(Boolean) : [];
      if (!outputs.length || outputs.some(id => !DIGITS.test(id))) throw new ConfigError(`${key}: output_node_ids must be digit IDs.`);
      const instance = text(row.instance_type || 'default', `${key}.instance_type`);
      if (!INSTANCE_TYPES.includes(instance)) throw new ConfigError(`${key}: invalid instance_type.`);
      Object.assign(target, {
        connection, workflow_id: workflowId, description: text(row.description, `${key}.description`, { max: 4000 }),
        inputs: validInputs(row.inputs ?? target.inputs, `${key}.inputs`), output_node_ids: outputs, instance_type: instance,
      });
      workflows[key] = target;
    }
    next.workflows = workflows;
  }
  if (update.active_workflow !== undefined) {
    const active = update.active_workflow === null ? null : text(update.active_workflow, 'active_workflow', { max: 100 }) || null;
    if (active !== null && !Object.hasOwn(next.workflows ?? {}, active)) throw new ConfigError(`Unknown active_workflow: ${active}.`);
    next.active_workflow = active;
  } else if (typeof next.active_workflow === 'string' && !Object.hasOwn(next.workflows ?? {}, next.active_workflow)) {
    next.active_workflow = null;
  }
  for (const field of ['output_dir', 'cache_dir']) {
    if (update[field] !== undefined) next[field] = text(update[field], field, { max: 1000 });
  }
  return next;
}

async function save(path, current, next) {
  await mkdir(dirname(path), { recursive: true });
  if (current.exists) {
    const stamp = new Date().toISOString().replace(/[-:.]/g, '');
    const backups = join(dirname(path), 'backups');
    await mkdir(backups, { recursive: true });
    await copyFile(path, join(backups, `providers-${stamp}.json`));
  }
  const temporary = `${path}.${process.pid}.${Date.now()}.tmp`;
  await writeFile(temporary, JSON.stringify(next, null, 2) + '\n', { encoding: 'utf8', mode: 0o600 });
  await rename(temporary, path);
}

export const CONFIG_ROUTE = '/api/fmage.config';
export const REVEAL_ROUTE = '/api/fmage.config.reveal';
export const MODELS_ROUTE = '/api/fmage.models';
const NO_STORE = { 'cache-control': 'no-store' };

async function respond(task) {
  try {
    return Response.json(await task(), { headers: NO_STORE });
  } catch (error) {
    const status = error instanceof ConfigError ? error.status : 500;
    return Response.json({ error: status === 500 ? 'Fmage configuration request failed.' : error.message }, { status, headers: NO_STORE });
  }
}

async function jsonBody(request) {
  try { return await request.json(); } catch { throw new ConfigError('Request body must be JSON.'); }
}

/** Exact owner-only routes inside the Connection authentication fence. */
export function registerConfigRoutes(ctx, api) {
  ctx.connection.fetch.register({
    path: CONFIG_ROUTE, methods: ['GET', 'PUT'], requestBody: 'buffered',
    fetch: request => respond(async () => request.method === 'GET' ? api.read() : api.update(await jsonBody(request))),
  });
  ctx.connection.fetch.register({
    path: REVEAL_ROUTE, methods: ['POST'], requestBody: 'buffered',
    fetch: request => respond(async () => api.reveal(await jsonBody(request))),
  });
  ctx.connection.fetch.register({
    path: MODELS_ROUTE, methods: ['POST'], requestBody: 'buffered',
    fetch: request => respond(async () => api.listModels(await jsonBody(request))),
  });
}

/** Config operations used by the Fetch routes; `path` is fixed at construction. */
export function createConfigApi(path = resolveConfigPath(), options = {}) {
  const fetchImpl = options.fetch ?? globalThis.fetch;
  let queue = Promise.resolve();
  const serial = task => { const run = queue.then(task, task); queue = run.catch(() => {}); return run; };
  return {
    path,
    async read() { return view(path, await load(path)); },
    listModels(body) { return listProviderModels(path, body, { fetch: fetchImpl }); },
    update(body) {
      return serial(async () => {
        const current = await load(path);
        if (!isObject(body)) throw new ConfigError('Update must be a JSON object.');
        if (body.revision !== current.revision) throw new ConfigError('providers.json changed on disk; reload before saving.', 409);
        const next = applyUpdate(current.config, body.changes);
        await save(path, current, next);
        return view(path, await load(path));
      });
    },
    async reveal(body) {
      const { config } = await load(path);
      if (!isObject(body) || typeof body.name !== 'string') throw new ConfigError('Reveal needs kind and name.');
      const table = body.kind === 'provider' ? config.providers : body.kind === 'connection' ? config.workflow_connections : undefined;
      if (!isObject(table) || !isObject(table[body.name])) throw new ConfigError('Unknown entry.', 404);
      const value = table[body.name].api_key;
      return { api_key: typeof value === 'string' ? value : '' };
    },
  };
}
