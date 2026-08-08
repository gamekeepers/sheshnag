'use client';

import { useState, Suspense } from 'react';
import Link from 'next/link';
import { useSearchParams } from 'next/navigation';

const BACKEND = process.env.NEXT_PUBLIC_BACKEND_URL || 'http://localhost:8005';

const INK = '#16182d';
const MUTED = '#5c5f73';
const PAPER = '#faf8f5';
const LINE = 'rgba(22,24,45,0.12)';

const inputStyle = (disabled) => ({
  width: '100%', boxSizing: 'border-box',
  background: disabled ? 'rgba(22,24,45,0.04)' : '#fff',
  border: '1px solid rgba(22,24,45,0.2)',
  borderRadius: 12, padding: '12px 14px', color: INK, fontSize: 15, outline: 'none',
});

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

function ResetPasswordForm() {
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [success, setSuccess] = useState(false);
  const searchParams = useSearchParams();
  const token = searchParams.get('token');

  async function handleReset() {
    if (!token) {
      setError('Invalid or missing reset token.');
      return;
    }
    if (!password || !confirm) {
      setError('Please fill in all fields.');
      return;
    }
    if (password !== confirm) {
      setError('Passwords do not match.');
      return;
    }
    setLoading(true);
    setError('');

    try {
      const res = await fetch(`${BACKEND}/v1/auth/reset-password`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...(process.env.NEXT_PUBLIC_NGROK_ENABLED === 'true' && { 'ngrok-skip-browser-warning': 'true' }) },
        body: JSON.stringify({ token, new_password: password }),
      });

      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        setError(data?.detail || 'Failed to reset password. Token may be expired.');
        return;
      }

      setSuccess(true);
    } catch {
      setError('Cannot reach server. Please try again.');
    } finally {
      setLoading(false);
    }
  }

  if (success) {
    return (
      <div style={{ textAlign: 'center' }}>
        <h1 style={{ fontFamily: "Georgia, 'Times New Roman', serif", fontWeight: 400, fontSize: 30, margin: '0 0 10px' }}>
          Password reset
        </h1>
        <p style={{ color: MUTED, fontSize: 14.5, margin: '0 0 28px' }}>
          Your password has been updated. Sign in with the new one.
        </p>
        <Link href="/login" style={{ ...primaryButtonStyle(false), display: 'block', textAlign: 'center', textDecoration: 'none', boxSizing: 'border-box' }}>
          Back to login
        </Link>
      </div>
    );
  }

  return (
    <>
      <h1 style={{ fontFamily: "Georgia, 'Times New Roman', serif", fontWeight: 400, fontSize: 30, textAlign: 'center', margin: '0 0 6px' }}>
        Reset password
      </h1>
      <p style={{ textAlign: 'center', color: MUTED, fontSize: 14.5, margin: '0 0 28px' }}>
        Enter your new password below.
      </p>

      {!token && (
        <div style={{ background: 'rgba(179,55,44,0.07)', border: '1px solid rgba(179,55,44,0.35)', borderRadius: 12, padding: '10px 14px', color: '#b3372c', fontSize: 13, textAlign: 'center', marginBottom: 16 }}>
          No reset token found in URL. Please request a new link.
        </div>
      )}

      <div style={{ marginBottom: 16 }}>
        <span style={labelStyle}>New password</span>
        <input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          disabled={!token}
          autoComplete="new-password"
          style={inputStyle(!token)}
        />
      </div>

      <div style={{ marginBottom: 16 }}>
        <span style={labelStyle}>Confirm password</span>
        <input
          type="password"
          value={confirm}
          onChange={(e) => setConfirm(e.target.value)}
          disabled={!token}
          autoComplete="new-password"
          style={inputStyle(!token)}
        />
      </div>

      {error && <p style={{ color: '#b3372c', fontSize: 13, marginBottom: 14 }}>{error}</p>}

      <button onClick={handleReset} disabled={loading || !token} style={primaryButtonStyle(loading || !token)}>
        {loading ? 'Resetting…' : 'Reset password'}
      </button>
    </>
  );
}

export default function ResetPasswordPage() {
  return (
    <div style={{ background: PAPER, minHeight: '100vh', display: 'flex', flexDirection: 'column', color: INK, fontFamily: "'Geist', 'Inter', -apple-system, sans-serif" }}>
      <div style={{ padding: '22px 28px' }}>
        <Link href="/" style={{ textDecoration: 'none' }}><Logo /></Link>
      </div>

      <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 24 }}>
        <div style={{ width: '100%', maxWidth: 400, background: '#fff', border: `1px solid ${LINE}`, borderRadius: 20, padding: '36px 32px' }}>
          <Suspense fallback={<div style={{ color: MUTED, textAlign: 'center', fontSize: 14 }}>Loading…</div>}>
            <ResetPasswordForm />
          </Suspense>
        </div>
      </div>
    </div>
  );
}
