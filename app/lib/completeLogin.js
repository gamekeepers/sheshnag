const BACKEND = process.env.NEXT_PUBLIC_BACKEND_URL || 'http://localhost:8000';

function authHeaders(token) {
  return {
    'Authorization': `Bearer ${token}`,
    ...(process.env.NEXT_PUBLIC_NGROK_ENABLED === 'true' && { 'ngrok-skip-browser-warning': 'true' }),
  };
}

// Shared post-auth completion for login, signup and Google sign-in:
// store the token, fetch the profile, store mk_user, route by role.
// requireSuperadmin (admin login mode) rejects non-superadmins.
// fallbackName seeds mk_user when the profile fetch fails.
// Returns { ok: true } or { ok: false, error } — caller shows the error.
export async function completeLogin(token, router, { requireSuperadmin = false, fallbackEmail = '', fallbackName = '' } = {}) {
  localStorage.setItem('mk_token', token);
  try {
    const res = await fetch(`${BACKEND}/v1/auth/me`, { headers: authHeaders(token) });
    const me = await res.json();
    const platformRole = me.platform_role || 'user';

    if (requireSuperadmin && platformRole !== 'superadmin') {
      localStorage.removeItem('mk_token');
      return { ok: false, error: 'This account does not have admin access.' };
    }

    localStorage.setItem('mk_user', JSON.stringify({
      id: me.id || '',
      email: me.email || fallbackEmail,
      full_name: me.full_name || fallbackName || fallbackEmail,
      platform_role: platformRole,
    }));

    if (me.must_change_password) {
      router.push('/change-password');
      return { ok: true };
    }

    router.push(platformRole === 'superadmin' ? '/admin' : '/dashboard');
    return { ok: true };
  } catch {
    if (requireSuperadmin) {
      // Never grant the admin surface on an unverified role.
      localStorage.removeItem('mk_token');
      return { ok: false, error: 'Could not verify admin access. Please try again.' };
    }
    localStorage.setItem('mk_user', JSON.stringify({
      email: fallbackEmail,
      full_name: fallbackName || fallbackEmail,
      platform_role: 'user',
    }));
    router.push('/dashboard');
    return { ok: true };
  }
}
