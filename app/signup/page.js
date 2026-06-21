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
    if (!firstName || !lastName || !email || !password || !confirm) {
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
      const res = await fetch('https://hungry-whacking-reflex.ngrok-free.dev/v1/auth/signup', {
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
      const loginRes = await fetch('https://hungry-whacking-reflex.ngrok-free.dev/v1/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'ngrok-skip-browser-warning': 'true' },
        body: JSON.stringify({ email, password }),
      });
      const loginData = await loginRes.json();
      if (loginRes.ok && loginData.access_token) {
        localStorage.setItem('mk_token', loginData.access_token);
        localStorage.setItem('mk_user', JSON.stringify({
          email,
          full_name: `${firstName} ${lastName}`,
          role: 'user',
        }));
        router.push('/jobs');
      } else {
        router.push('/login');
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

      {/* Top bar */}
<<<<<<< Updated upstream
      <div className="px-7 py-5">
        <Link href="/"><FalconLogo size={26} /></Link>
=======
      <div style={{ position: 'relative', zIndex: 3, padding: '20px 28px' }}>
        <Link href="/" style={{ display: 'flex', alignItems: 'center', gap: '8px', textDecoration: 'none' }}>
          <svg width="22" height="22" viewBox="0 0 32 32" fill="none">
            <circle cx="16" cy="16" r="12" fill="#fff" />
            <circle cx="20" cy="13" r="10" fill="#050505" />
          </svg>
          <span style={{ color: '#fff', fontSize: '14px', fontWeight: 500, letterSpacing: '0.12em' }}>MOONKNIGHT</span>
        </Link>
>>>>>>> Stashed changes
      </div>

      {/* Body */}
      <div style={{ position: 'relative', zIndex: 3, flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '24px' }}>
        <div style={{ width: '100%', maxWidth: '360px' }}>

          <h1 style={{ color: '#fff', fontSize: '22px', fontWeight: 500, textAlign: 'center', marginBottom: '28px' }}>
            Create an account
          </h1>

          {/* First + Last name */}
          <div style={{ display: 'flex', gap: '12px', marginBottom: '20px' }}>
            <div style={{ flex: 1 }}>
              <span style={{ fontSize: '12px', color: 'rgba(255,255,255,0.55)', marginBottom: '6px', display: 'block' }}>First name</span>
              <input
                type="text"
                value={firstName}
                onChange={e => setFirstName(e.target.value)}
                style={{ width: '100%', background: 'transparent', border: '1px solid rgba(255,255,255,0.25)', borderRadius: '24px', padding: '11px 16px', color: '#fff', fontSize: '14px', outline: 'none', boxSizing: 'border-box' }}
              />
            </div>
            <div style={{ flex: 1 }}>
              <span style={{ fontSize: '12px', color: 'rgba(255,255,255,0.55)', marginBottom: '6px', display: 'block' }}>Last name</span>
              <input
                type="text"
                value={lastName}
                onChange={e => setLastName(e.target.value)}
                style={{ width: '100%', background: 'transparent', border: '1px solid rgba(255,255,255,0.25)', borderRadius: '24px', padding: '11px 16px', color: '#fff', fontSize: '14px', outline: 'none', boxSizing: 'border-box' }}
              />
            </div>
          </div>

          {/* Email */}
          <div style={{ marginBottom: '14px' }}>
            <span style={{ fontSize: '12px', color: 'rgba(255,255,255,0.55)', marginBottom: '6px', display: 'block' }}>Email address</span>
            <input
              type="email"
              value={email}
              onChange={e => setEmail(e.target.value)}
              style={{ width: '100%', background: 'transparent', border: '1px solid rgba(255,255,255,0.25)', borderRadius: '24px', padding: '12px 16px', color: '#fff', fontSize: '14px', outline: 'none', boxSizing: 'border-box' }}
            />
          </div>

          {/* Password */}
          <div style={{ marginBottom: '14px' }}>
            <span style={{ fontSize: '12px', color: 'rgba(255,255,255,0.55)', marginBottom: '6px', display: 'block' }}>Password</span>
            <input
              type="password"
              value={password}
              onChange={e => setPassword(e.target.value)}
              style={{ width: '100%', background: 'transparent', border: '1px solid rgba(255,255,255,0.25)', borderRadius: '24px', padding: '12px 16px', color: '#fff', fontSize: '14px', outline: 'none', boxSizing: 'border-box' }}
            />
          </div>

          {/* Confirm password */}
          <div style={{ marginBottom: '14px' }}>
            <span style={{ fontSize: '12px', color: 'rgba(255,255,255,0.55)', marginBottom: '6px', display: 'block' }}>Confirm password</span>
            <input
              type="password"
              value={confirm}
              onChange={e => setConfirm(e.target.value)}
              style={{ width: '100%', background: 'transparent', border: '1px solid rgba(255,255,255,0.25)', borderRadius: '24px', padding: '12px 16px', color: '#fff', fontSize: '14px', outline: 'none', boxSizing: 'border-box' }}
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