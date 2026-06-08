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

const NAV_ITEMS = [
  { id: 'overview', label: 'Overview', icon: '⬛' },
  { id: 'jobs',     label: 'Jobs',     icon: '📋' },
  { id: 'users',    label: 'Users',    icon: '👥' },
  { id: 'providers',label: 'Providers',icon: '⚡' },
  { id: 'logs',     label: 'Logs',     icon: '📄' },
];

const DEMO_JOBS = [
  { id: 'job_a91f', user: 'achyut@mk.ai',  prompts: 5000, status: 'completed', provider: 'Nirav Shah',  started: '2h ago' },
  { id: 'job_b34c', user: 'user_2@mk.ai',  prompts: 2400, status: 'running',   provider: 'Nirav Shah',  started: '18m ago' },
  { id: 'job_c77e', user: 'user_5@mk.ai',  prompts: 800,  status: 'running',   provider: 'Vatsal K.',   started: '6m ago' },
  { id: 'job_d12a', user: 'user_9@mk.ai',  prompts: 3100, status: 'queued',    provider: '—',           started: 'Just now' },
  { id: 'job_e55b', user: 'riya@mk.ai',    prompts: 620,  status: 'failed',    provider: 'Vatsal K.',   started: '1h ago' },
  { id: 'job_f20c', user: 'karan@mk.ai',   prompts: 1200, status: 'completed', provider: 'Nirav Shah',  started: '3h ago' },
];

const DEMO_USERS = [
  { id: 'u001', name: 'Achyut Pathak',  email: 'achyut@mk.ai',  jobs: 12, joined: '23 May 2026', status: 'active' },
  { id: 'u002', name: 'User Two',       email: 'user_2@mk.ai',  jobs: 4,  joined: '24 May 2026', status: 'active' },
  { id: 'u003', name: 'User Five',      email: 'user_5@mk.ai',  jobs: 2,  joined: '24 May 2026', status: 'active' },
  { id: 'u004', name: 'Riya Sharma',    email: 'riya@mk.ai',    jobs: 7,  joined: '22 May 2026', status: 'suspended' },
  { id: 'u005', name: 'Karan Mehta',    email: 'karan@mk.ai',   jobs: 3,  joined: '25 May 2026', status: 'active' },
  { id: 'u006', name: 'User Nine',      email: 'user_9@mk.ai',  jobs: 1,  joined: '25 May 2026', status: 'active' },
];

const DEMO_PROVIDERS = [
  { id: 'p001', name: 'Nirav Shah',  gpu: 'A100 80GB', model: 'Llama 3.1 70B', concurrency: 8, jobs: 3, uptime: '99.2%', status: 'online'  },
  { id: 'p002', name: 'Vatsal K.',   gpu: 'RTX 4090',  model: 'Mistral 7B',    concurrency: 4, jobs: 1, uptime: '97.8%', status: 'online'  },
  { id: 'p003', name: 'Ankush R.',   gpu: 'A100 40GB', model: 'Llama 3.1 8B',  concurrency: 6, jobs: 0, uptime: '—',     status: 'offline' },
  { id: 'p004', name: 'Priya Dev',   gpu: 'H100 80GB', model: 'Gemma 2 27B',   concurrency: 8, jobs: 0, uptime: '—',     status: 'pending' },
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

const STATUS_COLORS = {
  completed: { bg: '#1a3a1a', border: '#2d5a2d', text: '#4ade80' },
  running:   { bg: '#1a2a4a', border: '#2d4a8a', text: '#60a5fa' },
  queued:    { bg: '#2a2010', border: '#5a4a20', text: '#fbbf24' },
  failed:    { bg: '#3a1a1a', border: '#5a2d2d', text: '#f87171' },
};

const LOG_COLORS = { info: '#60a5fa', warn: '#fbbf24', error: '#f87171' };

function StatusPill({ status }) {
  const c = STATUS_COLORS[status] || { bg: '#222', border: '#333', text: '#aaa' };
  return (
    <span style={{
      padding: '2px 10px', borderRadius: '999px', fontSize: '11px', fontWeight: 500,
      backgroundColor: c.bg, border: `1px solid ${c.border}`, color: c.text,
    }}>
      {status}
    </span>
  );
}

export default function AdminPage() {
  const [activeNav, setActiveNav] = useState('overview');
  const [jobFilter, setJobFilter] = useState('all');

  const filteredJobs = jobFilter === 'all'
    ? DEMO_JOBS
    : DEMO_JOBS.filter(j => j.status === jobFilter);

  const totalJobs      = DEMO_JOBS.length;
  const activeJobs     = DEMO_JOBS.filter(j => j.status === 'running').length;
  const totalUsers     = DEMO_USERS.length;
  const onlineProviders = DEMO_PROVIDERS.filter(p => p.status === 'online').length;

  return (
    <div style={{ display: 'flex', height: '100vh', backgroundColor: '#0a0a0a', color: '#ccc', fontFamily: "'Inter', sans-serif", overflow: 'hidden' }}>

      {/* ── SIDEBAR ── */}
      <div style={{
        width: '200px', flexShrink: 0, backgroundColor: '#0f0f0f',
        borderRight: '1px solid #1e1e1e', display: 'flex', flexDirection: 'column', padding: '16px 0',
      }}>
        {/* Logo */}
        <div style={{ padding: '0 16px 16px', borderBottom: '1px solid #1e1e1e', marginBottom: '12px' }}>
          <Link href="/"><MoonknightLogo size={22} /></Link>
          <div style={{
            marginTop: '8px', padding: '3px 8px', borderRadius: '6px',
            backgroundColor: '#1a1a1a', border: '1px solid #2a2a2a',
            fontSize: '10px', color: '#666', letterSpacing: '0.08em', display: 'inline-block',
          }}>
            ADMIN PANEL
          </div>
        </div>

        {/* Nav */}
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
                color: activeNav === item.id ? '#fff' : '#666',
              }}
            >
              <span style={{ fontSize: '14px' }}>{item.icon}</span>
              {item.label}
            </button>
          ))}
        </div>

        {/* Admin user at bottom */}
        <div style={{ padding: '12px 16px', borderTop: '1px solid #1e1e1e' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <div style={{
              width: '28px', height: '28px', borderRadius: '50%', backgroundColor: '#2d3a5a',
              display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '11px', color: '#fff', fontWeight: 600,
            }}>A</div>
            <div>
              <p style={{ fontSize: '12px', color: '#fff', margin: 0 }}>Admin</p>
              <p style={{ fontSize: '10px', color: '#555', margin: 0 }}>Platform admin</p>
            </div>
          </div>
        </div>
      </div>

      {/* ── MAIN CONTENT ── */}
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>

        {/* Top bar */}
        <div style={{
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          padding: '14px 24px', borderBottom: '1px solid #1e1e1e', flexShrink: 0,
        }}>
          <h1 style={{ fontSize: '16px', fontWeight: 500, color: '#fff', margin: 0 }}>
            {NAV_ITEMS.find(n => n.id === activeNav)?.label}
          </h1>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <span style={{
              width: '7px', height: '7px', borderRadius: '50%', backgroundColor: '#22c55e',
              boxShadow: '0 0 6px #22c55e', display: 'inline-block',
            }} />
            <span style={{ fontSize: '12px', color: '#555' }}>System online</span>
          </div>
        </div>

        {/* Scrollable body */}
        <div style={{ flex: 1, overflowY: 'auto', padding: '24px' }}>

          {/* ── OVERVIEW ── */}
          {activeNav === 'overview' && (
            <>
              {/* Stat cards */}
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: '12px', marginBottom: '20px' }}>
                <StatCard label="Total jobs" value={totalJobs} icon="📋" />
                <StatCard label="Active jobs" value={activeJobs} icon="⚙️" valueColor="#60a5fa" sub="running now" />
                <StatCard label="Total users" value={totalUsers} icon="👥" />
                <StatCard label="Providers online" value={`${onlineProviders}/${DEMO_PROVIDERS.length}`} icon="⚡" valueColor="#4ade80" sub="GPU nodes" />
              </div>

              {/* Recent jobs table */}
              <SectionTitle>Recent Jobs</SectionTitle>
              <Table
                headers={['Job ID', 'User', 'Prompts', 'Status', 'Provider', 'Started']}
                cols="130px 1fr 80px 120px 130px 90px"
              >
                {DEMO_JOBS.map((job, i) => (
                  <TableRow key={job.id} cols="130px 1fr 80px 120px 130px 90px" isLast={i === DEMO_JOBS.length - 1}>
                    <span style={{ fontFamily: 'monospace', fontSize: '12px', color: '#ddd' }}>{job.id}</span>
                    <span style={{ fontSize: '13px', color: '#aaa' }}>{job.user}</span>
                    <span style={{ fontSize: '13px', color: '#ddd' }}>{job.prompts.toLocaleString()}</span>
                    <StatusPill status={job.status} />
                    <span style={{ fontSize: '12px', color: '#888' }}>{job.provider}</span>
                    <span style={{ fontSize: '12px', color: '#555' }}>{job.started}</span>
                  </TableRow>
                ))}
              </Table>

              {/* Active providers */}
              <SectionTitle style={{ marginTop: '20px' }}>Active Providers</SectionTitle>
              <Table headers={['Provider', 'GPU', 'Model', 'Jobs', 'Uptime', 'Status']} cols="140px 110px 1fr 60px 80px 90px">
                {DEMO_PROVIDERS.map((p, i) => (
                  <TableRow key={p.id} cols="140px 110px 1fr 60px 80px 90px" isLast={i === DEMO_PROVIDERS.length - 1}>
                    <span style={{ fontSize: '13px', color: '#fff' }}>{p.name}</span>
                    <span style={{ fontSize: '12px', color: '#888' }}>{p.gpu}</span>
                    <span style={{ fontSize: '12px', color: '#aaa' }}>{p.model}</span>
                    <span style={{ fontSize: '13px', color: '#ddd' }}>{p.jobs}</span>
                    <span style={{ fontSize: '12px', color: '#4ade80' }}>{p.uptime}</span>
                    <ProviderStatusPill status={p.status} />
                  </TableRow>
                ))}
              </Table>
            </>
          )}

          {/* ── JOBS ── */}
          {activeNav === 'jobs' && (
            <>
              {/* Filter bar */}
              <div style={{ display: 'flex', gap: '8px', marginBottom: '16px' }}>
                {['all', 'running', 'completed', 'queued', 'failed'].map(f => (
                  <button
                    key={f}
                    onClick={() => setJobFilter(f)}
                    style={{
                      padding: '6px 14px', borderRadius: '999px', border: '1px solid',
                      fontSize: '12px', cursor: 'pointer', transition: 'all 0.15s',
                      borderColor: jobFilter === f ? '#fff' : '#2a2a2a',
                      backgroundColor: jobFilter === f ? '#fff' : 'transparent',
                      color: jobFilter === f ? '#000' : '#666',
                    }}
                  >
                    {f.charAt(0).toUpperCase() + f.slice(1)}
                  </button>
                ))}
                <span style={{ marginLeft: 'auto', fontSize: '12px', color: '#555', alignSelf: 'center' }}>
                  {filteredJobs.length} job{filteredJobs.length !== 1 ? 's' : ''}
                </span>
              </div>

              <Table headers={['Job ID', 'User', 'Prompts', 'Status', 'Provider', 'Started']} cols="130px 1fr 80px 120px 130px 90px">
                {filteredJobs.map((job, i) => (
                  <TableRow key={job.id} cols="130px 1fr 80px 120px 130px 90px" isLast={i === filteredJobs.length - 1}>
                    <span style={{ fontFamily: 'monospace', fontSize: '12px', color: '#ddd' }}>{job.id}</span>
                    <span style={{ fontSize: '13px', color: '#aaa' }}>{job.user}</span>
                    <span style={{ fontSize: '13px', color: '#ddd' }}>{job.prompts.toLocaleString()}</span>
                    <StatusPill status={job.status} />
                    <span style={{ fontSize: '12px', color: '#888' }}>{job.provider}</span>
                    <span style={{ fontSize: '12px', color: '#555' }}>{job.started}</span>
                  </TableRow>
                ))}
              </Table>
            </>
          )}

          {/* ── USERS ── */}
          {activeNav === 'users' && (
            <Table headers={['User', 'Email', 'Jobs', 'Joined', 'Status']} cols="160px 1fr 60px 130px 100px">
              {DEMO_USERS.map((u, i) => (
                <TableRow key={u.id} cols="160px 1fr 60px 130px 100px" isLast={i === DEMO_USERS.length - 1}>
                  <span style={{ fontSize: '13px', color: '#fff' }}>{u.name}</span>
                  <span style={{ fontSize: '13px', color: '#888' }}>{u.email}</span>
                  <span style={{ fontSize: '13px', color: '#ddd' }}>{u.jobs}</span>
                  <span style={{ fontSize: '12px', color: '#555' }}>{u.joined}</span>
                  <UserStatusPill status={u.status} />
                </TableRow>
              ))}
            </Table>
          )}

          {/* ── PROVIDERS ── */}
          {activeNav === 'providers' && (
            <Table headers={['Provider', 'GPU', 'Model', 'Concurrency', 'Active Jobs', 'Uptime', 'Status']} cols="140px 110px 1fr 110px 100px 80px 90px">
              {DEMO_PROVIDERS.map((p, i) => (
                <TableRow key={p.id} cols="140px 110px 1fr 110px 100px 80px 90px" isLast={i === DEMO_PROVIDERS.length - 1}>
                  <span style={{ fontSize: '13px', color: '#fff' }}>{p.name}</span>
                  <span style={{ fontSize: '12px', color: '#888' }}>{p.gpu}</span>
                  <span style={{ fontSize: '12px', color: '#aaa' }}>{p.model}</span>
                  <span style={{ fontSize: '13px', color: '#ddd' }}>{p.concurrency}</span>
                  <span style={{ fontSize: '13px', color: '#ddd' }}>{p.jobs}</span>
                  <span style={{ fontSize: '12px', color: '#4ade80' }}>{p.uptime}</span>
                  <ProviderStatusPill status={p.status} />
                </TableRow>
              ))}
            </Table>
          )}

          {/* ── LOGS ── */}
          {activeNav === 'logs' && (
            <div style={{ backgroundColor: '#0f0f0f', border: '1px solid #1e1e1e', borderRadius: '12px', padding: '16px', fontFamily: 'monospace' }}>
              <p style={{ fontSize: '11px', color: '#444', marginBottom: '12px', letterSpacing: '0.08em' }}>SYSTEM LOGS — LIVE</p>
              {DEMO_LOGS.map((log, i) => (
                <div key={i} style={{ display: 'flex', gap: '14px', padding: '7px 0', borderBottom: i < DEMO_LOGS.length - 1 ? '1px solid #161616' : 'none', alignItems: 'flex-start' }}>
                  <span style={{ fontSize: '11px', color: '#444', flexShrink: 0 }}>{log.time}</span>
                  <span style={{ fontSize: '11px', color: LOG_COLORS[log.level], flexShrink: 0, width: '36px' }}>{log.level}</span>
                  <span style={{ fontSize: '12px', color: '#888' }}>{log.msg}</span>
                </div>
              ))}
            </div>
          )}

        </div>
      </div>
    </div>
  );
}

/* ── Helper components ── */

function StatCard({ label, value, icon, valueColor, sub }) {
  return (
    <div style={{ backgroundColor: '#111', border: '1px solid #1e1e1e', borderRadius: '12px', padding: '18px 20px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '10px' }}>
        <p style={{ fontSize: '11px', color: '#555', textTransform: 'uppercase', letterSpacing: '0.06em', margin: 0 }}>{label}</p>
        <span style={{ fontSize: '16px' }}>{icon}</span>
      </div>
      <p style={{ fontSize: '28px', fontWeight: 600, color: valueColor || '#fff', margin: 0, lineHeight: 1 }}>{value}</p>
      {sub && <p style={{ fontSize: '11px', color: '#555', marginTop: '6px' }}>{sub}</p>}
    </div>
  );
}

function SectionTitle({ children }) {
  return (
    <p style={{ fontSize: '11px', color: '#444', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: '10px' }}>
      {children}
    </p>
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
  const styles = {
    online:  { bg: '#1a3a1a', border: '#2d5a2d', text: '#4ade80' },
    offline: { bg: '#2a2a2a', border: '#333',     text: '#555'    },
    pending: { bg: '#2a2010', border: '#5a4a20',  text: '#fbbf24' },
  };
  const c = styles[status] || styles.offline;
  return (
    <span style={{
      padding: '2px 10px', borderRadius: '999px', fontSize: '11px', fontWeight: 500,
      backgroundColor: c.bg, border: `1px solid ${c.border}`, color: c.text,
    }}>
      {status}
    </span>
  );
}

function UserStatusPill({ status }) {
  const styles = {
    active:    { bg: '#1a3a1a', border: '#2d5a2d', text: '#4ade80' },
    suspended: { bg: '#3a1a1a', border: '#5a2d2d', text: '#f87171' },
  };
  const c = styles[status] || styles.active;
  return (
    <span style={{
      padding: '2px 10px', borderRadius: '999px', fontSize: '11px', fontWeight: 500,
      backgroundColor: c.bg, border: `1px solid ${c.border}`, color: c.text,
    }}>
      {status}
    </span>
  );
}
