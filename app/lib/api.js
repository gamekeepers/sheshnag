// The only module that knows where the backend is, or how to authenticate to
// it. Pages call api()/apiJson() and never mention the base URL, the bearer
// token, or the ngrok header — so moving a port, renaming the token key, or
// changing an auth scheme is a one-file change here rather than a sweep
// through every page. (The port move that prompted this touched 11 files.)

const BASE = process.env.NEXT_PUBLIC_BACKEND_URL || 'http://localhost:8005';
const NGROK = process.env.NEXT_PUBLIC_NGROK_ENABLED === 'true';

// Storage keys live here too — they were previously spelled out at 24 call
// sites, which is the same duplication problem in a different costume.
export const TOKEN_KEY = 'mk_token';
export const USER_KEY = 'mk_user';

// A missing backend URL in a production build is a deploy bug, not a runtime
// one: NEXT_PUBLIC_* values are inlined at build time, so without this the
// bundle silently ships pointing at the builder's own laptop and fails later
// as a confusing CORS or network error. `npm run dev` keeps the localhost
// fallback.
if (!process.env.NEXT_PUBLIC_BACKEND_URL && process.env.NODE_ENV === 'production') {
  throw new Error(
    'NEXT_PUBLIC_BACKEND_URL must be set for production builds — put it in ' +
    '.env (or .env.local for the deployed service). See docs/setup.md.'
  );
}

/** Absolute URL for a backend path. Only needed where fetch isn't used. */
export function apiUrl(path) {
  return `${BASE}${path}`;
}

/** The stored bearer token, or null. Safe to call during SSR. */
export function getToken() {
  if (typeof window === 'undefined') return null;
  return localStorage.getItem(TOKEN_KEY);
}

/**
 * Headers for a hand-rolled fetch — used where the body is a FormData and the
 * browser must set its own multipart Content-Type boundary.
 */
export function authHeaders(extra = {}) {
  const h = { ...extra };
  const token = getToken();
  if (token) h.Authorization = `Bearer ${token}`;
  if (NGROK) h['ngrok-skip-browser-warning'] = 'true';
  return h;
}

/**
 * fetch() against the backend.
 *
 *   api('/v1/files')                                  // GET, authenticated
 *   api('/v1/orgs', { method: 'POST', json: {...} })  // JSON body
 *   api('/v1/auth/login', { method: 'POST', json: {...}, auth: false })
 *   api('/v1/files', { method: 'POST', body: formData })
 *
 * Options beyond fetch's own:
 *   json  — serialized as the body; sets Content-Type. Omit for FormData.
 *   auth  — false for public endpoints, so a stale token is never sent.
 *   token — use this token instead of the stored one (for a just-issued one).
 *
 * Returns the raw Response; callers already branch on res.ok and read .json()
 * or .blob() themselves.
 */
export function api(path, { json, auth = true, token, headers, ...rest } = {}) {
  const h = { ...headers };
  // Only set for JSON bodies: forcing it on a FormData request would override
  // the multipart boundary the browser generates and the upload would fail.
  if (json !== undefined) h['Content-Type'] = 'application/json';
  if (NGROK) h['ngrok-skip-browser-warning'] = 'true';

  const bearer = token ?? (auth ? getToken() : null);
  if (bearer) h.Authorization = `Bearer ${bearer}`;

  return fetch(`${BASE}${path}`, {
    ...rest,
    headers: h,
    ...(json !== undefined && { body: JSON.stringify(json) }),
  });
}

/**
 * api() plus response handling: returns parsed JSON, throws on a non-2xx with
 * the backend's `detail` as the message. For the many call sites that only
 * want the data and a message to show.
 */
export async function apiJson(path, opts) {
  const res = await api(path, opts);
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    const err = new Error(data?.detail || `Request failed (${res.status})`);
    err.status = res.status;
    err.data = data;
    throw err;
  }
  return data;
}

/**
 * One SSE frame -> { type, data }, per the event-stream field grammar:
 * `field: value`, a leading space after the colon dropped, `:` comments and
 * unknown fields ignored, repeated `data` lines joined with newlines.
 */
function parseFrame(frame) {
  let type = 'message';
  const data = [];

  for (const line of frame.split(/\r\n|\n|\r/)) {
    if (!line || line.startsWith(':')) continue;
    const colon = line.indexOf(':');
    const field = colon === -1 ? line : line.slice(0, colon);
    let value = colon === -1 ? '' : line.slice(colon + 1);
    if (value.startsWith(' ')) value = value.slice(1);

    if (field === 'event') type = value;
    else if (field === 'data') data.push(value);
  }

  return { type, data: data.join('\n') };
}

/**
 * Server-sent events stream for a backend path, authenticated.
 *
 *   const es = eventStream(`/v1/batches/${id}/events`);
 *   es.addEventListener('validation_complete', () => { ...; es.close(); });
 *   es.addEventListener('error', () => es.close());
 *
 * Deliberately not `new EventSource()`: EventSource cannot set request
 * headers, so it cannot send the bearer token, and every SSE route here sits
 * behind `get_human_context`. This reads the stream over fetch(), which does
 * carry the Authorization header, and parses the frames itself.
 *
 * The returned handle is EventSource-shaped for the two methods call sites
 * use — addEventListener(type, fn) and close() — but does not reconnect:
 * these streams terminate after their one terminal event, and the caller
 * closes on 'error' rather than retrying.
 */
export function eventStream(path, { auth = true, token, headers } = {}) {
  const listeners = new Map();
  const controller = new AbortController();
  let closed = false;

  function emit(type, event) {
    // Copied: a handler calling close() or removeEventListener() must not
    // mutate the list being iterated.
    for (const fn of [...(listeners.get(type) || [])]) {
      try {
        fn(event);
      } catch (err) {
        console.error(`SSE ${type} handler failed:`, err);
      }
    }
  }

  (async () => {
    try {
      const res = await api(path, {
        auth,
        token,
        headers: { ...headers, Accept: 'text/event-stream' },
        cache: 'no-store',
        signal: controller.signal,
      });
      if (!res.ok) throw new Error(`SSE stream failed (${res.status})`);
      if (!res.body) throw new Error('SSE stream has no body');

      const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
      let buffer = '';

      while (!closed) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += value;

        // Frames are separated by a blank line. Whatever follows the last
        // separator is a partial frame and stays buffered for the next chunk.
        const frames = buffer.split(/\r\n\r\n|\n\n|\r\r/);
        buffer = frames.pop();
        for (const frame of frames) {
          if (!frame.trim()) continue;
          const event = parseFrame(frame);
          if (!closed) emit(event.type, event);
        }
      }
    } catch (err) {
      // An abort is our own close(), not a failure worth reporting.
      if (!closed) emit('error', { type: 'error', error: err });
    }
  })();

  return {
    addEventListener(type, fn) {
      if (!listeners.has(type)) listeners.set(type, []);
      listeners.get(type).push(fn);
    },
    removeEventListener(type, fn) {
      const fns = listeners.get(type);
      if (!fns) return;
      const i = fns.indexOf(fn);
      if (i !== -1) fns.splice(i, 1);
    },
    close() {
      if (closed) return;
      closed = true;
      controller.abort();
    },
  };
}
