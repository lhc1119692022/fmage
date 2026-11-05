// Annotation workspace for the Deskop sidebar: the browser draws the layer,
// this module owns the files on disk.
//
// The provider needs the annotated image and, for markup edits, a separate
// transparent PNG that shares its canvas exactly. The browser has the pixels
// but no writable path, and the Conversation layer materializes an attachment
// only after the message is sent. Writing both files here means the edit can
// name real paths before anything is submitted.
import { mkdir, readFile, readdir, rm, stat, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { randomBytes } from 'node:crypto';

export const ANNOTATION_ROUTE = '/api/fmage.annotation';
const NO_STORE = { 'cache-control': 'no-store' };
const MAX_IMAGE_BYTES = 32 * 1024 * 1024;
const MAX_REQUEST_BYTES = 96 * 1024 * 1024;
const MANIFEST_NAME = 'annotation.json';
// Each save writes a new directory and nothing ever overwrites one, because a
// request may still be reading the layer it was given. Old workspaces are
// removed by age instead: a week is far longer than any single edit.
const RETENTION_MS = 7 * 24 * 60 * 60 * 1000;

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
  let bytes;
  try {
    bytes = Buffer.from(payload, 'base64');
  } catch {
    throw new AnnotationError(`${label} is not valid base64.`);
  }
  if (bytes.byteLength === 0) throw new AnnotationError(`${label} is empty.`);
  if (bytes.byteLength > MAX_IMAGE_BYTES) {
    throw new AnnotationError(`${label} exceeds ${MAX_IMAGE_BYTES} bytes.`, 413);
  }
  // A PNG signature keeps a mislabeled JPEG/WebP out of the markup field: the
  // provider requires PNG for the layer and checks the alpha channel.
  if (bytes.subarray(0, 8).toString('binary') !== '\x89PNG\r\n\x1a\n') {
    throw new AnnotationError(`${label} must be a PNG.`);
  }
  return bytes;
}

function normalizeComments(value) {
  if (value === undefined) return [];
  if (!Array.isArray(value)) throw new AnnotationError('comments must be an array.');
  return value.slice(0, 200).map((entry, index) => {
    if (entry === null || typeof entry !== 'object') {
      throw new AnnotationError(`comments[${index}] must be an object.`);
    }
    const text = typeof entry.text === 'string' ? entry.text.trim().slice(0, 2000) : '';
    if (!text) throw new AnnotationError(`comments[${index}].text is empty.`);
    const id = Number.isInteger(entry.id) ? entry.id : index + 1;
    const marker = entry.marker === null || entry.marker === undefined ? null : entry.marker;
    return { id, text, marker };
  });
}

function manifestOf(body) {
  const mode = body.mode === 'flatten' ? 'flatten' : body.mode === 'markup' ? 'markup' : '';
  if (!mode) throw new AnnotationError('mode must be "markup" or "flatten".');
  return {
    version: 1,
    mode,
    comments: normalizeComments(body.comments),
    sourceName: typeof body.sourceName === 'string' ? body.sourceName.slice(0, 200) : '',
    canvas: body.canvas ?? null,
  };
}

/**
 * Drop workspaces older than the retention window.
 *
 * Best effort and age-based only: the caller's own directory is always kept,
 * and a directory without our manifest is never touched.
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
      const manifest = await stat(join(target, MANIFEST_NAME));
      if (manifest.mtimeMs < cutoff) await rm(target, { recursive: true, force: true });
    } catch { /* not an annotation workspace, or already gone */ }
  }));
}

/**
 * Persist one annotation workspace.
 *
 * Writes `image.png` (the untouched target), `markup.png` (the layer, markup
 * mode only) and `annotation.json`. Revisioned, never overwritten: a layer the
 * caller replaced is still referenced by whatever request already read it.
 */
export function createAnnotationApi(root) {
  const directory = join(root, 'annotations');

  return {
    directory,

    async read(body) {
      const id = annotationId(body?.id);
      const target = join(directory, id);
      let manifest;
      try {
        manifest = JSON.parse(await readFile(join(target, MANIFEST_NAME), 'utf8'));
      } catch {
        throw new AnnotationError('Annotation not found.', 404);
      }
      return { id, directory: target, manifest: join(target, MANIFEST_NAME), ...manifest };
    },

    async save(body) {
      if (body === null || typeof body !== 'object') {
        throw new AnnotationError('Annotation request must be an object.');
      }
      const meta = manifestOf(body);
      // A markup layer is the whole artifact: the target image is already in the
      // message, and copying it here would only invite a second, stale version.
      // A flattened annotation replaces the image, so it must carry its pixels.
      if (meta.mode === 'markup' && body.markup === undefined) {
        throw new AnnotationError('A markup annotation needs the transparent layer.');
      }
      if (meta.mode === 'flatten' && body.image === undefined) {
        throw new AnnotationError('A flattened annotation needs the composited image.');
      }
      const image = body.image === undefined ? undefined : decodePng(body.image, 'image');
      const markup = body.markup === undefined ? undefined : decodePng(body.markup, 'markup');
      if (meta.mode === 'flatten' && markup !== undefined) {
        throw new AnnotationError('A flattened annotation must not carry a separate layer.');
      }

      const id = randomBytes(8).toString('hex');
      const target = join(directory, id);
      await mkdir(target, { recursive: true });
      if (image !== undefined) await writeFile(join(target, 'image.png'), image);
      if (markup !== undefined) await writeFile(join(target, 'markup.png'), markup);

      const manifest = {
        ...meta,
        id,
        createdAt: new Date().toISOString(),
        image: image === undefined ? null : join(target, 'image.png'),
        markup: markup === undefined ? null : join(target, 'markup.png'),
      };
      await writeFile(join(target, MANIFEST_NAME), JSON.stringify(manifest, null, 2) + '\n', 'utf8');
      // Best effort; a failed sweep never fails the save the user asked for.
      // Awaited so the response reflects the state on disk.
      await pruneExpired(directory, target);
      return { id, directory: target, manifest: join(target, MANIFEST_NAME), ...manifest };
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
