import { api, TOKEN_KEY, USER_KEY } from './api';

// Shared post-auth completion for login, signup and Google sign-in:
// store the token, fetch the profile, store mk_user, route by role.
// requireSuperadmin (admin login mode) rejects non-superadmins.
// fallbackName seeds mk_user when the profile fetch fails.
// Returns { ok: true } or { ok: false, error } — caller shows the error.
export async function completeLogin(token, router, { requireSuperadmin = false, fallbackEmail = '', fallbackName = '' } = {}) {
  localStorage.setItem(TOKEN_KEY, token);
  try {
    // Pass the token explicitly: it was just issued and the read-back from
    // storage is not worth depending on here.
    const res = await api('/v1/auth/me', { token });
    const me = await res.json();
    const platformRole = me.platform_role || 'user';

    if (requireSuperadmin && platformRole !== 'superadmin') {
      localStorage.removeItem(TOKEN_KEY);
      return { ok: false, error: 'This account does not have admin access.' };
    }

    localStorage.setItem(USER_KEY, JSON.stringify({
      id: me.id || '',
      email: me.email || fallbackEmail,
      full_name: me.full_name || fallbackName || fallbackEmail,
      platform_role: platformRole,
    }));
    router.push(platformRole === 'superadmin' ? '/admin' : '/dashboard');
    return { ok: true };
  } catch {
    if (requireSuperadmin) {
      // Never grant the admin surface on an unverified role.
      localStorage.removeItem(TOKEN_KEY);
      return { ok: false, error: 'Could not verify admin access. Please try again.' };
    }
    localStorage.setItem(USER_KEY, JSON.stringify({
      email: fallbackEmail,
      full_name: fallbackName || fallbackEmail,
      platform_role: 'user',
    }));
    router.push('/dashboard');
    return { ok: true };
  }
}
