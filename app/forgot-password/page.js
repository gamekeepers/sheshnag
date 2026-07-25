'use client';

import { useState } from 'react';
import Link from 'next/link';
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

export default function ForgotPasswordPage() {
  const [step, setStep] = useState(1);
  const [email, setEmail] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  async function handleSendCode() {
    if (!email) {
      setError('Please enter your email.');
      return;
    }
    setLoading(true);
    setError('');
    
    try {
      const res = await fetch(`${process.env.NEXT_PUBLIC_BACKEND_URL || 'http://localhost:8000'}/v1/auth/forgot-password`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...(process.env.NEXT_PUBLIC_NGROK_ENABLED === 'true' && { 'ngrok-skip-browser-warning': 'true' }) },
        body: JSON.stringify({ email }),
      });
      
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        setError(data?.detail || 'Failed to send reset link. Please try again.');
        return;
      }
      
      setStep(2);
    } catch {
      setError('Cannot reach server. Please try again.');
    } finally {
      setLoading(false);
    }
  }

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

          {/* Step 1 — Enter email */}
          {step === 1 && (
            <>
              <h1 className="text-white text-2xl font-medium text-center mb-2">
                Forgot password?
              </h1>
              <p className="text-[#555] text-sm text-center mb-6">
                Enter your email and we'll send you a reset link.
              </p>

              <div className="relative mb-4">
                <label className="absolute -top-2 left-3 bg-[#0a0a0a] px-1 text-[11px] text-[#2d7dd6]">
                  Email address
                </label>
                <input
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  className="w-full bg-transparent border border-[#2d7dd6] rounded-full px-4 py-3 text-white text-sm outline-none focus:border-[#4d9cf8]"
                />
              </div>

              {error && <p className="text-red-400 text-xs mb-3">{error}</p>}

              <button
                onClick={handleSendCode}
                disabled={loading}
                className="w-full bg-white text-black py-3 rounded-full text-sm font-medium hover:bg-gray-100 disabled:opacity-50"
              >
                {loading ? 'Sending...' : 'Send reset link'}
              </button>

              <p className="text-center text-[#666] text-sm mt-4">
                Remember your password?{' '}
                <Link href="/login" className="text-[#2d7dd6]">Log in</Link>
              </p>
            </>
          )}

          {/* Step 2 — Success */}
          {step === 2 && (
            <div className="text-center">
              <h1 className="text-white text-2xl font-medium mb-2">
                Check your email
              </h1>
              <p className="text-[#555] text-sm mb-8">
                We sent a password reset link to <span className="text-white">{email}</span>. Click the link in the email to set a new password.
              </p>
              <Link
                href="/login"
                className="w-full block bg-white text-black py-3 rounded-full text-sm font-medium hover:bg-gray-100 text-center"
              >
                Back to login
              </Link>
            </div>
          )}

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