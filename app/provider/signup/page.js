'use client';

import { useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';

const BACKEND = process.env.NEXT_PUBLIC_BACKEND_URL || 'http://localhost:8000';

function MoonknightLogo({ size = 26 }) {
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

export default function ProviderSignupPage() {
  const [companyName, setCompanyName]   = useState('');
  const [contactName, setContactName]   = useState('');
  const [email, setEmail]               = useState('');
  const [gpuModel, setGpuModel]         = useState('');
  const [gpuCount, setGpuCount]         = useState('');
  const [password, setPassword]         = useState('');
  const [confirm, setConfirm]           = useState('');
  const [error, setError]               = useState('');
  const [loading, setLoading]           = useState(false);
  const [submitted, setSubmitted]       = useState(false);
  const router = useRouter();

  async function handleSubmit() {
    if (!companyName || !contactName || !email || !gpuModel || !gpuCount || !password || !confirm) {
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
      const res = await fetch(`${BACKEND}/v1/auth/register`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'ngrok-skip-browser-warning': 'true' },
        body: JSON.stringify({
          email,
          password,
          full_name: contactName,
          role: 'provider',
          company_name: companyName,
          gpu_model: gpuModel,
          gpu_count: Number(gpuCount),
        }),
      });
      const data = await res.json();
      if (!res.ok) {
        setError(data?.detail || 'Registration failed. Please try again.');
        return;
      }
      setSubmitted(true);
    } catch {
      setError('Cannot reach server. Please try again.');
    } finally {
      setLoading(false);
    }
  }

  const inputStyle = {
    width: '100%', background: 'transparent', border: '1px solid #2d5a2d',
    borderRadius: '8px', padding: '11px 14px', color: '#fff', fontSize: '13px',
    outline: 'none', boxSizing: 'border-box',
    WebkitTextFillColor: '#fff', caretColor: '#fff',
    WebkitBoxShadow: '0 0 0 1000px #0a0a0a inset',
  };
  const labelStyle = { fontSize: '11px', color: '#4ade80', marginBottom: '5px', display: 'block', letterSpacing: '0.04em' };

  /* ── Success screen ── */
  if (submitted) {
    return (
      <div style={{ background: '#0a0a0a', minHeight: '100vh', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', fontFamily: 'sans-serif', padding: '24px', textAlign: 'center' }}>
        <div style={{ fontSize: '40px', marginBottom: '16px' }}>⚡</div>
        <h1 style={{ color: '#fff', fontSize: '22px', fontWeight: 500, marginBottom: '10px' }}>Application submitted!</h1>
        <p style={{ color: '#555', fontSize: '14px', maxWidth: '300px', lineHeight: 1.6, marginBottom: '24px' }}>
          Thank you for applying to join the Moonknight provider network. We'll review your application and get back to you shortly.
        </p>
        <Link href="/provider/login" style={{ padding: '10px 24px', background: '#22c55e', color: '#000', borderRadius: '8px', fontSize: '13px', fontWeight: 600, textDecoration: 'none' }}>
          Back to Provider Login
        </Link>
      </div>
    );
  }

  return (
    <div style={{ background: '#0a0a0a', minHeight: '100vh', display: 'flex', flexDirection: 'column', fontFamily: 'sans-serif' }}>

      {/* Top bar */}
      <div style={{ padding: '18px 28px', display: 'flex', alignItems: 'center', justifyContent: 'space-between', borderBottom: '1px solid #1a1a1a' }}>
        <Link href="/" style={{ textDecoration: 'none' }}><MoonknightLogo size={24} /></Link>
        <span style={{ fontSize: '11px', padding: '4px 10px', borderRadius: '999px', border: '1px solid #2d5a2d', backgroundColor: '#1a3a1a', color: '#4ade80' }}>
          ⚡ Provider Portal
        </span>
      </div>

      {/* Body */}
      <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '32px 24px' }}>
        <div style={{ width: '100%', maxWidth: '420px' }}>

          {/* Icon + title */}
          <div style={{ textAlign: 'center', marginBottom: '28px' }}>
            <div style={{ width: '56px', height: '56px', borderRadius: '16px', background: '#1a1a1a', border: '1px solid #2a2a2a', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '24px', margin: '0 auto 16px' }}>
              ⚡
            </div>
            <h1 style={{ color: '#fff', fontSize: '22px', fontWeight: 500, margin: '0 0 6px' }}>Apply to become a provider</h1>
            <p style={{ color: '#555', fontSize: '13px', margin: 0 }}>Join the Moonknight GPU provider network</p>
          </div>

          {/* Section: Company info */}
          <p style={{ fontSize: '10px', color: '#4ade80', textTransform: 'uppercase', letterSpacing: '0.1em', marginBottom: '12px' }}>Company Info</p>

          <div style={{ marginBottom: '14px' }}>
            <label style={labelStyle}>Company / Organisation name</label>
            <input type="text" value={companyName} onChange={e => setCompanyName(e.target.value)} placeholder="e.g. CloudGPU Labs" style={inputStyle} />
          </div>

          <div style={{ marginBottom: '14px' }}>
            <label style={labelStyle}>Your name (contact person)</label>
            <input type="text" value={contactName} onChange={e => setContactName(e.target.value)} placeholder="Full name" style={inputStyle} />
          </div>

          <div style={{ marginBottom: '20px' }}>
            <label style={labelStyle}>Business email</label>
            <input type="email" value={email} onChange={e => setEmail(e.target.value)} placeholder="you@company.com" style={inputStyle} />
          </div>

          {/* Section: GPU info */}
          <p style={{ fontSize: '10px', color: '#4ade80', textTransform: 'uppercase', letterSpacing: '0.1em', marginBottom: '12px' }}>GPU Resources</p>

          <div style={{ display: 'flex', gap: '12px', marginBottom: '20px' }}>
            <div style={{ flex: 2 }}>
              <label style={labelStyle}>GPU Model</label>
              <input type="text" value={gpuModel} onChange={e => setGpuModel(e.target.value)} placeholder="e.g. NVIDIA A100" style={inputStyle} />
            </div>
            <div style={{ flex: 1 }}>
              <label style={labelStyle}>Count</label>
              <input type="number" min="1" value={gpuCount} onChange={e => setGpuCount(e.target.value)} placeholder="8" style={inputStyle} />
            </div>
          </div>

          {/* Section: Account */}
          <p style={{ fontSize: '10px', color: '#4ade80', textTransform: 'uppercase', letterSpacing: '0.1em', marginBottom: '12px' }}>Account Password</p>

          <div style={{ marginBottom: '14px' }}>
            <label style={labelStyle}>Password</label>
            <input type="password" value={password} onChange={e => setPassword(e.target.value)} style={inputStyle} />
          </div>

          <div style={{ marginBottom: '20px' }}>
            <label style={labelStyle}>Confirm password</label>
            <input type="password" value={confirm} onChange={e => setConfirm(e.target.value)} style={inputStyle} />
          </div>

          {error && <p style={{ color: '#f87171', fontSize: '12px', marginBottom: '12px' }}>{error}</p>}

          <button
            onClick={handleSubmit}
            disabled={loading}
            style={{
              width: '100%', padding: '12px', background: loading ? '#1a3a1a' : '#22c55e',
              border: 'none', color: loading ? '#4ade80' : '#000', borderRadius: '8px',
              fontSize: '14px', fontWeight: 600, cursor: loading ? 'default' : 'pointer',
            }}
          >
            {loading ? 'Submitting...' : 'Submit Application'}
          </button>

          <p style={{ textAlign: 'center', fontSize: '13px', color: 'rgba(255,255,255,0.35)', marginTop: '18px' }}>
            Already registered?{' '}
            <Link href="/provider/login" style={{ color: '#4ade80' }}>Log in</Link>
          </p>

        </div>
      </div>

      {/* Footer */}
      <div style={{ textAlign: 'center', padding: '16px', borderTop: '1px solid #1a1a1a' }}>
        <Link href="#" style={{ color: '#333', fontSize: '11px', textDecoration: 'underline', marginRight: '12px' }}>Terms of Use</Link>
        <Link href="#" style={{ color: '#333', fontSize: '11px', textDecoration: 'underline' }}>Privacy Policy</Link>
      </div>

    </div>
  );
}