'use client';

import { useState, useEffect } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import GoogleAuthButton from '../components/GoogleAuthButton';
import confetti from 'canvas-confetti';
import { completeLogin } from '../lib/completeLogin';

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

export default function SignupPage() {
  const [firstName, setFirstName] = useState('');
  const [lastName, setLastName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [policy, setPolicy] = useState({ restricted: false, domains: [] });
  const router = useRouter();

  // The server is the gate; this is purely so people are told the rule before
  // they fill in the form rather than after.
  useEffect(() => {
    fetch(`${BACKEND}/v1/auth/signup-policy`)
      .then(r => (r.ok ? r.json() : null))
      .then(d => { if (d) setPolicy(d); })
      .catch(() => {});
  }, []);

  function domainAllowed(addr) {
    if (!policy.restricted) return true;
    const d = (addr || '').trim().toLowerCase().split('@').pop();
    return policy.domains.some(a => d === a || d.endsWith('.' + a));
  }

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
    if (!domainAllowed(email)) {
      setError(`Sign-ups are restricted to ${policy.domains.map(d => '@' + d).join(', ')}.`);
      return;
    }

    setLoading(true);
    try {
      const res = await fetch(`${BACKEND}/v1/auth/signup`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...(process.env.NEXT_PUBLIC_NGROK_ENABLED === 'true' && { 'ngrok-skip-browser-warning': 'true' }) },
        body: JSON.stringify({
          email,
          password,
          full_name: `${firstName} ${lastName}`,
        }),
      });
      const data = await res.json();
      if (!res.ok) {
        setError(data?.detail || 'Signup failed. Please try again.');
        return;
      }

      // Auto login after signup
      const loginRes = await fetch(`${BACKEND}/v1/auth/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...(process.env.NEXT_PUBLIC_NGROK_ENABLED === 'true' && { 'ngrok-skip-browser-warning': 'true' }) },
        body: JSON.stringify({ email, password }),
      });
      const loginData = await loginRes.json();
      if (loginRes.ok && loginData.access_token) {
        confetti({ particleCount: 80, spread: 65, origin: { y: 0.6 }, colors: [INK, '#4a4fa3', '#c9c4ff'] });
        await completeLogin(loginData.access_token, router, {
          fallbackEmail: email,
          fallbackName: `${firstName} ${lastName}`,
        });
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
    <div style={{ background: PAPER, minHeight: '100vh', display: 'flex', flexDirection: 'column', color: INK, fontFamily: "'Geist', 'Inter', -apple-system, sans-serif" }}>
      <div style={{ padding: '22px 28px' }}>
        <Link href="/" style={{ textDecoration: 'none' }}><Logo /></Link>
      </div>

      <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 24 }}>
        <div style={{ width: '100%', maxWidth: 440, background: '#fff', border: `1px solid ${LINE}`, borderRadius: 20, padding: '36px 32px' }}>
          <h1 style={{ fontFamily: "Georgia, 'Times New Roman', serif", fontWeight: 400, fontSize: 30, textAlign: 'center', margin: '0 0 6px' }}>
            Create your account
          </h1>
          <p style={{ textAlign: 'center', color: MUTED, fontSize: 14.5, margin: '0 0 28px' }}>
            A personal organization comes with it.
          </p>

          <div style={{ display: 'flex', gap: 12, marginBottom: 16 }}>
            <div style={{ flex: 1 }}>
              <span style={labelStyle}>First name</span>
              <input type="text" value={firstName} onChange={e => setFirstName(e.target.value)} autoComplete="given-name" style={inputStyle} />
            </div>
            <div style={{ flex: 1 }}>
              <span style={labelStyle}>Last name</span>
              <input type="text" value={lastName} onChange={e => setLastName(e.target.value)} autoComplete="family-name" style={inputStyle} />
            </div>
          </div>

          <div style={{ marginBottom: 16 }}>
            <span style={labelStyle}>Email address</span>
            <input type="email" value={email} onChange={e => setEmail(e.target.value)} autoComplete="email" style={inputStyle} />
            {policy.restricted && (
              <span style={{ display: 'block', marginTop: 6, fontSize: 12, color: '#888' }}>
                Use your {policy.domains.map(d => '@' + d).join(' or ')} address.
              </span>
            )}
          </div>

          <div style={{ marginBottom: 16 }}>
            <span style={labelStyle}>Password</span>
            <input type="password" value={password} onChange={e => setPassword(e.target.value)} autoComplete="new-password" style={inputStyle} />
          </div>

          <div style={{ marginBottom: 16 }}>
            <span style={labelStyle}>Confirm password</span>
            <input type="password" value={confirm} onChange={e => setConfirm(e.target.value)} autoComplete="new-password" style={inputStyle} />
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
            {loading ? 'Creating account…' : 'Create account'}
          </button>

          <GoogleAuthButton setError={setError} setLoading={setLoading} loading={loading} />

          <p style={{ textAlign: 'center', fontSize: 13.5, color: MUTED, marginTop: 20 }}>
            Already have an account?{' '}
            <Link href="/login" style={{ color: '#4a4fa3' }}>Log in</Link>
          </p>
        </div>
      </div>
    </div>
  );
}
