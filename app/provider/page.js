'use client';

import { useState } from 'react';
import Link from 'next/link';

function MoonknightLogo({ size = 28 }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: size / 4 }}>
      <svg width={size} height={size} viewBox="0 0 32 32" fill="none">
        <circle cx="16" cy="16" r="12" fill="#fff" />
        <circle cx="20" cy="13" r="10" fill="#1a1a1a" />
      </svg>
      <span style={{ color: '#fff', fontSize: size * 0.45, fontWeight: 500, letterSpacing: '0.12em' }}>MOONKNIGHT</span>
    </div>
  );
}

const DEMO_JOBS = [
  { id: 'job_a91f', user: 'achyut@mk.ai', prompts: 5000, status: 'completed', progress: 100, started: '2h ago' },
  { id: 'job_b34c', user: 'user_2@mk.ai', prompts: 2400, status: 'running', progress: 55, started: '18m ago' },
  { id: 'job_c77e', user: 'user_5@mk.ai', prompts: 800, status: 'running', progress: 30, started: '6m ago' },
  { id: 'job_d12a', user: 'user_9@mk.ai', prompts: 3100, status: 'queued', progress: 0, started: 'Just now' },
];

const STATUS_DOT = {
  completed: '#22c55e',
  running: '#3b82f6',
  queued: '#f59e0b',
  failed: '#ef4444',
};

export default function ProviderPage() {
  const [jobs] = useState(DEMO_JOBS);

  return (
    <div style={{ minHeight: '100vh', backgroundColor: '#0f0f0f', color: '#ccc', fontFamily: "'Inter', sans-serif" }}>

      {/* ── TOP BAR ── */}
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        padding: '14px 28px', borderBottom: '1px solid #222', backgroundColor: '#111',
      }}>
        <Link href="/"><MoonknightLogo size={24} /></Link>

        <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
          {/* Provider badge */}
          <span style={{
            padding: '4px 12px', borderRadius: '999px',
            backgroundColor: '#1a3a1a', border: '1px solid #2d5a2d',
            color: '#4ade80', fontSize: '12px', fontWeight: 500,
          }}>
            Provider
          </span>

          {/* User name */}
          <span style={{ fontSize: '14px', color: '#aaa' }}>Nirav Shah</span>

          {/* Avatar */}
          <div style={{
            width: '34px', height: '34px', borderRadius: '50%',
            backgroundColor: '#2d5a8a', display: 'flex', alignItems: 'center',
            justifyContent: 'center', fontSize: '13px', fontWeight: 600, color: '#fff',
          }}>
            NS
          </div>
        </div>
      </div>

      <div style={{ maxWidth: '960px', margin: '0 auto', padding: '32px 24px' }}>

        {/* ── OVERVIEW LABEL ── */}
        <p style={{ fontSize: '11px', letterSpacing: '0.1em', color: '#555', textTransform: 'uppercase', marginBottom: '14px' }}>
          Overview
        </p>

        {/* ── STAT CARDS ── */}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: '12px', marginBottom: '20px' }}>
          <StatCard label="Jobs processed" value="1,284" sub="+38 today" subColor="#4ade80" />
          <StatCard label="Active jobs" value="3" sub="2 queued" />
          <StatCard label="Avg. latency" value="1.4s" sub="Good" subColor="#4ade80" />
          <StatCard label="GPU utilisation" value="74%" sub="A100 · 80GB" valueColor="#f59e0b" />
        </div>

        {/* ── SERVER INFO + CAPACITY ── */}
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px', marginBottom: '20px' }}>

          {/* Server info */}
          <div style={{ backgroundColor: '#1a1a1a', border: '1px solid #2a2a2a', borderRadius: '12px', padding: '20px' }}>
            <p style={{ fontSize: '15px', fontWeight: 500, color: '#fff', marginBottom: '16px' }}>Server info</p>
            <InfoRow label="Status">
              <span style={{
                padding: '2px 10px', borderRadius: '999px',
                backgroundColor: '#1a3a1a', border: '1px solid #2d5a2d', color: '#4ade80', fontSize: '12px',
              }}>Online</span>
            </InfoRow>
            <InfoRow label="Model"><span style={{ color: '#fff' }}>Llama 3.1 70B</span></InfoRow>
            <InfoRow label="Endpoint"><span style={{ color: '#888', fontSize: '12px' }}>*.cfargotunnel.com</span></InfoRow>
            <InfoRow label="Max concurrency"><span style={{ color: '#fff' }}>8</span></InfoRow>
            <InfoRow label="Approved by"><span style={{ color: '#888' }}>Platform admin</span></InfoRow>
          </div>

          {/* Capacity */}
          <div style={{ backgroundColor: '#1a1a1a', border: '1px solid #2a2a2a', borderRadius: '12px', padding: '20px' }}>
            <p style={{ fontSize: '15px', fontWeight: 500, color: '#fff', marginBottom: '16px' }}>Capacity</p>
            <InfoRow label="Worker slots"><span style={{ color: '#fff' }}>8 total</span></InfoRow>
            <InfoRow label="In use">
              <span style={{
                padding: '2px 10px', borderRadius: '999px',
                backgroundColor: '#1a2a4a', border: '1px solid #2d4a8a', color: '#60a5fa', fontSize: '12px',
              }}>3 busy</span>
            </InfoRow>
            <InfoRow label="Idle">
              <span style={{
                padding: '2px 10px', borderRadius: '999px',
                backgroundColor: '#2a2010', border: '1px solid #5a4a20', color: '#fbbf24', fontSize: '12px',
              }}>5 idle</span>
            </InfoRow>
            <InfoRow label="Queue depth"><span style={{ color: '#fff' }}>2 jobs</span></InfoRow>
            <InfoRow label="Uptime"><span style={{ color: '#4ade80' }}>99.2%</span></InfoRow>
          </div>
        </div>

        {/* ── JOBS TABLE ── */}
        <div style={{ backgroundColor: '#1a1a1a', border: '1px solid #2a2a2a', borderRadius: '12px', overflow: 'hidden' }}>
          <div style={{ padding: '20px 20px 12px' }}>
            <p style={{ fontSize: '15px', fontWeight: 500, color: '#fff' }}>Jobs assigned to you</p>
          </div>

          {/* Table header */}
          <div style={{
            display: 'grid', gridTemplateColumns: '120px 1fr 90px 120px 160px 90px',
            padding: '8px 20px', borderTop: '1px solid #222', borderBottom: '1px solid #222',
          }}>
            {['Job ID', 'User', 'Prompts', 'Status', 'Progress', 'Started'].map(h => (
              <span key={h} style={{ fontSize: '11px', color: '#555', textTransform: 'uppercase', letterSpacing: '0.05em' }}>{h}</span>
            ))}
          </div>

          {/* Rows */}
          {jobs.map((job, i) => (
            <div key={job.id} style={{
              display: 'grid', gridTemplateColumns: '120px 1fr 90px 120px 160px 90px',
              padding: '14px 20px', borderBottom: i < jobs.length - 1 ? '1px solid #1e1e1e' : 'none',
              alignItems: 'center',
            }}>
              <span style={{ fontFamily: 'monospace', fontSize: '13px', color: '#ddd' }}>{job.id}</span>
              <span style={{ fontSize: '13px', color: '#aaa' }}>{job.user}</span>
              <span style={{ fontSize: '13px', color: '#ddd' }}>{job.prompts.toLocaleString()}</span>
              <span style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '13px', color: '#ddd' }}>
                <span style={{ width: '7px', height: '7px', borderRadius: '50%', backgroundColor: STATUS_DOT[job.status], display: 'inline-block', flexShrink: 0 }} />
                {job.status.charAt(0).toUpperCase() + job.status.slice(1)}
              </span>
              {/* Progress bar */}
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                <div style={{ flex: 1, height: '4px', backgroundColor: '#2a2a2a', borderRadius: '2px', overflow: 'hidden' }}>
                  <div style={{
                    height: '100%', borderRadius: '2px',
                    backgroundColor: job.progress === 100 ? '#22c55e' : job.progress > 0 ? '#3b82f6' : '#333',
                    width: `${job.progress}%`,
                    transition: 'width 0.4s ease',
                  }} />
                </div>
              </div>
              <span style={{ fontSize: '12px', color: '#666' }}>{job.started}</span>
            </div>
          ))}
        </div>

      </div>
    </div>
  );
}

function StatCard({ label, value, sub, valueColor, subColor }) {
  return (
    <div style={{ backgroundColor: '#1a1a1a', border: '1px solid #2a2a2a', borderRadius: '12px', padding: '18px 20px' }}>
      <p style={{ fontSize: '12px', color: '#666', marginBottom: '8px' }}>{label}</p>
      <p style={{ fontSize: '26px', fontWeight: 600, color: valueColor || '#fff', lineHeight: 1 }}>{value}</p>
      {sub && <p style={{ fontSize: '12px', color: subColor || '#555', marginTop: '6px' }}>{sub}</p>}
    </div>
  );
}

function InfoRow({ label, children }) {
  return (
    <div style={{
      display: 'flex', justifyContent: 'space-between', alignItems: 'center',
      padding: '9px 0', borderBottom: '1px solid #222',
    }}>
      <span style={{ fontSize: '13px', color: '#666' }}>{label}</span>
      <span style={{ fontSize: '13px' }}>{children}</span>
    </div>
  );
}
