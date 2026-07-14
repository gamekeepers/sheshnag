'use client';

import { useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import ParticleField from '../components/ParticleField';
import CursorEffect from '../components/CursorEffect';

const BACKEND = process.env.NEXT_PUBLIC_BACKEND_URL || 'http://localhost:8000';

export default function LoginPage() {
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
        const meRes = await fetch(`${BACKEND}/v1/auth/me`, {
          headers: { 'Authorization': `Bearer ${data.access_token}`, 'ngrok-skip-browser-warning': 'true' },
        });
        const me = await meRes.json();
        const userRole = me.role || 'user';

        localStorage.setItem('mk_user', JSON.stringify({
          id: me.id,
          email: me.email,
          full_name: me.full_name,
          platform_role: userRole,
        }));

        if (userRole === 'superadmin' || userRole === 'admin') {
          router.push('/admin');
        } else {
          router.push('/dashboard');
        }
      } catch {
        // Fallback for simple testing or network issue on /me
        localStorage.setItem('mk_user', JSON.stringify({
          id: 'mock-id',
          email,
          full_name: email,
          platform_role: 'user',
        }));
        router.push('/dashboard');
      }
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

      <div style={{ position: 'relative', zIndex: 3, padding: '20px 28px' }}>
        <Link href="/" style={{ display: 'flex', alignItems: 'center', gap: '8px', textDecoration: 'none' }}>
          <svg width="22" height="22" viewBox="0 0 32 32" fill="none">
            <circle cx="16" cy="16" r="12" fill="#fff" />
            <circle cx="20" cy="13" r="10" fill="#03030a" />
          </svg>
          <span style={{ color: '#fff', fontSize: '14px', fontWeight: 500, letterSpacing: '0.12em' }}>MOONKNIGHT</span>
        </Link>
      </div>

      <div style={{ position: 'relative', zIndex: 3, flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '24px' }}>
        <div style={{ width: '100%', maxWidth: '340px' }}>

          <h1 style={{ color: '#fff', fontSize: '21px', fontWeight: 500, textAlign: 'center', marginBottom: '24px' }}>
            Welcome back
          </h1>

          <div style={{ marginBottom: '14px' }}>
            <span style={{ fontSize: '12px', color: 'rgba(255,255,255,0.55)', marginBottom: '6px', display: 'block' }}>Email address</span>
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              autoComplete="off"
              style={{
                width: '100%', background: 'transparent', border: '1px solid rgba(255,255,255,0.25)',
                borderRadius: '24px', padding: '12px 16px', color: '#fff', fontSize: '14px', outline: 'none',
                WebkitTextFillColor: '#fff', caretColor: '#fff',
                WebkitBoxShadow: '0 0 0 1000px #050505 inset',
              }}
            />
          </div>

          <div style={{ marginBottom: '14px' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '6px' }}>
              <span style={{ fontSize: '12px', color: 'rgba(255,255,255,0.55)' }}>Password</span>
              <Link href="/forgot-password" style={{ fontSize: '11px', color: '#9bb8e8', textDecoration: 'none' }}>Forgot password?</Link>
            </div>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="new-password"
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
            onClick={handleSubmit}
            disabled={loading}
            style={{
              width: '100%', padding: '12px', background: 'transparent', border: '1px solid rgba(255,255,255,0.25)',
              color: loading ? 'rgba(255,255,255,0.4)' : '#fff', borderRadius: '24px', fontSize: '14px', fontWeight: 500, cursor: loading ? 'default' : 'pointer', marginTop: '6px',
            }}
          >
            {loading ? 'Logging in...' : 'Continue'}
          </button>

          <p style={{ textAlign: 'center', fontSize: '13px', color: 'rgba(255,255,255,0.45)', marginTop: '18px' }}>
            Don't have an account?{' '}
            <Link href="/signup" style={{ color: '#9bb8e8' }}>Sign up</Link>
          </p>

        </div>
      </div>
    </div>
  );
}