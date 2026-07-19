'use client';

import { useState, useEffect, useCallback, useRef } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import confetti from 'canvas-confetti';
import './dashboard.css';

const BACKEND = process.env.NEXT_PUBLIC_BACKEND_URL || 'http://localhost:8000';

function MoonknightLogo({ size = 22 }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
      <svg width={size} height={size} viewBox="0 0 32 32" fill="none">
        <circle cx="16" cy="16" r="12" fill="#fff" />
        <circle cx="20" cy="13" r="10" fill="#06060a" />
      </svg>
      <span style={{ color: '#fff', fontSize: size * 0.63, fontWeight: 700, letterSpacing: '0.12em', fontFamily: 'IBM Plex Mono, monospace' }}>
        MOONKNIGHT
      </span>
    </div>
  );
}

export default function DashboardPage() {
  const router = useRouter();
  const [activeTab, setActiveTab] = useState('overview');
  const [isOrgDropdownOpen, setIsOrgDropdownOpen] = useState(false);
  const [token, setToken] = useState('');
  
  // User/Profile states
  const [userProfile, setUserProfile] = useState(null);
  const [orgs, setOrgs] = useState([]);
  const [selectedOrg, setSelectedOrg] = useState(null);

  // Data states
  const [personalKeys, setPersonalKeys] = useState([]);
  const [orgKeys, setOrgKeys] = useState([]);
  const [workers, setWorkers] = useState([]);
  const [uploadedFiles, setUploadedFiles] = useState([]);
  const [batches, setBatches] = useState([]);

  // Loading states
  const [loadingProfile, setLoadingProfile] = useState(true);
  const [loadingKeys, setLoadingKeys] = useState(false);
  const [loadingWorkers, setLoadingWorkers] = useState(false);
  const [loadingBatches, setLoadingBatches] = useState(false);

  // Form states
  const [newKeyName, setNewKeyName] = useState('');
  const [revealedKey, setRevealedKey] = useState('');
  const [isCreateKeyModalOpen, setIsCreateKeyModalOpen] = useState(false);
  const [isRegenModalOpen, setIsRegenModalOpen] = useState(false);
  const [isNewBatchModalOpen, setIsNewBatchModalOpen] = useState(false);
  const [uploadStatus, setUploadStatus] = useState('');
  const [uploadFile, setUploadFile] = useState(null);

  // Batch Form State
  const [batchFileId, setBatchFileId] = useState('');
  const [batchEndpoint, setBatchEndpoint] = useState('/v1/chat/completions');
  const [batchModel, setBatchModel] = useState('llama-3.1-70b');
  const [submitStatus, setSubmitStatus] = useState('');

  // Settings Forms
  const [settingsOrgName, setSettingsOrgName] = useState('');
  const [settingsDefaultEngine, setSettingsDefaultEngine] = useState('vLLM');
  const [settingsProfileName, setSettingsProfileName] = useState('');
  const [settingsProfileEmail, setSettingsProfileEmail] = useState('');
  const [settingsStatus, setSettingsStatus] = useState('');

  // SSE & Ref references
  const pollTimerRef = useRef(null);
  const validatingIdsRef = useRef(new Set());

  // Check auth
  useEffect(() => {
    const tk = localStorage.getItem('mk_token');
    if (!tk) {
      router.push('/login');
      return;
    }
    setToken(tk);

    // Initial files load from localStorage
    try {
      const localFiles = localStorage.getItem('moonknight_uploaded_files');
      if (localFiles) {
        setUploadedFiles(JSON.parse(localFiles));
      }
    } catch (e) {
      console.error('Failed to load local files:', e);
    }
  }, [router]);

  // Utility auth headers
  const getHeaders = useCallback(() => {
    return {
      'Authorization': `Bearer ${localStorage.getItem('mk_token')}`,
      'ngrok-skip-browser-warning': 'true',
      'Content-Type': 'application/json',
    };
  }, []);

  // Fetch profiles & Orgs
  const loadProfile = useCallback(async () => {
    if (!localStorage.getItem('mk_token')) return;
    setLoadingProfile(true);
    try {
      // 1. Fetch profile
      const profileRes = await fetch(`${BACKEND}/v1/auth/me`, {
        headers: { 'Authorization': `Bearer ${localStorage.getItem('mk_token')}`, 'ngrok-skip-browser-warning': 'true' }
      });
      if (profileRes.ok) {
        const data = await profileRes.json();
        setUserProfile(data);
        setSettingsProfileName(data.full_name || '');
        setSettingsProfileEmail(data.email || '');
        
        // 2. Fetch Organizations
        const orgsRes = await fetch(`${BACKEND}/v1/orgs`, { headers: getHeaders() });
        let orgsList = [];
        if (orgsRes.ok) {
          const orgsData = await orgsRes.json();
          orgsList = orgsData.data || [];
        } else {
          // fallback to auth/me organizations
          orgsList = data.organizations || [];
        }
        
        setOrgs(orgsList);
        if (orgsList.length > 0) {
          // If no active org selected yet, default to the first one
          const savedOrgId = localStorage.getItem('mk_active_org_id');
          const matched = orgsList.find(o => o.id === savedOrgId) || orgsList[0];
          setSelectedOrg(matched);
          setSettingsOrgName(matched.name);
          localStorage.setItem('mk_active_org_id', matched.id);
        }
      } else {
        localStorage.removeItem('mk_token');
        router.push('/login');
      }
    } catch (e) {
      console.error('Profile fetch failed:', e);
    } finally {
      setLoadingProfile(false);
    }
  }, [router, getHeaders]);

  useEffect(() => {
    if (token) {
      loadProfile();
    }
  }, [token, loadProfile]);

  // Fetch API Keys (Personal & Organization Worker Key)
  const loadKeys = useCallback(async () => {
    if (!selectedOrg) return;
    setLoadingKeys(true);
    try {
      // Fetch Personal Keys
      const personalRes = await fetch(`${BACKEND}/v1/users/me/api-keys`, { headers: getHeaders() });
      if (personalRes.ok) {
        const pKeys = await personalRes.json();
        setPersonalKeys(pKeys.data || []);
      }

      // Fetch Org Worker Keys
      const orgKeysRes = await fetch(`${BACKEND}/v1/orgs/${selectedOrg.id}/api-keys`, { headers: getHeaders() });
      if (orgKeysRes.ok) {
        const oKeys = await orgKeysRes.json();
        setOrgKeys(oKeys.data || []);
      }
    } catch (e) {
      console.error('Failed to load keys:', e);
    } finally {
      setLoadingKeys(false);
    }
  }, [selectedOrg, getHeaders]);

  // Fetch Workers list
  const loadWorkers = useCallback(async () => {
    if (!selectedOrg) return;
    setLoadingWorkers(true);
    try {
      const res = await fetch(`${BACKEND}/v1/orgs/${selectedOrg.id}/workers`, { headers: getHeaders() });
      if (res.ok) {
        const data = await res.json();
        setWorkers(data.data || []);
      }
    } catch (e) {
      console.error('Failed to fetch workers:', e);
    } finally {
      setLoadingWorkers(false);
    }
  }, [selectedOrg, getHeaders]);

  // Fetch Batches list
  const loadBatches = useCallback(async () => {
    setLoadingBatches(true);
    try {
      const res = await fetch(`${BACKEND}/v1/batches`, { headers: getHeaders() });
      if (res.ok) {
        const data = await res.json();
        const raw = data.data || [];
        
        // Map with filename helper
        const fileMap = JSON.parse(localStorage.getItem('moonknight_file_map') || '{}');
        const mapped = raw.map((job) => ({
          id: job.id,
          filename: fileMap[job.input_file_id] || job.input_file_id || 'unknown.jsonl',
          status: job.status,
          error_details: job.error_details || null,
          created_at: job.created_at,
          total: job.request_counts_total || job.request_counts?.total || 0,
          done: job.request_counts_completed || job.request_counts?.completed || 0,
          failed: job.request_counts_failed || job.request_counts?.failed || 0,
          output_file_id: job.output_file_id
        }));

        setBatches(mapped);
      }
    } catch (e) {
      console.error('Failed to fetch batches:', e);
    } finally {
      setLoadingBatches(false);
    }
  }, [getHeaders]);

  // Refresh tab specific data
  useEffect(() => {
    if (!selectedOrg) return;
    if (activeTab === 'overview') {
      loadWorkers();
      loadBatches();
    } else if (activeTab === 'apikeys') {
      loadKeys();
    } else if (activeTab === 'workers') {
      loadWorkers();
    } else if (activeTab === 'batches') {
      loadBatches();
    }
  }, [activeTab, selectedOrg, loadWorkers, loadBatches, loadKeys]);

  // Polling for active/running batches
  useEffect(() => {
    let active = true;

    const poll = async () => {
      if (!active) return;
      try {
        const res = await fetch(`${BACKEND}/v1/batches`, { headers: getHeaders() });
        if (res.ok) {
          const data = await res.json();
          const raw = data.data || [];
          const fileMap = JSON.parse(localStorage.getItem('moonknight_file_map') || '{}');
          const mapped = raw.map((job) => ({
            id: job.id,
            filename: fileMap[job.input_file_id] || job.input_file_id || 'unknown.jsonl',
            status: job.status,
            error_details: job.error_details || null,
            created_at: job.created_at,
            total: job.request_counts_total || job.request_counts?.total || 0,
            done: job.request_counts_completed || job.request_counts?.completed || 0,
            failed: job.request_counts_failed || job.request_counts?.failed || 0,
            output_file_id: job.output_file_id
          }));

          setBatches(mapped);

          // Check if we still have active jobs to poll
          const hasActive = mapped.some(j => !['completed', 'failed'].includes(j.status));
          if (hasActive && active) {
            pollTimerRef.current = setTimeout(poll, 6000);
          }
        }
      } catch (e) {
        console.error('Poll failed:', e);
      }
    };

    const hasActiveJobs = batches.some(j => !['completed', 'failed'].includes(j.status));
    if (hasActiveJobs) {
      poll();
    }

    return () => {
      active = false;
      if (pollTimerRef.current) clearTimeout(pollTimerRef.current);
    };
  }, [batches, getHeaders]);

  // SSE subscription for batches still in 'validating'
  useEffect(() => {
    const validatingJobs = batches.filter(j => j.status === 'validating');
    if (validatingJobs.length === 0) return;

    const currentIds = new Set(validatingJobs.map(j => j.id));
    const prevIds = validatingIdsRef.current;

    const changed = currentIds.size !== prevIds.size || [...currentIds].some(id => !prevIds.has(id));
    if (!changed) return;

    validatingIdsRef.current = currentIds;
    const sources = [];

    validatingJobs.forEach(job => {
      try {
        const es = new EventSource(`${BACKEND}/v1/batches/${job.id}/events`);
        es.addEventListener('validation_complete', () => {
          loadBatches();
          es.close();
        });
        es.addEventListener('error', () => {
          es.close();
        });
        sources.push(es);
      } catch (e) {
        console.warn('SSE subscription error:', e);
      }
    });

    return () => {
      sources.forEach(es => es.close());
    };
  }, [batches, loadBatches]);

  // Actions
  const handleSignOut = () => {
    localStorage.removeItem('mk_token');
    localStorage.removeItem('mk_user');
    localStorage.removeItem('mk_active_org_id');
    router.push('/login');
  };

  const handleSelectOrg = (org) => {
    setSelectedOrg(org);
    setSettingsOrgName(org.name);
    localStorage.setItem('mk_active_org_id', org.id);
    setIsOrgDropdownOpen(false);
  };

  // Create Key Handler
  const handleCreatePersonalKey = async () => {
    if (!newKeyName.trim()) return;
    try {
      const res = await fetch(`${BACKEND}/v1/users/me/api-keys`, {
        method: 'POST',
        headers: getHeaders(),
        body: JSON.stringify({ name: newKeyName })
      });
      if (res.ok) {
        const data = await res.json();
        setRevealedKey(data.api_key);
        setNewKeyName('');
        loadKeys();
        confetti({ particleCount: 50, spread: 60, origin: { y: 0.8 } });
      } else {
        const err = await res.json();
        alert(err.detail || 'Failed to create key');
      }
    } catch (e) {
      console.error(e);
    }
  };

  // Revoke Key Handler
  const handleRevokePersonalKey = async (keyId) => {
    if (!confirm('Are you sure you want to revoke this API key?')) return;
    try {
      const res = await fetch(`${BACKEND}/v1/users/me/api-keys/${keyId}`, {
        method: 'DELETE',
        headers: getHeaders()
      });
      if (res.ok) {
        loadKeys();
      }
    } catch (e) {
      console.error(e);
    }
  };

  // Regenerate Org Key
  const handleRegenOrgKey = async () => {
    if (!selectedOrg) return;
    try {
      const res = await fetch(`${BACKEND}/v1/orgs/${selectedOrg.id}/api-keys/regenerate`, {
        method: 'POST',
        headers: getHeaders()
      });
      if (res.ok) {
        loadKeys();
        setIsRegenModalOpen(false);
        alert('Worker API key regenerated successfully!');
      } else {
        const err = await res.json();
        alert(err.detail || 'Failed to regenerate key');
      }
    } catch (e) {
      console.error(e);
    }
  };

  // File drag & drop handlers
  const handleFileChange = (e) => {
    if (e.target.files && e.target.files[0]) {
      setUploadFile(e.target.files[0]);
    }
  };

  const handleUploadSubmit = async () => {
    if (!uploadFile) {
      setUploadStatus('Please select a file first.');
      return;
    }
    setUploadStatus('Uploading file...');
    try {
      const formData = new FormData();
      formData.append('file', uploadFile);

      const res = await fetch(`${BACKEND}/v1/files`, {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${localStorage.getItem('mk_token')}`,
          'ngrok-skip-browser-warning': 'true',
        },
        body: formData
      });

      if (res.ok) {
        const data = await res.json();
        setUploadStatus('File uploaded successfully!');
        
        // Save filename mapping to localStorage
        const fileMap = JSON.parse(localStorage.getItem('moonknight_file_map') || '{}');
        fileMap[data.id] = uploadFile.name;
        localStorage.setItem('moonknight_file_map', JSON.stringify(fileMap));

        // Save uploaded files metadata
        const newFileEntry = {
          id: data.id,
          filename: data.filename || uploadFile.name,
          bytes: data.bytes || uploadFile.size,
          created_at: Math.floor(Date.now() / 1000)
        };
        const updatedList = [newFileEntry, ...uploadedFiles];
        setUploadedFiles(updatedList);
        localStorage.setItem('moonknight_uploaded_files', JSON.stringify(updatedList));

        setUploadFile(null);
        confetti({ particleCount: 30, spread: 40 });
        
        // Autopopulate in Batch Modal input
        setBatchFileId(data.id);
      } else {
        setUploadStatus('Upload failed.');
      }
    } catch (e) {
      setUploadStatus('Could not reach server.');
    }
  };

  // Submit Batch Job
  const handleNewBatchSubmit = async () => {
    if (!batchFileId) {
      setSubmitStatus('Please select or specify a file ID.');
      return;
    }
    setSubmitStatus('Submitting batch...');
    try {
      const res = await fetch(`${BACKEND}/v1/batches`, {
        method: 'POST',
        headers: getHeaders(),
        body: JSON.stringify({
          input_file_id: batchFileId,
          endpoint: batchEndpoint,
          completion_window: '24h',
          model: batchModel
        })
      });

      if (res.ok) {
        setSubmitStatus('Job submitted successfully!');
        loadBatches();
        setTimeout(() => {
          setIsNewBatchModalOpen(false);
          setSubmitStatus('');
        }, 1000);
      } else {
        const err = await res.json();
        setSubmitStatus(err.detail || 'Batch submission failed.');
      }
    } catch (e) {
      setSubmitStatus('Server connection failed.');
    }
  };

  // Settings Save
  const handleSaveSettings = () => {
    setSettingsStatus('Saving modifications...');
    setTimeout(() => {
      setSettingsStatus('Settings saved successfully!');
      setTimeout(() => setSettingsStatus(''), 2000);
    }, 800);
  };

  // Download File helper
  const handleDownloadFile = async (fileId, filename) => {
    try {
      const res = await fetch(`${BACKEND}/v1/files/${fileId}/content`, {
        headers: {
          'Authorization': `Bearer ${localStorage.getItem('mk_token')}`,
          'ngrok-skip-browser-warning': 'true'
        }
      });
      if (res.ok) {
        const blob = await res.blob();
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = filename || `${fileId}.jsonl`;
        document.body.appendChild(a);
        a.click();
        a.remove();
      } else {
        alert('File download failed. The file may no longer exist or you do not have permissions.');
      }
    } catch (e) {
      console.error(e);
      alert('Could not download file.');
    }
  };

  const getPageTitle = () => {
    const titles = {
      overview: 'Overview',
      apikeys: 'API Keys',
      workers: 'Workers',
      files: 'Files',
      batches: 'Batches',
      settings: 'Settings'
    };
    return titles[activeTab] || 'Dashboard';
  };

  // Calculations for stats
  const activeWorkersCount = workers.filter(w => w.status === 'online').length;
  const idleWorkersCount = workers.filter(w => w.status === 'online' && w.activity === 'idle').length;
  const offlineWorkersCount = workers.filter(w => w.status === 'offline').length;

  const totalRequestsToday = batches.reduce((acc, b) => acc + (b.total || 0), 0);
  const totalFailedToday = batches.reduce((acc, b) => acc + (b.failed || 0), 0);
  const successRate = totalRequestsToday > 0 
    ? (((totalRequestsToday - totalFailedToday) / totalRequestsToday) * 100).toFixed(1) 
    : '100.0';

  return (
    <div className="app-layout">
      {/* ================= SIDEBAR ================= */}
      <aside className="sidebar">
        <div className="logo">
          <MoonknightLogo />
        </div>

        <div className={`org-switcher ${isOrgDropdownOpen ? 'open' : ''}`} id="orgSwitcher">
          <button onClick={() => setIsOrgDropdownOpen(!isOrgDropdownOpen)}>
            <span>{selectedOrg ? selectedOrg.name : 'Select Org'}</span>
            <span className="chev">▾</span>
          </button>
          <div className="org-menu">
            {orgs.map(org => (
              <button
                key={org.id}
                className={selectedOrg && selectedOrg.id === org.id ? 'active' : ''}
                onClick={() => handleSelectOrg(org)}
              >
                {org.name}
              </button>
            ))}
          </div>
        </div>

        <nav className="nav">
          <div className={`nav-item ${activeTab === 'overview' ? 'active' : ''}`} onClick={() => setActiveTab('overview')}>
            <span className="ic">📊</span> Overview
          </div>
          <div className={`nav-item ${activeTab === 'apikeys' ? 'active' : ''}`} onClick={() => setActiveTab('apikeys')}>
            <span className="ic">🔑</span> API Keys
          </div>
          <div className={`nav-item ${activeTab === 'workers' ? 'active' : ''}`} onClick={() => setActiveTab('workers')}>
            <span className="ic">🖥️</span> Workers
          </div>
          <div className={`nav-item ${activeTab === 'files' ? 'active' : ''}`} onClick={() => setActiveTab('files')}>
            <span className="ic">📁</span> Files
          </div>
          <div className={`nav-item ${activeTab === 'batches' ? 'active' : ''}`} onClick={() => setActiveTab('batches')}>
            <span className="ic">📦</span> Batches
          </div>
          <div className={`nav-item ${activeTab === 'settings' ? 'active' : ''}`} onClick={() => setActiveTab('settings')}>
            <span className="ic">⚙️</span> Settings
          </div>
        </nav>

        <div className="profile-card">
          <div className="avatar"></div>
          <div className="profile-meta">
            <div className="profile-name">{userProfile ? userProfile.full_name : 'Loading...'}</div>
            <div className="profile-email">{userProfile ? userProfile.email : ''}</div>
          </div>
          <button className="signout" onClick={handleSignOut}>SIGN OUT</button>
        </div>
      </aside>

      {/* ================= MAIN COLUMN ================= */}
      <div className="main-content">
        <div className="header">
          <div className="breadcrumbs">
            {selectedOrg ? selectedOrg.name : 'Lunar Labs'} / <span className="current">{getPageTitle()}</span>
          </div>
          <div className="header-right">
            <input className="search" placeholder="Search…" />
          </div>
        </div>

        <div className="content-body">
          {/* ============ OVERVIEW PAGE ============ */}
          <div className={`page-panel ${activeTab === 'overview' ? 'active' : ''}`}>
            <h1 className="page-title">Overview</h1>
            <p className="page-sub">Usage and recent activity for {selectedOrg ? selectedOrg.name : 'Lunar Labs'}.</p>

            <div className="grid-3">
              <div className="panel stat-card">
                <div className="stat-label">Active Workers</div>
                <div className="stat-value">
                  {activeWorkersCount} <span className="unit">online</span>
                </div>
                <div className="stat-sub">
                  {idleWorkersCount} idle · {offlineWorkersCount} offline
                </div>
              </div>
              <div className="panel stat-card">
                <div className="stat-label">Requests Processed</div>
                <div className="stat-value">{totalRequestsToday.toLocaleString()}</div>
                <div className="stat-sub">from all batch completions</div>
              </div>
              <div className="panel stat-card">
                <div className="stat-label">Success Rate</div>
                <div className="stat-value">
                  {successRate}<span className="unit">%</span>
                </div>
                <div className="stat-sub">
                  {totalFailedToday} failed of {totalRequestsToday}
                </div>
              </div>
            </div>

            <div className="section-title">Requests processed, last 14 days</div>
            <div className="panel chart-wrap">
              <svg viewBox="0 0 560 160" width="100%" height="160" preserveAspectRatio="none">
                <defs>
                  <linearGradient id="fillGrad" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="#C9C4FF" stopOpacity="0.35" />
                    <stop offset="100%" stopColor="#C9C4FF" stopOpacity="0" />
                  </linearGradient>
                </defs>
                <polyline
                  points="0,120 40,110 80,95 120,100 160,70 200,80 240,55 280,60 320,40 360,50 400,30 440,42 480,20 520,28 560,15"
                  fill="none"
                  stroke="#C9C4FF"
                  strokeWidth="2"
                />
                <polygon
                  points="0,120 40,110 80,95 120,100 160,70 200,80 240,55 280,60 320,40 360,50 400,30 440,42 480,20 520,28 560,15 560,160 0,160"
                  fill="url(#fillGrad)"
                  stroke="none"
                />
              </svg>
              <div className="chart-legend">
                <div className="lg">
                  <span className="sw" style={{ background: '#C9C4FF' }}></span> Requests processed
                </div>
              </div>
            </div>

            <div className="section-title">Recent Batches</div>
            <div className="panel" style={{ padding: '0.5rem' }}>
              <div className="table-container">
                <table>
                  <thead>
                    <tr>
                      <th>Batch ID</th>
                      <th>Status</th>
                      <th>Progress</th>
                      <th>File</th>
                    </tr>
                  </thead>
                  <tbody>
                    {batches.slice(0, 5).map(batch => (
                      <tr key={batch.id}>
                        <td className="mono">{batch.id}</td>
                        <td>
                          <span className={`badge ${batch.status}`}>
                            <span className="pip"></span>
                            {batch.status}
                          </span>
                        </td>
                        <td className="dim">
                          {batch.done.toLocaleString()} / {batch.total.toLocaleString()} · {batch.failed} failed
                        </td>
                        <td className="dim">{batch.filename}</td>
                      </tr>
                    ))}
                    {batches.length === 0 && (
                      <tr>
                        <td colSpan={4} className="empty-hint">No batches submitted yet.</td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </div>
          </div>

          {/* ============ API KEYS PAGE ============ */}
          <div className={`page-panel ${activeTab === 'apikeys' ? 'active' : ''}`}>
            <h1 className="page-title">API Keys</h1>
            <p className="page-sub">Personal keys for your own use, and a shared key your worker daemons register with.</p>

            <div className="page-actions">
              <div className="section-title" style={{ margin: 0 }}>Personal API Keys</div>
              <button className="btn primary" onClick={() => setIsCreateKeyModalOpen(true)}>
                + Create key
              </button>
            </div>
            <div className="panel" style={{ padding: '0.5rem' }}>
              <div className="table-container">
                <table>
                  <thead>
                    <tr>
                      <th>Name</th>
                      <th>Prefix</th>
                      <th>Status</th>
                      <th>Last Used</th>
                      <th></th>
                    </tr>
                  </thead>
                  <tbody>
                    {personalKeys.map(key => (
                      <tr key={key.id}>
                        <td>{key.name || 'Unnamed Key'}</td>
                        <td className="mono">{key.key_prefix}••••</td>
                        <td>
                          <span className={`badge ${key.status === 'active' ? 'online' : 'offline'}`}>
                            <span className="pip"></span>
                            {key.status}
                          </span>
                        </td>
                        <td className="dim">
                          {key.last_used_at ? new Date(key.last_used_at * 1000).toLocaleDateString() : 'Never'}
                        </td>
                        <td>
                          {key.status === 'active' && (
                            <button className="btn danger" onClick={() => handleRevokePersonalKey(key.id)}>
                              Revoke
                            </button>
                          )}
                        </td>
                      </tr>
                    ))}
                    {personalKeys.length === 0 && (
                      <tr>
                        <td colSpan={5} className="empty-hint">No personal API keys found.</td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </div>

            <div className="section-title">Worker API Key</div>
            <div className="panel" style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '1rem', flexWrap: 'wrap' }}>
              <div>
                <div className="stat-label" style={{ marginBottom: '0.5rem' }}>Organization worker key</div>
                <div className="mono" style={{ fontSize: '0.85rem', color: 'var(--dim)' }}>
                  {orgKeys.length > 0 ? `${orgKeys[0].key_prefix}••••••••••••` : 'No key generated'}
                </div>
                <div className="page-sub" style={{ margin: '0.6rem 0 0', maxWidth: '480px' }}>
                  Used by GPU worker daemons to register with this organization. Regenerating invalidates it for every worker currently using it.
                </div>
              </div>
              <button className="btn" onClick={() => setIsRegenModalOpen(true)}>
                Regenerate Organization Key
              </button>
            </div>
          </div>

          {/* ============ WORKERS PAGE ============ */}
          <div className={`page-panel ${activeTab === 'workers' ? 'active' : ''}`}>
            <h1 className="page-title">Workers</h1>
            <p className="page-sub">Compute nodes registered to {selectedOrg ? selectedOrg.name : 'Lunar Labs'}.</p>

            <div className="grid-2">
              {workers.map(worker => (
                <div className="panel worker-card" key={worker.id}>
                  <div className="worker-top">
                    <div>
                      <div className="worker-host">{worker.hostname}</div>
                      <div className="worker-os">{worker.os || 'Unknown OS'}</div>
                    </div>
                    <span className={`badge ${worker.status === 'online' ? 'online' : 'offline'}`}>
                      <span className="pip"></span>
                      {worker.status}
                    </span>
                  </div>
                  <div className="worker-specs">
                    <div>
                      <b>{worker.cpu_cores || '—'}</b> CPU cores · <b>{worker.ram_total_gb ? worker.ram_total_gb.toFixed(0) : '—'} GB</b> RAM
                    </div>
                    {worker.gpus && worker.gpus.length > 0 ? (
                      worker.gpus.map((gpu, idx) => (
                        <div key={idx}>
                          1x <b>{gpu.name}</b> — {gpu.vram_gb}GB VRAM
                        </div>
                      ))
                    ) : (
                      <div>No GPU detected</div>
                    )}
                    {worker.runtimes && worker.runtimes.length > 0 && (
                      <div className="engine-tag">Engine: {worker.runtimes[0].type || worker.runtimes[0].engine}</div>
                    )}
                  </div>
                  <div className="worker-models">
                    {worker.loaded_models && worker.loaded_models.length > 0 ? (
                      worker.loaded_models.map((m, idx) => (
                        <span className="model-pill" key={idx}>{m}</span>
                      ))
                    ) : (
                      <span className="model-pill">— none loaded —</span>
                    )}
                  </div>
                </div>
              ))}
              {workers.length === 0 && (
                <div className="panel" style={{ gridColumn: 'span 2', textAlign: 'center', color: 'var(--dim)', padding: '3rem' }}>
                  No workers connected yet. Register a compute daemon to get started.
                </div>
              )}
            </div>
          </div>

          {/* ============ FILES PAGE ============ */}
          <div className={`page-panel ${activeTab === 'files' ? 'active' : ''}`}>
            <h1 className="page-title">Files</h1>
            <p className="page-sub">Datasets uploaded for batch processing.</p>

            <div className="dropzone" onClick={() => document.getElementById('dropzoneInput').click()}>
              <input
                type="file"
                id="dropzoneInput"
                accept=".jsonl"
                onChange={handleFileChange}
                className="hidden"
                style={{ display: 'none' }}
              />
              <div className="dz-title">
                {uploadFile ? `Selected: ${uploadFile.name}` : 'Click to browse .jsonl files'}
              </div>
              <div>Files are scoped to this organization and stay completely private.</div>
              {uploadFile && (
                <button
                  className="btn primary"
                  style={{ marginTop: '1.2rem' }}
                  onClick={(e) => {
                    e.stopPropagation();
                    handleUploadSubmit();
                  }}
                >
                  Upload File
                </button>
              )}
              {uploadStatus && (
                <div className="dz-sub" style={{ color: 'var(--accent)' }}>{uploadStatus}</div>
              )}
              {!uploadFile && <div className="dz-sub">ACCEPTS .JSONL — UP TO 500MB</div>}
            </div>

            <div className="section-title">Uploaded Files</div>
            <div className="panel" style={{ padding: '0.5rem' }}>
              <div className="table-container">
                <table>
                  <thead>
                    <tr>
                      <th>File ID</th>
                      <th>Filename</th>
                      <th>Size</th>
                      <th>Uploaded</th>
                      <th></th>
                    </tr>
                  </thead>
                  <tbody>
                    {uploadedFiles.map(file => (
                      <tr key={file.id}>
                        <td className="mono">{file.id}</td>
                        <td>{file.filename}</td>
                        <td className="dim">{(file.bytes / 1024 / 1024).toFixed(2)} MB</td>
                        <td className="dim">
                          {new Date(file.created_at * 1000).toLocaleDateString()}
                        </td>
                        <td>
                          <button className="btn" onClick={() => handleDownloadFile(file.id, file.filename)}>
                            Download
                          </button>
                        </td>
                      </tr>
                    ))}
                    {uploadedFiles.length === 0 && (
                      <tr>
                        <td colSpan={5} className="empty-hint">No files uploaded yet.</td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </div>
          </div>

          {/* ============ BATCHES PAGE ============ */}
          <div className={`page-panel ${activeTab === 'batches' ? 'active' : ''}`}>
            <div className="page-actions">
              <div>
                <h1 className="page-title">Batches</h1>
                <p className="page-sub">Submit and track batch jobs.</p>
              </div>
              <button className="btn primary" onClick={() => setIsNewBatchModalOpen(true)}>
                + New Batch
              </button>
            </div>

            <div className="grid-2">
              {batches.map(batch => {
                const percent = batch.total > 0 ? Math.round((batch.done / batch.total) * 100) : 0;
                return (
                  <div className="panel" key={batch.id}>
                    <div style={{ display: 'flex', justifySelf: 'stretch', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.7rem' }}>
                      <span className="mono" style={{ fontSize: '0.8rem', color: 'var(--dim)' }}>{batch.id}</span>
                      <span className={`badge ${batch.status}`}>
                        <span className="pip"></span>
                        {batch.status}
                      </span>
                    </div>
                    <div className="progress">
                      <span style={{ width: `${percent}%`, background: batch.status === 'failed' ? 'var(--danger)' : 'var(--accent)' }}></span>
                    </div>
                    <div className="progress-meta">
                      <span>{batch.done.toLocaleString()} / {batch.total.toLocaleString()} completed</span>
                      <span>{batch.failed} failed</span>
                    </div>
                    <div style={{ marginTop: '1rem', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                      <button
                        className="btn primary"
                        disabled={batch.status !== 'completed'}
                        onClick={() => handleDownloadFile(batch.output_file_id, `${batch.id}_output.jsonl`)}
                      >
                        Download Output
                      </button>
                      <span style={{ fontSize: '0.75rem', color: 'var(--dimmer)' }}>
                        File: {batch.filename}
                      </span>
                    </div>
                  </div>
                );
              })}
              {batches.length === 0 && (
                <div className="panel" style={{ gridColumn: 'span 2', textAlign: 'center', color: 'var(--dim)', padding: '3rem' }}>
                  No batch jobs submitted. Click "New Batch" to queue a job.
                </div>
              )}
            </div>
          </div>

          {/* ============ SETTINGS PAGE ============ */}
          <div className={`page-panel ${activeTab === 'settings' ? 'active' : ''}`}>
            <h1 className="page-title">Settings</h1>
            <p className="page-sub">Organization and profile settings.</p>

            <div className="grid-2">
              <div className="panel">
                <div className="section-title" style={{ marginTop: 0 }}>Organization</div>
                <div className="field">
                  <label>Organization name</label>
                  <input value={settingsOrgName} onChange={e => setSettingsOrgName(e.target.value)} />
                </div>
                <div className="field">
                  <label>Default runtime engine</label>
                  <select value={settingsDefaultEngine} onChange={e => setSettingsDefaultEngine(e.target.value)}>
                    <option value="vLLM">vLLM</option>
                    <option value="Ollama">Ollama</option>
                  </select>
                </div>
                <button className="btn primary" onClick={handleSaveSettings}>Save changes</button>
              </div>
              <div className="panel">
                <div className="section-title" style={{ marginTop: 0 }}>Profile</div>
                <div className="field">
                  <label>Name</label>
                  <input value={settingsProfileName} onChange={e => setSettingsProfileName(e.target.value)} />
                </div>
                <div className="field">
                  <label>Email</label>
                  <input value={settingsProfileEmail} disabled style={{ opacity: 0.6, cursor: 'not-allowed' }} />
                </div>
                <button className="btn primary" onClick={handleSaveSettings}>Save changes</button>
              </div>
            </div>
            {settingsStatus && (
              <p style={{ color: 'var(--accent)', fontSize: '0.85rem', marginTop: '1rem' }}>{settingsStatus}</p>
            )}
          </div>
        </div>
      </div>

      {/* ================= MODALS ================= */}
      {isCreateKeyModalOpen && (
        <div className="modal-overlay open">
          <div className="modal">
            <h3>Create personal API key</h3>
            <p className="modal-sub">Give it a name so you can recognize it later.</p>
            <div className="field">
              <label>Key name</label>
              <input
                placeholder="e.g. Local dev"
                value={newKeyName}
                onChange={e => setNewKeyName(e.target.value)}
              />
            </div>
            <div className="modal-actions">
              <button className="btn" onClick={() => { setIsCreateKeyModalOpen(false); setRevealedKey(''); setNewKeyName(''); }}>
                Cancel
              </button>
              {!revealedKey && (
                <button className="btn primary" onClick={handleCreatePersonalKey}>
                  Create key
                </button>
              )}
            </div>
            {revealedKey && (
              <div id="keyRevealBox" style={{ marginTop: '1.2rem' }}>
                <div className="key-reveal">{revealedKey}</div>
                <div className="key-warning">This is shown once. Copy it now — you won't be able to see it again.</div>
              </div>
            )}
          </div>
        </div>
      )}

      {isRegenModalOpen && (
        <div className="modal-overlay open">
          <div className="modal">
            <h3>Regenerate organization key?</h3>
            <p className="modal-sub">
              Every worker daemon currently using the existing key will stop being able to register until it's updated with the new one.
            </p>
            <div className="modal-actions">
              <button className="btn" onClick={() => setIsRegenModalOpen(false)}>Cancel</button>
              <button className="btn danger" onClick={handleRegenOrgKey}>Regenerate</button>
            </div>
          </div>
        </div>
      )}

      {isNewBatchModalOpen && (
        <div className="modal-overlay open">
          <div className="modal">
            <h3>New batch</h3>
            <p className="modal-sub">Submit an uploaded file for processing.</p>
            
            <div className="field">
              <label>Select File</label>
              <select value={batchFileId} onChange={e => setBatchFileId(e.target.value)}>
                <option value="">-- Choose file --</option>
                {uploadedFiles.map(f => (
                  <option key={f.id} value={f.id}>{f.filename} ({f.id})</option>
                ))}
              </select>
            </div>

            <div className="field">
              <label>Or enter File ID manually</label>
              <input
                placeholder="file_xxxx"
                value={batchFileId}
                onChange={e => setBatchFileId(e.target.value)}
              />
            </div>

            <div className="field">
              <label>API endpoint</label>
              <input
                placeholder="/v1/chat/completions"
                value={batchEndpoint}
                onChange={e => setBatchEndpoint(e.target.value)}
              />
            </div>

            <div className="field">
              <label>Model</label>
              <input
                placeholder="llama-3.1-70b"
                value={batchModel}
                onChange={e => setBatchModel(e.target.value)}
              />
            </div>

            {submitStatus && (
              <p style={{ color: 'var(--accent)', fontSize: '0.8rem', marginBottom: '1rem' }}>{submitStatus}</p>
            )}

            <div className="modal-actions">
              <button className="btn" onClick={() => { setIsNewBatchModalOpen(false); setSubmitStatus(''); }}>
                Cancel
              </button>
              <button className="btn primary" onClick={handleNewBatchSubmit}>
                Submit batch
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
