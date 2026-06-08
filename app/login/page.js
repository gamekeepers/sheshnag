'use client';

import { useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';

function MoonknightLogo({ size = 28 }) {
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

export default function LoginPage() {
  const [mode, setMode] = useState('user');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const router = useRouter();

  function handleSubmit() {
    if (!email || !password) {
      setError('Please fill in all fields.');
      return;
    }
    if (mode === 'admin') {
      router.push('/admin');
    } else {
      router.push('/jobs');
    }
  }

  return (
    <div className="bg-[#0a0a0a] min-h-screen flex flex-col font-sans">

      {/* Top bar */}
      <div className="px-7 py-5">
        <Link href="/"><MoonknightLogo size={26} /></Link>
      </div>

      {/* Body */}
      <div className="flex-1 flex items-center justify-center px-6">
        <div className="w-full max-w-sm">

          {/* Toggle */}
          <div className="flex bg-[#1a1a1a] border border-[#2a2a2a] rounded-lg p-1 mb-6">
            <button
              onClick={() => { setMode('user'); setError(''); }}
              className={`flex-1 py-1.5 text-xs rounded-md transition-all ${mode === 'user' ? 'bg-[#2a2a2a] text-white' : 'text-[#666]'}`}
            >
              User
            </button>
            <button
              onClick={() => { setMode('admin'); setError(''); }}
              className={`flex-1 py-1.5 text-xs rounded-md transition-all ${mode === 'admin' ? 'bg-[#2a2a2a] text-white' : 'text-[#666]'}`}
            >
              Admin
            </button>
          </div>

          {/* Heading */}
          <h1 className="text-white text-2xl font-medium text-center mb-6">
            {mode === 'admin' ? 'Admin login' : 'Welcome back'}
          </h1>

          {/* Admin badge */}
          {mode === 'admin' && (
            <div className="flex items-center justify-center gap-2 bg-[#1a1a1a] border border-[#3a3a3a] rounded-lg px-4 py-2 text-xs text-[#888] mb-4">
              🔐 Admin access only
            </div>
          )}

          {/* Email */}
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

          {/* Password */}
          <div className="relative mb-4">
            <label className="absolute -top-2 left-3 bg-[#0a0a0a] px-1 text-[11px] text-[#2d7dd6]">
              Password
            </label>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full bg-transparent border border-[#2d7dd6] rounded-full px-4 py-3 text-white text-sm outline-none focus:border-[#4d9cf8]"
            />
          </div>

          {/* Error */}
          {error && <p className="text-red-400 text-xs mb-3">{error}</p>}

          {/* Submit */}
          <button
            onClick={handleSubmit}
            className="w-full bg-white text-black py-3 rounded-full text-sm font-medium hover:bg-gray-100 mt-1"
          >
            {mode === 'admin' ? 'Login as Admin' : 'Continue'}
          </button>

          {/* Switch to signup */}
          {mode === 'user' && (
            <p className="text-center text-[#666] text-sm mt-4">
              Don't have an account?{' '}
              <Link href="/signup" className="text-[#2d7dd6]">Sign up</Link>
            </p>
          )}

          {/* Divider */}
          <div className="flex items-center gap-3 my-5">
            <hr className="flex-1 border-none border-t border-[#2a2a2a] h-px bg-[#2a2a2a]" />
            <span className="text-[#444] text-xs">OR</span>
            <hr className="flex-1 border-none border-t border-[#2a2a2a] h-px bg-[#2a2a2a]" />
          </div>

          <p className="text-center text-sm">
            <Link href="/forgot-password" className="text-[#2d7dd6]">Forgot password?</Link>
          </p>

          <div className="mt-6 p-3 border border-[#2a2a2a] rounded-xl text-center bg-[#111]">
            <p className="text-[#555] text-xs mb-1">Are you a GPU provider?</p>
            <Link href="/provider/login" className="text-[#4ade80] text-xs hover:underline">
              ⚡ Go to Provider Portal →
            </Link>
          </div>

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