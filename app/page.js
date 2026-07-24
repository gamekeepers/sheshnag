'use client';
import ParticleField from './components/ParticleField';
import { useState, useEffect, useRef } from 'react';
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

function MenuItem({ label, onClick, danger }) {
  return (
    <button
      onClick={onClick}
      style={{
        display: 'block', width: '100%', padding: '10px 14px',
        background: 'transparent', border: 'none',
        color: danger ? '#f87171' : '#fff', cursor: 'pointer',
        fontSize: '14px', textAlign: 'left', borderRadius: '8px',
        transition: 'background 0.15s',
      }}
      onMouseEnter={e => e.currentTarget.style.background = 'rgba(255,255,255,0.07)'}
      onMouseLeave={e => e.currentTarget.style.background = 'transparent'}
    >
      {label}
    </button>
  );
}

const SECTIONS = [
  {
    title: 'Process thousands',
    subtitle: 'of prompts in one shot.',
    desc: 'Upload a JSONL file with thousands of AI prompts. The platform processes them all in the background and delivers results when done.',
    badge: 'AI Batch Processing Platform',
  },
  {
    title: 'Distributed GPU',
    subtitle: 'Workers at scale.',
    desc: 'Jobs are routed to real GPU workers — H100, A100, RTX 4090 and more. The system automatically assigns jobs to the best available worker.',
    badge: 'Powered by real hardware',
  },
  {
    title: 'API-First',
    subtitle: 'Platform.',
    desc: 'OpenAI-compatible API. Drop it into your existing code with zero changes. Use personal API keys for direct programmatic access.',
    badge: 'OpenAI compatible',
  },
  {
    title: 'Organizations',
    subtitle: '& Teams.',
    desc: 'Create teams, invite members with Owner / Admin / Viewer roles, and share API keys within your organization.',
    badge: 'Built for teams',
  },
  {
    title: 'Usage Analytics',
    subtitle: '& Insights.',
    desc: 'Track token consumption with daily and weekly charts. Monitor per API-key usage, peak throughput, and job completion rates in real time.',
    badge: 'Real-time monitoring',
  },
];

export default function Home() {
  const router = useRouter();
  const [isLoggedIn, setIsLoggedIn] = useState(false);
  const [userName, setUserName] = useState('');
  const [menuOpen, setMenuOpen] = useState(false);
  const [scrollY, setScrollY] = useState(0);
  const [windowHeight, setWindowHeight] = useState(800);

  useEffect(() => {
    setWindowHeight(window.innerHeight);
    const handleResize = () => setWindowHeight(window.innerHeight);
    window.addEventListener('resize', handleResize, { passive: true });
    return () => window.removeEventListener('resize', handleResize);
  }, []);

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

  useEffect(() => {
    const handleScroll = () => setScrollY(window.scrollY);
    window.addEventListener('scroll', handleScroll, { passive: true });
    return () => window.removeEventListener('scroll', handleScroll);
  }, []);

  const handleLogout = () => {
    localStorage.removeItem('mk_token');
    localStorage.removeItem('mk_user');
    localStorage.removeItem('moonknight_file_map');
    setIsLoggedIn(false);
    setUserName('');
    setMenuOpen(false);
  };

  // ── SCROLL ANIMATION MATH ──
  // Total scroll journey = 5 sections × 100vh each
  const totalSections = SECTIONS.length;
  const sectionHeight = windowHeight;

  // Moon horizontal travel: starts at +35vw (right), ends at -35vw (left)
  const totalScroll = sectionHeight * totalSections;
  const globalProgress = Math.min(scrollY / totalScroll, 1);
  const moonX = 35 - (60 * globalProgress); // +35vw → -25vw
  
  // Moon zoom: 100% at top → 200% at bottom (entire page scroll)
  // Use a broader scroll range so zoom continues through CTA/footer sections
  const fullPageScroll = sectionHeight * (totalSections + 3); // include API + CTA + footer
  const zoomProgress = Math.min(scrollY / fullPageScroll, 1);
  const moonScale = 1 + (1.0 * zoomProgress); // 1.0x → 2.0x

  // Which section are we in?
  const currentSectionFloat = scrollY / sectionHeight;
  const currentSection = Math.min(Math.floor(currentSectionFloat), totalSections - 1);
  const sectionProgress = currentSectionFloat - currentSection;

  // Text horizontal travel: opposite of moon — starts at -40vw (left), ends at +40vw (right)
  const textX = -40 + (80 * globalProgress); // -40vw → +40vw

  // Per-section text opacity: fade in and out within each section
  const textOpacity = sectionProgress < 0.15
    ? sectionProgress / 0.15
    : sectionProgress > 0.85
    ? (1 - sectionProgress) / 0.15
    : 1;

  return (
    <div style={{
      minHeight: '100vh',
      backgroundColor: '#000000',
      color: '#fff',
      fontFamily: '-apple-system, BlinkMacSystemFont, "SF Pro Display", "SF Pro Text", "Helvetica Neue", Helvetica, Arial, sans-serif',
      overflowX: 'hidden',
    }}>
      <ParticleField />

      {/* ── NAVBAR — fully transparent ── */}
      <nav style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        padding: '20px 40px',
        position: 'fixed', top: 0, left: 0, right: 0, zIndex: 100,
        background: 'transparent',
      }}>
        <div style={{ cursor: 'pointer' }} onClick={() => router.push('/')}>
          <MoonknightLogo size={28} />
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
          {!isLoggedIn ? (
            <>
              <button onClick={() => router.push('/login')} style={{
                padding: '9px 20px', borderRadius: '10px',
                border: '1px solid rgba(255,255,255,0.15)',
                background: 'transparent', color: 'rgba(255,255,255,0.7)', cursor: 'pointer',
                fontSize: '14px', fontWeight: 500, transition: 'all 0.2s',
              }}
                onMouseEnter={e => { e.target.style.color = '#fff'; e.target.style.borderColor = 'rgba(255,255,255,0.4)'; }}
                onMouseLeave={e => { e.target.style.color = 'rgba(255,255,255,0.7)'; e.target.style.borderColor = 'rgba(255,255,255,0.15)'; }}
              >Log in</button>
              <button onClick={() => router.push('/signup')} style={{
                padding: '9px 20px', borderRadius: '10px',
                border: '1px solid rgba(255,255,255,0.4)',
                background: 'rgba(255,255,255,0.06)', color: '#fff', cursor: 'pointer',
                fontSize: '14px', fontWeight: 500, transition: 'all 0.2s',
              }}
                onMouseEnter={e => e.currentTarget.style.background = 'rgba(255,255,255,0.12)'}
                onMouseLeave={e => e.currentTarget.style.background = 'rgba(255,255,255,0.06)'}
              >Get started</button>
            </>
          ) : (
            <div 
              onClick={() => router.push('/dashboard')}
              style={{ 
                display: 'flex', 
                alignItems: 'center', 
                gap: '8px', 
                cursor: 'pointer',
                padding: '4px 8px',
                borderRadius: '8px',
                background: 'rgba(255,255,255,0.03)',
                border: '1px solid rgba(255,255,255,0.06)',
                transition: 'all 0.15s'
              }}
              onMouseEnter={e => e.currentTarget.style.background = 'rgba(255,255,255,0.08)'}
              onMouseLeave={e => e.currentTarget.style.background = 'rgba(255,255,255,0.03)'}
              title="Go to Dashboard"
            >
              <div style={{
                width: '34px', height: '34px', borderRadius: '50%',
                backgroundColor: '#1e3a5f', border: '1px solid #2d5a8a',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                fontSize: '13px', fontWeight: 600, color: '#60a5fa',
              }}>
                {userName.charAt(0).toUpperCase()}
              </div>
              <span style={{ fontSize: '14px', color: '#aaa', fontWeight: 500 }}>{userName}</span>
            </div>
          )}
        </div>
      </nav>

      {/* ── 3D MOON BACKGROUND (Commented out for PR merge - to be replaced in upcoming PR) ──
      <div style={{
        position: 'fixed',
        top: 0, left: 0, right: 0, bottom: 0,
        zIndex: 1,
        pointerEvents: 'none',
        transform: `translateX(${moonX}vw) scale(${moonScale})`,
        willChange: 'transform',
      }}>
        <iframe
          src="https://my.spline.design/moonrotationwobble-KYHvqGltUdXPEtSOImA22D2s/"
          frameBorder="0"
          width="100%"
          height="100%"
          style={{ border: 'none', display: 'block', pointerEvents: 'none' }}
          title="Moonknight 3D Moon"
          allow="autoplay"
        />
      </div>
      ── */}

      {/* ── SCROLL SECTIONS — text moves opposite to moon ── */}
      {SECTIONS.map((section, i) => {
        const secStart = i * sectionHeight;
        
        // Distance from viewport center to section center
        const secCenter = secStart + (sectionHeight / 2);
        const viewCenter = scrollY + (sectionHeight / 2);
        const distFromCenter = Math.abs(viewCenter - secCenter);
        const normalizedDist = distFromCenter / sectionHeight;
        
        let finalOpacity;
        if (i === 0) {
          // Hero section stays 100% visible for the first 40% of scroll, then fades out by 80%
          finalOpacity = scrollY < sectionHeight * 0.4 ? 1 : Math.max(0, 1 - (scrollY - sectionHeight * 0.4) / (sectionHeight * 0.4));
        } else {
          // Other sections are 100% visible at exact center, fade to 0 when normalizedDist > 0.7
          finalOpacity = Math.max(0, 1 - (normalizedDist / 0.7));
        }

        // Slight vertical parallax based on signed distance
        const signedDist = (secCenter - viewCenter) / sectionHeight;
        const yOffset = signedDist * 60;

        // Each section's text X offset — tracks opposite to moon
        const sectionMoonX = 35 - (70 * ((secStart + sectionHeight * 0.5) / totalScroll));
        const sectionTextX = -sectionMoonX; // Opposite side

        return (
          <section
            key={i}
            style={{
              height: '100vh',
              position: 'relative',
              display: 'flex',
              alignItems: 'center',
              justifyContent: sectionTextX > 0 ? 'flex-end' : 'flex-start',
              padding: '0 80px',
              zIndex: 10,
            }}
          >
            <div style={{
              maxWidth: '520px',
              opacity: finalOpacity,
              transform: `translateY(${yOffset}px)`,
              transition: 'opacity 0.1s ease-out, transform 0.1s ease-out',
              pointerEvents: finalOpacity > 0.3 ? 'all' : 'none',
            }}>
              {/* Badge */}
              <div style={{
                display: 'inline-flex', alignItems: 'center', gap: '8px',
                border: '1px solid rgba(255,255,255,0.15)', borderRadius: '999px',
                padding: '7px 16px', marginBottom: '28px',
                fontSize: '13px', color: 'rgba(255,255,255,0.6)',
                letterSpacing: '0.05em',
              }}>
                <span style={{
                  width: '6px', height: '6px', borderRadius: '50%',
                  background: '#22c55e', boxShadow: '0 0 8px #22c55e',
                  display: 'inline-block',
                }} />
                {section.badge}
              </div>

              <h1 style={{
                fontSize: 'clamp(42px, 6vw, 84px)',
                fontWeight: 600, lineHeight: 1.05,
                margin: '0 0 8px', letterSpacing: '-0.04em', color: '#fff',
                textShadow: '0 2px 20px rgba(0,0,0,0.5)',
              }}>
                {section.title}
              </h1>
              <h1 style={{
                fontSize: 'clamp(42px, 6vw, 84px)',
                fontWeight: 600, lineHeight: 1.05,
                margin: '0 0 24px', letterSpacing: '-0.04em',
                color: 'rgba(255,255,255,0.3)',
                textShadow: '0 2px 20px rgba(0,0,0,0.5)',
              }}>
                {section.subtitle}
              </h1>

              <p style={{
                fontSize: '17px', color: 'rgba(255,255,255,0.6)',
                lineHeight: 1.7, margin: '0 0 36px', maxWidth: '420px',
              }}>
                {section.desc}
              </p>

              {i === 0 && (
                <div style={{ display: 'flex', gap: '12px', flexWrap: 'wrap' }}>
                  <button onClick={() => router.push(isLoggedIn ? '/dashboard' : '/signup')} style={{
                    padding: '14px 30px', borderRadius: '12px',
                    border: '1px solid rgba(255,255,255,0.5)',
                    background: 'rgba(255,255,255,0.1)',
                    color: '#fff', cursor: 'pointer',
                    fontSize: '16px', fontWeight: 600, transition: 'all 0.2s',
                  }}
                    onMouseEnter={e => e.currentTarget.style.background = 'rgba(255,255,255,0.18)'}
                    onMouseLeave={e => e.currentTarget.style.background = 'rgba(255,255,255,0.1)'}
                  >
                    {isLoggedIn ? 'Go to Dashboard →' : 'Get started free →'}
                  </button>

                </div>
              )}
            </div>
          </section>
        );
      })}

      {/* ── API CODE SHOWCASE SECTION ── */}
      <section style={{
        minHeight: '100vh',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        padding: '120px 60px',
        position: 'relative', zIndex: 10,
      }}>
        <div style={{
          maxWidth: '740px', width: '100%', textAlign: 'center',
        }}>
          <div style={{
            fontSize: '13px', color: 'rgba(255,255,255,0.4)',
            letterSpacing: '0.15em', textTransform: 'uppercase', marginBottom: '24px',
          }}>
            OpenAI Compatible
          </div>
          <h2 style={{
            fontSize: 'clamp(36px, 5vw, 64px)', fontWeight: 600,
            letterSpacing: '-0.03em', color: '#fff', margin: '0 0 16px',
            textShadow: '0 2px 20px rgba(0,0,0,0.5)',
          }}>
            One-line migration.
          </h2>
          <p style={{
            fontSize: '18px', color: 'rgba(255,255,255,0.55)',
            lineHeight: 1.7, margin: '0 auto 48px', maxWidth: '500px',
          }}>
            Already using OpenAI&apos;s batch API? Switch to Moonknight by changing one line.
          </p>

          <div style={{
            background: 'rgba(5,5,5,0.8)', border: '1px solid rgba(255,255,255,0.08)',
            borderRadius: '16px', padding: '32px', textAlign: 'left',
            fontFamily: '"SF Mono", "Fira Code", "Cascadia Code", monospace',
            fontSize: '15px', lineHeight: 1.9,
          }}>
            <div style={{ color: 'rgba(255,255,255,0.3)', marginBottom: '12px', fontSize: '12px', letterSpacing: '0.05em' }}>BEFORE</div>
            <div style={{ color: '#f87171', opacity: 0.9 }}>{'base_url = "https://api.openai.com/v1"'}</div>
            <br />
            <div style={{ color: 'rgba(255,255,255,0.3)', marginBottom: '12px', fontSize: '12px', letterSpacing: '0.05em' }}>AFTER</div>
            <div style={{ color: '#4ade80' }}>{'base_url = "https://api.moonknight.dev/v1"'}</div>
            <div style={{ color: '#60a5fa', marginTop: '6px' }}>{'api_key = "gk-your_personal_key"'}</div>
            <div style={{ color: 'rgba(255,255,255,0.3)', marginTop: '20px', fontSize: '13px' }}># Everything else stays the same ✓</div>
          </div>
        </div>
      </section>

      {/* ── CTA SECTION ── */}
      <section style={{
        minHeight: '60vh',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        textAlign: 'center', padding: '100px 60px',
        position: 'relative', zIndex: 10,
      }}>
        <div style={{
          maxWidth: '640px', width: '100%',
        }}>
          <h2 style={{
            fontSize: 'clamp(42px, 6vw, 80px)', fontWeight: 600,
            letterSpacing: '-0.04em', color: '#fff', margin: '0 0 24px',
            textShadow: '0 2px 20px rgba(0,0,0,0.5)',
          }}>
            Ready to launch?
          </h2>
          <p style={{
            fontSize: '19px', color: 'rgba(255,255,255,0.6)',
            maxWidth: '420px', margin: '0 auto 48px', lineHeight: 1.7,
          }}>
            Sign up in seconds. No credit card required.
          </p>
          <button onClick={() => router.push('/signup')} style={{
            padding: '18px 48px', borderRadius: '16px',
            border: '1px solid rgba(255,255,255,0.5)',
            background: 'rgba(255,255,255,0.1)',
            color: '#fff', cursor: 'pointer',
            fontSize: '18px', fontWeight: 600, transition: 'all 0.25s',
            letterSpacing: '0.01em',
          }}
            onMouseEnter={e => {
              e.currentTarget.style.background = 'rgba(255,255,255,0.18)';
              e.currentTarget.style.transform = 'translateY(-2px)';
            }}
            onMouseLeave={e => {
              e.currentTarget.style.background = 'rgba(255,255,255,0.1)';
              e.currentTarget.style.transform = 'translateY(0)';
            }}
          >
            Get started free →
          </button>
        </div>
      </section>

      {/* ── FOOTER ── */}
      <footer style={{
        padding: '32px 60px',
        borderTop: '1px solid rgba(255,255,255,0.04)',
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        flexWrap: 'wrap', gap: '16px',
        position: 'relative', zIndex: 10,
      }}>
        <MoonknightLogo size={20} />
        <span style={{ fontSize: '13px', color: 'rgba(255,255,255,0.2)' }}>
          © {new Date().getFullYear()} Moonknight. All rights reserved.
        </span>
      </footer>

      {/* ── Scroll indicator ── */}
      <div style={{
        position: 'fixed', bottom: '30px', left: '50%', transform: 'translateX(-50%)',
        zIndex: 50, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '6px',
        opacity: Math.max(0, 1 - scrollY / 300),
        transition: 'opacity 0.3s', pointerEvents: 'none',
      }}>
        <span style={{ fontSize: '11px', color: 'rgba(255,255,255,0.25)', letterSpacing: '0.12em' }}>SCROLL</span>
        <div style={{
          width: '1px', height: '40px',
          background: 'linear-gradient(to bottom, rgba(255,255,255,0.25), transparent)',
          animation: 'scrollPulse 2s ease-in-out infinite',
        }} />
      </div>

      {/* ── Section dots ── */}
      <div style={{
        position: 'fixed', right: '24px', top: '50%',
        transform: 'translateY(-50%)', zIndex: 50,
        display: 'flex', flexDirection: 'column', gap: '10px',
      }}>
        {SECTIONS.map((_, i) => (
          <div
            key={i}
            onClick={() => window.scrollTo({ top: i * sectionHeight, behavior: 'smooth' })}
            style={{
              width: currentSection === i ? '8px' : '4px',
              height: currentSection === i ? '8px' : '4px',
              borderRadius: '50%',
              background: currentSection === i ? '#fff' : 'rgba(255,255,255,0.2)',
              transition: 'all 0.3s',
              cursor: 'pointer',
            }}
          />
        ))}
      </div>

      <style>{`
        * { box-sizing: border-box; margin: 0; padding: 0; }
        html { scroll-behavior: smooth; }
        body { background: #000000; -webkit-font-smoothing: antialiased; -moz-osx-font-smoothing: grayscale; }
        @keyframes scrollPulse {
          0%, 100% { opacity: 0.3; transform: scaleY(1); }
          50% { opacity: 1; transform: scaleY(1.2); }
        }
      `}</style>
    </div>
  );
}