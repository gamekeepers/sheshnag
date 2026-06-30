'use client';

import { useState, useEffect, useCallback } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';

const BACKEND = process.env.NEXT_PUBLIC_BACKEND_URL || 'http://localhost:8000';

function authHeaders() {
  const token = typeof window !== 'undefined' ? localStorage.getItem('mk_token') : '';
  return {
    'Authorization': `Bearer ${token}`,
    'ngrok-skip-browser-warning': 'true',
    'Content-Type': 'application/json',
  };
}

function MoonknightLogo({ size = 24 }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: size / 4 }}>
      <svg width={size} height={size} viewBox="0 0 32 32" fill="none">
        <circle cx="16" cy="16" r="12" fill="#fff" />
        <circle cx="20" cy="13" r="10" fill="#0f0f0f" />
      </svg>
      <span style={{ color: '#fff', fontSize: size * 0.5, fontWeight: 500, letterSpacing: '0.12em' }}>MOONKNIGHT</span>
    </div>
  );
}

const STATUS_COLORS = {
  completed:   { dot: '#22c55e', bg: '#1a3a1a', border: '#2d5a2d', text: '#4ade80' },
  running:     { dot: '#3b82f6', bg: '#1a2a4a', border: '#2d4a8a', text: '#60a5fa' },
  in_progress: { dot: '#3b82f6', bg: '#1a2a4a', border: '#2d4a8a', text: '#60a5fa' },
  queued:      { dot: '#f59e0b', bg: '#2a2010', border: '#5a4a20', text: '#fbbf24' },
  validating:  { dot: '#f59e0b', bg: '#2a2010', border: '#5a4a20', text: '#fbbf24' },
  failed:      { dot: '#ef4444', bg: '#3a1a1a', border: '#5a2d2d', text: '#f87171' },
  cancelled:   { dot: '#555',    bg: '#2a2a2a', border: '#333',    text: '#666'    },
};

function StatusPill({ status }) {
  const c = STATUS_COLORS[status] || { bg: '#222', border: '#333', text: '#888', dot: '#555' };
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: '5px', padding: '2px 10px', borderRadius: '999px', fontSize: '11px', fontWeight: 500, backgroundColor: c.bg, border: `1px solid ${c.border}`, color: c.text }}>
      <span style={{ width: '6px', height: '6px', borderRadius: '50%', backgroundColor: c.dot, flexShrink: 0 }} />
      {status?.replace(/_/g, ' ')}
    </span>
  );
}

function StatCard({ label, value, sub, valueColor, subColor, icon }) {
  return (
    <div style={{ backgroundColor: '#141414', border: '1px solid #222', borderRadius: '12px', padding: '18px 20px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '10px' }}>
        <p style={{ fontSize: '11px', color: '#555', textTransform: 'uppercase', letterSpacing: '0.06em', margin: 0 }}>{label}</p>
        {icon && <span style={{ fontSize: '16px' }}>{icon}</span>}
      </div>
      <p style={{ fontSize: '26px', fontWeight: 600, color: valueColor || '#fff', margin: 0, lineHeight: 1 }}>{value}</p>
      {sub && <p style={{ fontSize: '12px', color: subColor || '#555', marginTop: '6px', marginBottom: 0 }}>{sub}</p>}
    </div>
  );
}

function InfoRow({ label, children }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '9px 0', borderBottom: '1px solid #1e1e1e' }}>
      <span style={{ fontSize: '13px', color: '#555' }}>{label}</span>
      <span style={{ fontSize: '13px' }}>{children}</span>
    </div>
  );
}

export default function ProviderPage() {
  const router = useRouter();
  const [jobs, setJobs]               = useState([]);
  const [profile, setProfile]         = useState(null);
  const [isLoading, setIsLoading]     = useState(true);
  const [backendLive, setBackendLive] = useState(false);
  const [lastRefresh, setLastRefresh] = useState(null);

  const loadProfile = useCallback(async () => {
    try {
      const res = await fetch(`${BACKEND}/v1/auth/me`, { headers: authHeaders() });
      if (res.ok) setProfile(await res.json());
    } catch {}
  }, []);

  const loadJobs = useCallback(async () => {
    setIsLoading(true);
    try {
      const res = await fetch(`${BACKEND}/v1/batches`, { headers: authHeaders() });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      const list = data.data || data || [];
      setJobs(list.map(b => ({
        id:       b.id,
        user:     b.metadata?.user_email || b.user_id || '—',
        prompts:  b.request_counts?.total ?? 0,
        status:   b.status,
        progress: b.status === 'completed' ? 100
          : (b.status === 'running' || b.status === 'in_progress')
            ? Math.round(((b.request_counts?.completed ?? 0) / (b.request_counts?.total || 1)) * 100)
            : 0,
        started: b.created_at
          ? new Date(b.created_at * 1000).toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' })
          : '—',
      })));
      setBackendLive(true);
      setLastRefresh(new Date().toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', second: '2-digit' }));
    } catch {
      setBackendLive(false);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    const token = localStorage.getItem('mk_token');
    if (!token) { router.push('/provider/login'); return; }
    loadProfile();
    loadJobs();
  }, [router, loadProfile, loadJobs]);

  function handleLogout() {
    localStorage.removeItem('mk_token');
    localStorage.removeItem('mk_user');
    router.push('/provider/login');
  }

  const totalJobs     = jobs.length;
  const activeJobs    = jobs.filter(j => ['running', 'in_progress'].includes(j.status)).length;
  const completedJobs = jobs.filter(j => j.status === 'completed').length;
  const queuedJobs    = jobs.filter(j => j.status === 'queued').length;

  const displayName = profile?.full_name || profile?.email || 'Provider';
  const initials    = displayName.split(' ').map(w => w[0]).join('').toUpperCase().slice(0, 2);

  return (
    <div style={{ minHeight: '100vh', backgroundColor: '#0f0f0f', color: '#ccc', fontFamily: "'Inter', sans-serif" }}>

      {/* TOP BAR */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '14px 28px', borderBottom: '1px solid #1e1e1e', backgroundColor: '#111', position: 'sticky', top: 0, zIndex: 10 }}>
        <Link href="/"><MoonknightLogo size={22} /></Link>

        <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '5px' }}>
            <span style={{ width: '6px', height: '6px', borderRadius: '50%', backgroundColor: backendLive ? '#22c55e' : '#ef4444', boxShadow: backendLive ? '0 0 6px #22c55e' : 'none', display: 'inline-block' }} />
            <span style={{ fontSize: '11px', color: '#444' }}>{backendLive ? 'Live' : 'Offline'}</span>
          </div>

          {lastRefresh && <span style={{ fontSize: '11px', color: '#333' }}>Updated {lastRefresh}</span>}

          <button onClick={loadJobs} disabled={isLoading} style={{ padding: '5px 12px', borderRadius: '8px', border: '1px solid #2a2a2a', background: 'transparent', color: isLoading ? '#333' : '#666', cursor: isLoading ? 'default' : 'pointer', fontSize: '11px' }}>
            {isLoading ? '↻ Loading...' : '↻ Refresh'}
          </button>

          <span style={{ padding: '4px 12px', borderRadius: '999px', backgroundColor: '#1a3a1a', border: '1px solid #2d5a2d', color: '#4ade80', fontSize: '11px', fontWeight: 500 }}>
            ⚡ Provider
          </span>

          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <div style={{ width: '32px', height: '32px', borderRadius: '50%', backgroundColor: '#1a3a1a', border: '1px solid #2d5a2d', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '12px', fontWeight: 600, color: '#4ade80' }}>
              {initials || '?'}
            </div>
            <span style={{ fontSize: '13px', color: '#888', maxWidth: '140px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{displayName}</span>
          </div>

          <button onClick={handleLogout} style={{ padding: '6px 14px', borderRadius: '8px', border: '1px solid #3a1a1a', background: '#1a0a0a', color: '#f87171', cursor: 'pointer', fontSize: '12px', display: 'flex', alignItems: 'center', gap: '5px' }}>
            🚪 Logout
          </button>
        </div>
      </div>

      <div style={{ maxWidth: '1040px', margin: '0 auto', padding: '32px 24px' }}>

        <p style={{ fontSize: '11px', letterSpacing: '0.1em', color: '#444', textTransform: 'uppercase', marginBottom: '14px' }}>Overview</p>

        {/* Stat cards */}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: '12px', marginBottom: '20px' }}>
          <StatCard label="Total jobs"   value={isLoading ? '…' : totalJobs}     icon="📋" />
          <StatCard label="Active"       value={isLoading ? '…' : activeJobs}    icon="⚙️"  valueColor="#60a5fa" sub="running now" />
          <StatCard label="Completed"    value={isLoading ? '…' : completedJobs} icon="✅"  valueColor="#4ade80" />
          <StatCard label="Queued"       value={isLoading ? '…' : queuedJobs}    icon="⏳"  valueColor="#fbbf24" sub="waiting" />
        </div>

        {/* Server info + capacity */}
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px', marginBottom: '20px' }}>
          <div style={{ backgroundColor: '#141414', border: '1px solid #222', borderRadius: '12px', padding: '20px' }}>
            <p style={{ fontSize: '14px', fontWeight: 500, color: '#fff', marginBottom: '14px', marginTop: 0 }}>Server info</p>
            <InfoRow label="Status">
              <span style={{ padding: '2px 10px', borderRadius: '999px', backgroundColor: '#1a3a1a', border: '1px solid #2d5a2d', color: '#4ade80', fontSize: '12px' }}>Online</span>
            </InfoRow>
            <InfoRow label="Provider"><span style={{ color: '#fff' }}>{profile?.company_name || profile?.full_name || '—'}</span></InfoRow>
            <InfoRow label="GPU model"><span style={{ color: '#fff' }}>{profile?.gpu_model || '—'}</span></InfoRow>
            <InfoRow label="GPU count"><span style={{ color: '#fff' }}>{profile?.gpu_count ?? '—'}</span></InfoRow>
            <InfoRow label="Email"><span style={{ color: '#888', fontSize: '12px' }}>{profile?.email || '—'}</span></InfoRow>
          </div>

          <div style={{ backgroundColor: '#141414', border: '1px solid #222', borderRadius: '12px', padding: '20px' }}>
            <p style={{ fontSize: '14px', fontWeight: 500, color: '#fff', marginBottom: '14px', marginTop: 0 }}>Capacity</p>
            <InfoRow label="Total jobs received"><span style={{ color: '#fff' }}>{totalJobs}</span></InfoRow>
            <InfoRow label="Currently running">
              <span style={{ padding: '2px 10px', borderRadius: '999px', backgroundColor: '#1a2a4a', border: '1px solid #2d4a8a', color: '#60a5fa', fontSize: '12px' }}>{activeJobs} active</span>
            </InfoRow>
            <InfoRow label="Queued">
              <span style={{ padding: '2px 10px', borderRadius: '999px', backgroundColor: '#2a2010', border: '1px solid #5a4a20', color: '#fbbf24', fontSize: '12px' }}>{queuedJobs} waiting</span>
            </InfoRow>
            <InfoRow label="Completed"><span style={{ color: '#4ade80' }}>{completedJobs}</span></InfoRow>
            <InfoRow label="Role"><span style={{ color: '#888', textTransform: 'capitalize' }}>{profile?.role || 'provider'}</span></InfoRow>
          </div>
        </div>

        {/* Jobs table */}
        <div style={{ backgroundColor: '#141414', border: '1px solid #222', borderRadius: '12px', overflow: 'hidden' }}>
          <div style={{ padding: '18px 20px 12px', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <p style={{ fontSize: '14px', fontWeight: 500, color: '#fff', margin: 0 }}>Jobs assigned to you</p>
            <span style={{ fontSize: '12px', color: '#444' }}>{totalJobs} total</span>
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: '160px 1fr 90px 140px 160px 80px', padding: '8px 20px', borderTop: '1px solid #1e1e1e', borderBottom: '1px solid #1e1e1e' }}>
            {['Job ID', 'User', 'Prompts', 'Status', 'Progress', 'Started'].map(h => (
              <span key={h} style={{ fontSize: '10px', color: '#444', textTransform: 'uppercase', letterSpacing: '0.06em' }}>{h}</span>
            ))}
          </div>

          {isLoading ? (
            <div style={{ padding: '40px', textAlign: 'center', color: '#444', fontSize: '13px' }}>Loading jobs…</div>
          ) : jobs.length === 0 ? (
            <div style={{ padding: '40px', textAlign: 'center', color: '#444', fontSize: '13px' }}>
              {backendLive ? 'No jobs assigned yet.' : 'Backend is offline. Could not load jobs.'}
            </div>
          ) : jobs.map((job, i) => (
            <div key={job.id} style={{ display: 'grid', gridTemplateColumns: '160px 1fr 90px 140px 160px 80px', padding: '13px 20px', borderBottom: i < jobs.length - 1 ? '1px solid #1a1a1a' : 'none', alignItems: 'center' }}>
              <span style={{ fontFamily: 'monospace', fontSize: '12px', color: '#ddd' }}>{job.id}</span>
              <span style={{ fontSize: '13px', color: '#888' }}>{job.user}</span>
              <span style={{ fontSize: '13px', color: '#ddd' }}>{Number(job.prompts).toLocaleString()}</span>
              <StatusPill status={job.status} />
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                <div style={{ flex: 1, height: '4px', backgroundColor: '#2a2a2a', borderRadius: '2px', overflow: 'hidden' }}>
                  <div style={{ height: '100%', borderRadius: '2px', transition: 'width 0.4s ease', backgroundColor: job.progress === 100 ? '#22c55e' : job.progress > 0 ? '#3b82f6' : '#333', width: `${job.progress}%` }} />
                </div>
                <span style={{ fontSize: '11px', color: '#555', width: '28px' }}>{job.progress}%</span>
              </div>
              <span style={{ fontSize: '12px', color: '#555' }}>{job.started}</span>
            </div>
          ))}
        </div>

      </div>
    </div>
  );
}
