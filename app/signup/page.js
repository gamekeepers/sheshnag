'use client';

import { useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import ParticleField from '../components/ParticleField';
import CursorEffect from '../components/CursorEffect';

const BACKEND = process.env.NEXT_PUBLIC_BACKEND_URL || 'http://localhost:8000';

export default function SignupPage() {
  const [firstName, setFirstName] = useState('');
  const [lastName, setLastName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const router = useRouter();

  async function handleSubmit() {
    setError('');

    if (!firstName || !lastName || !email || !password || !confirm) {
      setError('Please fill in all fields.');
      return;
    }

    if (password !== confirm) {
      setError('Passwords do not match.');
      return;
    }

    setLoading(true);
    try {
      const res = await fetch(`${BACKEND}/v1/auth/signup`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'ngrok-skip-browser-warning': 'true' },
        body: JSON.stringify({
          email,
          password,
          full_name: `${firstName} ${lastName}`,
          role: 'user',
        }),
      });
      const data = await res.json();
      if (!res.ok) {
        setError(data?.detail || 'Signup failed. Please try again.');
        return;
      }
      
      // Auto login
      const loginRes = await fetch(`${BACKEND}/v1/auth/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'ngrok-skip-browser-warning': 'true' },
        body: JSON.stringify({ email, password }),
      });
      const loginData = await loginRes.json();
      if (loginRes.ok && loginData.access_token) {
        localStorage.setItem('mk_token', loginData.access_token);
        localStorage.setItem('mk_user', JSON.stringify({
          id: data.id,
          email,
          full_name: `${firstName} ${lastName}`,
          platform_role: 'user',
        }));
        router.push('/dashboard');
      } else {
        router.push('/login');
      }
    } catch {
      setError('Cannot reach server. Please try again.');
    } finally {
      setLoading(false);
    }
  }

  // Styles
  const containerStyle = {
    background: '#050505',
    minHeight: '100vh',
    display: 'flex',
    flexDirection: 'column',
    position: 'relative',
    fontFamily: 'sans-serif',
  };
  const labelStyle = {
    fontSize: '12px',
    color: 'rgba(255,255,255,0.55)',
    marginBottom: '6px',
    display: 'block',
  };
  const inputStyle = {
    width: '100%',
    background: 'transparent',
    border: '1px solid rgba(255,255,255,0.25)',
    borderRadius: '24px',
    padding: '12px 16px',
    color: '#fff',
    fontSize: '14px',
    outline: 'none',
    boxSizing: 'border-box',
    WebkitTextFillColor: '#fff',
    caretColor: '#fff',
    WebkitBoxShadow: '0 0 0 1000px #050505 inset',
  };

  return (
    <div style={containerStyle}>
      <ParticleField />
      <CursorEffect />

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
        <div style={{ width: '100%', maxWidth: '360px' }}>

          <h1 style={{ color: '#fff', fontSize: '22px', fontWeight: 500, textAlign: 'center', marginBottom: '24px' }}>
            Create an account
          </h1>

          {/* Name Fields */}
          <div style={{ display: 'flex', gap: '12px', marginBottom: '14px' }}>
            <div style={{ flex: 1 }}>
              <span style={labelStyle}>First name</span>
              <input
                type="text"
                value={firstName}
                onChange={e => setFirstName(e.target.value)}
                autoComplete="off"
                style={inputStyle}
              />
            </div>
            <div style={{ flex: 1 }}>
              <span style={labelStyle}>Last name</span>
              <input
                type="text"
                value={lastName}
                onChange={e => setLastName(e.target.value)}
                autoComplete="off"
                style={inputStyle}
              />
            </div>
          </div>

          {/* Email */}
          <div style={{ marginBottom: '14px' }}>
            <span style={labelStyle}>Email address</span>
            <input
              type="email"
              value={email}
              onChange={e => setEmail(e.target.value)}
              autoComplete="new-email"
              style={inputStyle}
            />
          </div>

          {/* Password */}
          <div style={{ marginBottom: '14px' }}>
            <span style={labelStyle}>Password</span>
            <input
              type="password"
              value={password}
              onChange={e => setPassword(e.target.value)}
              autoComplete="new-password"
              style={inputStyle}
            />
          </div>

          {/* Confirm password */}
          <div style={{ marginBottom: '14px' }}>
            <span style={labelStyle}>Confirm password</span>
            <input
              type="password"
              value={confirm}
              onChange={e => setConfirm(e.target.value)}
              autoComplete="new-password"
              style={inputStyle}
            />
          </div>

          {error && <p style={{ color: '#f87171', fontSize: '12px', marginBottom: '12px' }}>{error}</p>}

          <button
            onClick={handleSubmit}
            disabled={loading}
            style={{
              width: '100%', padding: '12px', background: 'transparent',
              border: '1px solid rgba(255,255,255,0.25)', color: loading ? 'rgba(255,255,255,0.4)' : '#fff',
              borderRadius: '24px', fontSize: '14px', fontWeight: 500,
              cursor: loading ? 'default' : 'pointer', marginTop: '6px',
            }}
          >
            {loading ? 'Creating account...' : 'Create account'}
          </button>

          <p style={{ textAlign: 'center', fontSize: '13px', color: 'rgba(255,255,255,0.45)', marginTop: '18px' }}>
            Already have an account?{' '}
            <Link href="/login" style={{ color: '#9bb8e8' }}>Log in</Link>
          </p>

        </div>
      </div>
    </div>
  );
}