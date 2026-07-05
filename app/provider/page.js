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

// Crescent Moon SVG Logo
function MoonknightLogo({ size = 24 }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
      <svg width={size} height={size} viewBox="0 0 32 32" fill="none">
        <circle cx="16" cy="16" r="12" fill="#fff" />
        <circle cx="20" cy="13" r="10" fill="#0a0a0a" />
      </svg>
      <span style={{ color: '#fff', fontSize: '15px', fontWeight: 600, letterSpacing: '0.12em', fontFamily: 'system-ui, sans-serif' }}>
        MOONKNIGHT
      </span>
    </div>
  );
}

// Helper to render status badges
function StatusBadge({ status }) {
  let bg = 'rgba(255,255,255,0.05)';
  let color = '#888';
  let label = status || 'Unknown';

  if (status === 'pending') {
    bg = 'rgba(250,204,21,0.15)';
    color = '#facc15';
    label = 'Pending';
  } else if (status === 'running') {
    bg = 'rgba(96,165,250,0.15)';
    color = '#60a5fa';
    label = 'Running';
  } else if (status === 'done' || status === 'completed') {
    bg = 'rgba(74,222,128,0.15)';
    color = '#4ade80';
    label = 'Done';
  } else if (status === 'failed') {
    bg = 'rgba(248,113,113,0.15)';
    color = '#f87171';
    label = 'Failed';
  }

  return (
    <span style={{
      backgroundColor: bg,
      color: color,
      padding: '3px 10px',
      borderRadius: '4px',
      fontSize: '11px',
      fontWeight: 600,
      textTransform: 'capitalize',
      display: 'inline-block'
    }}>
      {label}
    </span>
  );
}

export default function ProviderDashboard() {
  const router = useRouter();
  
  // Navigation Tabs: 'dashboard', 'jobs', 'history', 'models'
  const [activeTab, setActiveTab] = useState('dashboard');
  const [menuOpen, setMenuOpen] = useState(false);
  const [profile, setProfile] = useState(null);
  
  // API and State
  const [jobs, setJobs] = useState([]);
  const [workers, setWorkers] = useState([]);
  const [engines, setEngines] = useState(null);
  const [isLoading, setIsLoading] = useState(true);
  const [backendLive, setBackendLive] = useState(false);
  
  // Client side filters
  const [jobsFilter, setJobsFilter] = useState('All'); // 'All' | 'Pending' | 'Running' | 'Done'
  const [historySearch, setHistorySearch] = useState('');
  const [historyStatusFilter, setHistoryStatusFilter] = useState('All'); // 'All' | 'Completed' | 'Failed'
  const [historyEngineFilter, setHistoryEngineFilter] = useState('All'); // 'All' | 'Ollama' | 'vLLM'

  // Fetch engine status from backend (with worker status mapping fallback)
  const loadEngines = useCallback(async () => {
    try {
      const res = await fetch(`${BACKEND}/v1/provider/engines`, { headers: authHeaders() });
      if (res.ok) {
        setEngines(await res.json());
      } else {
        setEngines(null); // Clear to trigger dynamic mapping from workers list
      }
    } catch {
      setEngines(null);
    }
  }, []);

  // Fetch jobs for this provider (pulls from standard /v1/batches)
  const loadJobs = useCallback(async () => {
    setIsLoading(true);
    try {
      const res = await fetch(`${BACKEND}/v1/batches`, { headers: authHeaders() });
      if (res.ok) {
        const data = await res.json();
        const list = data.data || data || [];
        
        setJobs(list.map(b => {
          // Map backend status to UI status: pending, running, done, failed
          let uiStatus = 'pending';
          if (b.status === 'in_progress') uiStatus = 'running';
          else if (b.status === 'completed') uiStatus = 'done';
          else if (b.status === 'failed') uiStatus = 'failed';

          // Format duration if available
          let durationStr = '—';
          if (b.completed_at && b.created_at) {
            const diff = b.completed_at - b.created_at;
            const mins = Math.floor(diff / 60);
            const secs = diff % 60;
            durationStr = `${mins}m ${secs}s`;
          }

          // Format completed date
          let completedStr = '—';
          if (b.completed_at) {
            const date = new Date(b.completed_at * 1000);
            completedStr = date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' }) + `, ${date.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', hour12: false })}`;
          }

          // Format submitted time
          let submittedStr = '—';
          if (b.created_at) {
            const diffSecs = Math.floor(Date.now() / 1000) - b.created_at;
            if (diffSecs < 60) submittedStr = 'just now';
            else if (diffSecs < 3600) submittedStr = `${Math.floor(diffSecs / 60)}m ago`;
            else if (diffSecs < 86400) submittedStr = `${Math.floor(diffSecs / 3600)}h ago`;
            else submittedStr = `${Math.floor(diffSecs / 86400)}d ago`;
          }

          return {
            id: b.id,
            user: b.user_id || '—',
            prompts: b.request_counts?.total ?? 0,
            engine: b.endpoint?.includes('vllm') ? 'vLLM' : 'Ollama', // infer from endpoint
            model: b.model || '—',
            submitted: submittedStr,
            status: uiStatus,
            duration: durationStr,
            completed: completedStr,
            created_at_raw: b.created_at,
            completed_at_raw: b.completed_at
          };
        }));
        setBackendLive(true);
      } else {
        throw new Error();
      }
    } catch {
      setBackendLive(false);
      setJobs([]);
    } finally {
      setIsLoading(false);
    }
  }, []);

  // Fetch workers registered by daemon
  const loadWorkers = useCallback(async () => {
    try {
      const res = await fetch(`${BACKEND}/provider/workers`, { headers: authHeaders() });
      if (res.ok) {
        const data = await res.json();
        setWorkers(data.data || data || []);
      }
    } catch {}
  }, []);

  const loadProfile = useCallback(async () => {
    try {
      const res = await fetch(`${BACKEND}/v1/auth/me`, { headers: authHeaders() });
      if (res.ok) {
        setProfile(await res.json());
      }
    } catch {}
  }, []);

  // Accept job
  async function handleAccept(jobId) {
    try {
      const res = await fetch(`${BACKEND}/v1/provider/jobs/${jobId}/accept`, {
        method: 'POST',
        headers: authHeaders(),
      });
      if (res.ok) {
        loadJobs();
      }
    } catch {}
  }

  // Toggle model state
  async function handleToggleModel(engine, modelName) {
    try {
      const res = await fetch(`${BACKEND}/v1/provider/engines/${engine}/models/${modelName}/toggle`, {
        method: 'POST',
        headers: authHeaders(),
      });
      if (res.ok) {
        loadEngines();
        loadWorkers();
      }
    } catch {}
  }

  // Toggle engine accepting jobs
  async function handleToggleEngine(engine) {
    try {
      const res = await fetch(`${BACKEND}/v1/provider/engines/${engine}/toggle`, {
        method: 'POST',
        headers: authHeaders(),
      });
      if (res.ok) {
        loadEngines();
        loadWorkers();
      }
    } catch {}
  }

  useEffect(() => {
    const token = localStorage.getItem('mk_token');
    if (!token) {
      router.push('/login');
      return;
    }
    loadProfile();
    loadJobs();
    loadWorkers();
    loadEngines();

    const pollInterval = setInterval(() => {
      loadJobs();
      loadWorkers();
      loadEngines();
    }, 15000); // 15s poll

    return () => clearInterval(pollInterval);
  }, [router, loadProfile, loadJobs, loadWorkers, loadEngines]);

  function handleLogout() {
    localStorage.removeItem('mk_token');
    localStorage.removeItem('mk_user');
    router.push('/login');
  }

  // Real-time calculated KPIs
  const pendingCount = jobs.filter(j => j.status === 'pending').length;
  const runningCount = jobs.filter(j => j.status === 'running').length;

  // Completed / Failed Today calculation (last 24 hours)
  const nowUnix = Math.floor(Date.now() / 1000);
  const completedToday = jobs.filter(j => j.status === 'done' && j.completed_at_raw && (nowUnix - j.completed_at_raw < 86400)).length;
  const failedToday = jobs.filter(j => j.status === 'failed' && j.completed_at_raw && (nowUnix - j.completed_at_raw < 86400)).length;

  // Recent jobs (last 4)
  const recentJobs = jobs.slice(0, 4);

  // Username display
  const username = profile?.full_name || profile?.email || 'provider_1';
  const userInitials = username.split(/[_\s]/).map(w => w[0]).join('').toUpperCase().slice(0, 2) || 'PV';

  // Dynamic Engine mapping based on active workers connected
  const getDynamicEngines = () => {
    const defaultEngines = {
      ollama: {
        status: 'paused',
        accepting: false,
        stats: { jobsDone: 0, avgTime: '—', failRate: '0%' },
        models: [
          { name: 'llama-3-8b', active: false, load: 0 },
          { name: 'gemma-7b', active: false, load: 0 },
          { name: 'phi-3-mini', active: false, load: 0 }
        ]
      },
      vllm: {
        status: 'paused',
        accepting: false,
        stats: { jobsDone: 0, avgTime: '—', failRate: '0%' },
        models: [
          { name: 'llama-3-70b', active: false, load: 0 },
          { name: 'mistral-8x7b', active: false, load: 0 },
          { name: 'llama-3-70b-instruct', active: false, load: 0 }
        ]
      }
    };

    const completedJobsList = jobs.filter(j => j.status === 'done');
    const failedJobsList = jobs.filter(j => j.status === 'failed');
    
    let totalDurationSecs = 0;
    completedJobsList.forEach(j => {
      if (j.created_at_raw && j.completed_at_raw) {
        totalDurationSecs += (j.completed_at_raw - j.created_at_raw);
      }
    });
    const avgSecs = completedJobsList.length > 0 ? Math.round(totalDurationSecs / completedJobsList.length) : 0;
    const avgTimeStr = avgSecs > 0 ? `${Math.floor(avgSecs / 60)}m ${avgSecs % 60}s` : '—';
    const failRatePct = jobs.length > 0 ? Math.round((failedJobsList.length / jobs.length) * 100) : 0;

    defaultEngines.ollama.stats.jobsDone = completedJobsList.filter(j => j.engine === 'Ollama').length;
    defaultEngines.ollama.stats.avgTime = avgTimeStr;
    defaultEngines.ollama.stats.failRate = `${failRatePct}%`;

    defaultEngines.vllm.stats.jobsDone = completedJobsList.filter(j => j.engine === 'vLLM').length;
    defaultEngines.vllm.stats.avgTime = avgTimeStr;
    defaultEngines.vllm.stats.failRate = `${failRatePct}%`;

    // Map worker specifications to active engines and loaded models
    workers.forEach(w => {
      if (w.status === 'online') {
        const runtimes = w.runtimes || [];
        runtimes.forEach(r => {
          const type = r.type?.toLowerCase();
          if (defaultEngines[type]) {
            defaultEngines[type].status = 'online';
            defaultEngines[type].accepting = true;
            
            const loadedModelsList = r.models || [];
            defaultEngines[type].models = defaultEngines[type].models.map(m => {
              if (loadedModelsList.includes(m.name)) {
                return { ...m, active: true, load: w.status === 'busy' ? 80 : 15 };
              }
              return m;
            });
            
            // Add dynamically registered unlisted models
            loadedModelsList.forEach(modelName => {
              const exists = defaultEngines[type].models.some(m => m.name === modelName);
              if (!exists) {
                defaultEngines[type].models.push({
                  name: modelName,
                  active: true,
                  load: w.status === 'busy' ? 85 : 20
                });
              }
            });
          }
        });
      }
    });

    return defaultEngines;
  };

  const activeEngines = engines || getDynamicEngines();

  return (
    <div style={{ background: '#0a0a0a', minHeight: '100vh', color: '#fff', fontFamily: 'system-ui, -apple-system, sans-serif', paddingBottom: '40px' }}>
      
      {/* NAVBAR */}
      <div style={{ display: 'flex', alignItems: 'center', justifyBetween: 'space-between', padding: '14px 28px', borderBottom: '1.5px solid #1e1e1e', position: 'relative', zIndex: 10 }}>
        
        {/* Left Side Logo */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
          <MoonknightLogo />
          <span style={{
            background: 'rgba(250,204,21,0.08)',
            border: '1px solid #facc15',
            color: '#facc15',
            fontSize: '11px',
            fontWeight: 600,
            padding: '2px 8px',
            borderRadius: '999px',
            letterSpacing: '0.05em'
          }}>
            Provider
          </span>
        </div>

        {/* Right Side Controls */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '12px', position: 'relative' }}>
          
          {/* User chip */}
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', background: '#111111', border: '0.5px solid #1e1e1e', padding: '4px 12px 4px 6px', borderRadius: '24px' }}>
            <div style={{ width: '24px', height: '24px', borderRadius: '50%', background: '#333', color: '#aaa', fontSize: '11px', fontWeight: 600, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
              {userInitials}
            </div>
            <span style={{ fontSize: '12px', color: '#fff', fontWeight: 500 }}>{username}</span>
          </div>

          {/* Hamburger Menu button */}
          <button 
            onClick={() => setMenuOpen(!menuOpen)}
            style={{
              width: '30px', height: '30px', borderRadius: '6px', border: '0.5px solid #1e1e1e', background: '#111111',
              display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: '4px', cursor: 'pointer', outline: 'none'
            }}
          >
            <span style={{ width: '14px', height: '1.5px', background: '#fff', display: 'block' }}></span>
            <span style={{ width: '14px', height: '1.5px', background: '#fff', display: 'block' }}></span>
            <span style={{ width: '14px', height: '1.5px', background: '#fff', display: 'block' }}></span>
          </button>

          {/* Hamburger Dropdown Menu */}
          {menuOpen && (
            <div style={{
              position: 'absolute', top: '38px', right: 0, width: '150px', background: '#111111', border: '0.5px solid #1e1e1e',
              borderRadius: '8px', overflow: 'hidden', boxShadow: '0 4px 20px rgba(0,0,0,0.5)', zIndex: 100
            }}>
              <Link href="/docs" onClick={() => setMenuOpen(false)} style={{ display: 'block', padding: '10px 14px', color: '#ccc', fontSize: '13px', textDecoration: 'none', transition: 'background 0.2s' }}>
                Docs
              </Link>
              <button onClick={() => { setActiveTab('jobs'); setMenuOpen(false); }} style={{ display: 'block', width: '100%', textAlign: 'left', border: 'none', background: 'transparent', padding: '10px 14px', color: '#ccc', fontSize: '13px', cursor: 'pointer' }}>
                Jobs
              </button>
              <div style={{ height: '0.5px', background: '#1e1e1e' }}></div>
              <button 
                onClick={() => { handleLogout(); setMenuOpen(false); }}
                style={{ display: 'block', width: '100%', textAlign: 'left', border: 'none', background: 'transparent', padding: '10px 14px', color: '#f87171', fontSize: '13px', cursor: 'pointer', fontWeight: 500 }}
              >
                Log out
              </button>
            </div>
          )}
        </div>
      </div>

      {/* TABS NAVIGATION */}
      <div style={{ display: 'flex', gap: '24px', padding: '16px 28px 0 28px', borderBottom: '0.5px solid #1e1e1e' }}>
        {[
          { id: 'dashboard', label: 'Dashboard' },
          { id: 'jobs', label: 'Jobs', badge: pendingCount },
          { id: 'history', label: 'History' },
          { id: 'models', label: 'Models' }
        ].map(tab => {
          const active = activeTab === tab.id;
          return (
            <button
              key={tab.id}
              onClick={() => { setActiveTab(tab.id); setMenuOpen(false); }}
              style={{
                background: 'transparent', border: 'none', color: active ? '#fff' : '#555555',
                padding: '8px 0 14px 0', fontSize: '14px', fontWeight: active ? 600 : 500,
                cursor: 'pointer', display: 'inline-flex', alignItems: 'center', gap: '6px',
                borderBottom: active ? '2px solid #fff' : '2px solid transparent', transition: 'all 0.2s', outline: 'none'
              }}
            >
              <span>{tab.label}</span>
              {tab.badge !== undefined && tab.badge > 0 && (
                <span style={{
                  background: '#facc15', color: '#000', borderRadius: '999px', fontSize: '10px',
                  fontWeight: 700, padding: '1px 6px', minWidth: '10px', textAlign: 'center'
                }}>
                  {tab.badge}
                </span>
              )}
            </button>
          );
        })}
      </div>

      {/* VIEWPORT CONTROLLER */}
      <div style={{ maxWidth: '1080px', margin: '0 auto', padding: '32px 24px' }}>

        {/* PAGE 1: DASHBOARD */}
        {activeTab === 'dashboard' && (
          <div>
            {/* Greeting */}
            <div style={{ marginBottom: '28px' }}>
              <h1 style={{ fontSize: '24px', fontWeight: 600, margin: '0 0 6px 0', color: '#fff' }}>
                Good morning, {username}
              </h1>
              <p style={{ fontSize: '14px', color: '#555555', margin: 0 }}>
                {pendingCount} pending jobs waiting for acceptance.
              </p>
            </div>

            {/* KPI Cards */}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: '16px', marginBottom: '24px' }}>
              
              {/* Pending */}
              <div style={{ background: '#111111', border: '0.5px solid #1e1e1e', borderRadius: '10px', padding: '18px' }}>
                <p style={{ fontSize: '11px', color: '#555555', textTransform: 'uppercase', fontWeight: 600, margin: '0 0 10px 0', letterSpacing: '0.05em' }}>Pending</p>
                <p style={{ fontSize: '32px', fontWeight: 700, color: '#facc15', margin: '0 0 4px 0' }}>{pendingCount}</p>
                <p style={{ fontSize: '12px', color: '#555555', margin: 0 }}>Needs action</p>
              </div>

              {/* Running */}
              <div style={{ background: '#111111', border: '0.5px solid #1e1e1e', borderRadius: '10px', padding: '18px' }}>
                <p style={{ fontSize: '11px', color: '#555555', textTransform: 'uppercase', fontWeight: 600, margin: '0 0 10px 0', letterSpacing: '0.05em' }}>Running</p>
                <p style={{ fontSize: '32px', fontWeight: 700, color: '#60a5fa', margin: '0 0 4px 0' }}>{runningCount}</p>
                <p style={{ fontSize: '12px', color: '#555555', margin: 0 }}>In progress</p>
              </div>

              {/* Completed today */}
              <div style={{ background: '#111111', border: '0.5px solid #1e1e1e', borderRadius: '10px', padding: '18px' }}>
                <p style={{ fontSize: '11px', color: '#555555', textTransform: 'uppercase', fontWeight: 600, margin: '0 0 10px 0', letterSpacing: '0.05em' }}>Completed Today</p>
                <p style={{ fontSize: '32px', fontWeight: 700, color: '#4ade80', margin: '0 0 4px 0' }}>{completedToday}</p>
                <p style={{ fontSize: '12px', color: '#4ade80', margin: 0 }}>Active metrics</p>
              </div>

              {/* Failed today */}
              <div style={{ background: '#111111', border: '0.5px solid #1e1e1e', borderRadius: '10px', padding: '18px' }}>
                <p style={{ fontSize: '11px', color: '#555555', textTransform: 'uppercase', fontWeight: 600, margin: '0 0 10px 0', letterSpacing: '0.05em' }}>Failed Today</p>
                <p style={{ fontSize: '32px', fontWeight: 700, color: '#f87171', margin: '0 0 4px 0' }}>{failedToday}</p>
                <p style={{ fontSize: '12px', color: '#555555', margin: 0 }}>Check history</p>
              </div>
            </div>

            {/* Split layout: Recent Jobs & Engine Load */}
            <div style={{ display: 'grid', gridTemplateColumns: '1.2fr 0.8fr', gap: '20px' }}>
              
              {/* Recent Jobs */}
              <div style={{ background: '#111111', border: '0.5px solid #1e1e1e', borderRadius: '10px', padding: '20px' }}>
                <h3 style={{ fontSize: '12px', color: '#555555', textTransform: 'uppercase', fontWeight: 700, margin: '0 0 16px 0', letterSpacing: '0.05em' }}>Recent Jobs</h3>
                
                {isLoading ? (
                  <div style={{ padding: '20px', textAlign: 'center', color: '#555555', fontSize: '13px' }}>Loading...</div>
                ) : recentJobs.length === 0 ? (
                  <div style={{ padding: '20px', textAlign: 'center', color: '#555555', fontSize: '13px' }}>No jobs found</div>
                ) : (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: '14px' }}>
                    {recentJobs.map((job, idx) => (
                      <div key={idx} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', paddingBottom: idx < recentJobs.length - 1 ? '12px' : 0, borderBottom: idx < recentJobs.length - 1 ? '0.5px solid #1e1e1e' : 'none' }}>
                        <div>
                          <p style={{ fontSize: '13px', fontWeight: 600, color: '#fff', margin: '0 0 4px 0' }}>{job.id}</p>
                          <p style={{ fontSize: '11px', color: '#555555', margin: 0 }}>
                            {job.user} · {job.prompts} prompts · {job.model}
                          </p>
                        </div>
                        <StatusBadge status={job.status} />
                      </div>
                    ))}
                  </div>
                )}
              </div>

              {/* Engine Load */}
              <div style={{ background: '#111111', border: '0.5px solid #1e1e1e', borderRadius: '10px', padding: '20px' }}>
                <h3 style={{ fontSize: '12px', color: '#555555', textTransform: 'uppercase', fontWeight: 700, margin: '0 0 16px 0', letterSpacing: '0.05em' }}>Engine Load</h3>
                
                {activeEngines && (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
                    
                    {/* Ollama Section */}
                    <div>
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '8px' }}>
                        <span style={{ fontSize: '13px', fontWeight: 600 }}>Ollama</span>
                        <span style={{ fontSize: '11px', color: '#555555' }}>
                          {activeEngines.ollama?.models.filter(m => m.active).length} models loaded
                        </span>
                      </div>
                      <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                        {activeEngines.ollama?.models.filter(m => m.active).length === 0 ? (
                          <p style={{ fontSize: '12px', color: '#555555', margin: 0 }}>No active models</p>
                        ) : (
                          activeEngines.ollama?.models.filter(m => m.active).map((m, idx) => (
                            <div key={idx}>
                              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '11px', color: '#aaa', marginBottom: '4px' }}>
                                <span>{m.name}</span>
                                <span>{m.load}%</span>
                              </div>
                              <div style={{ height: '6px', background: '#222', borderRadius: '3px', overflow: 'hidden' }}>
                                <div style={{ height: '100%', background: '#60a5fa', width: `${m.load}%`, borderRadius: '3px', transition: 'width 0.5s' }} />
                              </div>
                            </div>
                          ))
                        )}
                      </div>
                    </div>

                    {/* vLLM Section */}
                    <div>
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '8px' }}>
                        <span style={{ fontSize: '13px', fontWeight: 600 }}>vLLM</span>
                        <span style={{ fontSize: '11px', color: '#555555' }}>
                          {activeEngines.vllm?.models.filter(m => m.active).length} models loaded
                        </span>
                      </div>
                      <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                        {activeEngines.vllm?.models.filter(m => m.active).length === 0 ? (
                          <p style={{ fontSize: '12px', color: '#555555', margin: 0 }}>No active models</p>
                        ) : (
                          activeEngines.vllm?.models.filter(m => m.active).map((m, idx) => (
                            <div key={idx}>
                              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '11px', color: '#aaa', marginBottom: '4px' }}>
                                <span>{m.name}</span>
                                <span>{m.load}%</span>
                              </div>
                              <div style={{ height: '6px', background: '#222', borderRadius: '3px', overflow: 'hidden' }}>
                                <div style={{ height: '100%', background: '#a78bfa', width: `${m.load}%`, borderRadius: '3px', transition: 'width 0.5s' }} />
                              </div>
                            </div>
                          ))
                        )}
                      </div>
                    </div>

                  </div>
                )}
              </div>
            </div>
          </div>
        )}

        {/* PAGE 2: JOBS */}
        {activeTab === 'jobs' && (
          <div>
            {/* Filter bar */}
            <div style={{ display: 'flex', gap: '10px', marginBottom: '24px' }}>
              {['All', 'Pending', 'Running', 'Done'].map(pill => (
                <button
                  key={pill}
                  onClick={() => setJobsFilter(pill)}
                  style={{
                    background: jobsFilter === pill ? 'rgba(255,255,255,0.08)' : 'transparent',
                    border: jobsFilter === pill ? '1px solid rgba(255,255,255,0.2)' : '1px solid #1e1e1e',
                    color: '#fff', fontSize: '12px', fontWeight: 500, padding: '6px 16px', borderRadius: '6px',
                    cursor: 'pointer', outline: 'none', transition: 'all 0.2s'
                  }}
                >
                  {pill}
                </button>
              ))}
            </div>

            {/* Jobs Table */}
            <div style={{ background: '#111111', border: '0.5px solid #1e1e1e', borderRadius: '10px', overflow: 'hidden' }}>
              
              {/* Header row */}
              <div style={{ display: 'grid', gridTemplateColumns: '1.2fr 1fr 80px 100px 1.2fr 100px 100px 100px', padding: '12px 20px', borderBottom: '1px solid #1e1e1e', background: 'rgba(255,255,255,0.01)' }}>
                {['File', 'User', 'Prompts', 'Engine', 'Model', 'Submitted', 'Status', 'Action'].map(col => (
                  <span key={col} style={{ fontSize: '10px', color: '#555555', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.05em' }}>{col}</span>
                ))}
              </div>

              {/* Rows */}
              {isLoading ? (
                <div style={{ padding: '40px', textAlign: 'center', color: '#555555', fontSize: '13px' }}>Loading jobs...</div>
              ) : jobs.length === 0 ? (
                <div style={{ padding: '40px', textAlign: 'center', color: '#555555', fontSize: '13px' }}>No jobs assigned yet</div>
              ) : (
                jobs.filter(j => {
                  if (jobsFilter === 'All') return true;
                  if (jobsFilter === 'Pending') return j.status === 'pending';
                  if (jobsFilter === 'Running') return j.status === 'running';
                  if (jobsFilter === 'Done') return j.status === 'done';
                  return true;
                }).map((job, idx) => (
                  <div
                    key={idx}
                    style={{
                      display: 'grid', gridTemplateColumns: '1.2fr 1fr 80px 100px 1.2fr 100px 100px 100px',
                      padding: '14px 20px', alignItems: 'center', borderBottom: '0.5px solid #1e1e1e',
                      transition: 'background 0.2s'
                    }}
                    onMouseEnter={e => e.currentTarget.style.backgroundColor = '#0e0e0e'}
                    onMouseLeave={e => e.currentTarget.style.backgroundColor = 'transparent'}
                  >
                    <span style={{ fontSize: '13px', fontWeight: 600, color: '#fff' }}>{job.id}</span>
                    <span style={{ fontSize: '12px', color: '#888' }}>{job.user}</span>
                    <span style={{ fontSize: '13px', color: '#ccc' }}>{job.prompts}</span>
                    <span style={{ fontSize: '12px', color: '#888' }}>{job.engine}</span>
                    <span style={{ fontSize: '12px', color: '#ccc' }}>{job.model}</span>
                    <span style={{ fontSize: '12px', color: '#555555' }}>{job.submitted}</span>
                    <div><StatusBadge status={job.status} /></div>
                    
                    {/* Action buttons */}
                    <div>
                      {job.status === 'pending' && (
                        <button
                          onClick={() => handleAccept(job.id)}
                          style={{
                            background: 'transparent', border: '1px solid #facc15', color: '#facc15',
                            padding: '4px 12px', borderRadius: '4px', fontSize: '11px', fontWeight: 600,
                            cursor: 'pointer', outline: 'none'
                          }}
                        >
                          Accept
                        </button>
                      )}
                      {job.status === 'running' && (
                        <button
                          style={{
                            background: 'transparent', border: '1px solid #333', color: '#aaa',
                            padding: '4px 12px', borderRadius: '4px', fontSize: '11px', fontWeight: 600,
                            cursor: 'pointer', outline: 'none'
                          }}
                        >
                          View
                        </button>
                      )}
                      {job.status === 'done' && (
                        <button
                          style={{
                            background: 'transparent', border: '1px solid #333', color: '#aaa',
                            padding: '4px 12px', borderRadius: '4px', fontSize: '11px', fontWeight: 600,
                            cursor: 'pointer', outline: 'none'
                          }}
                        >
                          Download
                        </button>
                      )}
                      {job.status === 'failed' && (
                        <button
                          style={{
                            background: 'transparent', border: '1px solid #333', color: '#aaa',
                            padding: '4px 12px', borderRadius: '4px', fontSize: '11px', fontWeight: 600,
                            cursor: 'pointer', outline: 'none'
                          }}
                        >
                          Retry
                        </button>
                      )}
                    </div>
                  </div>
                ))
              )}
            </div>
          </div>
        )}

        {/* PAGE 3: HISTORY */}
        {activeTab === 'history' && (
          <div>
            {/* Filter Bar */}
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '10px', marginBottom: '24px', alignItems: 'center' }}>
              
              {/* Search input */}
              <input
                type="text"
                placeholder="Search by file or user..."
                value={historySearch}
                onChange={e => setHistorySearch(e.target.value)}
                style={{
                  background: '#111111', border: '0.5px solid #1e1e1e', borderRadius: '6px',
                  padding: '8px 14px', color: '#fff', fontSize: '12px', width: '220px', outline: 'none'
                }}
              />

              {/* Status filter pills */}
              <div style={{ display: 'flex', gap: '6px', borderLeft: '1px solid #1e1e1e', paddingLeft: '10px' }}>
                {['All', 'Completed', 'Failed'].map(statusPill => (
                  <button
                    key={statusPill}
                    onClick={() => setHistoryStatusFilter(statusPill)}
                    style={{
                      background: historyStatusFilter === statusPill ? 'rgba(255,255,255,0.08)' : 'transparent',
                      border: historyStatusFilter === statusPill ? '1px solid rgba(255,255,255,0.2)' : '1px solid #1e1e1e',
                      color: '#fff', fontSize: '12px', fontWeight: 500, padding: '6px 14px', borderRadius: '6px',
                      cursor: 'pointer', outline: 'none', transition: 'all 0.2s'
                    }}
                  >
                    {statusPill}
                  </button>
                ))}
              </div>

              {/* Engine filter pills */}
              <div style={{ display: 'flex', gap: '6px', borderLeft: '1px solid #1e1e1e', paddingLeft: '10px' }}>
                {['All', 'Ollama', 'vLLM'].map(engPill => (
                  <button
                    key={engPill}
                    onClick={() => setHistoryEngineFilter(engPill)}
                    style={{
                      background: historyEngineFilter === engPill ? 'rgba(255,255,255,0.08)' : 'transparent',
                      border: historyEngineFilter === engPill ? '1px solid rgba(255,255,255,0.2)' : '1px solid #1e1e1e',
                      color: '#fff', fontSize: '12px', fontWeight: 500, padding: '6px 14px', borderRadius: '6px',
                      cursor: 'pointer', outline: 'none', transition: 'all 0.2s'
                    }}
                  >
                    {engPill}
                  </button>
                ))}
              </div>
            </div>

            {/* History Table */}
            <div style={{ background: '#111111', border: '0.5px solid #1e1e1e', borderRadius: '10px', overflow: 'hidden' }}>
              
              {/* Header row */}
              <div style={{ display: 'grid', gridTemplateColumns: '1.2fr 1fr 80px 100px 1.2fr 100px 120px 80px', padding: '12px 20px', borderBottom: '1px solid #1e1e1e', background: 'rgba(255,255,255,0.01)' }}>
                {['File', 'User', 'Prompts', 'Engine', 'Model', 'Duration', 'Completed', 'Status'].map(col => (
                  <span key={col} style={{ fontSize: '10px', color: '#555555', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.05em' }}>{col}</span>
                ))}
              </div>

              {/* Rows */}
              {isLoading ? (
                <div style={{ padding: '40px', textAlign: 'center', color: '#555555', fontSize: '13px' }}>Loading history...</div>
              ) : jobs.filter(j => j.status === 'done' || j.status === 'completed' || j.status === 'failed').length === 0 ? (
                <div style={{ padding: '40px', textAlign: 'center', color: '#555555', fontSize: '13px' }}>No completed history found</div>
              ) : (
                jobs.filter(j => {
                  return j.status === 'done' || j.status === 'completed' || j.status === 'failed';
                }).filter(j => {
                  if (!historySearch) return true;
                  const search = historySearch.toLowerCase();
                  return j.id.toLowerCase().includes(search) || j.user.toLowerCase().includes(search);
                }).filter(j => {
                  if (historyStatusFilter === 'All') return true;
                  if (historyStatusFilter === 'Completed') return j.status === 'done' || j.status === 'completed';
                  if (historyStatusFilter === 'Failed') return j.status === 'failed';
                  return true;
                }).filter(j => {
                  if (historyEngineFilter === 'All') return true;
                  return j.engine.toLowerCase() === historyEngineFilter.toLowerCase();
                }).map((job, idx) => (
                  <div
                    key={idx}
                    style={{
                      display: 'grid', gridTemplateColumns: '1.2fr 1fr 80px 100px 1.2fr 100px 120px 80px',
                      padding: '14px 20px', alignItems: 'center', borderBottom: '0.5px solid #1e1e1e',
                      transition: 'background 0.2s'
                    }}
                    onMouseEnter={e => e.currentTarget.style.backgroundColor = '#0e0e0e'}
                    onMouseLeave={e => e.currentTarget.style.backgroundColor = 'transparent'}
                  >
                    <span style={{ fontSize: '13px', fontWeight: 600, color: '#fff' }}>{job.id}</span>
                    <span style={{ fontSize: '12px', color: '#888' }}>{job.user}</span>
                    <span style={{ fontSize: '13px', color: '#ccc' }}>{job.prompts}</span>
                    <span style={{ fontSize: '12px', color: '#888' }}>{job.engine}</span>
                    <span style={{ fontSize: '12px', color: '#ccc' }}>{job.model}</span>
                    <span style={{ fontSize: '12px', color: '#ccc', fontFamily: 'monospace' }}>{job.duration || '—'}</span>
                    <span style={{ fontSize: '12px', color: '#555555' }}>{job.completed}</span>
                    <div><StatusBadge status={job.status} /></div>
                  </div>
                ))
              )}
            </div>
          </div>
        )}

        {/* PAGE 4: MODELS */}
        {activeTab === 'models' && activeEngines && (
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '20px' }}>
            
            {/* OLLAMA CARD */}
            <div style={{
              background: '#111111',
              border: activeEngines.ollama.accepting ? '1px solid #4ade80' : '0.5px solid #1e1e1e',
              borderRadius: '10px',
              padding: '24px',
              borderLeftWidth: activeEngines.ollama.accepting ? '4px' : '0.5px',
              transition: 'all 0.3s'
            }}>
              {/* Header */}
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '8px' }}>
                <div>
                  <h2 style={{ fontSize: '18px', fontWeight: 700, margin: '0 0 4px 0' }}>Ollama</h2>
                  <p style={{ fontSize: '12px', color: '#555555', margin: 0 }}>Local model runner · CPU + GPU</p>
                </div>
                <span style={{
                  fontSize: '11px', fontWeight: 600, padding: '3px 10px', borderRadius: '20px',
                  backgroundColor: activeEngines.ollama.accepting ? 'rgba(74,222,128,0.1)' : 'rgba(255,255,255,0.05)',
                  color: activeEngines.ollama.accepting ? '#4ade80' : '#888', border: activeEngines.ollama.accepting ? '1px solid #4ade80' : '1px solid #333'
                }}>
                  {activeEngines.ollama.accepting ? 'Online' : 'Paused'}
                </span>
              </div>

              {/* Stats Row */}
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '10px', margin: '20px 0' }}>
                <div style={{ background: '#0a0a0a', border: '0.5px solid #1e1e1e', borderRadius: '6px', padding: '10px 12px' }}>
                  <p style={{ fontSize: '9px', color: '#555555', textTransform: 'uppercase', margin: '0 0 4px 0' }}>Jobs Done</p>
                  <p style={{ fontSize: '15px', fontWeight: 700, margin: 0 }}>{activeEngines.ollama.stats.jobsDone}</p>
                </div>
                <div style={{ background: '#0a0a0a', border: '0.5px solid #1e1e1e', borderRadius: '6px', padding: '10px 12px' }}>
                  <p style={{ fontSize: '9px', color: '#555555', textTransform: 'uppercase', margin: '0 0 4px 0' }}>Avg Time</p>
                  <p style={{ fontSize: '15px', fontWeight: 700, margin: 0 }}>{activeEngines.ollama.stats.avgTime}</p>
                </div>
                <div style={{ background: '#0a0a0a', border: '0.5px solid #1e1e1e', borderRadius: '6px', padding: '10px 12px' }}>
                  <p style={{ fontSize: '9px', color: '#555555', textTransform: 'uppercase', margin: '0 0 4px 0' }}>Fail Rate</p>
                  <p style={{ fontSize: '15px', fontWeight: 700, margin: 0, color: '#f87171' }}>{activeEngines.ollama.stats.failRate}</p>
                </div>
              </div>

              {/* Loaded Models */}
              <div style={{ marginBottom: '24px' }}>
                <p style={{ fontSize: '10px', color: '#555555', textTransform: 'uppercase', fontWeight: 700, margin: '0 0 10px 0', letterSpacing: '0.05em' }}>Loaded Models</p>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px' }}>
                  {activeEngines.ollama.models.map((model, idx) => (
                    <button
                      key={idx}
                      onClick={() => handleToggleModel('ollama', model.name)}
                      style={{
                        background: model.active ? 'rgba(74,222,128,0.05)' : 'transparent',
                        border: model.active ? '1px solid #4ade80' : '1px solid #333',
                        color: model.active ? '#4ade80' : '#888',
                        padding: '6px 14px', borderRadius: '24px', fontSize: '12px', fontWeight: 500,
                        cursor: 'pointer', display: 'inline-flex', alignItems: 'center', gap: '6px', transition: 'all 0.2s', outline: 'none'
                      }}
                    >
                      {model.active ? '✓ ' : '+ '}
                      {model.name}
                    </button>
                  ))}
                </div>
              </div>

              {/* Footer Accepting Toggle */}
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', borderTop: '0.5px solid #1e1e1e', paddingTop: '16px' }}>
                <span style={{ fontSize: '13px', color: '#aaa', fontWeight: 500 }}>Accepting jobs</span>
                
                {/* Custom Toggle Switch */}
                <button
                  onClick={() => handleToggleEngine('ollama')}
                  style={{
                    width: '38px', height: '22px', borderRadius: '11px', border: 'none', outline: 'none',
                    background: activeEngines.ollama.accepting ? '#4ade80' : '#333', cursor: 'pointer',
                    position: 'relative', transition: 'background 0.2s'
                  }}
                >
                  <span style={{
                    width: '18px', height: '18px', borderRadius: '50%', background: '#fff', display: 'block',
                    position: 'absolute', top: '2px', left: activeEngines.ollama.accepting ? '18px' : '2px',
                    transition: 'left 0.2s'
                  }}></span>
                </button>
              </div>
            </div>

            {/* VLLM CARD */}
            <div style={{
              background: '#111111',
              border: activeEngines.vllm.accepting ? '1px solid #4ade80' : '0.5px solid #1e1e1e',
              borderRadius: '10px',
              padding: '24px',
              borderLeftWidth: activeEngines.vllm.accepting ? '4px' : '0.5px',
              transition: 'all 0.3s'
            }}>
              {/* Header */}
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '8px' }}>
                <div>
                  <h2 style={{ fontSize: '18px', fontWeight: 700, margin: '0 0 4px 0' }}>vLLM</h2>
                  <p style={{ fontSize: '12px', color: '#555555', margin: 0 }}>High-throughput serving · GPU required</p>
                </div>
                <span style={{
                  fontSize: '11px', fontWeight: 600, padding: '3px 10px', borderRadius: '20px',
                  backgroundColor: activeEngines.vllm.accepting ? 'rgba(74,222,128,0.1)' : 'rgba(255,255,255,0.05)',
                  color: activeEngines.vllm.accepting ? '#4ade80' : '#888', border: activeEngines.vllm.accepting ? '1px solid #4ade80' : '1px solid #333'
                }}>
                  {activeEngines.vllm.accepting ? 'Online' : 'Paused'}
                </span>
              </div>

              {/* Stats Row */}
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '10px', margin: '20px 0' }}>
                <div style={{ background: '#0a0a0a', border: '0.5px solid #1e1e1e', borderRadius: '6px', padding: '10px 12px' }}>
                  <p style={{ fontSize: '9px', color: '#555555', textTransform: 'uppercase', margin: '0 0 4px 0' }}>Jobs Done</p>
                  <p style={{ fontSize: '15px', fontWeight: 700, margin: 0 }}>{activeEngines.vllm.stats.jobsDone}</p>
                </div>
                <div style={{ background: '#0a0a0a', border: '0.5px solid #1e1e1e', borderRadius: '6px', padding: '10px 12px' }}>
                  <p style={{ fontSize: '9px', color: '#555555', textTransform: 'uppercase', margin: '0 0 4px 0' }}>Avg Time</p>
                  <p style={{ fontSize: '15px', fontWeight: 700, margin: 0 }}>{activeEngines.vllm.stats.avgTime}</p>
                </div>
                <div style={{ background: '#0a0a0a', border: '0.5px solid #1e1e1e', borderRadius: '6px', padding: '10px 12px' }}>
                  <p style={{ fontSize: '9px', color: '#555555', textTransform: 'uppercase', margin: '0 0 4px 0' }}>Fail Rate</p>
                  <p style={{ fontSize: '15px', fontWeight: 700, margin: 0, color: '#f87171' }}>{activeEngines.vllm.stats.failRate}</p>
                </div>
              </div>

              {/* Loaded Models */}
              <div style={{ marginBottom: '24px' }}>
                <p style={{ fontSize: '10px', color: '#555555', textTransform: 'uppercase', fontWeight: 700, margin: '0 0 10px 0', letterSpacing: '0.05em' }}>Loaded Models</p>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px' }}>
                  {activeEngines.vllm.models.map((model, idx) => (
                    <button
                      key={idx}
                      onClick={() => handleToggleModel('vllm', model.name)}
                      style={{
                        background: model.active ? 'rgba(74,222,128,0.05)' : 'transparent',
                        border: model.active ? '1px solid #4ade80' : '1px solid #333',
                        color: model.active ? '#4ade80' : '#888',
                        padding: '6px 14px', borderRadius: '24px', fontSize: '12px', fontWeight: 500,
                        cursor: 'pointer', display: 'inline-flex', alignItems: 'center', gap: '6px', transition: 'all 0.2s', outline: 'none'
                      }}
                    >
                      {model.active ? '✓ ' : '+ '}
                      {model.name}
                    </button>
                  ))}
                </div>
              </div>

              {/* Footer Accepting Toggle */}
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', borderTop: '0.5px solid #1e1e1e', paddingTop: '16px' }}>
                <span style={{ fontSize: '13px', color: '#aaa', fontWeight: 500 }}>Accepting jobs</span>
                
                {/* Custom Toggle Switch */}
                <button
                  onClick={() => handleToggleEngine('vllm')}
                  style={{
                    width: '38px', height: '22px', borderRadius: '11px', border: 'none', outline: 'none',
                    background: activeEngines.vllm.accepting ? '#4ade80' : '#333', cursor: 'pointer',
                    position: 'relative', transition: 'background 0.2s'
                  }}
                >
                  <span style={{
                    width: '18px', height: '18px', borderRadius: '50%', background: '#fff', display: 'block',
                    position: 'absolute', top: '2px', left: activeEngines.vllm.accepting ? '18px' : '2px',
                    transition: 'left 0.2s'
                  }}></span>
                </button>
              </div>
            </div>

          </div>
        )}

      </div>
    </div>
  );
}
