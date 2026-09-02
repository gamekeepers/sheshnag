'use client';

import { useState, useEffect, useCallback } from 'react';
import { useRouter } from 'next/navigation';
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer } from 'recharts';
import PortalSwitch from '../components/PortalSwitch';
import DocsLink, { DocsAnchor } from '../components/DocsLink';
import SheshnagLogo from '../components/SheshnagLogo';
import { buildStackedSeries, trimLeadingEmpty } from '../lib/usageSeries';
import '../dashboard/dashboard.css';

const BACKEND = process.env.NEXT_PUBLIC_BACKEND_URL || 'http://localhost:8000';

const CHART = { surface: '#111115', axis: '#5E5E5A' };

// Enough hues to stack a small fleet. Above SERIES_MAX the stack is unreadable
// and we fall back to one merged bar, so the palette never has to wrap.
const SERIES_COLORS = ['#8B7BF5', '#4ADE80', '#C9722E', '#38BDF8', '#F472B6', '#FBBF24'];
const SERIES_MAX = SERIES_COLORS.length;

const compactTokens = (n) => {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return `${n}`;
};

// `counted_jobs` is how many of a bucket's jobs had their rollup ingested.
// Zero of them means *not counted*, which is a different claim from "produced
// no tokens" — a fleet that predates §16 would otherwise read as idle. A
// partially counted bucket gets a "+" so the number is not mistaken for the
// whole truth, matching how the user dashboard marks a partial day.
function tokenCell(bucket) {
  if (!bucket || !bucket.counted_jobs) {
    return <span className="dim" title="No token counts recorded for these jobs">—</span>;
  }
  const missing = (bucket.jobs || 0) - bucket.counted_jobs;
  return (
    <>
      {(bucket.total_tokens || 0).toLocaleString()}
      {missing > 0 && (
        <span className="dim" title={`${missing} job${missing === 1 ? '' : 's'} not counted`}> +</span>
      )}
    </>
  );
}

function heartbeatAge(ts) {
  if (!ts) return 'never';
  const s = Math.max(0, Math.floor(Date.now() / 1000) - ts);
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

function statusPip(worker) {
  const color = worker.status === 'online'
    ? '#00D287'
    : worker.status === 'draining'
      ? '#EF9F27'
      : '#F85149';
  return <span style={{ width: 8, height: 8, borderRadius: '50%', background: color, display: 'inline-block', flexShrink: 0 }} />;
}

export default function ProviderPage() {
  const router = useRouter();
  const [activeTab, setActiveTab] = useState('overview');
  const [token, setToken] = useState('');
  const [isOrgDropdownOpen, setIsOrgDropdownOpen] = useState(false);

  const [orgs, setOrgs] = useState([]);
  const [selectedOrg, setSelectedOrg] = useState(null);

  const [workers, setWorkers] = useState([]);
  const [served, setServed] = useState([]);
  // The Contribution tab windows its own copy of the served jobs. Kept separate
  // from `served` because the Jobs tab wants the full recent history, and
  // narrowing that to the chart's window would silently shorten its table.
  const [contribJobs, setContribJobs] = useState([]);
  const [contribTruncated, setContribTruncated] = useState(false);
  const [contribRange, setContribRange] = useState(7);
  const [contribWorker, setContribWorker] = useState('');
  const [contribModel, setContribModel] = useState('');
  const [stats, setStats] = useState(null);
  const [orgKeys, setOrgKeys] = useState([]);
  const [orgMembers, setOrgMembers] = useState([]);
  const [orgInvites, setOrgInvites] = useState([]);

  // Organization tab forms
  const [orgName, setOrgName] = useState('');
  const [orgStatus, setOrgStatus] = useState('');
  const [inviteEmail, setInviteEmail] = useState('');
  const [inviteRole, setInviteRole] = useState('viewer');

  // Worker keys tab
  const [isNewKeyModalOpen, setIsNewKeyModalOpen] = useState(false);
  const [newKeyName, setNewKeyName] = useState('');
  const [revealedKey, setRevealedKey] = useState('');

  // Create organization
  const [isCreateOrgModalOpen, setIsCreateOrgModalOpen] = useState(false);
  const [newOrgName, setNewOrgName] = useState('');

  // Jobs filters
  const [jobsWorkerFilter, setJobsWorkerFilter] = useState('');
  const [jobsStatusFilter, setJobsStatusFilter] = useState('');

  const role = selectedOrg?.role || 'viewer';
  const canManage = role === 'owner' || role === 'admin';

  useEffect(() => {
    const tk = localStorage.getItem('mk_token');
    if (!tk) {
      router.push('/login');
      return;
    }
    setToken(tk);
  }, [router]);

  const getHeaders = useCallback(() => {
    const h = {
      'Authorization': `Bearer ${localStorage.getItem('mk_token')}`,
      'Content-Type': 'application/json',
    };
    if (process.env.NEXT_PUBLIC_NGROK_ENABLED === 'true') h['ngrok-skip-browser-warning'] = 'true';
    return h;
  }, []);

  // ── Loaders (all org-scoped, /v1/orgs — the single source of org data) ──

  const loadOrgs = useCallback(async () => {
    try {
      const res = await fetch(`${BACKEND}/v1/orgs`, { headers: getHeaders() });
      if (!res.ok) return;
      const data = await res.json();
      const list = data.data || [];
      setOrgs(list);
      if (list.length > 0) {
        const savedId = localStorage.getItem('mk_active_org_id');
        const matched = list.find(o => o.id === savedId) || list[0];
        setSelectedOrg(matched);
        setOrgName(matched.name);
        localStorage.setItem('mk_active_org_id', matched.id);
      }
    } catch (e) {
      console.error('Failed to load orgs:', e);
    }
  }, [getHeaders]);

  const loadWorkers = useCallback(async () => {
    if (!selectedOrg) return;
    try {
      const res = await fetch(`${BACKEND}/v1/orgs/${selectedOrg.id}/workers`, { headers: getHeaders() });
      if (res.ok) setWorkers((await res.json()).data || []);
    } catch (e) { console.error('Failed to load workers:', e); }
  }, [selectedOrg, getHeaders]);

  const loadServed = useCallback(async () => {
    if (!selectedOrg) return;
    try {
      const res = await fetch(`${BACKEND}/v1/orgs/${selectedOrg.id}/batches?limit=200`, { headers: getHeaders() });
      if (res.ok) setServed((await res.json()).data || []);
    } catch (e) { console.error('Failed to load served jobs:', e); }
  }, [selectedOrg, getHeaders]);

  const loadStats = useCallback(async () => {
    if (!selectedOrg) return;
    try {
      const res = await fetch(`${BACKEND}/v1/orgs/${selectedOrg.id}/stats?days=${contribRange}`, { headers: getHeaders() });
      if (res.ok) setStats(await res.json());
    } catch (e) { console.error('Failed to load stats:', e); }
  }, [selectedOrg, contribRange, getHeaders]);

  // Bounded by time, not row count. Newest-first with a bare limit drops the
  // *oldest* rows, so a busy org's chart would lose its early days and render
  // them as zero — a shorter window wearing a longer label.
  const loadContribJobs = useCallback(async () => {
    if (!selectedOrg) return;
    const since = Math.floor(Date.now() / 1000) - contribRange * 86400;
    try {
      const res = await fetch(
        `${BACKEND}/v1/orgs/${selectedOrg.id}/batches?since=${since}&limit=500`,
        { headers: getHeaders() },
      );
      if (res.ok) {
        const body = await res.json();
        setContribJobs(body.data || []);
        setContribTruncated(Boolean(body.truncated));
      }
    } catch (e) { console.error('Failed to load contribution jobs:', e); }
  }, [selectedOrg, contribRange, getHeaders]);

  const loadKeys = useCallback(async () => {
    if (!selectedOrg) return;
    try {
      const res = await fetch(`${BACKEND}/v1/orgs/${selectedOrg.id}/api-keys`, { headers: getHeaders() });
      if (res.ok) setOrgKeys((await res.json()).data || []);
    } catch (e) { console.error('Failed to load keys:', e); }
  }, [selectedOrg, getHeaders]);

  const loadMembers = useCallback(async () => {
    if (!selectedOrg) return;
    try {
      const res = await fetch(`${BACKEND}/v1/orgs/${selectedOrg.id}/members`, { headers: getHeaders() });
      if (res.ok) setOrgMembers((await res.json()).data || []);
      if (canManage) {
        const inv = await fetch(`${BACKEND}/v1/orgs/${selectedOrg.id}/members/invites`, { headers: getHeaders() });
        if (inv.ok) setOrgInvites((await inv.json()).data || []);
      }
    } catch (e) { console.error('Failed to load members:', e); }
  }, [selectedOrg, canManage, getHeaders]);

  useEffect(() => {
    if (token) loadOrgs();
  }, [token, loadOrgs]);

  // Org context changed → stale per-org data must never leak across orgs.
  // (Defined before the loader effect below so the clear runs first.)
  useEffect(() => {
    setWorkers([]); setServed([]); setStats(null);
    setOrgKeys([]); setOrgMembers([]); setOrgInvites([]);
  }, [selectedOrg?.id]);

  useEffect(() => {
    if (!selectedOrg) return;
    if (activeTab === 'overview') {
      loadWorkers(); loadServed(); loadStats();
    } else if (activeTab === 'workers') {
      loadWorkers();
    } else if (activeTab === 'jobs') {
      loadServed(); loadWorkers();
    } else if (activeTab === 'models') {
      loadWorkers();
    } else if (activeTab === 'contribution') {
      loadWorkers(); loadStats(); loadContribJobs();
    } else if (activeTab === 'keys') {
      loadKeys();
    } else if (activeTab === 'organization') {
      loadMembers();
    }
  }, [activeTab, selectedOrg, loadWorkers, loadServed, loadStats, loadContribJobs, loadMembers, loadKeys]);

  // Heartbeat freshness matters — refresh the fleet while watching it.
  useEffect(() => {
    if (!selectedOrg || (activeTab !== 'overview' && activeTab !== 'workers')) return;
    const interval = setInterval(loadWorkers, 15000);
    return () => clearInterval(interval);
  }, [selectedOrg, activeTab, loadWorkers]);

  // ── Actions ─────────────────────────────────────────────

  const handleSelectOrg = (org) => {
    setSelectedOrg(org);
    setOrgName(org.name);
    localStorage.setItem('mk_active_org_id', org.id);
    setIsOrgDropdownOpen(false);
  };

  const handleDrain = async (worker) => {
    const action = worker.status === 'draining' ? 'undrain' : 'drain';
    if (action === 'drain' && !confirm(`Drain ${worker.hostname}? It finishes its current batch and takes no new jobs.`)) return;
    try {
      const res = await fetch(`${BACKEND}/v1/orgs/${selectedOrg.id}/workers/${worker.id}/${action}`, {
        method: 'POST', headers: getHeaders(),
      });
      if (res.ok) loadWorkers();
      else alert((await res.json()).detail || `Failed to ${action}`);
    } catch (e) { console.error(e); }
  };

  const handleRemoveWorker = async (worker) => {
    if (!confirm(`Remove ${worker.hostname} and its inventory? Job history is kept.`)) return;
    try {
      const res = await fetch(`${BACKEND}/v1/orgs/${selectedOrg.id}/workers/${worker.id}`, {
        method: 'DELETE', headers: getHeaders(),
      });
      if (res.ok) loadWorkers();
      else alert((await res.json()).detail || 'Failed to remove worker');
    } catch (e) { console.error(e); }
  };

  const handleRenameOrg = async () => {
    if (!orgName.trim() || !selectedOrg) return;
    setOrgStatus('Saving…');
    try {
      const res = await fetch(`${BACKEND}/v1/orgs/${selectedOrg.id}`, {
        method: 'PUT', headers: getHeaders(), body: JSON.stringify({ name: orgName }),
      });
      if (res.ok) {
        setOrgStatus('Saved');
        loadOrgs();
      } else {
        setOrgStatus((await res.json()).detail || 'Failed to save');
      }
    } catch { setOrgStatus('Server unreachable'); }
    setTimeout(() => setOrgStatus(''), 2500);
  };

  const handleInvite = async () => {
    if (!inviteEmail) return;
    try {
      const res = await fetch(`${BACKEND}/v1/orgs/${selectedOrg.id}/members/invite`, {
        method: 'POST', headers: getHeaders(),
        body: JSON.stringify({ email: inviteEmail, role: inviteRole }),
      });
      if (res.ok) { setInviteEmail(''); loadMembers(); }
      else alert((await res.json()).detail || 'Failed to invite');
    } catch (e) { console.error(e); }
  };

  const handleRevokeInvite = async (tok) => {
    if (!confirm('Revoke this invite?')) return;
    try {
      const res = await fetch(`${BACKEND}/v1/orgs/${selectedOrg.id}/members/invites/${tok}`, {
        method: 'DELETE', headers: getHeaders(),
      });
      if (res.ok) loadMembers();
    } catch (e) { console.error(e); }
  };

  const handleUpdateRole = async (member, newRole) => {
    if (newRole === member.role) return;
    // Parked finding #3: promoting to owner is consequential — confirm it.
    if (newRole === 'owner' && !confirm(`Make ${member.full_name || member.email} an OWNER of ${selectedOrg.name}? Owners have full control, including over your own membership.`)) {
      loadMembers();
      return;
    }
    try {
      const res = await fetch(`${BACKEND}/v1/orgs/${selectedOrg.id}/members/${member.user_id}`, {
        method: 'PUT', headers: getHeaders(), body: JSON.stringify({ role: newRole }),
      });
      if (res.ok) loadMembers();
      else { alert((await res.json()).detail || 'Failed to update role'); loadMembers(); }
    } catch (e) { console.error(e); }
  };

  const handleRemoveMember = async (member) => {
    if (!confirm(`Remove ${member.full_name || member.email} from ${selectedOrg.name}?`)) return;
    try {
      const res = await fetch(`${BACKEND}/v1/orgs/${selectedOrg.id}/members/${member.user_id}`, {
        method: 'DELETE', headers: getHeaders(),
      });
      if (res.ok) loadMembers();
      else alert((await res.json()).detail || 'Failed to remove member');
    } catch (e) { console.error(e); }
  };

  const handleCreateKey = async () => {
    if (!newKeyName.trim()) return;
    try {
      const res = await fetch(`${BACKEND}/v1/orgs/${selectedOrg.id}/api-keys`, {
        method: 'POST', headers: getHeaders(),
        body: JSON.stringify({ name: newKeyName.trim() }),
      });
      if (res.ok) {
        // The backend returns the raw key exactly once — show it now.
        const data = await res.json();
        setRevealedKey(data.api_key || '');
        setNewKeyName('');
        loadKeys();
      } else {
        alert((await res.json()).detail || 'Failed to create key');
      }
    } catch (e) { console.error(e); }
  };

  const handleRevokeKey = async (key) => {
    if (!confirm(`Revoke "${key.name || key.key_prefix}"? Every daemon using it stops registering immediately.`)) return;
    try {
      const res = await fetch(`${BACKEND}/v1/orgs/${selectedOrg.id}/api-keys/${key.id}`, {
        method: 'DELETE', headers: getHeaders(),
      });
      if (res.ok) loadKeys();
      else alert((await res.json()).detail || 'Failed to revoke key');
    } catch (e) { console.error(e); }
  };

  const handleCreateOrg = async () => {
    if (!newOrgName.trim()) return;
    try {
      const res = await fetch(`${BACKEND}/v1/me/organizations`, {
        method: 'POST', headers: getHeaders(),
        body: JSON.stringify({ name: newOrgName.trim() }),
      });
      if (res.ok) {
        const org = await res.json();
        setIsCreateOrgModalOpen(false);
        setNewOrgName('');
        localStorage.setItem('mk_active_org_id', org.id);
        loadOrgs();
      } else {
        alert((await res.json()).detail || 'Failed to create organization');
      }
    } catch (e) { console.error(e); }
  };

  const handleSignOut = () => {
    localStorage.removeItem('mk_token');
    localStorage.removeItem('mk_user');
    localStorage.removeItem('mk_active_org_id');
    router.push('/login');
  };

  // ── Derived data ────────────────────────────────────────

  const onlineWorkers = workers.filter(w => w.status === 'online');
  const drainingWorkers = workers.filter(w => w.status === 'draining');
  const offlineWorkers = workers.filter(w => w.status === 'offline');
  const vramOnline = workers
    .filter(w => w.status !== 'offline')
    .reduce((acc, w) => acc + (w.vram_total_gb || 0), 0);
  const gpuCount = workers.filter(w => w.status !== 'offline').reduce((acc, w) => acc + (w.gpus?.length || 0), 0);

  const fleetSorted = [...workers].sort((a, b) => {
    const rank = s => (s === 'online' ? 0 : s === 'draining' ? 1 : 2);
    return rank(a.status) - rank(b.status);
  });

  const filteredJobs = served.filter(j =>
    (!jobsWorkerFilter || j.worker_id === jobsWorkerFilter) &&
    (!jobsStatusFilter || j.status === jobsStatusFilter)
  );

  // ── Contribution chart ────────────────────────────────────────────────────
  // Two independent filters, so the pair can answer "what did watchtower
  // produce on gemma3:27b" — a single combined selector cannot.
  const contribFiltered = contribJobs.filter(j =>
    (!contribWorker || j.worker_id === contribWorker) &&
    (!contribModel || j.model === contribModel)
  );

  // Stack by whichever dimension is still open, so "all workers" shows the mix
  // rather than one merged bar: a worker going dark on the 3rd is then visible
  // without touching a dropdown, and the dropdown becomes a drill-down.
  const stackByModel = Boolean(contribWorker);
  const contribRows = contribFiltered.map(j => ({
    // The provider's timeline is when its hardware picked the job up, not when
    // the customer submitted it — those can be different days.
    at: j.assigned_at,
    total: j.request_counts?.total || 0,
    done: j.request_counts?.completed || 0,
    failed: j.request_counts?.failed || 0,
    usage: j.usage,
    status: j.status,
    key: (stackByModel ? j.model : j.worker_hostname) || 'unknown',
  }));

  const { keys: seriesKeys, data: contribSeries } =
    buildStackedSeries(contribRows, contribRange, r => r.key);
  const contribChart = trimLeadingEmpty(contribSeries);
  const stacked = seriesKeys.length > 1 && seriesKeys.length <= SERIES_MAX;
  const contribTokens = contribSeries.reduce((acc, d) => acc + d.totalTokens, 0);
  const contribAwaiting = contribSeries.reduce((acc, d) => acc + d.awaitingCount, 0);
  const contribModels = [...new Set(contribJobs.map(j => j.model).filter(Boolean))].sort();

  const hostedModels = (() => {
    const map = {};
    workers.forEach(w => {
      const loaded = new Set(w.loaded_models || []);
      (w.runtimes || []).forEach(rt => (rt.models || []).forEach(m => {
        if (!map[m]) map[m] = { available: [], loaded: [] };
        map[m].available.push(w.hostname);
        if (loaded.has(m)) map[m].loaded.push(w.hostname);
      }));
    });
    return Object.entries(map).map(([model, v]) => ({ model, ...v }));
  })();

  const badgeFor = (status) => (
    <span className={`badge ${status}`} style={status === 'draining' ? { color: '#EF9F27' } : undefined}>
      <span className="pip"></span>{status}
    </span>
  );

  const getPageTitle = () => ({
    overview: 'Overview', workers: 'Workers', jobs: 'Jobs served',
    models: 'Hosted models', contribution: 'Contribution',
    keys: 'Worker keys', organization: 'Organization',
  }[activeTab] || 'Provider');

  // ── Render ──────────────────────────────────────────────

  return (
    <div className="app-layout">
      <aside className="sidebar">
        <div className="logo">
          <SheshnagLogo />
        </div>

        <div className="sidebar-eyebrow">Provider portal</div>

        <div className={`org-switcher ${isOrgDropdownOpen ? 'open' : ''}`}>
          <button onClick={() => setIsOrgDropdownOpen(!isOrgDropdownOpen)}>
            <span>{selectedOrg ? selectedOrg.name : 'Select org'}</span>
            <span className="chev">▾</span>
          </button>
          {selectedOrg && (
            <div style={{ padding: '2px 1rem 6px', display: 'flex', gap: 8, alignItems: 'center' }}>
              <span style={{ fontSize: 10, padding: '1px 8px', borderRadius: 8, background: 'rgba(201,196,255,0.15)', color: '#C9C4FF' }}>{role}</span>
              <span style={{ fontSize: 10, color: 'rgba(255,255,255,0.35)' }}>{workers.length} worker{workers.length === 1 ? '' : 's'}</span>
            </div>
          )}
          <div className="org-menu">
            {orgs.map(org => (
              <button
                key={org.id}
                className={selectedOrg && selectedOrg.id === org.id ? 'active' : ''}
                onClick={() => handleSelectOrg(org)}
              >
                {org.name} <span style={{ opacity: 0.5, fontSize: 11 }}>({org.role})</span>
              </button>
            ))}
            <button onClick={() => { setIsOrgDropdownOpen(false); setIsCreateOrgModalOpen(true); }} style={{ color: '#C9C4FF' }}>
              + New organization
            </button>
          </div>
        </div>

        <nav className="nav">
          <div className={`nav-item ${activeTab === 'overview' ? 'active' : ''}`} onClick={() => setActiveTab('overview')}>
            <span className="ic">📊</span> Overview
          </div>
          <div className={`nav-item ${activeTab === 'workers' ? 'active' : ''}`} onClick={() => setActiveTab('workers')}>
            <span className="ic">🖥️</span> Workers
          </div>
          <div className={`nav-item ${activeTab === 'jobs' ? 'active' : ''}`} onClick={() => setActiveTab('jobs')}>
            <span className="ic">📦</span> Jobs served
          </div>
          <div className={`nav-item ${activeTab === 'models' ? 'active' : ''}`} onClick={() => setActiveTab('models')}>
            <span className="ic">🧠</span> Hosted models
          </div>
          <div className={`nav-item ${activeTab === 'contribution' ? 'active' : ''}`} onClick={() => setActiveTab('contribution')}>
            <span className="ic">📈</span> Contribution
          </div>
          <div className={`nav-item ${activeTab === 'keys' ? 'active' : ''}`} onClick={() => setActiveTab('keys')}>
            <span className="ic">🔑</span> Worker keys
          </div>
          <div className={`nav-item ${activeTab === 'organization' ? 'active' : ''}`} onClick={() => setActiveTab('organization')}>
            <span className="ic">👥</span> Organization
          </div>
        </nav>

        <div className="sidebar-bottom">
          <PortalSwitch to="user" />
          <DocsLink page="provider/" />
          <button className="btn" style={{ width: '100%' }} onClick={handleSignOut}>Sign out</button>
        </div>
      </aside>

      <div className="main-content">
        <div className="header">
          <div className="breadcrumbs">
            {selectedOrg ? selectedOrg.name : 'Provider'} / <span className="current">{getPageTitle()}</span>
          </div>
        </div>

        <div className="content-body">
          {!selectedOrg && (
            <div className="panel" style={{ textAlign: 'center', color: 'var(--dim)', padding: '3rem' }}>
              <p style={{ marginBottom: '1rem' }}>No organizations yet.</p>
              <button className="btn primary" onClick={() => setIsCreateOrgModalOpen(true)}>+ Create organization</button>
            </div>
          )}

          {/* ============ OVERVIEW ============ */}
          {selectedOrg && (
          <div className={`page-panel ${activeTab === 'overview' ? 'active' : ''}`}>
            <h1 className="page-title">Overview</h1>
            <p className="page-sub">{selectedOrg.name} — everything below is this organization only.</p>

            <div className="grid-3">
              <div className="panel stat-card">
                <div className="stat-label">Workers online</div>
                <div className="stat-value">{onlineWorkers.length} <span className="unit">/ {workers.length}</span></div>
                <div className="stat-sub">{onlineWorkers.filter(w => w.activity === 'busy').length} busy · {drainingWorkers.length} draining · {offlineWorkers.length} offline</div>
              </div>
              <div className="panel stat-card">
                <div className="stat-label">VRAM online</div>
                <div className="stat-value">{Math.round(vramOnline)} <span className="unit">GB</span></div>
                <div className="stat-sub">{gpuCount} GPU{gpuCount === 1 ? '' : 's'}</div>
              </div>
              <div className="panel stat-card">
                <div className="stat-label">Served, last 7 days</div>
                <div className="stat-value">{stats ? stats.totals.jobs : '—'} <span className="unit">jobs</span></div>
                <div className="stat-sub">{stats ? `${stats.totals.requests_completed.toLocaleString()} requests completed` : 'loading…'}</div>
              </div>
            </div>

            {offlineWorkers.length > 0 && (
              <div className="panel" style={{ borderColor: 'rgba(248,81,73,0.4)', marginBottom: '1rem' }}>
                <span style={{ color: '#F85149', fontSize: '0.85rem' }}>
                  {offlineWorkers.length} worker{offlineWorkers.length === 1 ? ' is' : 's are'} offline: {offlineWorkers.map(w => w.hostname).join(', ')}
                </span>
              </div>
            )}

            <div className="section-title">Fleet health</div>
            <div className="panel">
              {fleetSorted.map(w => (
                <div key={w.id} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '6px 0', borderBottom: '1px solid rgba(255,255,255,0.05)' }}>
                  {statusPip(w)}
                  <span className="mono" style={{ fontSize: '0.85rem' }}>{w.hostname}</span>
                  <span style={{ fontSize: '0.78rem', color: 'var(--dim)' }}>
                    {w.status === 'online' ? w.activity : w.status} · {heartbeatAge(w.last_heartbeat)}
                  </span>
                  <span style={{ marginLeft: 'auto', fontSize: '0.78rem', color: 'var(--dim)' }}>
                    {w.vram_available_gb != null ? `${Math.round(w.vram_available_gb)} / ${Math.round(w.vram_total_gb || 0)} GB free` : '—'}
                  </span>
                </div>
              ))}
              {workers.length === 0 && <div className="empty-hint">No workers registered. Install the daemon with this org&apos;s API key.</div>}
            </div>

            <div className="section-title">Recently served</div>
            <div className="panel" style={{ padding: '0.5rem' }}>
              <div className="table-container">
                <table>
                  <thead><tr><th>Batch</th><th>Model</th><th>Status</th><th>Requests</th><th>Worker</th></tr></thead>
                  <tbody>
                    {served.slice(0, 5).map(j => (
                      <tr key={j.id}>
                        <td className="mono">{j.id}</td>
                        <td>{j.model || '—'}</td>
                        <td>{badgeFor(j.status)}</td>
                        <td className="dim">{j.request_counts.completed.toLocaleString()} / {j.request_counts.total.toLocaleString()}</td>
                        <td className="dim">{j.worker_hostname}</td>
                      </tr>
                    ))}
                    {served.length === 0 && <tr><td colSpan={5} className="empty-hint">Nothing served yet.</td></tr>}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
          )}

          {/* ============ WORKERS ============ */}
          {selectedOrg && (
          <div className={`page-panel ${activeTab === 'workers' ? 'active' : ''}`}>
            <h1 className="page-title">Workers</h1>
            <p className="page-sub">Compute nodes registered to {selectedOrg.name}.{!canManage && ' Read-only: you are a viewer in this organization.'}</p>

            <div className="grid-2">
              {workers.map(worker => (
                <div className="panel worker-card" key={worker.id}>
                  <div className="worker-top">
                    <div>
                      <div className="worker-host">{worker.hostname}</div>
                      <div className="worker-os">{worker.os || 'Unknown OS'} · heartbeat {heartbeatAge(worker.last_heartbeat)}</div>
                    </div>
                    {badgeFor(worker.status)}
                  </div>
                  <div className="worker-specs">
                    <div>
                      <b>{worker.cpu_cores || '—'}</b> CPU cores · <b>{worker.ram_total_gb ? worker.ram_total_gb.toFixed(0) : '—'} GB</b> RAM
                      {/* `|| 0` here would render Apple Silicon's unknown (null)
                          free figure as "0/18 GB free" — a worker that is in
                          fact idle, shown as saturated. Unified memory exposes
                          no machine-wide "in use" counter, so say what we know:
                          the ceiling, and that free is unknown. */}
                      {worker.vram_total_gb != null && (
                        worker.vram_available_gb != null
                          ? <> · <b>{Math.round(worker.vram_available_gb)}/{Math.round(worker.vram_total_gb)} GB</b> VRAM free</>
                          : <> · <b>{Math.round(worker.vram_total_gb)} GB</b> VRAM · free unknown</>
                      )}
                    </div>
                    {(worker.gpus || []).map((gpu, idx) => (
                      <div key={idx}>1x <b>{gpu.name || 'GPU'}</b> — {gpu.vram_gb} GB VRAM{gpu.driver ? ` · driver ${gpu.driver}` : ''}</div>
                    ))}
                    {(worker.gpus || []).length === 0 && <div>No GPU detected</div>}
                    {(worker.runtimes || []).length > 0 && (
                      <div className="engine-tag">Engine: {worker.runtimes[0].type}</div>
                    )}
                  </div>
                  <div className="worker-models">
                    {(worker.loaded_models || []).length > 0
                      ? worker.loaded_models.map((m, idx) => <span className="model-pill" key={idx}>{m}</span>)
                      : <span className="model-pill">— none loaded —</span>}
                  </div>
                  {canManage && (
                    <div style={{ marginTop: '0.8rem', display: 'flex', gap: '0.5rem' }}>
                      {worker.status !== 'offline' && (
                        <button className="btn" onClick={() => handleDrain(worker)}>
                          {worker.status === 'draining' ? 'Undrain' : 'Drain'}
                        </button>
                      )}
                      {worker.status === 'offline' && (
                        <button className="btn" style={{ color: 'var(--danger)' }} onClick={() => handleRemoveWorker(worker)}>
                          Remove
                        </button>
                      )}
                    </div>
                  )}
                </div>
              ))}
              {workers.length === 0 && (
                <div className="panel" style={{ gridColumn: 'span 2', textAlign: 'center', color: 'var(--dim)', padding: '3rem' }}>
                  No workers connected. Register a daemon with this organization&apos;s API key.
                </div>
              )}
            </div>
          </div>
          )}

          {/* ============ JOBS SERVED ============ */}
          {selectedOrg && (
          <div className={`page-panel ${activeTab === 'jobs' ? 'active' : ''}`}>
            <h1 className="page-title">Jobs served</h1>
            <p className="page-sub">Metadata only — providers never see input files, prompts, or outputs.</p>

            <div style={{ display: 'flex', gap: '0.6rem', marginBottom: '1rem' }}>
              <select value={jobsWorkerFilter} onChange={e => setJobsWorkerFilter(e.target.value)}>
                <option value="">All workers</option>
                {workers.map(w => <option key={w.id} value={w.id}>{w.hostname}</option>)}
              </select>
              <select value={jobsStatusFilter} onChange={e => setJobsStatusFilter(e.target.value)}>
                <option value="">All statuses</option>
                {['in_progress', 'completed', 'failed', 'validated'].map(s => <option key={s} value={s}>{s}</option>)}
              </select>
            </div>

            <div className="panel" style={{ padding: '0.5rem' }}>
              <div className="table-container">
                <table>
                  <thead><tr><th>Batch</th><th>Model</th><th>Status</th><th>Requests</th><th>Tokens</th><th>Worker</th><th>Assigned</th></tr></thead>
                  <tbody>
                    {filteredJobs.map(j => (
                      <tr key={`${j.id}-${j.assigned_at}`}>
                        <td className="mono">{j.id}</td>
                        <td>{j.model || '—'}</td>
                        <td>{badgeFor(j.status)}</td>
                        <td className="dim">{j.request_counts.completed.toLocaleString()} / {j.request_counts.total.toLocaleString()} · {j.request_counts.failed} failed</td>
                        {/* null until the output file is ingested — an em dash
                            rather than 0, so "not counted yet" and "counted,
                            and it came to zero" never read the same. */}
                        <td className="dim">{j.usage ? (j.usage.total_tokens || 0).toLocaleString() : '—'}</td>
                        <td className="dim">{j.worker_hostname}</td>
                        <td className="dim">{j.assigned_at ? new Date(j.assigned_at * 1000).toLocaleString() : '—'}</td>
                      </tr>
                    ))}
                    {filteredJobs.length === 0 && <tr><td colSpan={7} className="empty-hint">No served jobs match.</td></tr>}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
          )}

          {/* ============ HOSTED MODELS ============ */}
          {selectedOrg && (
          <div className={`page-panel ${activeTab === 'models' ? 'active' : ''}`}>
            <h1 className="page-title">Hosted models</h1>
            <p className="page-sub">What this organization&apos;s workers can serve. Loaded = in VRAM right now.</p>

            <div className="panel" style={{ padding: '0.5rem' }}>
              <div className="table-container">
                <table>
                  <thead><tr><th>Model</th><th>On disk</th><th>Loaded</th></tr></thead>
                  <tbody>
                    {hostedModels.map(m => (
                      <tr key={m.model}>
                        <td className="mono">{m.model}</td>
                        <td className="dim">{m.available.join(', ') || '—'}</td>
                        <td>{m.loaded.length > 0
                          ? m.loaded.map(h => <span className="model-pill" key={h}>{h}</span>)
                          : <span className="dim">not loaded</span>}
                        </td>
                      </tr>
                    ))}
                    {hostedModels.length === 0 && <tr><td colSpan={3} className="empty-hint">No models advertised by this org&apos;s workers.</td></tr>}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
          )}

          {/* ============ CONTRIBUTION ============ */}
          {selectedOrg && (
          <div className={`page-panel ${activeTab === 'contribution' ? 'active' : ''}`}>
            <h1 className="page-title">Contribution</h1>
            <p className="page-sub">
              Work served by {selectedOrg.name}. Tokens are the meaningful figure for contributed
              hardware — request sizes vary by orders of magnitude, so a request count says little
              about what a GPU actually did.
            </p>

            {/* One control, scoping the cards, the chart and both tables. */}
            <div className="range-switch" style={{ marginBottom: '1.25rem' }}>
              {[7, 14, 30].map(d => (
                <button
                  key={d}
                  className={`range-btn ${contribRange === d ? 'active' : ''}`}
                  onClick={() => setContribRange(d)}
                >
                  {d}d
                </button>
              ))}
            </div>

            <div className="grid-4">
              <div className="panel stat-card">
                <div className="stat-label">Jobs served</div>
                <div className="stat-value">{stats ? stats.totals.jobs : '—'}</div>
                <div className="stat-sub">{stats ? `${stats.totals.completed} completed · ${stats.totals.failed} failed` : ''}</div>
              </div>
              <div className="panel stat-card">
                <div className="stat-label">Tokens produced</div>
                {/* An em dash, not a zero: before any rollup has landed the
                    fleet has not been measured, which is not the same as
                    having produced nothing. */}
                <div className="stat-value">
                  {stats && stats.totals.counted_jobs > 0
                    ? stats.totals.total_tokens.toLocaleString()
                    : '—'}
                </div>
                <div className="stat-sub">
                  {stats && stats.totals.counted_jobs > 0
                    ? `${stats.totals.prompt_tokens.toLocaleString()} prompt · ${stats.totals.completion_tokens.toLocaleString()} completion`
                    : 'no counted jobs in this window'}
                </div>
              </div>
              <div className="panel stat-card">
                <div className="stat-label">Requests completed</div>
                <div className="stat-value">{stats ? stats.totals.requests_completed.toLocaleString() : '—'}</div>
                <div className="stat-sub">across all models</div>
              </div>
              <div className="panel stat-card">
                <div className="stat-label">Active workers</div>
                <div className="stat-value">{stats ? stats.by_worker.length : '—'}</div>
                <div className="stat-sub">served ≥ 1 job in window</div>
              </div>
            </div>

            <div className="section-title">Tokens per day</div>
            <div style={{ display: 'flex', gap: '0.6rem', marginBottom: '0.8rem', flexWrap: 'wrap' }}>
              <select value={contribWorker} onChange={e => setContribWorker(e.target.value)}>
                <option value="">All workers</option>
                {workers.map(w => <option key={w.id} value={w.id}>{w.hostname}</option>)}
              </select>
              <select value={contribModel} onChange={e => setContribModel(e.target.value)}>
                <option value="">All models</option>
                {contribModels.map(m => <option key={m} value={m}>{m}</option>)}
              </select>
              <span className="stat-sub" style={{ alignSelf: 'center' }}>
                {stacked && `split by ${stackByModel ? 'model' : 'worker'}`}
              </span>
            </div>

            {/* The cap is bounded by time, not rows, so this should not fire —
                but if it ever does the chart is short and must say so rather
                than draw its missing days as zero. */}
            {contribTruncated && (
              <div className="panel warn" style={{ padding: '0.7rem 0.9rem', marginBottom: '0.8rem', fontSize: '0.8rem' }}>
                More jobs were served in this window than could be loaded — the earliest days are
                incomplete. Narrow the window to see an accurate chart.
              </div>
            )}

            <div className="panel chart-wrap" style={{ height: '260px', padding: '1rem 1rem 0.5rem' }}>
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={contribChart} barCategoryGap="28%">
                  <XAxis
                    dataKey="displayDate"
                    stroke={CHART.axis}
                    fontSize={12}
                    tickLine={false}
                    axisLine={false}
                    minTickGap={24}
                  />
                  <YAxis
                    stroke={CHART.axis}
                    fontSize={12}
                    tickLine={false}
                    axisLine={false}
                    width={52}
                    tickFormatter={compactTokens}
                  />
                  <Tooltip
                    cursor={{ fill: 'rgba(255,255,255,0.04)' }}
                    contentStyle={{ backgroundColor: CHART.surface, border: '1px solid var(--border)', borderRadius: '8px' }}
                    labelStyle={{ color: 'var(--fg)' }}
                    formatter={(v, name) => [v.toLocaleString(), name]}
                  />
                  {/* Above SERIES_MAX the stack is unreadable, so it collapses
                      to one bar and the dropdowns carry the breakdown instead.
                      The stroke is the gap between stacked segments. */}
                  {stacked
                    ? seriesKeys.map((k, i) => (
                        <Bar
                          key={k}
                          dataKey={`tok:${k}`}
                          name={k}
                          stackId="t"
                          fill={SERIES_COLORS[i]}
                          stroke={CHART.surface}
                          strokeWidth={2}
                        />
                      ))
                    : <Bar dataKey="totalTokens" name="Tokens" fill={SERIES_COLORS[0]} radius={[4, 4, 0, 0]} />}
                </BarChart>
              </ResponsiveContainer>
            </div>
            {stacked && (
              <div className="chart-legend">
                {seriesKeys.map((k, i) => (
                  <span className="lg" key={k}>
                    <span className="sw" style={{ background: SERIES_COLORS[i] }} /> {k}
                  </span>
                ))}
              </div>
            )}
            <div className="stat-sub" style={{ marginTop: '0.5rem' }}>
              {contribTokens > 0
                ? `${contribTokens.toLocaleString()} tokens in view`
                : 'No token counts in this window yet.'}
              {contribAwaiting > 0 && ` · ${contribAwaiting} completed job${contribAwaiting === 1 ? '' : 's'} not counted`}
            </div>

            <div className="section-title">By model</div>
            <div className="panel" style={{ padding: '0.5rem' }}>
              <div className="table-container">
                <table>
                  <thead><tr><th>Model</th><th>Jobs</th><th>Requests completed</th><th>Tokens</th></tr></thead>
                  <tbody>
                    {(stats?.by_model || []).map(m => (
                      <tr key={m.model}>
                        <td className="mono">{m.model}</td>
                        <td>{m.jobs}</td>
                        <td className="dim">{m.requests_completed.toLocaleString()}</td>
                        <td className="dim">{tokenCell(m)}</td>
                      </tr>
                    ))}
                    {(!stats || stats.by_model.length === 0) && <tr><td colSpan={4} className="empty-hint">Nothing served in this window.</td></tr>}
                  </tbody>
                </table>
              </div>
            </div>

            <div className="section-title">By worker</div>
            <div className="panel" style={{ padding: '0.5rem' }}>
              <div className="table-container">
                <table>
                  <thead><tr><th>Worker</th><th>Jobs</th><th>Requests completed</th><th>Tokens</th></tr></thead>
                  <tbody>
                    {(stats?.by_worker || []).map(w => (
                      <tr key={w.worker_id}>
                        <td className="mono">{w.hostname}</td>
                        <td>{w.jobs}</td>
                        <td className="dim">{w.requests_completed.toLocaleString()}</td>
                        <td className="dim">{tokenCell(w)}</td>
                      </tr>
                    ))}
                    {(!stats || stats.by_worker.length === 0) && <tr><td colSpan={4} className="empty-hint">Nothing served in this window.</td></tr>}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
          )}

          {/* ============ WORKER KEYS ============ */}
          {selectedOrg && (
          <div className={`page-panel ${activeTab === 'keys' ? 'active' : ''}`}>
            <div className="page-actions">
              <div>
                <h1 className="page-title">Worker keys</h1>
                <p className="page-sub">
                  Daemons register with an org key — one per lab machine or cluster keeps revocation surgical.{' '}
                  <DocsAnchor className="docs-inline" page="provider/#1-get-your-worker-key">How keys work</DocsAnchor>
                </p>
              </div>
              {canManage && (
                <button className="btn primary" onClick={() => { setRevealedKey(''); setIsNewKeyModalOpen(true); }}>
                  + {orgKeys.some(k => k.status === 'active') ? 'New key' : 'Generate key'}
                </button>
              )}
            </div>

            <div className="panel" style={{ padding: '0.5rem' }}>
              <div className="table-container">
                <table>
                  <thead><tr><th>Name</th><th>Prefix</th><th>Status</th><th>Last used</th><th>Created</th>{canManage && <th></th>}</tr></thead>
                  <tbody>
                    {orgKeys.map(k => (
                      <tr key={k.id}>
                        <td>{k.name || 'Unnamed key'}</td>
                        <td className="mono">{k.key_prefix}••••</td>
                        <td>
                          <span className={`badge ${k.status === 'active' ? 'online' : 'offline'}`}>
                            <span className="pip"></span>{k.status}
                          </span>
                        </td>
                        <td className="dim">{k.last_used_at ? heartbeatAge(k.last_used_at) : 'never'}</td>
                        <td className="dim">{k.created_at ? new Date(k.created_at * 1000).toLocaleDateString() : '—'}</td>
                        {canManage && (
                          <td>
                            {k.status === 'active' && (
                              <button className="btn" style={{ color: 'var(--danger)' }} onClick={() => handleRevokeKey(k)}>Revoke</button>
                            )}
                          </td>
                        )}
                      </tr>
                    ))}
                    {orgKeys.length === 0 && (
                      <tr><td colSpan={canManage ? 6 : 5} className="empty-hint">
                        No worker keys yet — generate one, then start the daemon with it to register your first worker.{' '}
                        <DocsAnchor className="docs-inline" page="provider/">Provider guide</DocsAnchor>
                      </td></tr>
                    )}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
          )}

          {/* ============ ORGANIZATION ============ */}
          {selectedOrg && (
          <div className={`page-panel ${activeTab === 'organization' ? 'active' : ''}`}>
            <h1 className="page-title">Organization</h1>
            <p className="page-sub">{canManage ? 'Manage this organization: details, members, invites, worker key.' : 'Read-only: you are a viewer in this organization.'}</p>

            <div className="panel">
              <div className="section-title" style={{ marginTop: 0 }}>Details</div>
              <div className="field" style={{ maxWidth: 420 }}>
                <label>Organization name</label>
                <input value={orgName} onChange={e => setOrgName(e.target.value)} disabled={!canManage} />
              </div>
              {canManage && <button className="btn primary" onClick={handleRenameOrg}>Save</button>}
              {orgStatus && <p style={{ color: 'var(--accent)', fontSize: '0.8rem', marginTop: '0.6rem' }}>{orgStatus}</p>}
            </div>

            <div className="section-title">Members</div>
            <div className="panel" style={{ padding: '0.5rem' }}>
              <div className="table-container">
                <table>
                  <thead><tr><th>Member</th><th>Role</th>{canManage && <th>Actions</th>}</tr></thead>
                  <tbody>
                    {orgMembers.map(m => (
                      <tr key={m.user_id || m.membership_id}>
                        <td>{m.full_name} <span className="dim">({m.email})</span></td>
                        <td>
                          {canManage ? (
                            <select value={m.role} onChange={e => handleUpdateRole(m, e.target.value)}>
                              <option value="owner">owner</option>
                              <option value="admin">admin</option>
                              <option value="viewer">viewer</option>
                            </select>
                          ) : m.role}
                        </td>
                        {canManage && (
                          <td>
                            <button className="btn" style={{ color: 'var(--danger)' }} onClick={() => handleRemoveMember(m)}>Remove</button>
                          </td>
                        )}
                      </tr>
                    ))}
                    {orgMembers.length === 0 && <tr><td colSpan={canManage ? 3 : 2} className="empty-hint">No members loaded.</td></tr>}
                  </tbody>
                </table>
              </div>
            </div>

            {canManage && (
              <>
                <div className="section-title">Pending invites</div>
                <div className="panel" style={{ padding: '0.5rem' }}>
                  <div className="table-container">
                    <table>
                      <thead><tr><th>Email</th><th>Role</th><th>Expires</th><th>Actions</th></tr></thead>
                      <tbody>
                        {orgInvites.map(inv => (
                          <tr key={inv.id || inv.token}>
                            <td>{inv.email}</td>
                            <td><span className="badge">{inv.role}</span></td>
                            <td className="dim">{inv.expires_at ? new Date(inv.expires_at * 1000).toLocaleDateString() : '—'}</td>
                            <td><button className="btn" style={{ color: 'var(--danger)' }} onClick={() => handleRevokeInvite(inv.token)}>Revoke</button></td>
                          </tr>
                        ))}
                        {orgInvites.length === 0 && <tr><td colSpan={4} className="empty-hint">No pending invites.</td></tr>}
                      </tbody>
                    </table>
                  </div>
                  <div style={{ padding: '1rem', borderTop: '1px solid var(--border)', display: 'flex', gap: '0.5rem', alignItems: 'flex-end' }}>
                    <div className="field" style={{ margin: 0, flex: 1 }}>
                      <label>Email address</label>
                      <input type="email" placeholder="colleague@dau.ac.in" value={inviteEmail} onChange={e => setInviteEmail(e.target.value)} />
                    </div>
                    <div className="field" style={{ margin: 0, width: 120 }}>
                      <label>Role</label>
                      <select value={inviteRole} onChange={e => setInviteRole(e.target.value)}>
                        <option value="viewer">viewer</option>
                        <option value="admin">admin</option>
                      </select>
                    </div>
                    <button className="btn primary" onClick={handleInvite} disabled={!inviteEmail}>Invite</button>
                  </div>
                </div>
              </>
            )}
          </div>
          )}
        </div>
      </div>

      {isNewKeyModalOpen && (
        <div className="modal-overlay open">
          <div className="modal">
            <h3>{orgKeys.some(k => k.status === 'active') ? 'New worker key' : 'Generate worker key'}</h3>
            <p className="modal-sub">Name it after the machine or cluster that will use it.</p>
            <div className="field">
              <label>Key name</label>
              <input placeholder="e.g. lab-pc-01" value={newKeyName} onChange={e => setNewKeyName(e.target.value)} />
            </div>
            <div className="modal-actions">
              <button className="btn" onClick={() => { setIsNewKeyModalOpen(false); setRevealedKey(''); setNewKeyName(''); }}>
                {revealedKey ? 'Done' : 'Cancel'}
              </button>
              {!revealedKey && (
                <button className="btn primary" onClick={handleCreateKey} disabled={!newKeyName.trim()}>Create</button>
              )}
            </div>
            {revealedKey && (
              <div style={{ marginTop: '1rem' }}>
                <div className="key-reveal">{revealedKey}</div>
                <div className="key-warning">Shown once — copy it into the daemon&apos;s config now.</div>
                <p className="modal-sub" style={{ marginTop: '0.6rem' }}>
                  Hand this key to whoever owns the machine, along with{' '}
                  <DocsAnchor className="docs-inline" page="provider/#2-install">the install command</DocsAnchor>.
                </p>
              </div>
            )}
          </div>
        </div>
      )}

      {isCreateOrgModalOpen && (
        <div className="modal-overlay open">
          <div className="modal">
            <h3>Create organization</h3>
            <p className="modal-sub">A lab, course, or cluster. You become its owner.</p>
            <div className="field">
              <label>Organization name</label>
              <input placeholder="e.g. Sharma Lab" value={newOrgName} onChange={e => setNewOrgName(e.target.value)} />
            </div>
            <div className="modal-actions">
              <button className="btn" onClick={() => { setIsCreateOrgModalOpen(false); setNewOrgName(''); }}>Cancel</button>
              <button className="btn primary" onClick={handleCreateOrg} disabled={!newOrgName.trim()}>Create</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
