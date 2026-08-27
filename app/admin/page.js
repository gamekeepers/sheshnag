'use client';

import { useState, useEffect, useCallback } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import SheshnagLogo from '../components/SheshnagLogo';
import PortalSwitch from '../components/PortalSwitch';
import DocsLink from '../components/DocsLink';
import '../dashboard/dashboard.css';

const BACKEND = process.env.NEXT_PUBLIC_BACKEND_URL || 'http://localhost:8000';

function authHeaders() {
  const token = typeof window !== 'undefined' ? localStorage.getItem('mk_token') : '';
  return {
    'Authorization': `Bearer ${token}`,
    ...(process.env.NEXT_PUBLIC_NGROK_ENABLED === 'true' && { 'ngrok-skip-browser-warning': 'true' }),
    'Content-Type': 'application/json',
  };
}

/* ── Nav ── */
const NAV_ITEMS = [
  { id: 'overview',  label: 'Overview',  icon: '⬛' },
  { id: 'jobs',      label: 'Jobs',      icon: '📋' },
  { id: 'users',     label: 'Users',     icon: '👥' },
  { id: 'workers',   label: 'Workers',   icon: '⚡' },
  { id: 'domains',   label: 'Domains',   icon: '🔒' },
];

/* Status pill.
   The whole lifecycle is styled by .badge.<status> in dashboard.css, the same
   sheet the user and provider portals use, so this no longer carries a palette
   of its own. Anything unrecognised falls back to the bare .badge. */
function StatusPill({ status }) {
  return (
    <span className={`badge ${status || ''}`}>
      {(status || 'unknown').replace(/_/g, ' ')}
    </span>
  );
}

/* ── Check if user is superadmin ── */
function isSuperadmin(user) {
  return user?.platform_role === 'superadmin';
}

/* ── Get display role ── */
function getDisplayRole(user) {
  return user?.platform_role || 'user';
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

/* Generic table shell.
   Used to be a CSS-grid of divs with a `cols` prop and its own borders — a
   third table implementation, after the semantic <table> the other two portals
   share. It now emits exactly the markup they do (.table-container > table,
   .empty-hint for the empty row) so all three are styled by one set of rules.
   Kept as a component only because admin draws five of these; the DOM it
   produces is the same one dashboard and provider write by hand. */
function AdminTable({ headers, rows, renderRow, emptyMsg = 'No data' }) {
  return (
    <div className="table-container">
      <table>
        <thead>
          <tr>{headers.map(h => <th key={h}>{h}</th>)}</tr>
        </thead>
        <tbody>
          {rows.map((row, i) => <tr key={row.id || i}>{renderRow(row)}</tr>)}
          {rows.length === 0 && (
            <tr><td colSpan={headers.length} className="empty-hint">{emptyMsg}</td></tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

/* ── Edit User Modal ── */
function EditUserModal({ user, onClose, onSaved }) {
  const [fullName, setFullName] = useState(user.full_name || '');
  const [platformRole, setPlatformRole] = useState(user.platform_role || 'user');
  const [saving, setSaving]     = useState(false);
  const [err, setErr]           = useState('');

  async function handleSave() {
    setSaving(true); setErr('');
    try {
      const res = await fetch(`${BACKEND}/v1/users/${user.id}`, {
        method: 'PUT',
        headers: authHeaders(),
        body: JSON.stringify({ full_name: fullName, platform_role: platformRole }),
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

  return (
    <div className="modal-overlay open">
      <div className="modal">
        <h3>Edit user</h3>
        <p className="modal-sub">{user.email}</p>

        <div className="field">
          <label>Email (read-only)</label>
          <input value={user.email || ''} disabled />
        </div>

        <div className="field">
          <label>Full name</label>
          <input value={fullName} onChange={e => setFullName(e.target.value)} />
        </div>

        <div className="field">
          <label>Role</label>
          <select value={platformRole} onChange={e => setPlatformRole(e.target.value)}>
            <option value="user">User</option>
            <option value="superadmin">Superadmin</option>
          </select>
        </div>

        {err && <p className="empty-hint" style={{ color: 'var(--danger)' }}>{err}</p>}

        <div className="modal-actions">
          <button className="btn" onClick={onClose}>Cancel</button>
          <button className="btn primary" onClick={handleSave} disabled={saving}>
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
  const [workers,       setWorkers]       = useState([]);
  const [jobFilter,     setJobFilter]     = useState('all');
  const [backendStatus, setBackendStatus] = useState('checking');
  const [isLoading,     setIsLoading]     = useState(true);
  const [lastRefresh,   setLastRefresh]   = useState(null);
  const [adminUser,     setAdminUser]     = useState({ name: 'Admin', platform_role: 'superadmin' });
  const [editingUser,   setEditingUser]   = useState(null);
  const [domains,       setDomains]       = useState([]);
  const [domainForm,    setDomainForm]    = useState({ domain: '', include_subdomains: false, note: '' });
  const [domainErr,     setDomainErr]     = useState('');
  const [domainBusy,    setDomainBusy]    = useState(false);

  /* ── load admin profile ── */
  async function loadAdminProfile() {
    try {
      const res = await fetch(`${BACKEND}/v1/auth/me`, { headers: authHeaders() });
      if (res.ok) {
        const d = await res.json();
        setAdminUser({ name: d.full_name || d.email || 'Admin', platform_role: d.platform_role || 'superadmin' });
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
      const res = await fetch(`${BACKEND}/v1/admin/users`, { headers: authHeaders() });
      if (res.ok) {
        const d = await res.json();
        setUsers(Array.isArray(d) ? d : (d.data || d.users || []));
      }
    } catch {}
  }, []);

  /* ── load all workers ── */
  const loadWorkers = useCallback(async () => {
    try {
      const res = await fetch(`${BACKEND}/v1/admin/workers`, { headers: authHeaders() });
      if (res.ok) {
        const d = await res.json();
        setWorkers(Array.isArray(d) ? d : (d.data || []));
      }
    } catch {}
  }, []);

  /* ── load allowed signup domains ── */
  const loadDomains = useCallback(async () => {
    try {
      const res = await fetch(`${BACKEND}/v1/admin/allowed-domains`, { headers: authHeaders() });
      if (res.ok) {
        const d = await res.json();
        setDomains(d.data || []);
      }
    } catch {}
  }, []);

  async function handleAddDomain(e) {
    e.preventDefault();
    setDomainErr('');
    setDomainBusy(true);
    try {
      const res = await fetch(`${BACKEND}/v1/admin/allowed-domains`, {
        method: 'POST',
        headers: authHeaders(),
        body: JSON.stringify({
          domain: domainForm.domain,
          include_subdomains: domainForm.include_subdomains,
          note: domainForm.note || null,
        }),
      });
      if (!res.ok) {
        const d = await res.json().catch(() => ({}));
        throw new Error(d.detail || `HTTP ${res.status}`);
      }
      setDomainForm({ domain: '', include_subdomains: false, note: '' });
      await loadDomains();
    } catch (err) {
      setDomainErr(err.message);
    } finally {
      setDomainBusy(false);
    }
  }

  async function handleRemoveDomain(id, domain) {
    // Removing the last entry reopens signup to everyone — make that explicit
    // rather than letting an admin discover it from the empty state.
    const last = domains.length === 1;
    const warning = last
      ? `Remove "${domain}"?\n\nThis is the last allowed domain. Sign-ups will reopen to ANY email address.`
      : `Remove "${domain}"?\n\nExisting accounts are unaffected — this only stops new sign-ups from that domain.`;
    if (!window.confirm(warning)) return;
    setDomainErr('');
    try {
      const res = await fetch(`${BACKEND}/v1/admin/allowed-domains/${id}`, {
        method: 'DELETE', headers: authHeaders(),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      await loadDomains();
    } catch (err) {
      setDomainErr(err.message);
    }
  }

  useEffect(() => {
    const token = localStorage.getItem('mk_token');
    const user  = JSON.parse(localStorage.getItem('mk_user') || '{}');
    if (!token || !isSuperadmin(user)) { router.push('/login'); return; }
    loadAdminProfile();
    loadJobs();
    loadUsers();
    loadWorkers();
    loadDomains();
  }, [router, loadJobs, loadUsers, loadWorkers, loadDomains]);

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

  const pageTitle = NAV_ITEMS.find(n => n.id === activeNav)?.label;

  return (
    <div className="app-layout">

      {/* ── SIDEBAR ── */}
      <aside className="sidebar">
        <div className="logo">
          <Link href="/"><SheshnagLogo /></Link>
        </div>

        <div className="sidebar-eyebrow">Admin panel</div>

        <nav className="nav">
          {NAV_ITEMS.map(item => (
            <div
              key={item.id}
              className={`nav-item ${activeNav === item.id ? 'active' : ''}`}
              onClick={() => setActiveNav(item.id)}
            >
              <span className="ic">{item.icon}</span> {item.label}
            </div>
          ))}
        </nav>

        <div className="sidebar-bottom">
          <PortalSwitch to="user" />
          <DocsLink page="self-host/" />
          <div className="profile-card">
            <div className="avatar"></div>
            <div className="profile-meta">
              <div className="profile-name">{adminUser.name}</div>
              <div className="profile-email" style={{ textTransform: 'capitalize' }}>{adminUser.platform_role}</div>
            </div>
            <button className="signout" onClick={handleLogout}>Sign out</button>
          </div>
        </div>
      </aside>

      {/* ── MAIN ── */}
      <div className="main-content">

        <div className="header">
          <div className="breadcrumbs">
            Admin / <span className="current">{pageTitle}</span>
          </div>
          <div className="header-right">
            {lastRefresh && <span className="page-sub" style={{ margin: 0 }}>Updated {lastRefresh}</span>}
            <button className="btn" onClick={() => { loadJobs(); loadUsers(); }} disabled={isLoading}>
              {isLoading ? '↻ Loading…' : '↻ Refresh'}
            </button>
            <span className={`badge ${backendStatus === 'live' ? 'online' : backendStatus === 'offline' ? 'failed' : 'busy'}`}>
              <span className="pip"></span>
              {backendStatus === 'live' ? 'Backend live' : backendStatus === 'offline' ? 'Backend offline' : 'Connecting…'}
            </span>
          </div>
        </div>

        <div className="content-body">

          {/* ── OVERVIEW ── */}
          {activeNav === 'overview' && (
            <div className="page-panel active">
              <h1 className="page-title">Overview</h1>
              <p className="page-sub">Everything on this deployment, across all organizations.</p>

              <div className="grid-4">
                <div className="panel stat-card">
                  <div className="stat-label">Total jobs</div>
                  <div className="stat-value">{totalJobs}</div>
                </div>
                <div className="panel stat-card">
                  <div className="stat-label">Active</div>
                  <div className="stat-value">{activeJobs}</div>
                </div>
                <div className="panel stat-card">
                  <div className="stat-label">Completed</div>
                  <div className="stat-value">{completedJobs}</div>
                </div>
                <div className="panel stat-card">
                  <div className="stat-label">Failed</div>
                  <div className="stat-value">{failedJobs}</div>
                </div>
              </div>

              <div className="grid-2">
                <div className="panel stat-card">
                  <div className="stat-label">Total users</div>
                  <div className="stat-value">{users.length}</div>
                </div>
                <div className="panel stat-card">
                  <div className="stat-label">Superadmins</div>
                  <div className="stat-value">{users.filter(isSuperadmin).length}</div>
                </div>
              </div>

              <div className="section-title">Recent jobs</div>
              <div className="panel">
                <AdminTable
                  headers={['Job ID', 'User', 'Prompts', 'Status', 'Started']}
                  rows={jobs.slice(0, 8)}
                  emptyMsg={isLoading ? 'Loading…' : 'No jobs yet'}
                  renderRow={job => (<>
                    <td className="mono">{job.id}</td>
                    <td className="dim">{job.user}</td>
                    <td>{Number(job.prompts).toLocaleString()}</td>
                    <td><StatusPill status={job.status} /></td>
                    <td className="dim">{job.started}</td>
                  </>)}
                />
              </div>
            </div>
          )}

          {/* ── JOBS ── */}
          {activeNav === 'jobs' && (
            <div className="page-panel active">
              <h1 className="page-title">Jobs</h1>
              <p className="page-sub">
                {filteredJobs.length} job{filteredJobs.length !== 1 ? 's' : ''}
                {jobFilter !== 'all' && <> with status <span className="mono">{jobFilter.replace(/_/g, ' ')}</span></>}
              </p>

              <div className="filter-row">
                {['all', 'validating', 'running', 'in_progress', 'completed', 'failed', 'cancelled', 'expired'].map(f => (
                  <button
                    key={f}
                    className={`btn ${jobFilter === f ? 'primary' : ''}`}
                    onClick={() => setJobFilter(f)}
                  >
                    {f.replace(/_/g, ' ')}
                  </button>
                ))}
              </div>

              <div className="panel">
                <AdminTable
                  headers={['Job ID', 'User', 'Prompts', 'Status', 'Provider', 'Started']}
                  rows={filteredJobs}
                  emptyMsg={isLoading ? 'Loading…' : 'No jobs found'}
                  renderRow={job => (<>
                    <td className="mono">{job.id}</td>
                    <td className="dim">{job.user}</td>
                    <td>{Number(job.prompts).toLocaleString()}</td>
                    <td><StatusPill status={job.status} /></td>
                    <td className="dim">{job.provider}</td>
                    <td className="dim">{job.started}</td>
                  </>)}
                />
              </div>
            </div>
          )}

          {/* ── USERS ── */}
          {activeNav === 'users' && (
            <div className="page-panel active">
              <h1 className="page-title">Users</h1>
              <p className="page-sub">{users.length} account{users.length === 1 ? '' : 's'} on this deployment.</p>

              <div className="panel">
                <AdminTable
                  headers={['Name', 'Email', 'Role', 'Actions']}
                  rows={users}
                  emptyMsg="No users found"
                  renderRow={user => (<>
                    <td>{user.full_name || '—'}</td>
                    <td className="dim">{user.email}</td>
                    <td>
                      <span className={`badge ${isSuperadmin(user) ? 'superadmin' : 'user'}`}>
                        {getDisplayRole(user)}
                      </span>
                    </td>
                    <td>
                      <button className="btn" onClick={() => setEditingUser(user)}>Edit</button>
                    </td>
                  </>)}
                />
              </div>
            </div>
          )}

          {/* ── WORKERS ── */}
          {activeNav === 'workers' && (
            <div className="page-panel active">
              <h1 className="page-title">Workers</h1>
              <p className="page-sub">{workers.length} worker{workers.length === 1 ? '' : 's'} registered across all organizations.</p>

              <div className="panel">
                <AdminTable
                  headers={['Worker ID', 'Org ID', 'GPUs', 'VRAM total', 'Loaded models', 'Status', 'Last heartbeat']}
                  rows={workers}
                  emptyMsg="No workers found"
                  renderRow={p => (<>
                    <td className="mono">{p.worker_id || p.id || '—'}</td>
                    <td className="dim">{p.org_id || '—'}</td>
                    <td className="dim">{(p.gpus || []).map(g => g.name).join(', ') || '—'}</td>
                    <td>{(p.gpus || []).reduce((acc, g) => acc + (g.vram_gb || 0), 0)} GB</td>
                    <td className="dim">{(p.runtimes || []).flatMap(r => r.models || []).join(', ') || '—'}</td>
                    <td>
                      <span className={`badge ${p.status === 'online' ? 'online' : p.status === 'busy' ? 'busy' : 'offline'}`}>
                        <span className="pip"></span>{p.status || 'unknown'}
                      </span>
                    </td>
                    <td className="dim">
                      {p.last_heartbeat ? new Date(p.last_heartbeat * 1000).toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' }) : '—'}
                    </td>
                  </>)}
                />
              </div>
            </div>
          )}

          {/* ── DOMAINS ── */}
          {activeNav === 'domains' && (
            <div className="page-panel active">
              <h1 className="page-title">Domains</h1>
              <p className="page-sub">Which email domains may create an account without an invitation.</p>

              {domains.length === 0 ? (
                <div className="panel warn">
                  <strong>No restrictions — anyone with any email address can sign up.</strong>
                  <p className="page-sub" style={{ margin: '0.4rem 0 0' }}>
                    Add a domain below to restrict sign-ups to your institution.
                  </p>
                </div>
              ) : (
                <div className="panel ok">
                  <strong>
                    Sign-ups are restricted to {domains.length} approved domain{domains.length === 1 ? '' : 's'}.
                  </strong>
                  <p className="page-sub" style={{ margin: '0.4rem 0 0' }}>
                    Invited users and accounts created by an admin bypass this list by design.
                  </p>
                </div>
              )}

              <form onSubmit={handleAddDomain} className="panel form-row">
                <div className="field">
                  <label>Domain</label>
                  <input
                    value={domainForm.domain}
                    onChange={e => setDomainForm({ ...domainForm, domain: e.target.value })}
                    placeholder="dau.ac.in"
                    required
                  />
                </div>
                <div className="field">
                  <label>Note (optional)</label>
                  <input
                    value={domainForm.note}
                    onChange={e => setDomainForm({ ...domainForm, note: e.target.value })}
                    placeholder="e.g. Students"
                  />
                </div>
                <label className="check">
                  <input
                    type="checkbox"
                    checked={domainForm.include_subdomains}
                    onChange={e => setDomainForm({ ...domainForm, include_subdomains: e.target.checked })}
                  />
                  Include subdomains
                </label>
                <button type="submit" className="btn primary" disabled={domainBusy}>
                  {domainBusy ? 'Adding…' : 'Add domain'}
                </button>
              </form>

              {domainErr && <div className="panel alert">{domainErr}</div>}

              <div className="panel">
                <AdminTable
                  headers={['Domain', 'Subdomains', 'Note', 'Actions']}
                  rows={domains}
                  emptyMsg="No domains — sign-up is open to everyone"
                  renderRow={d => (<>
                    <td>{d.domain}</td>
                    <td className="dim">{d.include_subdomains ? 'Included' : 'Exact only'}</td>
                    <td className="dim">{d.note || '—'}</td>
                    <td>
                      <button className="btn danger" onClick={() => handleRemoveDomain(d.id, d.domain)}>
                        Remove
                      </button>
                    </td>
                  </>)}
                />
              </div>

              <p className="page-sub">
                This list governs <strong>self-service sign-up only</strong>. Removing a domain does not
                disable existing accounts, and does not affect users added by invitation.
              </p>
            </div>
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
