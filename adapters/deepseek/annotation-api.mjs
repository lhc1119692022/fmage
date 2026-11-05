// Annotation workspace for the Desktop sidebar: the browser draws a layer,
// this module owns the file on disk.
//
// The model never sees this file's path. `edit_image` looks for the newest
// unused layer in `cache/annotations` and attaches it itself, so nothing has
// to be pasted into the conversation. That is why the directory is a cache:
// every entry is disposable, and an unused one expires.
//
// Layout, also read by mcp/server.mjs:
//   <root>/cache/annotations/<id>/layer.png
//   <root>/cache/annotations/<id>/meta.json  { id, mode, width, height, createdAt, consumedAt, comments }
import { mkdir, readFile, readdir, rm, stat, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { randomBytes } from 'node:crypto';

export const ANNOTATION_ROUTE = '/api/fmage.annotation';
export const ANNOTATION_CACHE_DIR = ['cache', 'annotations'];
const NO_STORE = { 'cache-control': 'no-store' };
const MAX_LAYER_BYTES = 32 * 1024 * 1024;
const MAX_REQUEST_BYTES = 40 * 1024 * 1024;
const META_NAME = 'meta.json';
const LAYER_NAME = 'layer.png';
// A layer is only meaningful for the edit the user is about to ask for. An
// hour is long enough to write a prompt and short enough that a forgotten
// layer cannot attach itself to an unrelated request later.
const RETENTION_MS = 60 * 60 * 1000;

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

function metaOf(body) {
  const mode = body.mode === 'flatten' ? 'flatten' : body.mode === 'markup' ? 'markup' : '';
  if (!mode) throw new AnnotationError('mode must be "markup" or "flatten".');
  const width = Number(body.width);
  const height = Number(body.height);
  if (!Number.isInteger(width) || !Number.isInteger(height) || width < 1 || height < 1) {
    throw new AnnotationError('width and height must be positive integers.');
  }
  return {
    version: 1,
    mode,
    width,
    height,
    comments: normalizeComments(body.comments),
    sourceName: typeof body.sourceName === 'string' ? body.sourceName.slice(0, 200) : '',
  };
}

/**
 * Drop entries past the retention window.
 *
 * Best effort and age-based: the entry just written is always kept, and a
 * directory without our meta file is never touched.
 */
async function pruneExpired(directory, keep) {
  let entries;
  try {
    entries = await readdir(directory, { withFileTypes: true });
  } catch {
    return;
  }
  const cutoff = Date.now() - RETENTION_MS;
  await Promise.all(entries.filter(entry => entry.isDirectory()).map(async entry => {
    const target = join(directory, entry.name);
    if (target === keep) return;
    try {
      const meta = await stat(join(target, META_NAME));
      if (meta.mtimeMs < cutoff) await rm(target, { recursive: true, force: true });
    } catch { /* not an annotation entry, or already gone */ }
  }));
}

/**
 * Persist one annotation layer.
 *
 * Only the marks are written. The image being annotated stays the one the user
 * attached, so there is no second copy to drift from it.
 */
export function createAnnotationApi(root) {
  const directory = join(root, ...ANNOTATION_CACHE_DIR);

  return {
    directory,

    async read(body) {
      const id = annotationId(body?.id);
      const target = join(directory, id);
      let meta;
      try {
        meta = JSON.parse(await readFile(join(target, META_NAME), 'utf8'));
      } catch {
        throw new AnnotationError('Annotation not found.', 404);
      }
      return { ...meta, id, directory: target, layer: join(target, LAYER_NAME) };
    },

    async save(body) {
      if (body === null || typeof body !== 'object') {
        throw new AnnotationError('Annotation request must be an object.');
      }
      const meta = metaOf(body);
      const layer = decodePng(body.layer, 'layer');

      const id = randomBytes(8).toString('hex');
      const target = join(directory, id);
      await mkdir(target, { recursive: true });
      await writeFile(join(target, LAYER_NAME), layer);
      const record = { ...meta, id, createdAt: new Date().toISOString(), consumedAt: null };
      await writeFile(join(target, META_NAME), JSON.stringify(record, null, 2) + '\n', 'utf8');
      await pruneExpired(directory, target);
      return { ...record, directory: target, layer: join(target, LAYER_NAME) };
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
      return api.save(body);
    }),
  });
}
