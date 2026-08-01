'use client';

import { useState, useEffect } from 'react';
import { useRouter } from 'next/navigation';

const INK = '#16182d';
const MUTED = '#5c5f73';
const PAPER = '#faf8f5';
const LINE = 'rgba(22,24,45,0.12)';

function Logo({ size = 26 }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
      <svg width={size} height={size} viewBox="0 0 32 32" fill="none">
        <circle cx="16" cy="16" r="12" fill={INK} />
        <circle cx="20" cy="13" r="10" fill={PAPER} />
      </svg>
      <span style={{ color: INK, fontSize: size * 0.55, fontWeight: 700, letterSpacing: '0.14em' }}>
        MOONKNIGHT
      </span>
    </div>
  );
}

function PillButton({ children, onClick, primary }) {
  return (
    <button
      onClick={onClick}
      style={{
        padding: '12px 28px', borderRadius: 999, fontSize: 15, fontWeight: 500, cursor: 'pointer',
        background: primary ? INK : 'transparent',
        color: primary ? PAPER : INK,
        border: primary ? `1px solid ${INK}` : `1px solid ${LINE}`,
        transition: 'opacity 0.15s',
      }}
      onMouseEnter={e => { e.currentTarget.style.opacity = '0.8'; }}
      onMouseLeave={e => { e.currentTarget.style.opacity = '1'; }}
    >
      {children}
    </button>
  );
}

function SectionLabel({ children }) {
  return (
    <div style={{ fontSize: 13, letterSpacing: '0.18em', color: MUTED, textTransform: 'uppercase', textAlign: 'center', marginBottom: 14 }}>
      {children}
    </div>
  );
}

const FEATURES = [
  {
    title: 'Batch API',
    desc: 'Upload a JSONL of prompts, submit a batch, download results. OpenAI-compatible — existing code migrates by changing one line.',
  },
  {
    title: 'Distributed GPU workers',
    desc: 'Jobs route to real GPUs pooled from labs and clusters. Workers pull work, so they run anywhere — even behind campus NAT.',
  },
  {
    title: 'Organizations & teams',
    desc: 'A lab is an org. Owner, admin and viewer roles, shared worker keys, and every job attributable to the team that ran it.',
  },
  {
    title: 'Usage analytics',
    desc: 'Requests, models, and per-worker contribution tracked from day one — the utilization evidence your institution wants.',
  },
];

const VALUES = [
  {
    title: 'Pull-based by design',
    desc: 'Workers poll for work and disappear without ceremony. Intermittent lab machines are a feature, not a failure.',
  },
  {
    title: 'Fault tolerant',
    desc: 'Silent workers are detected in minutes and their jobs requeued automatically. Batches survive the hardware they run on.',
  },
  {
    title: 'Curated model catalogue',
    desc: 'Every servable model is a pinned artifact — weights, quantization, runtime. Reproducible by construction.',
  },
];

const MODELS = ['Llama 3', 'Mistral', 'Qwen', 'Gemma', 'Phi', 'DeepSeek'];

export default function Home() {
  const router = useRouter();
  const [isLoggedIn, setIsLoggedIn] = useState(false);

  useEffect(() => {
    setIsLoggedIn(!!localStorage.getItem('mk_token'));
  }, []);

  const go = (path) => () => router.push(path);

  return (
    <div style={{ background: PAPER, minHeight: '100vh', color: INK, fontFamily: "'Geist', 'Inter', -apple-system, sans-serif" }}>

      {/* ── Floating pill nav ── */}
      <nav style={{
        position: 'sticky', top: 16, zIndex: 50,
        maxWidth: 1080, margin: '16px auto 0', padding: '14px 28px',
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        background: 'rgba(255,255,255,0.92)', backdropFilter: 'blur(8px)',
        border: `1px solid ${LINE}`, borderRadius: 999,
      }}>
        <div style={{ cursor: 'pointer' }} onClick={go('/')}><Logo /></div>
        <div style={{ display: 'flex', gap: 32, fontSize: 14, color: MUTED }}>
          {[['Platform', '#platform'], ['Developers', '#developers'], ['Providers', '#providers']].map(([label, href]) => (
            <a key={href} href={href} style={{ color: MUTED, textDecoration: 'none' }}>{label}</a>
          ))}
        </div>
        <div style={{ display: 'flex', gap: 10 }}>
          {isLoggedIn ? (
            <PillButton primary onClick={go('/dashboard')}>Dashboard</PillButton>
          ) : (
            <>
              <PillButton primary onClick={go('/login')}>Log in</PillButton>
              <PillButton onClick={go('/signup')}>Sign up</PillButton>
            </>
          )}
        </div>
      </nav>

      {/* ── Hero ── */}
      <header style={{ maxWidth: 780, margin: '0 auto', padding: '110px 24px 70px', textAlign: 'center' }}>
        <div style={{ color: MUTED, fontSize: 18, letterSpacing: '0.35em', marginBottom: 24 }}>❋ ❋ ❋</div>
        <div style={{ width: 200, height: 1, background: LINE, margin: '0 auto 24px' }} />
        <div style={{ fontSize: 15, color: '#4a4fa3', marginBottom: 30 }}>
          The shared GPU compute platform
        </div>
        <h1 style={{
          fontFamily: "Georgia, 'Times New Roman', serif", fontWeight: 400,
          fontSize: 'clamp(44px, 7vw, 76px)', lineHeight: 1.08, margin: '0 0 28px', letterSpacing: '-0.01em',
        }}>
          Idle GPUs, working together
        </h1>
        <p style={{ fontSize: 19, color: MUTED, lineHeight: 1.6, margin: '0 auto 40px', maxWidth: 560 }}>
          Built on pooled compute. Powered by open-weight models.<br />
          Delivering batch AI at campus scale.
        </p>
        <div style={{ display: 'flex', gap: 14, justifyContent: 'center', flexWrap: 'wrap' }}>
          <PillButton primary onClick={go(isLoggedIn ? '/dashboard' : '/signup')}>
            {isLoggedIn ? 'Go to dashboard' : 'Sign up'}
          </PillButton>
          <PillButton onClick={go('/provider')}>Become a provider</PillButton>
        </div>
      </header>

      {/* ── Models strip ── */}
      <section style={{ borderTop: `1px solid ${LINE}`, borderBottom: `1px solid ${LINE}`, padding: '26px 24px', background: '#ffffff' }}>
        <SectionLabel>Runs open-weight models</SectionLabel>
        <div style={{ display: 'flex', gap: 42, justifyContent: 'center', flexWrap: 'wrap' }}>
          {MODELS.map(m => (
            <span key={m} style={{ fontSize: 20, fontWeight: 600, color: 'rgba(22,24,45,0.35)', letterSpacing: '0.02em' }}>{m}</span>
          ))}
        </div>
      </section>

      {/* ── Platform features ── */}
      <section id="platform" style={{ maxWidth: 1080, margin: '0 auto', padding: '90px 24px 40px' }}>
        <SectionLabel>Platform</SectionLabel>
        <h2 style={{ fontFamily: 'Georgia, serif', fontWeight: 400, fontSize: 'clamp(30px, 4vw, 44px)', textAlign: 'center', margin: '0 0 14px' }}>
          Everything a batch needs
        </h2>
        <p style={{ textAlign: 'center', color: MUTED, fontSize: 17, margin: '0 0 48px' }}>
          From upload to download, the pool does the rest.
        </p>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))', gap: 18 }}>
          {FEATURES.map(f => (
            <div key={f.title} style={{ background: '#fff', border: `1px solid ${LINE}`, borderRadius: 18, padding: '26px 24px' }}>
              <h3 style={{ fontSize: 18, fontWeight: 600, margin: '0 0 10px' }}>{f.title}</h3>
              <p style={{ fontSize: 14.5, color: MUTED, lineHeight: 1.65, margin: 0 }}>{f.desc}</p>
            </div>
          ))}
        </div>
      </section>

      {/* ── Developers ── */}
      <section id="developers" style={{ maxWidth: 1080, margin: '0 auto', padding: '70px 24px 40px' }}>
        <SectionLabel>Developers</SectionLabel>
        <h2 style={{ fontFamily: 'Georgia, serif', fontWeight: 400, fontSize: 'clamp(30px, 4vw, 44px)', textAlign: 'center', margin: '0 0 48px' }}>
          One-line migration
        </h2>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))', gap: 18, alignItems: 'stretch' }}>
          <div style={{ background: INK, color: '#e9e9f2', borderRadius: 18, padding: '28px 26px', fontFamily: "'SF Mono', 'Fira Code', monospace", fontSize: 14.5, lineHeight: 2 }}>
            <div style={{ color: 'rgba(233,233,242,0.45)', fontSize: 12, letterSpacing: '0.08em', marginBottom: 10 }}>BEFORE</div>
            <div style={{ color: '#f3a0a0' }}>base_url = &quot;https://api.openai.com/v1&quot;</div>
            <div style={{ color: 'rgba(233,233,242,0.45)', fontSize: 12, letterSpacing: '0.08em', margin: '18px 0 10px' }}>AFTER</div>
            <div style={{ color: '#a8e6b8' }}>base_url = &quot;https://moonknight.gamekeepers.in/v1&quot;</div>
            <div style={{ color: '#9db8ee', marginTop: 4 }}>api_key = &quot;gk-your_personal_key&quot;</div>
            <div style={{ color: 'rgba(233,233,242,0.4)', marginTop: 16, fontSize: 13 }}># Everything else stays the same</div>
          </div>
          <div style={{ display: 'grid', gap: 18 }}>
            {[
              ['OpenAI-compatible REST', 'Files and batches, verbatim. Your SDK already speaks it.'],
              ['Model catalogue', 'Pick a pinned model id from /v1/models — copy, paste, run.'],
              ['Dashboard', 'Submit, track, and download without touching curl.'],
            ].map(([t, d]) => (
              <div key={t} style={{ background: '#fff', border: `1px solid ${LINE}`, borderRadius: 18, padding: '20px 24px' }}>
                <h3 style={{ fontSize: 16.5, fontWeight: 600, margin: '0 0 6px' }}>{t}</h3>
                <p style={{ fontSize: 14, color: MUTED, margin: 0, lineHeight: 1.6 }}>{d}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ── Providers / values ── */}
      <section id="providers" style={{ maxWidth: 1080, margin: '0 auto', padding: '70px 24px 40px' }}>
        <SectionLabel>Providers</SectionLabel>
        <h2 style={{ fontFamily: 'Georgia, serif', fontWeight: 400, fontSize: 'clamp(30px, 4vw, 44px)', textAlign: 'center', margin: '0 0 48px' }}>
          Built for machines that come and go
        </h2>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: 18 }}>
          {VALUES.map(v => (
            <div key={v.title} style={{ background: '#fff', border: `1px solid ${LINE}`, borderRadius: 18, padding: '26px 24px' }}>
              <h3 style={{ fontSize: 18, fontWeight: 600, margin: '0 0 10px' }}>{v.title}</h3>
              <p style={{ fontSize: 14.5, color: MUTED, lineHeight: 1.65, margin: 0 }}>{v.desc}</p>
            </div>
          ))}
        </div>
      </section>

      {/* ── CTA ── */}
      <section style={{ maxWidth: 720, margin: '0 auto', padding: '90px 24px 100px', textAlign: 'center' }}>
        <h2 style={{ fontFamily: 'Georgia, serif', fontWeight: 400, fontSize: 'clamp(34px, 5vw, 54px)', margin: '0 0 18px' }}>
          Put idle GPUs to work
        </h2>
        <p style={{ color: MUTED, fontSize: 18, margin: '0 0 36px' }}>
          Sign up in seconds. No credit card — this is a commons, not a cloud bill.
        </p>
        <div style={{ display: 'flex', gap: 14, justifyContent: 'center', flexWrap: 'wrap' }}>
          <PillButton primary onClick={go('/signup')}>Get started</PillButton>
          <PillButton onClick={go('/login')}>Log in</PillButton>
        </div>
      </section>

      {/* ── Footer ── */}
      <footer style={{ borderTop: `1px solid ${LINE}`, background: '#fff' }}>
        <div style={{ maxWidth: 1080, margin: '0 auto', padding: '30px 24px', display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 14 }}>
          <Logo size={20} />
          <div style={{ display: 'flex', gap: 24, fontSize: 13.5 }}>
            <a href="/login" style={{ color: MUTED, textDecoration: 'none' }}>Log in</a>
            <a href="/signup" style={{ color: MUTED, textDecoration: 'none' }}>Sign up</a>
            <a href="/provider" style={{ color: MUTED, textDecoration: 'none' }}>Provider portal</a>
          </div>
          <span style={{ fontSize: 13, color: 'rgba(22,24,45,0.4)' }}>
            © {new Date().getFullYear()} Moonknight · Gamekeepers
          </span>
        </div>
      </footer>
    </div>
  );
}
