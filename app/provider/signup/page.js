'use client';

import { useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import ParticleField from '../components/ParticleField';

export default function SignupPage() {
  const [firstName, setFirstName] = useState('');
  const [lastName, setLastName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [error, setError] = useState('');
  const router = useRouter();

  function handleSubmit() {
    if (!firstName || !lastName || !email || !password || !confirm) {
      setError('Please fill in all fields.');
      return;
    }
    if (password !== confirm) {
      setError('Passwords do not match.');
      return;
    }
    router.push('/jobs');
  }

  const inputStyle = {
    width: '100%', background: 'transparent', border: '1px solid rgba(255,255,255,0.25)',
    borderRadius: '24px', padding: '12px 16px', color: '#fff', fontSize: '14px', outline: 'none',
    WebkitTextFillColor: '#fff', caretColor: '#fff',
  };

  const labelStyle = { fontSize: '12px', color: 'rgba(255,255,255,0.55)', marginBottom: '6px', display: 'block' };

  return (
    <div style={{ background: '#050505', minHeight: '100vh', display: 'flex', flexDirection: 'column', position: 'relative', fontFamily: 'sans-serif', overflow: 'hidden' }}>
      <ParticleField />

      <div style={{ position: 'relative', zIndex: 3, padding: '20px 28px' }}>
        <Link href="/" style={{ display: 'flex', alignItems: 'center', gap: '8px', textDecoration: 'none' }}>
          <svg width="22" height="22" viewBox="0 0 32 32" fill="none">
            <circle cx="16" cy="16" r="12" fill="#fff" />
            <circle cx="20" cy="13" r="10" fill="#050505" />
          </svg>
          <span style={{ color: '#fff', fontSize: '14px', fontWeight: 500, letterSpacing: '0.12em' }}>MOONKNIGHT</span>
        </Link>
      </div>

      <div style={{ position: 'relative', zIndex: 3, flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '24px' }}>
        <div style={{ width: '100%', maxWidth: '340px' }}>

          <h1 style={{ color: '#fff', fontSize: '21px', fontWeight: 500, textAlign: 'center', marginBottom: '24px' }}>
            Create an account
          </h1>

          <div style={{ display: 'flex', gap: '10px', marginBottom: '14px' }}>
            <div style={{ flex: 1 }}>
              <span style={labelStyle}>First name</span>
              <input type="text" value={firstName} onChange={(e) => setFirstName(e.target.value)} style={inputStyle} />
            </div>
            <div style={{ flex: 1 }}>
              <span style={labelStyle}>Last name</span>
              <input type="text" value={lastName} onChange={(e) => setLastName(e.target.value)} style={inputStyle} />
            </div>
          </div>

          <div style={{ marginBottom: '14px' }}>
            <span style={labelStyle}>Email address</span>
            <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} style={inputStyle} />
          </div>

          <div style={{ marginBottom: '14px' }}>
            <span style={labelStyle}>Password</span>
            <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} style={inputStyle} />
          </div>

          <div style={{ marginBottom: '14px' }}>
            <span style={labelStyle}>Confirm password</span>
            <input type="password" value={confirm} onChange={(e) => setConfirm(e.target.value)} style={inputStyle} />
          </div>

          {error && <p style={{ color: '#f87171', fontSize: '12px', marginBottom: '12px' }}>{error}</p>}

          <button
            onClick={handleSubmit}
            style={{
              width: '100%', padding: '12px', background: 'transparent', border: '1px solid rgba(255,255,255,0.25)',
              color: '#fff', borderRadius: '24px', fontSize: '14px', fontWeight: 500, cursor: 'pointer', marginTop: '6px',
            }}
          >
            Create account
          </button>

          <p style={{ textAlign: 'center', fontSize: '13px', color: 'rgba(255,255,255,0.45)', marginTop: '18px' }}>
            Already have an account?{' '}
            <Link href="/login" style={{ color: '#9bb8e8' }}>Log in</Link>
          </p>

          <p style={{ textAlign: 'center', fontSize: '11px', color: 'rgba(255,255,255,0.3)', marginTop: '14px', lineHeight: 1.6 }}>
            By creating an account you agree to our<br />
            <Link href="#" style={{ color: 'rgba(255,255,255,0.5)', textDecoration: 'underline' }}>Terms of Use</Link>{' '}
            and{' '}
            <Link href="#" style={{ color: 'rgba(255,255,255,0.5)', textDecoration: 'underline' }}>Privacy Policy</Link>
          </p>

        </div>
      </div>
    </div>
  );
}