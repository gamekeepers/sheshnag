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

/* ── Logo ── */
function MoonknightLogo({ size = 22 }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
      <svg width={size} height={size} viewBox="0 0 32 32" fill="none">
        <circle cx="16" cy="16" r="12" fill="#fff" />
        <circle cx="20" cy="13" r="10" fill="#0f0f0f" />
      </svg>
      <span style={{ color: '#fff', fontSize: size * 0.63, fontWeight: 500, letterSpacing: '0.1em' }}>MOONKNIGHT</span>
    </div>
  );
}

/* ── Nav ── */
const NAV_ITEMS = [
  { id: 'overview', label: 'Overview', icon: '⬛' },
  { id: 'jobs',     label: 'Jobs',     icon: '📋' },
  { id: 'users',    label: 'Users',    icon: '👥' },
];

/* ── Status colours ── */
const JOB_STATUS_COLORS = {
  completed:  { bg: '#1a3a1a', border: '#2d5a2d', text: '#4ade80' },
  running:    { bg: '#1a2a4a', border: '#2d4a8a', text: '#60a5fa' },
  in_progress:{ bg: '#1a2a4a', border: '#2d4a8a', text: '#60a5fa' },
  queued:     { bg: '#2a2010', border: '#5a4a20', text: '#fbbf24' },
  validating: { bg: '#2a2010', border: '#5a4a20', text: '#fbbf24' },
  finalizing: { bg: '#1a2a4a', border: '#2d4a8a', text: '#60a5fa' },
  failed:     { bg: '#3a1a1a', border: '#5a2d2d', text: '#f87171' },
  expired:    { bg: '#3a1a1a', border: '#5a2d2d', text: '#f87171' },
  cancelled:  { bg: '#2a2a2a', border: '#333',    text: '#666' },
  cancelling: { bg: '#2a2010', border: '#5a4a20', text: '#fbbf24' },
};

function StatusPill({ status }) {
  const c = JOB_STATUS_COLORS[status] || { bg: '#222', border: '#333', text: '#888' };
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

/* ── Map batch → row ── */
function mapBatch(b) {
  const fileMap = typeof window !== 'undefined'
    ? JSON.parse(localStorage.getItem('moonknight_file_map') || '{}') : {};
  return {
    id:       b.id,
    user:     b.metadata?.user_email || b.user_id || '—',
    prompts:  b.request_counts?.total ?? 0,
    status:   b.status,
    provider: b.metadata?.provider || '—',
    started:  b.created_at ? new Date(b.created_at * 1000).toLocaleString('en-IN') : '—',
    filename: fileMap[b.input_file_id] || b.input_file_id || '—',
  };
}

/* ── Generic table shell ── */
function AdminTable({ headers, cols, rows, renderRow, emptyMsg = 'No data' }) {
  return (
    <div style={{ backgroundColor: '#111', border: '1px solid #1e1e1e', borderRadius: '12px', overflow: 'hidden' }}>
      <div style={{ display: 'grid', gridTemplateColumns: cols, padding: '10px 18px', borderBottom: '1px solid #1a1a1a' }}>
        {headers.map(h => (
          <span key={h} style={{ fontSize: '10px', color: '#444', textTransform: 'uppercase', letterSpacing: '0.06em' }}>{h}</span>
        ))}
      </div>
      {rows.length === 0
        ? <div style={{ padding: '40px', textAlign: 'center', color: '#444', fontSize: '13px' }}>{emptyMsg}</div>
        : rows.map((row, i) => (
            <div key={row.id || i} style={{
              display: 'grid', gridTemplateColumns: cols, padding: '12px 18px', alignItems: 'center',
              borderBottom: i === rows.length - 1 ? 'none' : '1px solid #161616',
            }}>
              {renderRow(row)}
            </div>
          ))
      }
    </div>
  );
}

/* ── Edit User Modal ── */
function EditUserModal({ user, onClose, onSaved }) {
  const [fullName, setFullName] = useState(user.full_name || '');
  const [role, setRole]         = useState(user.role || 'user');
  const [saving, setSaving]     = useState(false);
  const [err, setErr]           = useState('');

  async function handleSave() {
    setSaving(true); setErr('');
    try {
      const res = await fetch(`${BACKEND}/v1/users/${user.id}`, {
        method: 'PUT',
        headers: authHeaders(),
        body: JSON.stringify({ full_name: fullName, role }),
      });
      if (!res.ok) {
        const d = await res.json().catch(() => ({}));
        throw new Error(d.detail || `HTTP ${res.status}`);
      }
      const updated = await res.json();
      onSaved(updated);
    } catch (e) {
      setErr(e.message);
    } finally {
      setSaving(false);
    }
  }

  const inputStyle = {
    width: '100%', padding: '9px 12px', backgroundColor: '#1a1a1a', border: '1px solid #2a2a2a',
    borderRadius: '8px', color: '#fff', fontSize: '13px', outline: 'none', boxSizing: 'border-box',
  };
  const labelStyle = { fontSize: '11px', color: '#555', textTransform: 'uppercase', letterSpacing: '0.06em', display: 'block', marginBottom: '6px' };

  return (
    <div style={{
      position: 'fixed', inset: 0, backgroundColor: 'rgba(0,0,0,0.7)',
      display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 999,
    }}>
      <div style={{ backgroundColor: '#111', border: '1px solid #2a2a2a', borderRadius: '16px', padding: '28px', width: '380px' }}>
        <h2 style={{ color: '#fff', fontSize: '15px', fontWeight: 500, margin: '0 0 20px' }}>Edit User</h2>

        <div style={{ marginBottom: '16px' }}>
          <label style={labelStyle}>Email (read-only)</label>
          <input value={user.email || ''} disabled style={{ ...inputStyle, color: '#444', cursor: 'not-allowed' }} />
        </div>

        <div style={{ marginBottom: '16px' }}>
          <label style={labelStyle}>Full Name</label>
          <input value={fullName} onChange={e => setFullName(e.target.value)} style={inputStyle} />
        </div>

        <div style={{ marginBottom: '20px' }}>
          <label style={labelStyle}>Role</label>
          <select value={role} onChange={e => setRole(e.target.value)} style={{ ...inputStyle, cursor: 'pointer' }}>
            <option value="user">User</option>
            <option value="admin">Admin</option>
            <option value="provider">Provider</option>
          </select>
        </div>

        {err && <p style={{ color: '#f87171', fontSize: '12px', marginBottom: '12px' }}>{err}</p>}

        <div style={{ display: 'flex', gap: '10px', justifyContent: 'flex-end' }}>
          <button onClick={onClose} style={{ padding: '8px 18px', borderRadius: '8px', border: '1px solid #2a2a2a', background: 'transparent', color: '#aaa', cursor: 'pointer', fontSize: '13px' }}>
            Cancel
          </button>
          <button onClick={handleSave} disabled={saving} style={{ padding: '8px 18px', borderRadius: '8px', border: 'none', background: '#fff', color: '#000', cursor: saving ? 'default' : 'pointer', fontSize: '13px', fontWeight: 500 }}>
            {saving ? 'Saving…' : 'Save'}
          </button>
        </div>
      </div>
    </div>
  );
}

/* ══════════════════════════════ MAIN PAGE ══════════════════════════════ */
export default function AdminPage() {
  const router = useRouter();
  const [activeNav,     setActiveNav]     = useState('overview');
  const [jobs,          setJobs]          = useState([]);
  const [users,         setUsers]         = useState([]);
  const [jobFilter,     setJobFilter]     = useState('all');
  const [backendStatus, setBackendStatus] = useState('checking');
  const [isLoading,     setIsLoading]     = useState(true);
  const [lastRefresh,   setLastRefresh]   = useState(null);
  const [adminUser,     setAdminUser]     = useState({ name: 'Admin', role: 'admin' });
  const [editingUser,   setEditingUser]   = useState(null);

  /* ── load admin profile ── */
  async function loadAdminProfile() {
    try {
      const res = await fetch(`${BACKEND}/v1/auth/me`, { headers: authHeaders() });
      if (res.ok) {
        const d = await res.json();
        setAdminUser({ name: d.full_name || d.email || 'Admin', role: d.role || 'admin' });
      }
    } catch {}
  }

  /* ── load all jobs ── */
  const loadJobs = useCallback(async () => {
    setIsLoading(true);
    try {
      const res = await fetch(`${BACKEND}/v1/batches`, { headers: authHeaders() });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setJobs((data.data || data || []).map(mapBatch));
      setBackendStatus('live');
      setLastRefresh(new Date().toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', second: '2-digit' }));
    } catch {
      setBackendStatus('offline');
    } finally {
      setIsLoading(false);
    }
  }, []);

  /* ── load all users ── */
  const loadUsers = useCallback(async () => {
    try {
      const res = await fetch(`${BACKEND}/v1/users`, { headers: authHeaders() });
      if (res.ok) {
        const d = await res.json();
        setUsers(Array.isArray(d) ? d : (d.data || d.users || []));
      }
    } catch {}
  }, []);

  useEffect(() => {
    const token = localStorage.getItem('mk_token');
    const user  = JSON.parse(localStorage.getItem('mk_user') || '{}');
    if (!token || user.role !== 'admin') { router.push('/login'); return; }
    loadAdminProfile();
    loadJobs();
    loadUsers();
  }, [router, loadJobs, loadUsers]);

  function handleLogout() {
    localStorage.removeItem('mk_token');
    localStorage.removeItem('mk_user');
    router.push('/login');
  }

  function handleUserSaved(updated) {
    setUsers(prev => prev.map(u => u.id === updated.id ? { ...u, ...updated } : u));
    setEditingUser(null);
  }

  /* ── derived stats ── */
  const totalJobs     = jobs.length;
  const activeJobs    = jobs.filter(j => ['running','in_progress'].includes(j.status)).length;
  const completedJobs = jobs.filter(j => j.status === 'completed').length;
  const failedJobs    = jobs.filter(j => ['failed','expired','cancelled'].includes(j.status)).length;
  const filteredJobs  = jobFilter === 'all' ? jobs : jobs.filter(j => j.status === jobFilter);

  /* ─────────────────── Shared styles ─────────────────── */
  const S = {
    card: { backgroundColor: '#111', border: '1px solid #1e1e1e', borderRadius: '12px', padding: '18px 20px' },
  };

  return (
    <div style={{ display: 'flex', height: '100vh', backgroundColor: '#0a0a0a', color: '#ccc', fontFamily: "'Inter', sans-serif", overflow: 'hidden' }}>

      {/* ── SIDEBAR ── */}
      <div style={{ width: '210px', flexShrink: 0, backgroundColor: '#0f0f0f', borderRight: '1px solid #1e1e1e', display: 'flex', flexDirection: 'column', padding: '16px 0' }}>
        <div style={{ padding: '0 16px 16px', borderBottom: '1px solid #1e1e1e', marginBottom: '12px' }}>
          <Link href="/"><MoonknightLogo size={22} /></Link>
          <div style={{ marginTop: '8px', padding: '3px 8px', borderRadius: '6px', backgroundColor: '#1a1a1a', border: '1px solid #2a2a2a', fontSize: '10px', color: '#555', letterSpacing: '0.08em', display: 'inline-block' }}>
            ADMIN PANEL
          </div>
        </div>

        <div style={{ flex: 1, padding: '0 8px' }}>
          <p style={{ fontSize: '9px', color: '#444', letterSpacing: '0.1em', textTransform: 'uppercase', padding: '0 8px', marginBottom: '6px' }}>Manage</p>
          {NAV_ITEMS.map(item => (
            <button key={item.id} onClick={() => setActiveNav(item.id)} style={{
              display: 'flex', alignItems: 'center', gap: '10px',
              width: '100%', padding: '9px 10px', borderRadius: '8px', border: 'none', cursor: 'pointer',
              fontSize: '13px', textAlign: 'left', marginBottom: '2px', transition: 'all 0.15s',
              backgroundColor: activeNav === item.id ? '#1e1e1e' : 'transparent',
              color: activeNav === item.id ? '#fff' : '#555',
            }}>
              <span style={{ fontSize: '14px' }}>{item.icon}</span>
              {item.label}
            </button>
          ))}
        </div>

        {/* Admin user + logout */}
        <div style={{ padding: '12px 16px', borderTop: '1px solid #1e1e1e' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <div style={{ width: '28px', height: '28px', borderRadius: '50%', backgroundColor: '#2d3a5a', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '11px', color: '#fff', fontWeight: 600 }}>
              {adminUser.name.charAt(0).toUpperCase()}
            </div>
            <div>
              <p style={{ fontSize: '12px', color: '#fff', margin: 0, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', maxWidth: '120px' }}>{adminUser.name}</p>
              <p style={{ fontSize: '10px', color: '#444', margin: 0, textTransform: 'capitalize' }}>{adminUser.role}</p>
            </div>
          </div>
          <button onClick={handleLogout} style={{ display: 'flex', alignItems: 'center', gap: '8px', width: '100%', padding: '8px 12px', marginTop: '12px', borderRadius: '6px', border: '1px solid #3a1a1a', cursor: 'pointer', fontSize: '12px', backgroundColor: '#1a0a0a', color: '#f87171' }}>
            <span>🚪</span> Logout
          </button>
        </div>
      </div>

      {/* ── MAIN ── */}
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>

        {/* Top bar */}
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '14px 24px', borderBottom: '1px solid #1e1e1e', flexShrink: 0 }}>
          <h1 style={{ fontSize: '15px', fontWeight: 500, color: '#fff', margin: 0 }}>
            {NAV_ITEMS.find(n => n.id === activeNav)?.label}
          </h1>
          <div style={{ display: 'flex', alignItems: 'center', gap: '16px' }}>
            {lastRefresh && <span style={{ fontSize: '11px', color: '#444' }}>Updated {lastRefresh}</span>}
            <button onClick={() => { loadJobs(); loadUsers(); }} disabled={isLoading} style={{ padding: '5px 12px', borderRadius: '8px', border: '1px solid #2a2a2a', background: 'transparent', color: isLoading ? '#444' : '#aaa', cursor: isLoading ? 'default' : 'pointer', fontSize: '12px' }}>
              {isLoading ? '↻ Loading...' : '↻ Refresh'}
            </button>
            <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
              <span style={{ width: '7px', height: '7px', borderRadius: '50%', display: 'inline-block', backgroundColor: backendStatus === 'live' ? '#22c55e' : backendStatus === 'offline' ? '#ef4444' : '#f59e0b', boxShadow: backendStatus === 'live' ? '0 0 6px #22c55e' : 'none' }} />
              <span style={{ fontSize: '11px', color: '#555' }}>
                {backendStatus === 'live' ? 'Backend live' : backendStatus === 'offline' ? 'Backend offline' : 'Connecting...'}
              </span>
            </div>
          </div>
        </div>

        {/* Body */}
        <div style={{ flex: 1, overflowY: 'auto', padding: '24px' }}>

          {/* ── OVERVIEW ── */}
          {activeNav === 'overview' && (
            <>
              {/* Stat cards */}
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: '12px', marginBottom: '24px' }}>
                {[
                  { label: 'Total Jobs',  value: totalJobs,     icon: '📋', color: '#fff' },
                  { label: 'Active',      value: activeJobs,    icon: '⚙️',  color: '#60a5fa' },
                  { label: 'Completed',   value: completedJobs, icon: '✅',  color: '#4ade80' },
                  { label: 'Failed',      value: failedJobs,    icon: '❌',  color: '#f87171' },
                ].map(c => (
                  <div key={c.label} style={S.card}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '10px' }}>
                      <p style={{ fontSize: '11px', color: '#444', textTransform: 'uppercase', letterSpacing: '0.06em', margin: 0 }}>{c.label}</p>
                      <span style={{ fontSize: '16px' }}>{c.icon}</span>
                    </div>
                    <p style={{ fontSize: '28px', fontWeight: 600, color: c.color, margin: 0 }}>{c.value}</p>
                  </div>
                ))}
              </div>

              {/* Users quick summary */}
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: '12px', marginBottom: '24px' }}>
                <div style={S.card}>
                  <p style={{ fontSize: '11px', color: '#444', textTransform: 'uppercase', letterSpacing: '0.06em', margin: '0 0 10px' }}>Total Users</p>
                  <p style={{ fontSize: '28px', fontWeight: 600, color: '#fff', margin: 0 }}>{users.length}</p>
                </div>
                <div style={S.card}>
                  <p style={{ fontSize: '11px', color: '#444', textTransform: 'uppercase', letterSpacing: '0.06em', margin: '0 0 10px' }}>Admin Users</p>
                  <p style={{ fontSize: '28px', fontWeight: 600, color: '#a78bfa', margin: 0 }}>{users.filter(u => u.role === 'admin').length}</p>
                </div>
              </div>

              <p style={{ fontSize: '11px', color: '#444', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: '10px' }}>Recent Jobs</p>
              <AdminTable
                headers={['Job ID', 'User', 'Prompts', 'Status', 'Started']}
                cols="180px 1fr 80px 130px 160px"
                rows={jobs.slice(0, 8)}
                emptyMsg={isLoading ? 'Loading…' : 'No jobs yet'}
                renderRow={job => (<>
                  <span style={{ fontFamily: 'monospace', fontSize: '11px', color: '#ddd' }}>{job.id}</span>
                  <span style={{ fontSize: '12px', color: '#888' }}>{job.user}</span>
                  <span style={{ fontSize: '13px', color: '#ddd' }}>{Number(job.prompts).toLocaleString()}</span>
                  <StatusPill status={job.status} />
                  <span style={{ fontSize: '11px', color: '#555' }}>{job.started}</span>
                </>)}
              />
            </>
          )}

          {/* ── JOBS ── */}
          {activeNav === 'jobs' && (
            <>
              {/* Filter bar */}
              <div style={{ display: 'flex', gap: '8px', marginBottom: '16px', alignItems: 'center', flexWrap: 'wrap' }}>
                {['all', 'validating', 'running', 'in_progress', 'completed', 'failed', 'cancelled', 'expired'].map(f => (
                  <button key={f} onClick={() => setJobFilter(f)} style={{
                    padding: '5px 14px', borderRadius: '999px', border: '1px solid',
                    fontSize: '12px', cursor: 'pointer', transition: 'all 0.15s',
                    borderColor: jobFilter === f ? '#fff' : '#2a2a2a',
                    backgroundColor: jobFilter === f ? '#fff' : 'transparent',
                    color: jobFilter === f ? '#000' : '#555',
                    textTransform: 'capitalize',
                  }}>
                    {f.replace(/_/g, ' ')}
                  </button>
                ))}
                <span style={{ marginLeft: 'auto', fontSize: '12px', color: '#444' }}>
                  {filteredJobs.length} job{filteredJobs.length !== 1 ? 's' : ''}
                </span>
              </div>

              <AdminTable
                headers={['Job ID', 'User', 'Prompts', 'Status', 'Provider', 'Started']}
                cols="180px 1fr 80px 140px 120px 160px"
                rows={filteredJobs}
                emptyMsg={isLoading ? 'Loading…' : 'No jobs found'}
                renderRow={job => (<>
                  <span style={{ fontFamily: 'monospace', fontSize: '11px', color: '#ddd' }}>{job.id}</span>
                  <span style={{ fontSize: '12px', color: '#888' }}>{job.user}</span>
                  <span style={{ fontSize: '13px', color: '#ddd' }}>{Number(job.prompts).toLocaleString()}</span>
                  <StatusPill status={job.status} />
                  <span style={{ fontSize: '12px', color: '#666' }}>{job.provider}</span>
                  <span style={{ fontSize: '11px', color: '#555' }}>{job.started}</span>
                </>)}
              />
            </>
          )}

          {/* ── USERS ── */}
          {activeNav === 'users' && (
            <>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px' }}>
                <p style={{ fontSize: '11px', color: '#444', textTransform: 'uppercase', letterSpacing: '0.08em', margin: 0 }}>
                  All Users — {users.length} total
                </p>
              </div>
              <AdminTable
                headers={['Name', 'Email', 'Role', 'Actions']}
                cols="1fr 1fr 120px 100px"
                rows={users}
                emptyMsg="No users found"
                renderRow={user => (<>
                  <span style={{ fontSize: '13px', color: '#fff' }}>{user.full_name || '—'}</span>
                  <span style={{ fontSize: '12px', color: '#888' }}>{user.email}</span>
                  <span style={{ fontSize: '11px', padding: '2px 10px', borderRadius: '999px', border: '1px solid', display: 'inline-block',
                    ...(user.role === 'admin'
                      ? { backgroundColor: '#1e1040', borderColor: '#4c1d95', color: '#a78bfa' }
                      : user.role === 'provider'
                      ? { backgroundColor: '#1a2a4a', borderColor: '#2d4a8a', color: '#60a5fa' }
                      : { backgroundColor: '#1a3a1a', borderColor: '#2d5a2d', color: '#4ade80' })
                  }}>
                    {user.role || 'user'}
                  </span>
                  <button onClick={() => setEditingUser(user)} style={{ padding: '5px 12px', borderRadius: '6px', border: '1px solid #2a2a2a', background: 'transparent', color: '#aaa', cursor: 'pointer', fontSize: '12px' }}>
                    Edit
                  </button>
                </>)}
              />
            </>
          )}

        </div>
      </div>

      {/* ── EDIT USER MODAL ── */}
      {editingUser && (
        <EditUserModal
          user={editingUser}
          onClose={() => setEditingUser(null)}
          onSaved={handleUserSaved}
        />
      )}
    </div>
  );
}