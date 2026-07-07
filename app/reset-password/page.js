'use client';

import { useState, Suspense } from 'react';
import Link from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';
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
      <div className="text-center">
        <div className="text-5xl mb-4">✅</div>
        <h1 className="text-white text-2xl font-medium mb-2">
          Password reset!
        </h1>
        <p className="text-[#555] text-sm mb-8">
          Your password has been reset successfully.
        </p>
        <Link
          href="/login"
          className="w-full block bg-white text-black py-3 rounded-full text-sm font-medium hover:bg-gray-100 text-center"
        >
          Back to login
        </Link>
      </div>
    );
  }

  return (
    <>
      <h1 className="text-white text-2xl font-medium text-center mb-2">
        Reset password
      </h1>
      <p className="text-[#555] text-sm text-center mb-6">
        Enter your new password below.
      </p>

      {!token && (
        <div className="bg-red-900/30 border border-red-500/50 p-3 rounded text-red-400 text-xs mb-4 text-center">
          No reset token found in URL. Please request a new link.
        </div>
      )}

      <div className="relative mb-4">
        <label className="absolute -top-2 left-3 bg-[#0a0a0a] px-1 text-[11px] text-[#2d7dd6]">
          New password
        </label>
        <input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          disabled={!token}
          className="w-full bg-transparent border border-[#2d7dd6] rounded-full px-4 py-3 text-white text-sm outline-none focus:border-[#4d9cf8]"
        />
      </div>

      <div className="relative mb-4">
        <label className="absolute -top-2 left-3 bg-[#0a0a0a] px-1 text-[11px] text-[#2d7dd6]">
          Confirm password
        </label>
        <input
          type="password"
          value={confirm}
          onChange={(e) => setConfirm(e.target.value)}
          disabled={!token}
          className="w-full bg-transparent border border-[#2d7dd6] rounded-full px-4 py-3 text-white text-sm outline-none focus:border-[#4d9cf8]"
        />
      </div>

      {error && <p className="text-red-400 text-xs mb-3">{error}</p>}

      <button
        onClick={handleReset}
        disabled={loading || !token}
        className="w-full bg-white text-black py-3 rounded-full text-sm font-medium hover:bg-gray-100 disabled:opacity-50"
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
          <Suspense fallback={<div className="text-[#555] text-center text-sm">Loading...</div>}>
            <ResetPasswordForm />
          </Suspense>
        </div>
      </div>

      {/* Footer */}
      <div className="text-center py-4 border-t border-[#1a1a1a]">
        <Link href="#" className="text-[#444] text-xs underline mx-2">Terms of Use</Link>
        <span className="text-[#444] text-xs">|</span>
        <Link href="#" className="text-[#444] text-xs underline mx-2">Privacy Policy</Link>
      </div>

    </div>
  );
}
