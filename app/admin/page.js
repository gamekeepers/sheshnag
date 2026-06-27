'use client';

import { useState, useEffect } from 'react';
import Link from 'next/link';

const BACKEND = process.env.NEXT_PUBLIC_BACKEND_URL || 'http://localhost:8000';

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

const NAV_ITEMS = [
  { id: 'overview',  label: 'Overview',  icon: '⬛' },
  { id: 'jobs',      label: 'Jobs',       icon: '📋' },
  { id: 'users',     label: 'Users',      icon: '👥' },
  { id: 'providers', label: 'Providers',  icon: '⚡' },
  { id: 'logs',      label: 'Logs',       icon: '📄' },
];

/* ── Status styling ── */
const JOB_STATUS_COLORS = {
  completed: { bg: '#1a3a1a', border: '#2d5a2d', text: '#4ade80' },
  running:   { bg: '#1a2a4a', border: '#2d4a8a', text: '#60a5fa' },
  queued:    { bg: '#2a2010', border: '#5a4a20', text: '#fbbf24' },
  failed:    { bg: '#3a1a1a', border: '#5a2d2d', text: '#f87171' },
  in_progress: { bg: '#1a2a4a', border: '#2d4a8a', text: '#60a5fa' },
  validating:  { bg: '#2a2010', border: '#5a4a20', text: '#fbbf24' },
  finalizing:  { bg: '#1a2a4a', border: '#2d4a8a', text: '#60a5fa' },
  cancelling:  { bg: '#2a2010', border: '#5a4a20', text: '#fbbf24' },
  cancelled:   { bg: '#2a2a2a', border: '#333',    text: '#666' },
  expired:     { bg: '#3a1a1a', border: '#5a2d2d', text: '#f87171' },
};
const LOG_COLORS = { info: '#60a5fa', warn: '#fbbf24', error: '#f87171' };

function StatusPill({ status, colors }) {
  const c = (colors || JOB_STATUS_COLORS)[status] || { bg: '#222', border: '#333', text: '#888' };
  return (
    <span style={{
      padding: '2px 10px', borderRadius: '999px', fontSize: '11px', fontWeight: 500,
      backgroundColor: c.bg, border: `1px solid ${c.border}`, color: c.text,
      textTransform: 'capitalize',
    }}>
      {status?.replace(/_/g, ' ')}
    </span>
  );
}

/* ── Map backend batch → display job ── */
function mapBatchToJob(batch) {
  const fileMap = typeof window !== 'undefined'
    ? JSON.parse(localStorage.getItem('moonknight_file_map') || '{}')
    : {};
  return {
    id: batch.id,
    user: batch.metadata?.user_email || '—',
    prompts: batch.request_counts?.total || 0,
    status: batch.status,
    provider: batch.metadata?.provider || '—',
    started: batch.created_at
      ? new Date(batch.created_at * 1000).toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' })
      : '—',
    filename: fileMap[batch.input_file_id] || batch.input_file_id || '—',
  };
}

export default function AdminPage() {
  const [activeNav, setActiveNav]   = useState('overview');
  const [jobs, setJobs]             = useState([]);
  const [jobFilter, setJobFilter]   = useState('all');
  const [backendStatus, setBackendStatus] = useState('checking'); // 'live' | 'offline' | 'checking'
  const [isLoading, setIsLoading]   = useState(true);
  const [lastRefresh, setLastRefresh] = useState(null);
  const [adminUser, setAdminUser] = useState({ name: 'Admin', role: 'Platform admin' });

  async function loadAdminProfile() {
    try {
      const token = localStorage.getItem('mk_token');
      if (!token) return;
      const res = await fetch(`${BACKEND}/v1/auth/me`, {
        headers: { 'Authorization': `Bearer ${token}`, 'ngrok-skip-browser-warning': 'true' }
      });
      if (res.ok) {
        const data = await res.json();
        setAdminUser({ name: data.full_name || data.email || 'Admin', role: data.role || 'Platform admin' });
      }
    } catch {}
  }

  /* ── Fetch real jobs from backend ── */
  async function loadJobs() {
    setIsLoading(true);
    try {
      const token = localStorage.getItem('mk_token') || '';
      const res = await fetch(`${BACKEND}/v1/batches`, {
        headers: {
          'ngrok-skip-browser-warning': 'true',
          'Authorization': `Bearer ${token}`
        },
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      const list = data.data || [];
      if (list.length > 0) {
        setJobs(list.map(mapBatchToJob));
      }
      setBackendStatus('live');
      setLastRefresh(new Date().toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', second: '2-digit' }));
    } catch {
      setBackendStatus('offline');
      // keep demo data shown
    } finally {
      setIsLoading(false);
    }
  }

  useEffect(() => { loadJobs(); loadAdminProfile(); }, []);

  /* ── Filtered jobs ── */
  const filteredJobs = jobFilter === 'all' ? jobs : jobs.filter(j => j.status === jobFilter);

  /* ── Stats ── */
  const totalJobs       = jobs.length;
  const activeJobs      = jobs.filter(j => ['running', 'in_progress'].includes(j.status)).length;
  const completedJobs   = jobs.filter(j => j.status === 'completed').length;
  const failedJobs      = jobs.filter(j => ['failed', 'expired', 'cancelled'].includes(j.status)).length;

  return (
    <div style={{ display: 'flex', height: '100vh', backgroundColor: '#0a0a0a', color: '#ccc', fontFamily: "'Inter', sans-serif", overflow: 'hidden' }}>

      {/* ── SIDEBAR ── */}
      <div style={{
        width: '210px', flexShrink: 0, backgroundColor: '#0f0f0f',
        borderRight: '1px solid #1e1e1e', display: 'flex', flexDirection: 'column', padding: '16px 0',
      }}>
        <div style={{ padding: '0 16px 16px', borderBottom: '1px solid #1e1e1e', marginBottom: '12px' }}>
          <Link href="/"><MoonknightLogo size={22} /></Link>
          <div style={{
            marginTop: '8px', padding: '3px 8px', borderRadius: '6px',
            backgroundColor: '#1a1a1a', border: '1px solid #2a2a2a',
            fontSize: '10px', color: '#555', letterSpacing: '0.08em', display: 'inline-block',
          }}>
            ADMIN PANEL
          </div>
        </div>

        <div style={{ flex: 1, padding: '0 8px' }}>
          <p style={{ fontSize: '9px', color: '#444', letterSpacing: '0.1em', textTransform: 'uppercase', padding: '0 8px', marginBottom: '6px' }}>
            Manage
          </p>
          {NAV_ITEMS.map(item => (
            <button
              key={item.id}
              onClick={() => setActiveNav(item.id)}
              style={{
                display: 'flex', alignItems: 'center', gap: '10px',
                width: '100%', padding: '9px 10px', borderRadius: '8px', border: 'none', cursor: 'pointer',
                fontSize: '13px', textAlign: 'left', marginBottom: '2px', transition: 'all 0.15s',
                backgroundColor: activeNav === item.id ? '#1e1e1e' : 'transparent',
                color: activeNav === item.id ? '#fff' : '#555',
              }}
            >
              <span style={{ fontSize: '14px' }}>{item.icon}</span>
              {item.label}
            </button>
          ))}
        </div>

        {/* Admin user */}
        <div style={{ padding: '12px 16px', borderTop: '1px solid #1e1e1e' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <div style={{
              width: '28px', height: '28px', borderRadius: '50%', backgroundColor: '#2d3a5a',
              display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '11px', color: '#fff', fontWeight: 600,
            }}>{adminUser.name.charAt(0).toUpperCase()}</div>
            <div>
              <p style={{ fontSize: '12px', color: '#fff', margin: 0, textTransform: 'capitalize', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', maxWidth: '120px' }}>{adminUser.name}</p>
              <p style={{ fontSize: '10px', color: '#444', margin: 0, textTransform: 'capitalize' }}>{adminUser.role}</p>
            </div>
          </div>
        </div>
      </div>

      {/* ── MAIN ── */}
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>

        {/* Top bar */}
        <div style={{
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          padding: '14px 24px', borderBottom: '1px solid #1e1e1e', flexShrink: 0,
        }}>
          <h1 style={{ fontSize: '15px', fontWeight: 500, color: '#fff', margin: 0 }}>
            {NAV_ITEMS.find(n => n.id === activeNav)?.label}
          </h1>

          {/* Right side: backend status + refresh */}
          <div style={{ display: 'flex', alignItems: 'center', gap: '16px' }}>
            {lastRefresh && (
              <span style={{ fontSize: '11px', color: '#444' }}>Updated {lastRefresh}</span>
            )}
            <button
              onClick={loadJobs}
              disabled={isLoading}
              style={{
                padding: '5px 12px', borderRadius: '8px', border: '1px solid #2a2a2a',
                background: 'transparent', color: isLoading ? '#444' : '#aaa', cursor: isLoading ? 'default' : 'pointer',
                fontSize: '12px',
              }}
            >
              {isLoading ? '↻ Loading...' : '↻ Refresh'}
            </button>
            {/* Backend status pill */}
            <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
              <span style={{
                width: '7px', height: '7px', borderRadius: '50%', display: 'inline-block',
                backgroundColor: backendStatus === 'live' ? '#22c55e' : backendStatus === 'offline' ? '#ef4444' : '#f59e0b',
                boxShadow: backendStatus === 'live' ? '0 0 6px #22c55e' : 'none',
              }} />
              <span style={{ fontSize: '11px', color: '#555' }}>
                {backendStatus === 'live' ? 'Backend live' : backendStatus === 'offline' ? 'Backend offline (demo data)' : 'Connecting...'}
              </span>
            </div>
          </div>
        </div>

        {/* Body */}
        <div style={{ flex: 1, overflowY: 'auto', padding: '24px' }}>

          {/* ── OVERVIEW ── */}
          {activeNav === 'overview' && (
            <>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '12px', marginBottom: '20px' }}>
                <StatCard label="Total jobs"      value={totalJobs}      icon="📋" />
                <StatCard label="Active"          value={activeJobs}     icon="⚙️"  valueColor="#60a5fa" sub="running now" />
                <StatCard label="Completed"       value={completedJobs}  icon="✅"  valueColor="#4ade80" />

              </div>

              <SectionLabel>Recent Jobs {backendStatus === 'offline' && <DemoBadge />}</SectionLabel>
              {jobs.length === 0 && backendStatus !== 'checking' ? (
                <div style={{ backgroundColor: '#111', border: '1px solid #1e1e1e', borderRadius: '12px', padding: '40px', textAlign: 'center' }}>
                  <p style={{ fontSize: '13px', color: '#555' }}>No jobs yet</p>
                </div>
              ) : (
                <JobsTable jobs={jobs.slice(0, 6)} />
              )}
            </>
          )}

          {/* ── JOBS ── */}
          {activeNav === 'jobs' && (
            <>
              {/* Filter bar */}
              <div style={{ display: 'flex', gap: '8px', marginBottom: '16px', alignItems: 'center', flexWrap: 'wrap' }}>
                {['all', 'running', 'in_progress', 'completed', 'queued', 'failed'].map(f => (
                  <button
                    key={f}
                    onClick={() => setJobFilter(f)}
                    style={{
                      padding: '5px 14px', borderRadius: '999px', border: '1px solid',
                      fontSize: '12px', cursor: 'pointer', transition: 'all 0.15s',
                      borderColor: jobFilter === f ? '#fff' : '#2a2a2a',
                      backgroundColor: jobFilter === f ? '#fff' : 'transparent',
                      color: jobFilter === f ? '#000' : '#555',
                      textTransform: 'capitalize',
                    }}
                  >
                    {f.replace('_', ' ')}
                  </button>
                ))}
                <span style={{ marginLeft: 'auto', fontSize: '12px', color: '#444' }}>
                  {filteredJobs.length} job{filteredJobs.length !== 1 ? 's' : ''}
                  {backendStatus === 'offline' && <span style={{ color: '#f59e0b', marginLeft: '8px' }}>(demo data)</span>}
                </span>
              </div>

              <JobsTable jobs={filteredJobs} showFilename />
            </>
          )}

          {/* ── USERS ── */}
          {activeNav === 'users' && (
            <>
              <SectionLabel>All Users</SectionLabel>
              <div style={{ backgroundColor: '#111', border: '1px solid #1e1e1e', borderRadius: '12px', padding: '40px', textAlign: 'center' }}>
                <p style={{ fontSize: '13px', color: '#555' }}>User management not yet implemented</p>
              </div>
            </>
          )}

          {/* ── PROVIDERS ── */}
          {activeNav === 'providers' && (
            <>
              <SectionLabel>All Providers</SectionLabel>
              <div style={{ backgroundColor: '#111', border: '1px solid #1e1e1e', borderRadius: '12px', padding: '40px', textAlign: 'center' }}>
                <p style={{ fontSize: '13px', color: '#555' }}>Provider management not yet implemented</p>
              </div>
            </>
          )}

          {/* ── LOGS ── */}
          {activeNav === 'logs' && (
            <>
              <SectionLabel>System Logs</SectionLabel>
              <div style={{ backgroundColor: '#111', border: '1px solid #1e1e1e', borderRadius: '12px', padding: '40px', textAlign: 'center' }}>
                <p style={{ fontSize: '13px', color: '#555' }}>Log streaming not yet implemented</p>
              </div>
            </>
          )}

        </div>
      </div>
    </div>
  );
}

/* ── Sub-components ── */

function JobsTable({ jobs, showFilename }) {
  const headers = showFilename
    ? ['Job ID', 'File / User', 'Prompts', 'Status', 'Started']
    : ['Job ID', 'User', 'Prompts', 'Status', 'Provider', 'Started'];
  const cols = showFilename
    ? '150px 1fr 80px 130px 90px'
    : '150px 1fr 80px 130px 130px 90px';

  return (
    <Table headers={headers} cols={cols}>
      {jobs.length === 0 ? (
        <div style={{ padding: '40px', textAlign: 'center', color: '#444', fontSize: '13px' }}>
          No jobs found
        </div>
      ) : jobs.map((job, i) => (
        <TableRow key={job.id} cols={cols} isLast={i === jobs.length - 1}>
          <span style={{ fontFamily: 'monospace', fontSize: '12px', color: '#ddd' }}>{job.id}</span>
          <span style={{ fontSize: '13px', color: '#888' }}>{showFilename ? (job.filename || job.user) : job.user}</span>
          <span style={{ fontSize: '13px', color: '#ddd' }}>{Number(job.prompts || 0).toLocaleString()}</span>
          <StatusPill status={job.status} />
          {!showFilename && <span style={{ fontSize: '12px', color: '#666' }}>{job.provider}</span>}
          <span style={{ fontSize: '12px', color: '#555' }}>{job.started}</span>
        </TableRow>
      ))}
    </Table>
  );
}

function ProvidersTable({ providers, showAll }) {
  const headers = showAll
    ? ['Provider', 'GPU', 'Model', 'Concurrency', 'Active Jobs', 'Uptime', 'Status']
    : ['Provider', 'GPU', 'Model', 'Jobs', 'Uptime', 'Status'];
  const cols = showAll
    ? '140px 110px 1fr 110px 100px 80px 90px'
    : '140px 110px 1fr 60px 80px 90px';

  return (
    <Table headers={headers} cols={cols}>
      {providers.map((p, i) => (
        <TableRow key={p.id} cols={cols} isLast={i === providers.length - 1}>
          <span style={{ fontSize: '13px', color: '#fff' }}>{p.name}</span>
          <span style={{ fontSize: '12px', color: '#888' }}>{p.gpu}</span>
          <span style={{ fontSize: '12px', color: '#aaa' }}>{p.model}</span>
          {showAll && <span style={{ fontSize: '13px', color: '#ddd' }}>{p.concurrency}</span>}
          <span style={{ fontSize: '13px', color: '#ddd' }}>{p.jobs}</span>
          <span style={{ fontSize: '12px', color: '#4ade80' }}>{p.uptime}</span>
          <ProviderStatusPill status={p.status} />
        </TableRow>
      ))}
    </Table>
  );
}

function StatCard({ label, value, icon, valueColor, sub }) {
  return (
    <div style={{ backgroundColor: '#111', border: '1px solid #1e1e1e', borderRadius: '12px', padding: '18px 20px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '10px' }}>
        <p style={{ fontSize: '11px', color: '#444', textTransform: 'uppercase', letterSpacing: '0.06em', margin: 0 }}>{label}</p>
        <span style={{ fontSize: '16px' }}>{icon}</span>
      </div>
      <p style={{ fontSize: '28px', fontWeight: 600, color: valueColor || '#fff', margin: 0, lineHeight: 1 }}>{value}</p>
      {sub && <p style={{ fontSize: '11px', color: '#444', marginTop: '6px' }}>{sub}</p>}
    </div>
  );
}

function SectionLabel({ children }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '10px' }}>
      <p style={{ fontSize: '11px', color: '#444', textTransform: 'uppercase', letterSpacing: '0.08em', margin: 0 }}>
        {children}
      </p>
    </div>
  );
}

function DemoBadge() {
  return (
    <span style={{
      fontSize: '9px', padding: '1px 6px', borderRadius: '4px',
      backgroundColor: '#2a2010', border: '1px solid #5a4a20', color: '#fbbf24',
      letterSpacing: '0.05em', fontWeight: 500, verticalAlign: 'middle', marginLeft: '4px',
    }}>
      DEMO
    </span>
  );
}

function Table({ headers, cols, children }) {
  return (
    <div style={{ backgroundColor: '#111', border: '1px solid #1e1e1e', borderRadius: '12px', overflow: 'hidden' }}>
      <div style={{ display: 'grid', gridTemplateColumns: cols, padding: '10px 18px', borderBottom: '1px solid #1a1a1a' }}>
        {headers.map(h => (
          <span key={h} style={{ fontSize: '10px', color: '#444', textTransform: 'uppercase', letterSpacing: '0.06em' }}>{h}</span>
        ))}
      </div>
      {children}
    </div>
  );
}

function TableRow({ cols, children, isLast }) {
  return (
    <div style={{
      display: 'grid', gridTemplateColumns: cols, padding: '13px 18px', alignItems: 'center',
      borderBottom: isLast ? 'none' : '1px solid #161616',
    }}>
      {children}
    </div>
  );
}

function ProviderStatusPill({ status }) {
  const s = {
    online:  { bg: '#1a3a1a', border: '#2d5a2d', text: '#4ade80' },
    offline: { bg: '#2a2a2a', border: '#333',     text: '#555' },
    pending: { bg: '#2a2010', border: '#5a4a20',  text: '#fbbf24' },
  };
  const c = s[status] || s.offline;
  return (
    <span style={{
      padding: '2px 10px', borderRadius: '999px', fontSize: '11px', fontWeight: 500,
      backgroundColor: c.bg, border: `1px solid ${c.border}`, color: c.text,
    }}>{status}</span>
  );
}

function UserStatusPill({ status }) {
  const s = {
    active:    { bg: '#1a3a1a', border: '#2d5a2d', text: '#4ade80' },
    suspended: { bg: '#3a1a1a', border: '#5a2d2d', text: '#f87171' },
  };
  const c = s[status] || s.active;
  return (
    <span style={{
      padding: '2px 10px', borderRadius: '999px', fontSize: '11px', fontWeight: 500,
      backgroundColor: c.bg, border: `1px solid ${c.border}`, color: c.text,
    }}>{status}</span>
  );
}
 