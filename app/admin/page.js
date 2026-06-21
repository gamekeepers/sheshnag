'use client';

import { useState, useEffect } from 'react';
import Link from 'next/link';

const BACKEND = 'https://hungry-whacking-reflex.ngrok-free.dev';

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

/* ── Demo fallback data ── */
const DEMO_JOBS = [
  { id: 'job_a91f', user: 'achyut@mk.ai', prompts: 5000, status: 'completed', provider: 'Nirav Shah', started: '2h ago' },
  { id: 'job_b34c', user: 'user_2@mk.ai', prompts: 2400, status: 'running',   provider: 'Nirav Shah', started: '18m ago' },
  { id: 'job_c77e', user: 'user_5@mk.ai', prompts: 800,  status: 'running',   provider: 'Vatsal K.',  started: '6m ago' },
  { id: 'job_d12a', user: 'user_9@mk.ai', prompts: 3100, status: 'queued',    provider: '—',          started: 'Just now' },
  { id: 'job_e55b', user: 'riya@mk.ai',   prompts: 620,  status: 'failed',    provider: 'Vatsal K.',  started: '1h ago' },
  { id: 'job_f20c', user: 'karan@mk.ai',  prompts: 1200, status: 'completed', provider: 'Nirav Shah', started: '3h ago' },
];

const DEMO_USERS = [
  { id: 'u001', name: 'Achyut Pathak', email: 'achyut@mk.ai',  jobs: 12, joined: '23 May 2026', status: 'active' },
  { id: 'u002', name: 'User Two',      email: 'user_2@mk.ai',  jobs: 4,  joined: '24 May 2026', status: 'active' },
  { id: 'u003', name: 'User Five',     email: 'user_5@mk.ai',  jobs: 2,  joined: '24 May 2026', status: 'active' },
  { id: 'u004', name: 'Riya Sharma',   email: 'riya@mk.ai',    jobs: 7,  joined: '22 May 2026', status: 'suspended' },
  { id: 'u005', name: 'Karan Mehta',   email: 'karan@mk.ai',   jobs: 3,  joined: '25 May 2026', status: 'active' },
  { id: 'u006', name: 'User Nine',     email: 'user_9@mk.ai',  jobs: 1,  joined: '25 May 2026', status: 'active' },
];

const DEMO_PROVIDERS = [
  { id: 'p001', name: 'Nirav Shah', gpu: 'A100 80GB', model: 'Llama 3.1 70B', concurrency: 8, jobs: 3, uptime: '99.2%', status: 'online'  },
  { id: 'p002', name: 'Vatsal K.', gpu: 'RTX 4090',  model: 'Mistral 7B',    concurrency: 4, jobs: 1, uptime: '97.8%', status: 'online'  },
  { id: 'p003', name: 'Ankush R.', gpu: 'A100 40GB', model: 'Llama 3.1 8B',  concurrency: 6, jobs: 0, uptime: '—',     status: 'offline' },
  { id: 'p004', name: 'Priya Dev', gpu: 'H100 80GB', model: 'Gemma 2 27B',   concurrency: 8, jobs: 0, uptime: '—',     status: 'pending' },
];

const DEMO_LOGS = [
  { time: '23:18:42', level: 'info',  msg: 'Job job_b34c picked up by provider Nirav Shah' },
  { time: '23:17:01', level: 'info',  msg: 'New job submitted: job_d12a by user_9@mk.ai (3100 prompts)' },
  { time: '23:15:50', level: 'error', msg: 'Job job_e55b failed — malformed JSONL input' },
  { time: '23:10:22', level: 'info',  msg: 'Job job_a91f completed — 5000/5000 prompts done' },
  { time: '23:05:11', level: 'warn',  msg: 'Provider Ankush R. went offline — 0 active jobs' },
  { time: '22:58:03', level: 'info',  msg: 'Provider Nirav Shah registered and approved' },
  { time: '22:50:14', level: 'info',  msg: 'New user signup: karan@mk.ai' },
  { time: '22:44:39', level: 'warn',  msg: 'Queue depth reached 5 — consider adding more providers' },
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

  /* ── Fetch real jobs from backend ── */
  async function loadJobs() {
    setIsLoading(true);
    try {
      const res = await fetch(`${BACKEND}/v1/batches`, {
        headers: { 'ngrok-skip-browser-warning': 'true' },
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

  useEffect(() => { loadJobs(); }, []);

  /* ── Filtered jobs ── */
  const filteredJobs = jobFilter === 'all' ? jobs : jobs.filter(j => j.status === jobFilter);

  /* ── Stats ── */
  const totalJobs       = jobs.length;
  const activeJobs      = jobs.filter(j => ['running', 'in_progress'].includes(j.status)).length;
  const completedJobs   = jobs.filter(j => j.status === 'completed').length;
  const failedJobs      = jobs.filter(j => ['failed', 'expired', 'cancelled'].includes(j.status)).length;
  const onlineProviders = DEMO_PROVIDERS.filter(p => p.status === 'online').length;
  const totalUsers      = DEMO_USERS.length;

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
            }}>A</div>
            <div>
              <p style={{ fontSize: '12px', color: '#fff', margin: 0 }}>Admin</p>
              <p style={{ fontSize: '10px', color: '#444', margin: 0 }}>Platform admin</p>
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
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: '12px', marginBottom: '20px' }}>
                <StatCard label="Total jobs"      value={totalJobs}      icon="📋" />
                <StatCard label="Active"          value={activeJobs}     icon="⚙️"  valueColor="#60a5fa" sub="running now" />
                <StatCard label="Completed"       value={completedJobs}  icon="✅"  valueColor="#4ade80" />
                <StatCard label="Providers online" value={`${onlineProviders}/${DEMO_PROVIDERS.length}`} icon="⚡" valueColor="#4ade80" />
              </div>

              <SectionLabel>Recent Jobs {backendStatus === 'offline' && <DemoBadge />}</SectionLabel>
              <JobsTable jobs={DEMO_JOBS.slice(0, 6)} />

              <div style={{ marginTop: '20px' }}>
                <SectionLabel>Active Providers <DemoBadge /></SectionLabel>
                <ProvidersTable providers={DEMO_PROVIDERS} />
              </div>
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
              <SectionLabel>All Users <DemoBadge /></SectionLabel>
              <Table headers={['User', 'Email', 'Jobs submitted', 'Joined', 'Status']} cols="180px 1fr 120px 140px 100px">
                {DEMO_USERS.map((u, i) => (
                  <TableRow key={u.id} cols="180px 1fr 120px 140px 100px" isLast={i === DEMO_USERS.length - 1}>
                    <span style={{ fontSize: '13px', color: '#fff' }}>{u.name}</span>
                    <span style={{ fontSize: '13px', color: '#888' }}>{u.email}</span>
                    <span style={{ fontSize: '13px', color: '#ddd', textAlign: 'center' }}>{u.jobs}</span>
                    <span style={{ fontSize: '12px', color: '#555' }}>{u.joined}</span>
                    <UserStatusPill status={u.status} />
                  </TableRow>
                ))}
              </Table>
            </>
          )}

          {/* ── PROVIDERS ── */}
          {activeNav === 'providers' && (
            <>
              <SectionLabel>All Providers <DemoBadge /></SectionLabel>
              <ProvidersTable providers={DEMO_PROVIDERS} showAll />
            </>
          )}

          {/* ── LOGS ── */}
          {activeNav === 'logs' && (
            <>
              <SectionLabel>System Logs <DemoBadge /></SectionLabel>
              <div style={{ backgroundColor: '#0f0f0f', border: '1px solid #1e1e1e', borderRadius: '12px', padding: '16px', fontFamily: 'monospace' }}>
                {DEMO_LOGS.map((log, i) => (
                  <div key={i} style={{
                    display: 'flex', gap: '14px', padding: '8px 0',
                    borderBottom: i < DEMO_LOGS.length - 1 ? '1px solid #161616' : 'none',
                  }}>
                    <span style={{ fontSize: '11px', color: '#333', flexShrink: 0, width: '60px' }}>{log.time}</span>
                    <span style={{ fontSize: '11px', color: LOG_COLORS[log.level], flexShrink: 0, width: '40px', textTransform: 'uppercase' }}>{log.level}</span>
                    <span style={{ fontSize: '12px', color: '#666' }}>{log.msg}</span>
                  </div>
                ))}
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
 