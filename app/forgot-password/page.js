'use client';

import { useState } from 'react';
import Link from 'next/link';
import ParticleField from '../components/ParticleField';

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

export default function ForgotPasswordPage() {
  const [step, setStep] = useState(1);
  const [email, setEmail] = useState('');
  const [code, setCode] = useState('');
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [error, setError] = useState('');

  function handleSendCode() {
    if (!email) {
      setError('Please enter your email.');
      return;
    }
    setError('');
    setStep(2);
  }

  function handleVerifyCode() {
    if (!code) {
      setError('Please enter the code.');
      return;
    }
    setError('');
    setStep(3);
  }

  function handleReset() {
    if (!password || !confirm) {
      setError('Please fill in all fields.');
      return;
    }
    if (password !== confirm) {
      setError('Passwords do not match.');
      return;
    }
    setError('');
    setStep(4);
  }

  return (
    <div style={{ background: '#050505', minHeight: '100vh', display: 'flex', flexDirection: 'column', position: 'relative', fontFamily: 'sans-serif' }}>
      <ParticleField />

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
        <div style={{ width: '100%', maxWidth: '340px' }}>

          {/* Step 1 — Enter email */}
          {step === 1 && (
            <>
              <h1 className="text-white text-2xl font-medium text-center mb-2">
                Forgot password?
              </h1>
              <p className="text-[#555] text-sm text-center mb-6">
                Enter your email and we'll send you a reset code.
              </p>

              <div className="relative mb-4">
                <label className="absolute -top-2 left-3 bg-[#0a0a0a] px-1 text-[11px] text-[#2d7dd6]">
                  Email address
                </label>
                <input
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  className="w-full bg-transparent border border-[#2d7dd6] rounded-full px-4 py-3 text-white text-sm outline-none focus:border-[#4d9cf8]"
                />
              </div>

              {error && <p className="text-red-400 text-xs mb-3">{error}</p>}

              <button
                onClick={handleSendCode}
                className="w-full bg-white text-black py-3 rounded-full text-sm font-medium hover:bg-gray-100"
              >
                Send reset code
              </button>

              <p className="text-center text-[#666] text-sm mt-4">
                Remember your password?{' '}
                <Link href="/login" className="text-[#2d7dd6]">Log in</Link>
              </p>
            </>
          )}

          {/* Step 2 — Enter code */}
          {step === 2 && (
            <>
              <h1 className="text-white text-2xl font-medium text-center mb-2">
                Check your email
              </h1>
              <p className="text-[#555] text-sm text-center mb-6">
                We sent a 6-digit code to <span className="text-white">{email}</span>
              </p>

              <div className="relative mb-4">
                <label className="absolute -top-2 left-3 bg-[#0a0a0a] px-1 text-[11px] text-[#2d7dd6]">
                  Reset code
                </label>
                <input
                  type="text"
                  value={code}
                  onChange={(e) => setCode(e.target.value)}
                  maxLength={6}
                  placeholder="000000"
                  className="w-full bg-transparent border border-[#2d7dd6] rounded-full px-4 py-3 text-white text-sm outline-none focus:border-[#4d9cf8] tracking-widest text-center"
                />
              </div>

              {error && <p className="text-red-400 text-xs mb-3">{error}</p>}

              <button
                onClick={handleVerifyCode}
                className="w-full bg-white text-black py-3 rounded-full text-sm font-medium hover:bg-gray-100"
              >
                Verify code
              </button>

              <p className="text-center text-[#666] text-sm mt-4">
                Didn't get a code?{' '}
                <span
                  onClick={() => setStep(1)}
                  className="text-[#2d7dd6] cursor-pointer"
                >
                  Resend
                </span>
              </p>
            </>
          )}

          {/* Step 3 — New password */}
          {step === 3 && (
            <>
              <h1 className="text-white text-2xl font-medium text-center mb-2">
                Reset password
              </h1>
              <p className="text-[#555] text-sm text-center mb-6">
                Enter your new password below.
              </p>

              <div className="relative mb-4">
                <label className="absolute -top-2 left-3 bg-[#0a0a0a] px-1 text-[11px] text-[#2d7dd6]">
                  New password
                </label>
                <input
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  className="w-full bg-transparent border border-[#2d7dd6] rounded-full px-4 py-3 text-white text-sm outline-none focus:border-[#4d9cf8]"
                />
              </div>

              <div className="relative mb-4">
                <label className="absolute -top-2 left-3 bg-[#0a0a0a] px-1 text-[11px] text-[#2d7dd6]">
                  Confirm password
                </label>
                <input
                  type="password"
                  value={confirm}
                  onChange={(e) => setConfirm(e.target.value)}
                  className="w-full bg-transparent border border-[#2d7dd6] rounded-full px-4 py-3 text-white text-sm outline-none focus:border-[#4d9cf8]"
                />
              </div>

              {error && <p className="text-red-400 text-xs mb-3">{error}</p>}

              <button
                onClick={handleReset}
                className="w-full bg-white text-black py-3 rounded-full text-sm font-medium hover:bg-gray-100"
              >
                Reset password
              </button>
            </>
          )}

          {/* Step 4 — Success */}
          {step === 4 && (
            <div className="text-center">
              <div className="text-5xl mb-4">✅</div>
              <h1 className="text-white text-2xl font-medium mb-2">
                Password reset!
              </h1>
              <p className="text-[#555] text-sm mb-8">
                Your password has been reset successfully.
              </p>
              <Link
                href="/login"
                className="w-full block bg-white text-black py-3 rounded-full text-sm font-medium hover:bg-gray-100 text-center"
              >
                Back to login
              </Link>
            </div>
          )}

        </div>
      </div>

      {/* Footer */}
      <div className="text-center py-4 border-t border-[#1a1a1a]">
        <Link href="#" className="text-[#444] text-xs underline mx-2">Terms of Use</Link>
        <span className="text-[#444] text-xs">|</span>
        <Link href="#" className="text-[#444] text-xs underline mx-2">Privacy Policy</Link>
      </div>

    </div>
  );
}