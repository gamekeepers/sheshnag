'use client';

import { useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
<<<<<<< Updated upstream

function FalconLogo({ size = 28 }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: size / 4 }}>
      <svg width={size} height={size} viewBox="0 0 36 36" fill="none">
        <line x1="4" y1="28" x2="20" y2="8" stroke="#fff" strokeWidth="2.5" strokeLinecap="round"/>
        <line x1="12" y1="28" x2="28" y2="8" stroke="#fff" strokeWidth="2.5" strokeLinecap="round" opacity="0.5"/>
        <line x1="20" y1="28" x2="32" y2="12" stroke="#fff" strokeWidth="2.5" strokeLinecap="round" opacity="0.25"/>
      </svg>
      <span style={{ color: '#fff', fontSize: size * 0.55, fontWeight: 500, letterSpacing: '0.15em' }}>FALCON</span>
    </div>
  );
}
=======
import ParticleField from '../components/ParticleField';
import CursorEffect from '../components/CursorEffect';
>>>>>>> Stashed changes

export default function LoginPage() {
  const [mode, setMode] = useState('user');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const router = useRouter();

  const [loading, setLoading] = useState(false);

  async function handleSubmit() {
    if (!email || !password) {
      setError('Please fill in all fields.');
      return;
    }
    if (mode === 'admin') {
      router.push('/admin');
      return;
    }
    setLoading(true);
    setError('');
    try {
      const res = await fetch('https://hungry-whacking-reflex.ngrok-free.dev/v1/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'ngrok-skip-browser-warning': 'true' },
        body: JSON.stringify({ email, password }),
      });
      const data = await res.json();
      if (!res.ok) {
        setError(data?.detail || 'Invalid email or password.');
        return;
      }
      localStorage.setItem('mk_token', data.access_token);
      try {
        const meRes = await fetch('https://hungry-whacking-reflex.ngrok-free.dev/v1/auth/me', {
          headers: { 'Authorization': `Bearer ${data.access_token}`, 'ngrok-skip-browser-warning': 'true' },
        });
        const me = await meRes.json();
        localStorage.setItem('mk_user', JSON.stringify({
          email: me.email || email,
          full_name: me.full_name || email,
          role: me.role || 'user',
        }));
      } catch {
        localStorage.setItem('mk_user', JSON.stringify({ email, full_name: email }));
      }
      router.push('/jobs');
    } catch {
      setError('Cannot reach server. Please try again.');
    } finally {
      setLoading(false);
    }
  }

  return (
    <div style={{ background: '#050505', minHeight: '100vh', display: 'flex', flexDirection: 'column', position: 'relative', fontFamily: 'sans-serif' }}>
      <ParticleField />
      <CursorEffect />

<<<<<<< Updated upstream
      {/* Top bar */}
      <div className="px-7 py-5">
        <Link href="/"><FalconLogo size={26} /></Link>
=======
      <div style={{ position: 'relative', zIndex: 3, padding: '20px 28px' }}>
        <Link href="/" style={{ display: 'flex', alignItems: 'center', gap: '8px', textDecoration: 'none' }}>
          <svg width="22" height="22" viewBox="0 0 32 32" fill="none">
            <circle cx="16" cy="16" r="12" fill="#fff" />
            <circle cx="20" cy="13" r="10" fill="#03030a" />
          </svg>
          <span style={{ color: '#fff', fontSize: '14px', fontWeight: 500, letterSpacing: '0.12em' }}>MOONKNIGHT</span>
        </Link>
>>>>>>> Stashed changes
      </div>

      <div style={{ position: 'relative', zIndex: 3, flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '24px' }}>
        <div style={{ width: '100%', maxWidth: '340px' }}>

          <div style={{ display: 'flex', background: 'rgba(255,255,255,0.05)', border: '1px solid rgba(255,255,255,0.1)', borderRadius: '8px', padding: '3px', marginBottom: '20px' }}>
            <button
              onClick={() => { setMode('user'); setError(''); }}
              style={{
                flex: 1, padding: '7px', fontSize: '12px', borderRadius: '6px', border: 'none', cursor: 'pointer',
                background: mode === 'user' ? 'rgba(255,255,255,0.12)' : 'transparent',
                color: mode === 'user' ? '#fff' : 'rgba(255,255,255,0.4)',
              }}
            >
              User
            </button>
            <button
              onClick={() => { setMode('admin'); setError(''); }}
              style={{
                flex: 1, padding: '7px', fontSize: '12px', borderRadius: '6px', border: 'none', cursor: 'pointer',
                background: mode === 'admin' ? 'rgba(255,255,255,0.12)' : 'transparent',
                color: mode === 'admin' ? '#fff' : 'rgba(255,255,255,0.4)',
              }}
            >
              Admin
            </button>
          </div>

          <h1 style={{ color: '#fff', fontSize: '21px', fontWeight: 500, textAlign: 'center', marginBottom: '24px' }}>
            {mode === 'admin' ? 'Admin login' : 'Welcome back'}
          </h1>

          {mode === 'admin' && (
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '6px', background: 'rgba(255,255,255,0.05)', border: '1px solid rgba(255,255,255,0.15)', borderRadius: '8px', padding: '8px 14px', fontSize: '12px', color: 'rgba(255,255,255,0.6)', marginBottom: '16px' }}>
              Admin access only
            </div>
          )}

          <div style={{ marginBottom: '14px' }}>
            <span style={{ fontSize: '12px', color: 'rgba(255,255,255,0.55)', marginBottom: '6px', display: 'block' }}>Email address</span>
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              style={{
                width: '100%', background: 'transparent', border: '1px solid rgba(255,255,255,0.25)',
                borderRadius: '24px', padding: '12px 16px', color: '#fff', fontSize: '14px', outline: 'none',
                WebkitTextFillColor: '#fff', caretColor: '#fff',
              }}
            />
          </div>

          <div style={{ marginBottom: '14px' }}>
            <span style={{ fontSize: '12px', color: 'rgba(255,255,255,0.55)', marginBottom: '6px', display: 'block' }}>Password</span>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              style={{
                width: '100%', background: 'transparent', border: '1px solid rgba(255,255,255,0.25)',
                borderRadius: '24px', padding: '12px 16px', color: '#fff', fontSize: '14px', outline: 'none',
                WebkitTextFillColor: '#fff', caretColor: '#fff',
              }}
            />
          </div>

          {error && <p style={{ color: '#f87171', fontSize: '12px', marginBottom: '12px' }}>{error}</p>}

          <button
            onClick={handleSubmit}
            disabled={loading}
            style={{
              width: '100%', padding: '12px', background: 'transparent', border: '1px solid rgba(255,255,255,0.25)',
              color: loading ? 'rgba(255,255,255,0.4)' : '#fff', borderRadius: '24px', fontSize: '14px', fontWeight: 500, cursor: loading ? 'default' : 'pointer', marginTop: '6px',
            }}
          >
            {loading ? 'Logging in...' : mode === 'admin' ? 'Login as Admin' : 'Continue'}
          </button>

          {mode === 'user' && (
            <p style={{ textAlign: 'center', fontSize: '13px', color: 'rgba(255,255,255,0.45)', marginTop: '18px' }}>
              Don't have an account?{' '}
              <Link href="/signup" style={{ color: '#9bb8e8' }}>Sign up</Link>
            </p>
          )}


<<<<<<< Updated upstream
          <p className="text-center text-sm">
            <Link href="/forgot-password" className="text-[#2d7dd6]">Forgot password?</Link>
          </p>
=======
>>>>>>> Stashed changes

        </div>
      </div>
    </div>
  );
}