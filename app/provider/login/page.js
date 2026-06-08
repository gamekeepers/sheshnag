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

export default function ProviderLoginPage() {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const router = useRouter();

  function handleSubmit() {
    if (!email || !password) {
      setError('Please fill in all fields.');
      return;
    }
    router.push('/provider');
  }

  return (
    <div className="bg-[#0a0a0a] min-h-screen flex flex-col font-sans">

      {/* Top bar */}
      <div className="px-7 py-5 flex items-center justify-between border-b border-[#1a1a1a]">
        <Link href="/"><MoonknightLogo size={26} /></Link>
        <span className="text-xs px-3 py-1 rounded-full border border-[#2d5a2d] bg-[#1a3a1a] text-[#4ade80]">
          ⚡ Provider Portal
        </span>
      </div>

      {/* Body */}
      <div className="flex-1 flex items-center justify-center px-6">
        <div className="w-full max-w-sm">

          {/* Icon */}
          <div className="flex justify-center mb-6">
            <div className="w-14 h-14 rounded-2xl bg-[#1a1a1a] border border-[#2a2a2a] flex items-center justify-center text-2xl">
              ⚡
            </div>
          </div>

          <h1 className="text-white text-2xl font-medium text-center mb-1">Provider login</h1>
          <p className="text-[#555] text-sm text-center mb-7">
            Access your GPU provider dashboard
          </p>

          {/* Email */}
          <div className="relative mb-4">
            <label className="absolute -top-2 left-3 bg-[#0a0a0a] px-1 text-[11px] text-[#4ade80]">
              Provider email
            </label>
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="you@provider.com"
              className="w-full bg-transparent border border-[#2d5a2d] rounded-full px-4 py-3 text-white text-sm outline-none focus:border-[#4ade80] placeholder-[#333]"
            />
          </div>

          {/* Password */}
          <div className="relative mb-4">
            <label className="absolute -top-2 left-3 bg-[#0a0a0a] px-1 text-[11px] text-[#4ade80]">
              Password
            </label>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full bg-transparent border border-[#2d5a2d] rounded-full px-4 py-3 text-white text-sm outline-none focus:border-[#4ade80]"
            />
          </div>

          {/* Error */}
          {error && <p className="text-red-400 text-xs mb-3">{error}</p>}

          {/* Submit */}
          <button
            onClick={handleSubmit}
            className="w-full bg-[#22c55e] text-black py-3 rounded-full text-sm font-semibold hover:bg-[#16a34a] transition-colors mt-1"
          >
            Login to Provider Dashboard
          </button>

          {/* Links */}
          <p className="text-center text-[#666] text-sm mt-5">
            New provider?{' '}
            <Link href="/provider/signup" className="text-[#4ade80]">Apply to join</Link>
          </p>

          <div className="flex items-center gap-3 my-5">
            <hr className="flex-1 border-none h-px bg-[#2a2a2a]" />
            <span className="text-[#444] text-xs">OR</span>
            <hr className="flex-1 border-none h-px bg-[#2a2a2a]" />
          </div>

          <p className="text-center text-sm">
            <Link href="/login" className="text-[#555] hover:text-white text-xs">
              ← Back to user login
            </Link>
          </p>

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
