'use client';

import { useState, useEffect } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import ParticleField from '../components/ParticleField';
import CursorEffect from '../components/CursorEffect';

const BACKEND = process.env.NEXT_PUBLIC_BACKEND_URL || 'http://localhost:8000';

export default function SignupPage() {
  const [mode, setMode] = useState('user'); // 'user' or 'provider'
  
  // User fields
  const [firstName, setFirstName] = useState('');
  const [lastName, setLastName] = useState('');
  
  // Provider fields
  const [orgName, setOrgName] = useState('');
  const [contactName, setContactName] = useState('');
  const [gpuModel, setGpuModel] = useState('');
  const [customGpuModel, setCustomGpuModel] = useState('');
  const [gpuVram, setGpuVram] = useState('');
  const [gpuCount, setGpuCount] = useState('');
  
  // Shared fields
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const [apiKey, setApiKey] = useState('');
  const router = useRouter();

  useEffect(() => {
    if (typeof window !== 'undefined') {
      const params = new URLSearchParams(window.location.search);
      const urlMode = params.get('mode');
      if (urlMode === 'provider') {
        setMode('provider');
      }
    }
  }, []);

  async function handleSubmit() {
    setError('');

    // Validation
    if (mode === 'user') {
      if (!firstName || !lastName || !email || !password || !confirm) {
        setError('Please fill in all fields.');
        return;
      }
    } else {
      const selectedModel = gpuModel === 'Others' ? customGpuModel : gpuModel;
      if (!orgName || !contactName || !email || !selectedModel || !gpuVram || !gpuCount || !password || !confirm) {
        setError('Please fill in all fields.');
        return;
      }
      if (Number(gpuVram) < 24) {
        setError('Minimum GPU VRAM requirement is 24 GB.');
        return;
      }
    }

    if (password !== confirm) {
      setError('Passwords do not match.');
      return;
    }

    setLoading(true);
    try {
      if (mode === 'user') {
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
        
        // Auto login for user
        const loginRes = await fetch(`${BACKEND}/v1/auth/login`, {
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
      } else {
        // Provider signup
        const selectedModel = gpuModel === 'Others' ? customGpuModel : gpuModel;
        const res = await fetch(`${BACKEND}/provider/signup`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'ngrok-skip-browser-warning': 'true' },
          body: JSON.stringify({
            email,
            password,
            full_name: contactName,
            org_name: orgName,
            gpu_model: selectedModel,
            gpu_vram: Number(gpuVram),
            gpu_count: Number(gpuCount),
          }),
        });
        const data = await res.json();
        if (!res.ok) {
          setError(data?.detail || 'Signup failed. Please try again.');
          return;
        }
        setApiKey(data.api_key || '');
        setSubmitted(true);
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

  /* ── Success screen for Provider ── */
  if (submitted && mode === 'provider') {
    return (
      <div style={containerStyle}>
        <ParticleField />
        <div style={{ position: 'relative', zIndex: 3, flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '24px', textAlign: 'center' }}>
          <div style={{ width: '100%', maxWidth: '400px' }}>
            <div style={{ fontSize: '40px', marginBottom: '16px' }}>⚡</div>
            <h1 style={{ color: '#fff', fontSize: '22px', fontWeight: 500, marginBottom: '10px' }}>Application submitted!</h1>
            <p style={{ color: 'rgba(255,255,255,0.5)', fontSize: '14px', lineHeight: 1.6, marginBottom: '24px' }}>
              Thank you for joining the Moonknight provider network.
            </p>

            {apiKey && (
              <div style={{ background: '#111', border: '1px solid #222', borderRadius: '12px', padding: '16px', marginBottom: '24px' }}>
                <p style={{ color: '#4ade80', fontSize: '12px', fontWeight: 600, textTransform: 'uppercase', marginBottom: '8px', letterSpacing: '0.05em' }}>Your Provider API Key</p>
                <p style={{ color: '#fff', fontSize: '14px', fontFamily: 'monospace', wordBreak: 'break-all', margin: '0 0 12px 0', background: '#000', padding: '12px', borderRadius: '8px', border: '1px solid #333' }}>
                  {apiKey}
                </p>
                <p style={{ color: '#f87171', fontSize: '11px', margin: 0, lineHeight: 1.4 }}>
                  ⚠️ Copy this key now. It will not be shown again. You will need it to run your provider daemon.
                </p>
              </div>
            )}

            <Link href="/login" style={{ display: 'block', padding: '12px', background: '#fff', color: '#000', borderRadius: '24px', fontSize: '14px', fontWeight: 600, textDecoration: 'none' }}>
              Back to Login
            </Link>
          </div>
        </div>
      </div>
    );
  }

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

          {/* Toggle Tabs */}
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
              onClick={() => { setMode('provider'); setError(''); }}
              style={{
                flex: 1, padding: '7px', fontSize: '12px', borderRadius: '6px', border: 'none', cursor: 'pointer',
                background: mode === 'provider' ? 'rgba(255,255,255,0.12)' : 'transparent',
                color: mode === 'provider' ? '#fff' : 'rgba(255,255,255,0.4)',
              }}
            >
              Provider
            </button>
          </div>

          <h1 style={{ color: '#fff', fontSize: '22px', fontWeight: 500, textAlign: 'center', marginBottom: '24px' }}>
            {mode === 'provider' ? 'Register as Provider' : 'Create an account'}
          </h1>

          {/* CONDITIONAL FIELDS */}
          {mode === 'user' ? (
            /* User Name Fields */
            <div style={{ display: 'flex', gap: '12px', marginBottom: '20px' }}>
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
          ) : (
            /* Provider Fields */
            <>
              <div style={{ marginBottom: '14px' }}>
                <span style={labelStyle}>Company / Organisation name</span>
                <input
                  type="text"
                  value={orgName}
                  onChange={e => setOrgName(e.target.value)}
                  placeholder="e.g. CloudGPU Labs"
                  autoComplete="off"
                  style={inputStyle}
                />
              </div>
              <div style={{ marginBottom: '14px' }}>
                <span style={labelStyle}>Your name (contact person)</span>
                <input
                  type="text"
                  value={contactName}
                  onChange={e => setContactName(e.target.value)}
                  placeholder="Full name"
                  autoComplete="off"
                  style={inputStyle}
                />
              </div>
              
              <div style={{ marginBottom: '14px' }}>
                <span style={labelStyle}>GPU Model</span>
                <select
                  value={gpuModel}
                  onChange={e => {
                    const val = e.target.value;
                    setGpuModel(val);
                    // Autofill VRAM based on selected GPU
                    if (val.includes('80GB')) setGpuVram('80');
                    else if (val.includes('40GB')) setGpuVram('40');
                    else if (val.includes('48GB')) setGpuVram('48');
                    else if (val.includes('24GB')) setGpuVram('24');
                    else if (val === 'Others') setGpuVram('');
                  }}
                  style={{
                    width: '100%', background: '#050505', border: '1px solid rgba(255,255,255,0.25)',
                    borderRadius: '24px', padding: '12px 16px', color: '#fff', fontSize: '14px',
                    outline: 'none', boxSizing: 'border-box', cursor: 'pointer',
                  }}
                >
                  <option value="" disabled style={{ background: '#050505', color: 'rgba(255,255,255,0.3)' }}>Select GPU model</option>
                  <option value="NVIDIA H100 (80GB)" style={{ background: '#050505' }}>NVIDIA H100 (80GB)</option>
                  <option value="NVIDIA A100 (80GB)" style={{ background: '#050505' }}>NVIDIA A100 (80GB)</option>
                  <option value="NVIDIA A100 (40GB)" style={{ background: '#050505' }}>NVIDIA A100 (40GB)</option>
                  <option value="NVIDIA A10G (24GB)" style={{ background: '#050505' }}>NVIDIA A10G (24GB)</option>
                  <option value="NVIDIA L4 (24GB)" style={{ background: '#050505' }}>NVIDIA L4 (24GB)</option>
                  <option value="RTX 4090 (24GB)" style={{ background: '#050505' }}>RTX 4090 (24GB)</option>
                  <option value="RTX 3090 (24GB)" style={{ background: '#050505' }}>RTX 3090 (24GB)</option>
                  <option value="Others" style={{ background: '#050505' }}>Others (unlisted)</option>
                </select>
              </div>

              {gpuModel === 'Others' && (
                <div style={{ marginBottom: '14px' }}>
                  <span style={labelStyle}>Specify GPU Model Name</span>
                  <input
                    type="text"
                    value={customGpuModel}
                    onChange={e => setCustomGpuModel(e.target.value)}
                    placeholder="e.g. NVIDIA RTX A6000"
                    autoComplete="off"
                    style={inputStyle}
                  />
                </div>
              )}
              
              <div style={{ display: 'flex', gap: '12px', marginBottom: '14px' }}>
                <div style={{ flex: 1 }}>
                  <span style={labelStyle}>GPU VRAM (GB per card)</span>
                  <input
                    type="number"
                    min="24"
                    value={gpuVram}
                    onChange={e => setGpuVram(e.target.value)}
                    placeholder="Min 24 GB"
                    autoComplete="off"
                    style={inputStyle}
                  />
                </div>
                <div style={{ flex: 1 }}>
                  <span style={labelStyle}>GPU Count</span>
                  <input
                    type="number"
                    min="1"
                    value={gpuCount}
                    onChange={e => setGpuCount(e.target.value)}
                    placeholder="e.g. 8"
                    autoComplete="off"
                    style={inputStyle}
                  />
                </div>
              </div>
            </>
          )}

          {/* Email */}
          <div style={{ marginBottom: '14px' }}>
            <span style={labelStyle}>{mode === 'provider' ? 'Business email' : 'Email address'}</span>
            <input
              type="email"
              value={email}
              onChange={e => setEmail(e.target.value)}
              placeholder={mode === 'provider' ? 'you@company.com' : ''}
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