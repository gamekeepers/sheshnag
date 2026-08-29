# Google sign-in (OAuth/OIDC) — how Sheshnag implements it

*Last updated 2026-08-02. Moved into its current location on 2026-08-26 and **not** re-verified against code since.*

This doc covers Sheshnag specifics only; for how OAuth/OIDC works as a
mechanism, see any OIDC primer (the flow below assumes you know an ID token
is a signed JWT verified against Google's public keys).

## Flow

```
Frontend                      Backend                        Google
GoogleAuthButton ── click ──────────────────────────────▶ account picker popup
        ◀────────────── signed ID token (JWT) ──────────────────┘
POST /v1/auth/google {id_token}
        └──▶ verify_google_token():            (auth.py)
             signature vs Google JWKS, aud == GOOGLE_CLIENT_ID, iss, exp
             email_verified gate
        └──▶ account logic:                    (routers/auth.py::google_auth)
             1. lookup users.google_id == token.sub   → login
             2. else lookup by normalized email       → link (auth_provider="both")
             3. else create user + personal org       → is_new_user=true
        ◀── Sheshnag JWT (GoogleTokenOut) — Google token discarded
completeLogin(): store token, fetch /v1/auth/me, route by role
```

## Files

- `app/components/GoogleAuthProviderWrapper.js` — mounts the Google script; no-op without client id
- `app/components/GoogleAuthButton.js` — styled button over invisible GoogleLogin iframe; hides when unconfigured
- `app/lib/completeLogin.js` — shared post-auth completion
- `backend/auth.py::verify_google_token` — token verification (reads GOOGLE_CLIENT_ID at call time)
- `backend/routers/auth.py::google_auth` — account login/link/create
- `backend/models.py` — `users.google_id` (unique, = Google `sub`), `users.auth_provider` (local|google|both)
- `backend/tests/test_auth_google.py`

## Setup

1. Google Cloud Console → OAuth client (Web application); authorized JS origin
   `http://localhost:3000` (exact origin, incl. port). No redirect URI needed
   (ID-token flow). Consent screen in Testing → add yourself as a test user.
2. `.env.local`: `NEXT_PUBLIC_GOOGLE_CLIENT_ID=<id>` (untracked; see `.env.example`)
3. `backend/.env`: `GOOGLE_CLIENT_ID=<same id>` (backend verifies token audience against it)
4. Restart both servers — env is read at startup. The client id is PUBLIC (it
   ships in the JS bundle); there is no client secret in this flow.

## Rules

- Password login for a Google-only account → 400 "uses Google sign-in"
  (deliberate enumeration tradeoff).
- Linking requires `email_verified=true` from Google; emails normalized
  (lower/trim) on all paths.
- Never key accounts on email — `sub` is the permanent identity.

## Troubleshooting (field-tested)

| Symptom | Cause |
| --- | --- |
| No button at all | `NEXT_PUBLIC_GOOGLE_CLIENT_ID` unset at *server start* (by design) — set env, restart `next dev` |
| 500 "not configured" on click | backend `GOOGLE_CLIENT_ID` missing — check `backend/.env` line breaks, restart uvicorn |
| Google `access_denied` | your account isn't a test user on the consent screen |
| `origin_mismatch` in console | browsing origin not in Authorized JS origins (localhost ≠ 127.0.0.1) |
| Button renders, click does nothing | third-party-cookie/FedCM blocking (Brave/strict FF) |
