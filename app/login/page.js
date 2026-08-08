'use client';

import { useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import GoogleAuthButton from '../components/GoogleAuthButton';
import confetti from 'canvas-confetti';
import { completeLogin } from '../lib/completeLogin';

const BACKEND = process.env.NEXT_PUBLIC_BACKEND_URL || 'http://localhost:8005';

const INK = '#16182d';
const MUTED = '#5c5f73';
const PAPER = '#faf8f5';
const LINE = 'rgba(22,24,45,0.12)';

const inputStyle = {
  width: '100%', boxSizing: 'border-box',
  background: '#fff', border: '1px solid rgba(22,24,45,0.2)',
  borderRadius: 12, padding: '12px 14px', color: INK, fontSize: 15, outline: 'none',
};

const labelStyle = {
  fontSize: 13, color: MUTED, marginBottom: 6, display: 'block',
};

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

export default function LoginPage() {
  const [mode, setMode] = useState('user'); // 'user' or 'admin'
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const router = useRouter();

  async function handleSubmit() {
    if (!email || !password) {
      setError('Please fill in all fields.');
      return;
    }

    setLoading(true);
    setError('');
    try {
      const res = await fetch(`${BACKEND}/v1/auth/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...(process.env.NEXT_PUBLIC_NGROK_ENABLED === 'true' && { 'ngrok-skip-browser-warning': 'true' }) },
        body: JSON.stringify({ email, password }),
      });
      const data = await res.json();
      if (!res.ok) {
        setError(data?.detail || 'Invalid email or password.');
        return;
      }

      confetti({ particleCount: 80, spread: 65, origin: { y: 0.6 }, colors: [INK, '#4a4fa3', '#c9c4ff'] });

      const result = await completeLogin(data.access_token, router, {
        requireSuperadmin: mode === 'admin',
        fallbackEmail: email,
      });
      if (!result.ok) setError(result.error);
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
          <h1 style={{ fontFamily: "Georgia, 'Times New Roman', serif", fontWeight: 400, fontSize: 30, textAlign: 'center', margin: '0 0 6px' }}>
            {mode === 'admin' ? 'Admin sign in' : 'Welcome back'}
          </h1>
          <p style={{ textAlign: 'center', color: MUTED, fontSize: 14.5, margin: '0 0 28px' }}>
            {mode === 'admin' ? 'Restricted to platform administrators.' : 'Sign in to your account.'}
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

          <div style={{ marginBottom: 16 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
              <span style={{ ...labelStyle, marginBottom: 0 }}>Password</span>
              <Link href="/forgot-password" style={{ fontSize: 12.5, color: '#4a4fa3', textDecoration: 'none' }}>Forgot password?</Link>
            </div>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
              style={inputStyle}
            />
          </div>

          {error && <p style={{ color: '#b3372c', fontSize: 13, marginBottom: 14 }}>{error}</p>}

          <button
            onClick={handleSubmit}
            disabled={loading}
            style={{
              width: '100%', padding: '13px', borderRadius: 999, border: `1px solid ${INK}`,
              background: INK, color: PAPER, fontSize: 15, fontWeight: 500,
              cursor: loading ? 'default' : 'pointer', opacity: loading ? 0.6 : 1,
            }}
          >
            {loading ? 'Signing in…' : 'Continue'}
          </button>

          {mode !== 'admin' && (
            <GoogleAuthButton setError={setError} setLoading={setLoading} loading={loading} />
          )}

          {mode !== 'admin' && (
            <p style={{ textAlign: 'center', fontSize: 13.5, color: MUTED, marginTop: 20 }}>
              Don&apos;t have an account?{' '}
              <Link href="/signup" style={{ color: '#4a4fa3' }}>Sign up</Link>
            </p>
          )}

          <div style={{ marginTop: 20, borderTop: `1px solid ${LINE}`, paddingTop: 14, textAlign: 'center' }}>
            <button
              onClick={() => { setMode(mode === 'admin' ? 'user' : 'admin'); setError(''); }}
              style={{ background: 'transparent', border: 'none', cursor: 'pointer', fontSize: 12.5, color: 'rgba(22,24,45,0.45)' }}
            >
              {mode === 'admin' ? '👤 User sign in' : '🔒 Admin sign in'}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
