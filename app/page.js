<<<<<<< Updated upstream
import Link from 'next/link';
=======
'use client';
import ParticleField from './components/ParticleField';
import CursorEffect from './components/CursorEffect';
import { useState, useEffect } from 'react';
import { useRouter } from 'next/navigation';
>>>>>>> Stashed changes

function FalconLogo({ size = 32 }) {
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

export default function Home() {
<<<<<<< Updated upstream
  return (
    <div className="bg-[#0a0a0a] min-h-screen flex flex-col font-sans">

      {/* Navbar */}
      <nav className="flex items-center justify-between px-10 py-4 border-b border-[#1e1e1e]">
        <FalconLogo size={28} />
        <div className="flex gap-8">
          <span className="text-[#666] text-sm cursor-pointer hover:text-white transition-colors">Docs</span>
          <Link href="/upload" className="text-[#666] text-sm cursor-pointer hover:text-white transition-colors">Jobs</Link>
          <span className="text-[#666] text-sm cursor-pointer hover:text-white transition-colors">Logs</span>
        </div>
      </nav>

      {/* Hero */}
      <div className="flex flex-col items-center justify-center text-center px-8 pt-24 pb-16">
        <div className="flex items-center gap-2 px-4 py-1.5 border border-[#2a2a2a] rounded-full text-[#666] text-xs mb-8 tracking-wide">
          ⚡ AI Batch Processing Platform
=======
  const router = useRouter();
  const [isLoggedIn, setIsLoggedIn] = useState(false);
  const [userName, setUserName] = useState('');
  const [menuOpen, setMenuOpen] = useState(false);

  useEffect(() => {
    const token = localStorage.getItem('mk_token');
    const userRaw = localStorage.getItem('mk_user');
    setIsLoggedIn(!!token);
    if (userRaw) {
      try {
        const u = JSON.parse(userRaw);
        setUserName(u.full_name || u.email || '');
      } catch {}
    }
  }, []);

  const handleLogout = () => {
    localStorage.removeItem('mk_token');
    localStorage.removeItem('mk_user');
    localStorage.removeItem('moonknight_file_map');
    setIsLoggedIn(false);
    setUserName('');
    setMenuOpen(false);
  };

return (
    <div style={{
      minHeight: '100vh',
      backgroundColor: '#06070f',
      color: '#fff',
      fontFamily: "'Geist', 'Inter', sans-serif",
      position: 'relative',
      overflow: 'hidden',
    }}>

      <ParticleField />
      <CursorEffect />

      {/* ── NAVBAR ── */}
      <nav style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        padding: '20px 40px',
        borderBottom: '1px solid rgba(255,255,255,0.08)',
        position: 'relative',
        zIndex: 40,
      }}>
        {/* Logo */}
        <div style={{ cursor: 'pointer' }} onClick={() => router.push('/')}>
          <MoonknightLogo size={28} />
        </div>

        {/* Right side */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
          {!isLoggedIn ? (
            <>
              <button
                onClick={() => router.push('/login')}
                style={{
                  padding: '10px 22px',
                  borderRadius: '10px',
                  border: '1px solid rgba(255,255,255,0.25)',
                  background: 'transparent',
                  color: '#fff',
                  cursor: 'pointer',
                  fontSize: '15px',
                  fontWeight: 500,
                  transition: 'background 0.2s',
                }}
                onMouseEnter={e => e.target.style.background = 'rgba(255,255,255,0.08)'}
                onMouseLeave={e => e.target.style.background = 'transparent'}
              >
                Log in
              </button>
              <button
                onClick={() => router.push('/signup')}
                style={{
                  padding: '10px 22px',
                  borderRadius: '10px',
                  border: '1px solid rgba(255,255,255,0.25)',
                  background: 'transparent',
                  color: '#fff',
                  cursor: 'pointer',
                  fontSize: '15px',
                  fontWeight: 500,
                  transition: 'background 0.2s',
                }}
                onMouseEnter={e => e.target.style.background = 'rgba(255,255,255,0.08)'}
                onMouseLeave={e => e.target.style.background = 'transparent'}
              >
                Sign up
              </button>
            </>
          ) : (
            <>
              {/* User avatar + name */}
              {userName && (
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                  <div style={{
                    width: '34px', height: '34px', borderRadius: '50%',
                    backgroundColor: '#1e3a5f', border: '1px solid #2d5a8a',
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                    fontSize: '13px', fontWeight: 600, color: '#60a5fa',
                  }}>
                    {userName.charAt(0).toUpperCase()}
                  </div>
                  <span style={{ fontSize: '14px', color: '#aaa', maxWidth: '160px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {userName}
                  </span>
                </div>
              )}
            </>
          )}

          {/* Hamburger menu */}
          <button
            onClick={() => setMenuOpen(!menuOpen)}
            style={{
              width: '42px',
              height: '42px',
              borderRadius: '10px',
              border: '1px solid rgba(255,255,255,0.25)',
              background: 'transparent',
              color: '#fff',
              cursor: 'pointer',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              flexDirection: 'column',
              gap: '5px',
              padding: '0',
            }}
          >
            <span style={{ display: 'block', width: '18px', height: '1.5px', background: '#fff' }} />
            <span style={{ display: 'block', width: '18px', height: '1.5px', background: '#fff' }} />
            <span style={{ display: 'block', width: '18px', height: '1.5px', background: '#fff' }} />
          </button>
        </div>

        {/* Dropdown menu */}
        {menuOpen && (
          <div style={{
            position: 'absolute',
            top: '72px',
            right: '40px',
            background: '#1a1a1a',
            border: '1px solid rgba(255,255,255,0.12)',
            borderRadius: '12px',
            padding: '8px',
            minWidth: '180px',
            zIndex: 100,
            boxShadow: '0 16px 40px rgba(0,0,0,0.6)',
          }}>
            {isLoggedIn ? (
              <>
                <MenuItem label="My Jobs" onClick={() => { router.push('/jobs'); setMenuOpen(false); }} />
                <MenuItem label="Upload Batch" onClick={() => { router.push('/upload'); setMenuOpen(false); }} />
                <div style={{ height: '1px', background: 'rgba(255,255,255,0.08)', margin: '6px 0' }} />
                <MenuItem label="Log out" onClick={handleLogout} danger />
              </>
            ) : (
              <>
                <MenuItem label="Log in" onClick={() => { router.push('/login'); setMenuOpen(false); }} />
                <MenuItem label="Sign up" onClick={() => { router.push('/signup'); setMenuOpen(false); }} />
              </>
            )}
          </div>
        )}
      </nav>

     {/* HERO */}
      <main style={{
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        minHeight: 'calc(100vh - 160px)',
        textAlign: 'center',
        padding: '60px 20px',
        position: 'relative',
        zIndex: 10,
      }}>

        {/* Badge */}
        <div style={{
          display: 'inline-flex',
          alignItems: 'center',
          gap: '8px',
          border: '1px solid rgba(255,255,255,0.15)',
          borderRadius: '999px',
          padding: '8px 18px',
          marginBottom: '40px',
          fontSize: '14px',
          color: 'rgba(255,255,255,0.65)',
          letterSpacing: '0.03em',
        }}>
          <span style={{
            width: '7px', height: '7px', borderRadius: '50%',
            background: '#22c55e',
            boxShadow: '0 0 8px #22c55e',
            display: 'inline-block',
          }} />
          AI Batch Processing Platform
>>>>>>> Stashed changes
        </div>

        <h1 className="text-6xl font-medium text-white leading-tight mb-6 tracking-tight max-w-3xl">
          Process thousands of<br />
          <span className="text-[#444]">prompts in one shot.</span>
        </h1>

        <p className="text-sm text-[#555] max-w-md leading-relaxed mb-10">
          Upload a JSONL file, submit a batch job, and let FALCON handle the rest.
          Fast, simple, and reliable AI batch processing.
        </p>

        <div className="flex gap-4">
          <Link
            href="/upload"
            className="flex items-center gap-2 bg-white text-black px-7 py-3 rounded-lg text-sm font-medium hover:bg-gray-100 transition-colors"
          >
            📁 Upload a batch
          </Link>
          <Link
            href="/upload"
            className="text-[#888] border border-[#2a2a2a] px-7 py-3 rounded-lg text-sm hover:bg-[#111] transition-colors"
          >
            View jobs →
          </Link>
        </div>
      </div>

      {/* Stats Bar */}
      <div className="flex justify-center gap-16 py-10 border-y border-[#1a1a1a] mx-10 mb-16">
        <div className="text-center">
          <p className="text-white font-medium text-xl">JSONL</p>
          <p className="text-[#444] text-xs mt-1 tracking-wide">Input format</p>
        </div>
        <div className="text-center">
          <p className="text-white font-medium text-xl">vLLM</p>
          <p className="text-[#444] text-xs mt-1 tracking-wide">AI runtime</p>
        </div>
        <div className="text-center">
          <p className="text-white font-medium text-xl">Real-time</p>
          <p className="text-[#444] text-xs mt-1 tracking-wide">Status updates</p>
        </div>
        <div className="text-center">
          <p className="text-white font-medium text-xl">1-click</p>
          <p className="text-[#444] text-xs mt-1 tracking-wide">Output download</p>
        </div>
      </div>

      {/* How it works */}
      <div className="px-16 mb-20">
        <p className="text-[#444] text-xs uppercase tracking-widest text-center mb-10">How it works</p>
        <div className="grid grid-cols-4 gap-4">
          {[
            { step: '01', title: 'Upload', desc: 'Upload your JSONL file with all your prompts.', icon: '📁' },
            { step: '02', title: 'Submit', desc: 'Submit the batch job with one click.', icon: '🚀' },
            { step: '03', title: 'Process', desc: 'FALCON runs your prompts through vLLM automatically.', icon: '⚙️' },
            { step: '04', title: 'Download', desc: 'Download your outputs.jsonl when complete.', icon: '📥' },
          ].map((item) => (
            <div key={item.step} className="border border-[#1e1e1e] rounded-xl p-6 hover:border-[#2a2a2a] transition-colors">
              <p className="text-[#333] text-xs mb-4 font-mono">{item.step}</p>
              <div className="text-2xl mb-3">{item.icon}</div>
              <p className="text-white text-sm font-medium mb-2">{item.title}</p>
              <p className="text-[#555] text-xs leading-relaxed">{item.desc}</p>
            </div>
          ))}
        </div>
      </div>

      {/* CTA */}
      <div className="mx-16 mb-16 border border-[#1e1e1e] rounded-2xl p-12 flex flex-col items-center text-center">
        <FalconLogo size={40} />
        <h2 className="text-white text-2xl font-medium mt-6 mb-3">Ready to run your first batch?</h2>
        <p className="text-[#555] text-sm mb-8 max-w-sm">Upload a JSONL file and get results in minutes.</p>
        <Link
          href="/upload"
          className="bg-white text-black px-8 py-3 rounded-lg text-sm font-medium hover:bg-gray-100 transition-colors"
        >
          Get started →
        </Link>
      </div>

      {/* Footer */}
      <div className="flex items-center justify-between px-16 py-6 border-t border-[#1a1a1a] mt-auto">
        <FalconLogo size={20} />
        <p className="text-[#333] text-xs">Built for speed. Built for scale.</p>
      </div>

    </div>
  );
}