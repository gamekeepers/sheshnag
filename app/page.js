'use client';
import { useState, useEffect } from 'react';
import { useRouter } from 'next/navigation';

function MoonknightLogo({ size = 28 }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: size / 4 }}>
      <svg width={size} height={size} viewBox="0 0 32 32" fill="none">
        <circle cx="16" cy="16" r="12" fill="#fff" />
        <circle cx="20" cy="13" r="10" fill="#0a0a0a" />
      </svg>
      <span style={{ color: '#fff', fontSize: size * 0.45, fontWeight: 500, letterSpacing: '0.12em' }}>
        MOONKNIGHT
      </span>
    </div>
  );
}

export default function Home() {
  const router = useRouter();
  const [isLoggedIn, setIsLoggedIn] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);

  useEffect(() => {
    const token = localStorage.getItem('token');
    setIsLoggedIn(!!token);
  }, []);

  const handleLogout = () => {
    localStorage.removeItem('token');
    localStorage.removeItem('user');
    setIsLoggedIn(false);
    setMenuOpen(false);
  };

  return (
    <div style={{
      minHeight: '100vh',
      backgroundColor: '#0a0a0a',
      color: '#fff',
      fontFamily: "'Geist', 'Inter', sans-serif",
      position: 'relative',
      overflow: 'hidden',
    }}>

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
              <button
                onClick={() => router.push('/upload')}
                style={{
                  padding: '10px 22px',
                  borderRadius: '10px',
                  border: '1px solid rgba(255,255,255,0.25)',
                  background: 'transparent',
                  color: '#fff',
                  cursor: 'pointer',
                  fontSize: '15px',
                  fontWeight: 500,
                }}
              >
                Upload
              </button>
              <button
                onClick={() => router.push('/jobs')}
                style={{
                  padding: '10px 22px',
                  borderRadius: '10px',
                  border: '1px solid rgba(255,255,255,0.25)',
                  background: 'transparent',
                  color: '#fff',
                  cursor: 'pointer',
                  fontSize: '15px',
                  fontWeight: 500,
                }}
              >
                Jobs
              </button>
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

      {/* ── HERO ── */}
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
        </div>

        {/* Headline */}
        <h1 style={{
          fontSize: 'clamp(48px, 8vw, 88px)',
          fontWeight: 700,
          lineHeight: 1.05,
          margin: '0 0 16px',
          letterSpacing: '-0.02em',
          color: '#fff',
        }}>
          Process thousands of
        </h1>
        <h1 style={{
          fontSize: 'clamp(48px, 8vw, 88px)',
          fontWeight: 700,
          lineHeight: 1.05,
          margin: '0 0 32px',
          letterSpacing: '-0.02em',
          color: 'rgba(255,255,255,0.18)',
        }}>
          prompts in one shot.
        </h1>

        {/* Subtext */}
        <p style={{
          fontSize: '17px',
          color: 'rgba(255,255,255,0.45)',
          maxWidth: '420px',
          lineHeight: 1.6,
          margin: '0 0 48px',
        }}>
          Upload a JSONL file, submit a batch job, and let MOONKNIGHT handle the rest.
        </p>

        {/* CTAs */}
        <div style={{ display: 'flex', gap: '14px', flexWrap: 'wrap', justifyContent: 'center' }}>
          <button
            onClick={() => router.push('/upload')}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '10px',
              padding: '14px 28px',
              borderRadius: '12px',
              border: '1px solid rgba(255,255,255,0.22)',
              background: 'transparent',
              color: '#fff',
              cursor: 'pointer',
              fontSize: '16px',
              fontWeight: 500,
              transition: 'background 0.2s, border-color 0.2s',
            }}
            onMouseEnter={e => e.currentTarget.style.background = 'rgba(255,255,255,0.07)'}
            onMouseLeave={e => e.currentTarget.style.background = 'transparent'}
          >
            <span style={{ fontSize: '20px' }}>📁</span> Upload a batch
          </button>

          <button
            onClick={() => router.push('/jobs')}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '10px',
              padding: '14px 28px',
              borderRadius: '12px',
              border: '1px solid rgba(255,255,255,0.22)',
              background: 'transparent',
              color: '#fff',
              cursor: 'pointer',
              fontSize: '16px',
              fontWeight: 500,
              transition: 'background 0.2s',
            }}
            onMouseEnter={e => e.currentTarget.style.background = 'rgba(255,255,255,0.07)'}
            onMouseLeave={e => e.currentTarget.style.background = 'transparent'}
          >
            View jobs →
          </button>
        </div>
      </main>
    </div>
  );
}

function MenuItem({ label, onClick, danger }) {
  return (
    <button
      onClick={onClick}
      style={{
        display: 'block',
        width: '100%',
        padding: '10px 14px',
        background: 'transparent',
        border: 'none',
        color: danger ? '#f87171' : '#fff',
        cursor: 'pointer',
        fontSize: '14px',
        textAlign: 'left',
        borderRadius: '8px',
        transition: 'background 0.15s',
      }}
      onMouseEnter={e => e.currentTarget.style.background = 'rgba(255,255,255,0.07)'}
      onMouseLeave={e => e.currentTarget.style.background = 'transparent'}
    >
      {label}
    </button>
  );
}