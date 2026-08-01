'use client';

import { useState } from 'react';
import Link from 'next/link';

const BACKEND = process.env.NEXT_PUBLIC_BACKEND_URL || 'http://localhost:8000';

const INK = '#16182d';
const MUTED = '#5c5f73';
const PAPER = '#faf8f5';
const LINE = 'rgba(22,24,45,0.12)';

const inputStyle = {
  width: '100%', boxSizing: 'border-box',
  background: '#fff', border: '1px solid rgba(22,24,45,0.2)',
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
        <circle cx="16" cy="16" r="12" fill={INK} />
        <circle cx="20" cy="13" r="10" fill={PAPER} />
      </svg>
      <span style={{ color: INK, fontSize: size * 0.55, fontWeight: 700, letterSpacing: '0.14em' }}>MOONKNIGHT</span>
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
      const res = await fetch(`${BACKEND}/v1/auth/forgot-password`, {
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
    <div style={{ background: PAPER, minHeight: '100vh', display: 'flex', flexDirection: 'column', color: INK, fontFamily: "'Geist', 'Inter', -apple-system, sans-serif" }}>
      <div style={{ padding: '22px 28px' }}>
        <Link href="/" style={{ textDecoration: 'none' }}><Logo /></Link>
      </div>

      <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 24 }}>
        <div style={{ width: '100%', maxWidth: 400, background: '#fff', border: `1px solid ${LINE}`, borderRadius: 20, padding: '36px 32px' }}>

          {step === 1 && (
            <>
              <h1 style={{ fontFamily: "Georgia, 'Times New Roman', serif", fontWeight: 400, fontSize: 30, textAlign: 'center', margin: '0 0 6px' }}>
                Forgot password?
              </h1>
              <p style={{ textAlign: 'center', color: MUTED, fontSize: 14.5, margin: '0 0 28px' }}>
                Enter your email and we&apos;ll send you a reset link.
              </p>

              <div style={{ marginBottom: 16 }}>
                <span style={labelStyle}>Email address</span>
                <input
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  autoComplete="email"
                  style={inputStyle}
                />
              </div>

              {error && <p style={{ color: '#b3372c', fontSize: 13, marginBottom: 14 }}>{error}</p>}

              <button onClick={handleSendCode} disabled={loading} style={primaryButtonStyle(loading)}>
                {loading ? 'Sending…' : 'Send reset link'}
              </button>

              <p style={{ textAlign: 'center', fontSize: 13.5, color: MUTED, marginTop: 20 }}>
                Remember your password?{' '}
                <Link href="/login" style={{ color: '#4a4fa3' }}>Log in</Link>
              </p>
            </>
          )}

          {step === 2 && (
            <div style={{ textAlign: 'center' }}>
              <h1 style={{ fontFamily: "Georgia, 'Times New Roman', serif", fontWeight: 400, fontSize: 30, margin: '0 0 10px' }}>
                Check your email
              </h1>
              <p style={{ color: MUTED, fontSize: 14.5, lineHeight: 1.6, margin: '0 0 28px' }}>
                We sent a password reset link to <span style={{ color: INK, fontWeight: 500 }}>{email}</span>.
                Click the link in the email to set a new password.
              </p>
              <Link href="/login" style={{ ...primaryButtonStyle(false), display: 'block', textAlign: 'center', textDecoration: 'none', boxSizing: 'border-box' }}>
                Back to login
              </Link>
            </div>
          )}

        </div>
      </div>
    </div>
  );
}
