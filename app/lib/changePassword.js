const BACKEND = process.env.NEXT_PUBLIC_BACKEND_URL || 'http://localhost:8000';

/**
 * POST /v1/auth/change-password — the authenticated password change.
 *
 * Distinct from the forgot/reset pair: this one proves identity with the
 * current password rather than an emailed token, so it works for accounts
 * whose address cannot receive mail. The seeded superadmin
 * (admin@platform.com) is exactly that case, and until this was wired up the
 * endpoint had no caller at all.
 *
 * Shared by the forced-change page and the Settings card. Only the request
 * lives here — each surface renders in its own idiom.
 *
 * Returns { ok: true } or { ok: false, error } — callers display the error.
 */
export async function changePassword({ oldPassword, newPassword, confirmPassword }) {
  if (!oldPassword || !newPassword || !confirmPassword) {
    return { ok: false, error: 'Please fill in all fields.' };
  }
  if (newPassword !== confirmPassword) {
    return { ok: false, error: 'New passwords do not match.' };
  }
  if (newPassword === oldPassword) {
    return { ok: false, error: 'The new password must be different from the current one.' };
  }

  const token = typeof window !== 'undefined' ? localStorage.getItem('mk_token') : null;
  if (!token) {
    return { ok: false, error: 'Your session has expired. Please sign in again.' };
  }

  try {
    const res = await fetch(`${BACKEND}/v1/auth/change-password`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${token}`,
        ...(process.env.NEXT_PUBLIC_NGROK_ENABLED === 'true' && { 'ngrok-skip-browser-warning': 'true' }),
      },
      body: JSON.stringify({ old_password: oldPassword, new_password: newPassword }),
    });

    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      return { ok: false, error: data?.detail || 'Could not change password.' };
    }
    return { ok: true };
  } catch {
    return { ok: false, error: 'Cannot reach server. Please try again.' };
  }
}
