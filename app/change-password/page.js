'use client';

import { useState, useEffect } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { changePassword } from '../lib/changePassword';

const INK = '#16182d';
const MUTED = '#5c5f73';
const PAPER = '#faf8f5';
const LINE = 'rgba(22,24,45,0.12)';

const inputStyle = {
  width: '100%', boxSizing: 'border-box', background: '#fff',
  border: '1px solid rgba(22,24,45,0.2)',
  borderRadius: 12, padding: '12px 14px', color: INK, fontSize: 15, outline: 'none',
};

const labelStyle = { fontSize: 13, color: MUTED, marginBottom: 6, display: 'block' };

const primaryButtonStyle = (disabled) => ({
  width: '100%', padding: '13px', borderRadius: 999, border: `1px solid ${INK}`,
  background: INK, color: PAPER, fontSize: 15, fontWeight: 500,
  cursor: disabled ? 'default' : 'pointer', opacity: disabled ? 0.6 : 1,
});

function Logo({ size = 24 }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
      <svg width={size} height={size} viewBox="0 0 32 32" fill="none">
        <path d="M22.5 6.5C11.5 5.5 9.5 13 15.5 15.6C21.5 18.2 23 25.5 11 26.5" stroke={INK} strokeWidth="3.2" strokeLinecap="round"/>
        <circle cx="23" cy="6.7" r="2.2" fill={INK}/>
      </svg>
      <span style={{ color: INK, fontSize: size * 0.55, fontWeight: 700, letterSpacing: '0.14em' }}>SHESHNAG</span>
    </div>
  );
}

/**
 * The forced password change, shown when the account carries
 * must_change_password — which the backend sets on the seeded superadmin.
 *
 * Reached only by redirect from completeLogin() or from the portal guards;
 * someone arriving here with nothing to change can leave via the link at the
 * bottom. There is deliberately no "skip": the seeded account ships with a
 * published password, and this page is what stands in front of it.
 */
export default function ChangePasswordPage() {
  const router = useRouter();
  const [oldPassword, setOldPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  // Only bounce anonymous visitors. The role is read at redirect time instead
  // of being mirrored into state — nothing renders differently for it, and
  // localStorage is unavailable while the page is prerendered.
  useEffect(() => {
    if (!localStorage.getItem('mk_token')) router.replace('/login');
  }, [router]);

  async function handleSubmit(e) {
    e.preventDefault();
    setLoading(true);
    setError('');

    const result = await changePassword({
      oldPassword,
      newPassword,
      confirmPassword: confirm,
    });

    setLoading(false);
    if (!result.ok) {
      setError(result.error);
      return;
    }
    // The flag is cleared server-side by the same call, so the portal guards
    // will let this session through from here.
    let role = 'user';
    try {
      role = JSON.parse(localStorage.getItem('mk_user') || '{}').platform_role || 'user';
    } catch { /* fall back to the user portal */ }
    router.replace(role === 'superadmin' ? '/admin' : '/dashboard');
  }

  return (
    <div style={{ background: PAPER, minHeight: '100vh', display: 'flex', flexDirection: 'column', color: INK, fontFamily: "'Geist', 'Inter', -apple-system, sans-serif" }}>
      <div style={{ padding: '22px 28px' }}>
        <Link href="/" style={{ textDecoration: 'none' }}><Logo /></Link>
      </div>

      <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 24 }}>
        <form
          onSubmit={handleSubmit}
          style={{ width: '100%', maxWidth: 400, background: '#fff', border: `1px solid ${LINE}`, borderRadius: 20, padding: '36px 32px' }}
        >
          <h1 style={{ fontFamily: "Georgia, 'Times New Roman', serif", fontWeight: 400, fontSize: 30, textAlign: 'center', margin: '0 0 6px' }}>
            Choose a new password
          </h1>
          <p style={{ textAlign: 'center', color: MUTED, fontSize: 14.5, margin: '0 0 28px' }}>
            This account is still using the password it was created with. Pick
            your own before continuing.
          </p>

          <div style={{ marginBottom: 16 }}>
            <span style={labelStyle}>Current password</span>
            <input
              type="password"
              value={oldPassword}
              onChange={(e) => setOldPassword(e.target.value)}
              autoComplete="current-password"
              style={inputStyle}
            />
          </div>

          <div style={{ marginBottom: 16 }}>
            <span style={labelStyle}>New password</span>
            <input
              type="password"
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              autoComplete="new-password"
              style={inputStyle}
            />
          </div>

          <div style={{ marginBottom: 16 }}>
            <span style={labelStyle}>Confirm new password</span>
            <input
              type="password"
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
              autoComplete="new-password"
              style={inputStyle}
            />
          </div>

          {error && <p style={{ color: '#b3372c', fontSize: 13, marginBottom: 14 }}>{error}</p>}

          <button type="submit" disabled={loading} style={primaryButtonStyle(loading)}>
            {loading ? 'Saving…' : 'Set password'}
          </button>
        </form>
      </div>
    </div>
  );
}
