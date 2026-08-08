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
// one: without this the bundle silently ships pointing at the developer's own
// laptop and fails later as a confusing CORS or network error.
if (!process.env.NEXT_PUBLIC_BACKEND_URL && process.env.NODE_ENV === 'production') {
  throw new Error('NEXT_PUBLIC_BACKEND_URL must be set for production builds');
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

/** Server-sent events stream for a backend path. */
export function eventSource(path) {
  return new EventSource(`${BASE}${path}`);
}
