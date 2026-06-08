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

  return (
    <div className="bg-[#0a0a0a] min-h-screen flex flex-col font-sans">

      {/* Top bar */}
      <div className="px-7 py-5">
        <Link href="/"><MoonknightLogo size={26} /></Link>
      </div>

      {/* Body */}
      <div className="flex-1 flex items-center justify-center px-6 py-8">
        <div className="w-full max-w-sm">

          <h1 className="text-white text-2xl font-medium text-center mb-6">
            Create an account
          </h1>

          {/* First + Last name */}
          <div className="flex gap-3 mb-4">
            <div className="relative flex-1">
              <label className="absolute -top-2 left-3 bg-[#0a0a0a] px-1 text-[11px] text-[#2d7dd6]">
                First name
              </label>
              <input
                type="text"
                value={firstName}
                onChange={(e) => setFirstName(e.target.value)}
                className="w-full bg-transparent border border-[#2d7dd6] rounded-full px-4 py-3 text-white text-sm outline-none focus:border-[#4d9cf8]"
              />
            </div>
            <div className="relative flex-1">
              <label className="absolute -top-2 left-3 bg-[#0a0a0a] px-1 text-[11px] text-[#2d7dd6]">
                Last name
              </label>
              <input
                type="text"
                value={lastName}
                onChange={(e) => setLastName(e.target.value)}
                className="w-full bg-transparent border border-[#2d7dd6] rounded-full px-4 py-3 text-white text-sm outline-none focus:border-[#4d9cf8]"
              />
            </div>
          </div>

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

          {/* Confirm password */}
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

          {/* Error */}
          {error && <p className="text-red-400 text-xs mb-3">{error}</p>}

          {/* Submit */}
          <button
            onClick={handleSubmit}
            className="w-full bg-white text-black py-3 rounded-full text-sm font-medium hover:bg-gray-100 mt-1"
          >
            Create account
          </button>

          {/* Switch to login */}
          <p className="text-center text-[#666] text-sm mt-4">
            Already have an account?{' '}
            <Link href="/login" className="text-[#2d7dd6]">Log in</Link>
          </p>

          {/* Divider */}
          <div className="flex items-center gap-3 my-5">
            <hr className="flex-1 border-none h-px bg-[#2a2a2a]" />
            <span className="text-[#444] text-xs">OR</span>
            <hr className="flex-1 border-none h-px bg-[#2a2a2a]" />
          </div>

          <p className="text-center text-[#444] text-xs leading-relaxed">
            By creating an account you agree to our{' '}
            <Link href="#" className="underline">Terms of Use</Link>{' '}
            and{' '}
            <Link href="#" className="underline">Privacy Policy</Link>
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