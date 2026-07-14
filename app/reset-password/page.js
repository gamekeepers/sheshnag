'use client';

import { useState, useEffect } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import ParticleField from '../components/ParticleField';

function MoonknightLogo({ size = 28 }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: size / 4 }}>
      <svg width={size} height={size} viewBox="0 0 32 32" fill="none">
        <circle cx="16" cy="16" r="12" fill="#fff" />
        <circle cx="20" cy="13" r="10" fill="#0a0a0a" />
      </svg>
      <span style={{ color: '#fff', fontSize: size * 0.45, fontWeight: 500, letterSpacing: '0.12em' }}>MOONKNIGHT</span>
    </div>
  );
}

function ResetPasswordForm() {
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [success, setSuccess] = useState(false);
  const [token, setToken] = useState('');

  useEffect(() => {
    if (typeof window !== 'undefined') {
      const params = new URLSearchParams(window.location.search);
      setToken(params.get('token') || '');
    }
  }, []);

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
      const res = await fetch(`${process.env.NEXT_PUBLIC_BACKEND_URL || 'http://localhost:8000'}/v1/auth/reset-password`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'ngrok-skip-browser-warning': 'true' },
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
        <div style={{ fontSize: '40px', marginBottom: '16px' }}>✅</div>
        <h1 style={{ color: '#fff', fontSize: '20px', fontWeight: 500, marginBottom: '8px' }}>
          Password reset!
        </h1>
        <p style={{ color: 'rgba(255,255,255,0.45)', fontSize: '13px', marginBottom: '24px' }}>
          Your password has been reset successfully.
        </p>
        <Link
          href="/login"
          style={{ width: '100%', display: 'block', backgroundColor: '#fff', color: '#000', padding: '12px 0', borderRadius: '24px', fontSize: '13px', fontWeight: 500, textDecoration: 'none', textAlign: 'center' }}
        >
          Back to login
        </Link>
      </div>
    );
  }

  return (
    <>
      <h1 style={{ color: '#fff', fontSize: '22px', fontWeight: 500, textAlign: 'center', marginBottom: '8px' }}>
        Reset password
      </h1>
      <p style={{ color: 'rgba(255,255,255,0.45)', fontSize: '13px', textAlign: 'center', marginBottom: '24px' }}>
        Enter your new password below.
      </p>

      {!token && (
        <div style={{ background: 'rgba(239,68,68,0.1)', border: '1px solid rgba(239,68,68,0.3)', padding: '12px', borderRadius: '8px', color: '#f87171', fontSize: '12px', marginBottom: '16px', textAlign: 'center' }}>
          No reset token found in URL. Please request a new link.
        </div>
      )}

      <div style={{ position: 'relative', marginBottom: '16px' }}>
        <span style={{ fontSize: '12px', color: 'rgba(255,255,255,0.55)', marginBottom: '6px', display: 'block' }}>New password</span>
        <input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          disabled={!token}
          style={{
            width: '100%', background: 'transparent', border: '1px solid rgba(255,255,255,0.25)',
            borderRadius: '24px', padding: '12px 16px', color: '#fff', fontSize: '14px', outline: 'none',
            WebkitTextFillColor: '#fff', caretColor: '#fff',
            WebkitBoxShadow: '0 0 0 1000px #050505 inset',
          }}
        />
      </div>

      <div style={{ position: 'relative', marginBottom: '16px' }}>
        <span style={{ fontSize: '12px', color: 'rgba(255,255,255,0.55)', marginBottom: '6px', display: 'block' }}>Confirm password</span>
        <input
          type="password"
          value={confirm}
          onChange={(e) => setConfirm(e.target.value)}
          disabled={!token}
          style={{
            width: '100%', background: 'transparent', border: '1px solid rgba(255,255,255,0.25)',
            borderRadius: '24px', padding: '12px 16px', color: '#fff', fontSize: '14px', outline: 'none',
            WebkitTextFillColor: '#fff', caretColor: '#fff',
            WebkitBoxShadow: '0 0 0 1000px #050505 inset',
          }}
        />
      </div>

      {error && <p style={{ color: '#f87171', fontSize: '12px', marginBottom: '12px' }}>{error}</p>}

      <button
        onClick={handleReset}
        disabled={loading || !token}
        style={{
          width: '100%', padding: '12px', background: 'transparent',
          border: '1px solid rgba(255,255,255,0.25)', color: (loading || !token) ? 'rgba(255,255,255,0.4)' : '#fff',
          borderRadius: '24px', fontSize: '14px', fontWeight: 500,
          cursor: (loading || !token) ? 'default' : 'pointer', marginTop: '6px',
        }}
      >
        {loading ? 'Resetting...' : 'Reset password'}
      </button>
    </>
  );
}

export default function ResetPasswordPage() {
  return (
    <div style={{ background: '#050505', minHeight: '100vh', display: 'flex', flexDirection: 'column', position: 'relative', fontFamily: 'sans-serif' }}>
      <ParticleField />

      {/* Top bar */}
      <div style={{ position: 'relative', zIndex: 3, padding: '20px 28px' }}>
        <Link href="/" style={{ display: 'flex', alignItems: 'center', gap: '8px', textDecoration: 'none' }}>
          <svg width="22" height="22" viewBox="0 0 32 32" fill="none">
            <circle cx="16" cy="16" r="12" fill="#fff" />
            <circle cx="20" cy="13" r="10" fill="#050505" />
          </svg>
          <span style={{ color: '#fff', fontSize: '14px', fontWeight: 500, letterSpacing: '0.12em' }}>MOONKNIGHT</span>
        </Link>
      </div>

      {/* Body */}
      <div style={{ position: 'relative', zIndex: 3, flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '24px' }}>
        <div style={{ width: '100%', maxWidth: '340px' }}>
          <ResetPasswordForm />
        </div>
      </div>

      {/* Footer */}
      <div style={{ textAlign: 'center', py: '16px', borderTop: '1px solid #1a1a1a', position: 'relative', zIndex: 3, padding: '16px' }}>
        <Link href="#" style={{ color: '#444', fontSize: '12px', textDecoration: 'underline', margin: '0 8px' }}>Terms of Use</Link>
        <span style={{ color: '#444', fontSize: '12px' }}>|</span>
        <Link href="#" style={{ color: '#444', fontSize: '12px', textDecoration: 'underline', margin: '0 8px' }}>Privacy Policy</Link>
      </div>

    </div>
  );
}
