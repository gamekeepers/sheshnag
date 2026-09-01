'use client';

import { useState, useEffect, useCallback, useRef } from 'react';
import { useRouter } from 'next/navigation';
import confetti from 'canvas-confetti';
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer } from 'recharts';
import { CopyableCode, TeachingEmptyState, TeachingStep } from '../components/Teaching';
import { groupValidationErrors, preflightJsonl } from '../lib/validationHelp';
import PortalSwitch from '../components/PortalSwitch';
import DocsLink from '../components/DocsLink';
import { changePassword } from '../lib/changePassword';
import SheshnagLogo from '../components/SheshnagLogo';
import './dashboard.css';

const BACKEND = process.env.NEXT_PUBLIC_BACKEND_URL || 'http://localhost:8000';

// Both /v1/batches readers (the initial load and the 5s poll) mapped the
// response independently, so a field added to one went silently missing from
// the other. One mapper, both callers.
function mapBatch(job, fileMap) {
  return {
    id: job.id,
    filename: fileMap[job.input_file_id] || job.input_file_id || 'unknown.jsonl',
    status: job.status,
    error_details: job.error_details || null,
    created_at: job.created_at,
    total: job.request_counts_total || job.request_counts?.total || 0,
    done: job.request_counts_completed || job.request_counts?.completed || 0,
    failed: job.request_counts_failed || job.request_counts?.failed || 0,
    output_file_id: job.output_file_id,
    // Null until the backend has ingested the output JSONL. Left null rather
    // than defaulted to zeroes: "not counted yet" and "counted, and it came to
    // zero" must not render as the same number.
    usage: job.usage || null,
  };
}

const formatTokens = (n) => (typeof n === 'number' ? n.toLocaleString() : '—');

const CHART = {
  ok: '#4ADE80',
  failed: '#F87171',
  prompt: '#8B7BF5',
  completion: '#C9722E',
  surface: '#111115',
  axis: '#5E5E5A',
};

export default function DashboardPage() {
  const router = useRouter();
  const [activeTab, setActiveTab] = useState('home');
  // Scopes both Usage charts and its table — one control, one slice.
  const [usageRange, setUsageRange] = useState(14);
  const [showUsageTable, setShowUsageTable] = useState(false);
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
  const [preflight, setPreflight] = useState(null);

  // Batch Form State
  const [batchFileId, setBatchFileId] = useState('');
  const [batchEndpoint, setBatchEndpoint] = useState('/v1/chat/completions');
  const [submitStatus, setSubmitStatus] = useState('');
  const [availableModels, setAvailableModels] = useState([]);
  const [modelCatalog, setModelCatalog] = useState([]);
  const [modelsLoaded, setModelsLoaded] = useState(false);
  const [copiedModelId, setCopiedModelId] = useState(null);

  // Settings Forms
  const [settingsOrgName, setSettingsOrgName] = useState('');
  const [settingsDefaultEngine, setSettingsDefaultEngine] = useState('vLLM');
  const [settingsProfileName, setSettingsProfileName] = useState('');
  const [settingsProfileEmail, setSettingsProfileEmail] = useState('');
  const [settingsStatus, setSettingsStatus] = useState('');
  const [settingsLoading, setSettingsLoading] = useState(false);
  // Change password (Settings). Kept separate from settingsStatus so a
  // password error never reads as a profile-save error, or the reverse.
  const [pwOld, setPwOld] = useState('');
  const [pwNew, setPwNew] = useState('');
  const [pwConfirm, setPwConfirm] = useState('');
  const [pwError, setPwError] = useState('');
  const [pwStatus, setPwStatus] = useState('');
  const [pwLoading, setPwLoading] = useState(false);
  // Aggregate pool capacity for the Batches strip (no per-worker detail).
  const [poolCapacity, setPoolCapacity] = useState(null);
  const [orgMembers, setOrgMembers] = useState([]);
  const [orgInvites, setOrgInvites] = useState([]);
  const [inviteEmail, setInviteEmail] = useState('');
  const [inviteRole, setInviteRole] = useState('viewer');
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
  }, [router]);

  // Utility auth headers
  const getHeaders = useCallback(() => {
    const h = {
      'Authorization': `Bearer ${localStorage.getItem('mk_token')}`,
      'Content-Type': 'application/json',
    };
    if (process.env.NEXT_PUBLIC_NGROK_ENABLED === 'true') h['ngrok-skip-browser-warning'] = 'true';
    return h;
  }, []);

  // Fetch Files list — the server is the source of truth; localStorage only
  // caches client-side sniffed metadata (body.model) the backend doesn't store.
  const loadFiles = useCallback(async () => {
    try {
      const res = await fetch(`${BACKEND}/v1/files`, { headers: getHeaders() });
      if (res.ok) {
        const data = await res.json();
        const meta = JSON.parse(localStorage.getItem('moonknight_file_meta') || '{}');
        const entries = (data.data || []).map(f => ({
          id: f.id,
          filename: f.filename,
          bytes: f.bytes || 0,
          created_at: f.created_at,
          model: meta[f.id]?.model || null,
          mixed_models: meta[f.id]?.mixed_models || null,
        }));
        setUploadedFiles(entries);

        // Keep the id→filename map fresh for batch cards on any device
        const fileMap = {};
        entries.forEach(f => { fileMap[f.id] = f.filename; });
        localStorage.setItem('moonknight_file_map', JSON.stringify(fileMap));
      }
    } catch (e) {
      console.error('Failed to fetch files:', e);
    }
  }, [getHeaders]);

  // Fetch profiles & Orgs
  const loadProfile = useCallback(async () => {
    if (!localStorage.getItem('mk_token')) return;
    setLoadingProfile(true);
    try {
      // 1. Fetch profile
      const profileRes = await fetch(`${BACKEND}/v1/auth/me`, {
        headers: getHeaders()
      });
      if (profileRes.ok) {
        const data = await profileRes.json();

        // An account still on its issued password never reaches the portal —
        // the same check runs in completeLogin(), so typing this URL directly
        // does not skip it.
        if (data.must_change_password) {
          router.replace('/change-password');
          return;
        }

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
      loadFiles();  // batch cards need id→filename before any tab is opened
    }
  }, [token, loadProfile, loadFiles]);

  // Fetch Members & Invites
  const loadMembers = useCallback(async () => {
    if (!selectedOrg) return;
    setSettingsLoading(true);
    try {
      const res = await fetch(`${BACKEND}/v1/orgs/${selectedOrg.id}/members`, { headers: getHeaders() });
      if (res.ok) {
        const data = await res.json();
        setOrgMembers(data.data || []);
      }
      const invRes = await fetch(`${BACKEND}/v1/orgs/${selectedOrg.id}/members/invites`, { headers: getHeaders() });
      if (invRes.ok) {
        const data = await invRes.json();
        setOrgInvites(data.data || []);
      }
    } catch (e) {
      console.error('Failed to load members:', e);
    } finally {
      setSettingsLoading(false);
    }
  }, [selectedOrg, getHeaders]);

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
        const mapped = raw.map((job) => mapBatch(job, fileMap));

        setBatches(mapped);
      }
    } catch (e) {
      console.error('Failed to fetch batches:', e);
    } finally {
      setLoadingBatches(false);
    }
  }, [getHeaders]);

  // Load available models from /v1/models
  const loadModels = useCallback(async () => {
    try {
      const res = await fetch(`${BACKEND}/v1/models`, { headers: getHeaders() });
      if (res.ok) {
        const data = await res.json();
        const entries = data.data || [];
        setModelCatalog(entries);
        setAvailableModels(entries.map(m => m.id));
      }
    } catch (e) {
      console.error('Failed to fetch models:', e);
    } finally {
      // The teaching samples name a live catalogue id. Until this resolves the
      // catalogue is empty for a reason we cannot yet distinguish from "this
      // deployment publishes no models", and the two want different copy.
      setModelsLoaded(true);
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
      loadFiles();
      loadModels();  // the first-batch sample names a live catalogue id
    } else if (activeTab === 'models') {
      loadModels();
    } else if (activeTab === 'files') {
      loadFiles();
      loadModels();  // the dropzone sample line names a live catalogue id
    } else if (activeTab === 'settings') {
      // loadMembers();  // org settings parked — relocating to the provider portal
    }
  }, [activeTab, selectedOrg, loadWorkers, loadBatches, loadKeys, loadModels, loadFiles]);

  const batchesRef = useRef(batches);
  useEffect(() => {
    batchesRef.current = batches;
  }, [batches]);

  // Pool capacity — "is anyone online, and can they run my model?"
  const loadPoolCapacity = useCallback(async () => {
    try {
      const res = await fetch(`${BACKEND}/v1/pool/capacity`, { headers: getHeaders() });
      if (res.ok) setPoolCapacity(await res.json());
    } catch (e) {
      console.error('Failed to fetch pool capacity:', e);
    }
  }, [getHeaders]);

  // Runs for the whole dashboard, not one tab: the strip lives in the sticky
  // header. 30s, not the batches loop's 5s — workers heartbeat every 30s, so a
  // faster poll only re-renders identical numbers. Paused while the tab is
  // hidden so a dashboard left open overnight isn't a heartbeat of its own.
  useEffect(() => {
    const tick = () => {
      if (document.visibilityState === 'visible') loadPoolCapacity();
    };
    tick();
    const interval = setInterval(tick, 30000);
    document.addEventListener('visibilitychange', tick);

    return () => {
      clearInterval(interval);
      document.removeEventListener('visibilitychange', tick);
    };
  }, [loadPoolCapacity]);

  // Polling for active/running batches
  useEffect(() => {
    let active = true;

    const poll = async () => {
      if (!active) return;
      const hasActiveJobs = batchesRef.current.some(j => !['completed', 'failed'].includes(j.status));
      if (!hasActiveJobs) return; // Only poll if there's an active job

      try {
        const res = await fetch(`${BACKEND}/v1/batches`, { headers: getHeaders() });
        if (res.ok) {
          const data = await res.json();
          const raw = data.data || [];
          const fileMap = JSON.parse(localStorage.getItem('moonknight_file_map') || '{}');
          const mapped = raw.map((job) => mapBatch(job, fileMap));

          setBatches(mapped);
        }
      } catch (e) {
        console.error('Poll failed:', e);
      }
    };

    const interval = setInterval(poll, 5000);

    return () => {
      active = false;
      clearInterval(interval);
    };
  }, [getHeaders]);

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
  const handleFileChange = async (e) => {
    if (!e.target.files || !e.target.files[0]) return;
    const file = e.target.files[0];
    setUploadFile(file);

    // check line shape here, while the file is still local. The server
    // accepts a malformed batch and fails it asynchronously a minute later,
    // so anything catchable in the browser should never cost that round trip.
    setPreflight(null);
    try {
      const SLICE = 256 * 1024;
      const text = await file.slice(0, SLICE).text();
      setPreflight(preflightJsonl(text, { truncated: file.size > SLICE }));
    } catch {
      setPreflight(null);  // unreadable file — let the server have the last word
    }
  };

  const handleUploadSubmit = async () => {
    if (!uploadFile) {
      setUploadStatus('Please select a file first.');
      return;
    }
    setUploadStatus('Uploading file...');
    try {
      // Model is defined by body.model inside the JSONL (OpenAI batch format);
      // sniff the head of the file so the batch modal can display and verify
      // it — and catch mixed-model files before the backend rejects them.
      let detectedModel = null;
      let mixedModels = null;
      try {
        const head = await uploadFile.slice(0, 65536).text();
        const seen = [];
        for (const line of head.split('\n')) {
          if (!line.trim()) continue;
          try {
            const m = JSON.parse(line)?.body?.model;
            if (m && !seen.includes(m)) seen.push(m);
          } catch {
            // last line of the head slice may be truncated mid-JSON — skip
          }
        }
        detectedModel = seen[0] || null;
        if (seen.length > 1) mixedModels = seen;
      } catch {
        detectedModel = null;
      }

      const formData = new FormData();
      formData.append('file', uploadFile);

      const res = await fetch(`${BACKEND}/v1/files`, {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${localStorage.getItem('mk_token')}`,
          ...(process.env.NEXT_PUBLIC_NGROK_ENABLED === 'true' && { 'ngrok-skip-browser-warning': 'true' }),
        },
        body: formData
      });

      if (res.ok) {
        const data = await res.json();
        setUploadStatus('File uploaded successfully!');
        
        // Cache client-side sniffed metadata the server doesn't store,
        // then re-fetch the authoritative list.
        const meta = JSON.parse(localStorage.getItem('moonknight_file_meta') || '{}');
        meta[data.id] = { model: detectedModel, mixed_models: mixedModels };
        localStorage.setItem('moonknight_file_meta', JSON.stringify(meta));
        loadFiles();

        setUploadFile(null);
        setPreflight(null);
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
          completion_window: '24h'
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
  const handleSaveOrgSettings = async () => {
    if (!settingsOrgName || !selectedOrg) return;
    setSettingsStatus('Saving org settings...');
    try {
      const res = await fetch(`${BACKEND}/v1/orgs/${selectedOrg.id}`, {
        method: 'PUT',
        headers: getHeaders(),
        body: JSON.stringify({ name: settingsOrgName })
      });
      if (res.ok) {
        setSettingsStatus('Org settings saved!');
        // Refresh org list to reflect new name
        const orgRes = await fetch(`${BACKEND}/v1/me/organizations`, { headers: getHeaders() });
        if (orgRes.ok) {
           const body = await orgRes.json();
           setOrgs(body.data);
           const updatedOrg = body.data.find(o => o.id === selectedOrg.id);
           if (updatedOrg) setSelectedOrg(updatedOrg);
        }
      } else {
        const err = await res.json();
        setSettingsStatus(err.detail || 'Failed to save org settings.');
      }
    } catch (e) {
      setSettingsStatus('Server connection failed.');
    }
    setTimeout(() => setSettingsStatus(''), 3000);
  };

  const handleSaveProfile = async () => {
    if (!settingsProfileName) return;
    setSettingsStatus('Saving profile...');
    try {
      const res = await fetch(`${BACKEND}/v1/auth/me`, {
        method: 'PUT',
        headers: getHeaders(),
        body: JSON.stringify({ full_name: settingsProfileName })
      });
      if (res.ok) {
        setSettingsStatus('Profile saved!');
        // Refresh user profile state
        const profRes = await fetch(`${BACKEND}/v1/auth/me`, { headers: getHeaders() });
        if (profRes.ok) {
           setUserProfile(await profRes.json());
        }
      } else {
        const err = await res.json();
        setSettingsStatus(err.detail || 'Failed to save profile.');
      }
    } catch (e) {
      setSettingsStatus('Server connection failed.');
    }
    setTimeout(() => setSettingsStatus(''), 3000);
  };

  const handleChangePassword = async () => {
    setPwLoading(true);
    setPwError('');
    setPwStatus('');

    const result = await changePassword({
      oldPassword: pwOld,
      newPassword: pwNew,
      confirmPassword: pwConfirm,
    });

    setPwLoading(false);
    if (!result.ok) {
      setPwError(result.error);
      return;
    }

    setPwOld(''); setPwNew(''); setPwConfirm('');
    setPwStatus('Password changed.');
    setTimeout(() => setPwStatus(''), 3000);
  };

  const handleInvite = async () => {
    if (!inviteEmail) return;
    setSettingsLoading(true);
    try {
      const res = await fetch(`${BACKEND}/v1/orgs/${selectedOrg.id}/members/invite`, {
        method: 'POST',
        headers: getHeaders(),
        body: JSON.stringify({ email: inviteEmail, role: inviteRole })
      });
      if (res.ok) {
        setInviteEmail('');
        loadMembers();
        setSettingsStatus('Invite sent!');
        setTimeout(() => setSettingsStatus(''), 2000);
      } else {
        const err = await res.json();
        alert(err.detail || 'Failed to send invite');
      }
    } catch (e) {
      console.error(e);
    } finally {
      setSettingsLoading(false);
    }
  };

  const handleRevokeInvite = async (token) => {
    if (!confirm('Revoke this invite?')) return;
    try {
      const res = await fetch(`${BACKEND}/v1/orgs/${selectedOrg.id}/members/invites/${token}`, {
        method: 'DELETE',
        headers: getHeaders()
      });
      if (res.ok) loadMembers();
    } catch (e) { console.error(e); }
  };

  const handleUpdateRole = async (userId, newRole) => {
    try {
      const res = await fetch(`${BACKEND}/v1/orgs/${selectedOrg.id}/members/${userId}`, {
        method: 'PUT',
        headers: getHeaders(),
        body: JSON.stringify({ role: newRole })
      });
      if (res.ok) loadMembers();
      else {
        const err = await res.json();
        alert(err.detail || 'Failed to update role');
      }
    } catch (e) { console.error(e); }
  };

  const handleRemoveMember = async (userId) => {
    if (!confirm('Remove this member?')) return;
    try {
      const res = await fetch(`${BACKEND}/v1/orgs/${selectedOrg.id}/members/${userId}`, {
        method: 'DELETE',
        headers: getHeaders()
      });
      if (res.ok) loadMembers();
      else {
        const err = await res.json();
        alert(err.detail || 'Failed to remove member');
      }
    } catch (e) { console.error(e); }
  };

  // Download File helper
  const handleDownloadFile = async (fileId, filename) => {
    try {
      const res = await fetch(`${BACKEND}/v1/files/${fileId}/content`, {
        headers: {
          'Authorization': `Bearer ${localStorage.getItem('mk_token')}`,
          ...(process.env.NEXT_PUBLIC_NGROK_ENABLED === 'true' && { 'ngrok-skip-browser-warning': 'true' })
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

  const handleDeleteFile = async (fileId) => {
    if (!confirm('Delete this file? This cannot be undone.')) return;
    try {
      const res = await fetch(`${BACKEND}/v1/files/${fileId}`, {
        method: 'DELETE',
        headers: getHeaders()
      });
      if (res.ok) {
        const meta = JSON.parse(localStorage.getItem('moonknight_file_meta') || '{}');
        delete meta[fileId];
        localStorage.setItem('moonknight_file_meta', JSON.stringify(meta));
        if (batchFileId === fileId) setBatchFileId('');
        loadFiles();
      } else {
        const err = await res.json();
        alert(err.detail || 'Failed to delete file');
      }
    } catch (e) {
      console.error(e);
      alert('Could not reach server.');
    }
  };

  const handleCopyModelId = async (id) => {
    try {
      await navigator.clipboard.writeText(id);
    } catch {
      // Clipboard API unavailable (http origin) — fall back to a hidden textarea
      const ta = document.createElement('textarea');
      ta.value = id;
      document.body.appendChild(ta);
      ta.select();
      document.execCommand('copy');
      ta.remove();
    }
    setCopiedModelId(id);
    setTimeout(() => setCopiedModelId(current => (current === id ? null : current)), 1500);
  };

  // ── First-batch teaching ───────────────────────────────────────────────
  // Four facts a new user can get nowhere else in the product: input is OpenAI
  // batch JSONL; every line needs custom_id/method/url/body; files upload first
  // and a batch references the returned file_id; validation is asynchronous.
  // The model id comes from the live catalogue rather than a constant, so the
  // sample never names a model this deployment cannot serve.

  const sampleModelId = modelCatalog[0]?.id || null;

  const buildSampleJsonl = (modelId) => [
    'Summarise the causes of the 1973 oil crisis in two sentences.',
    'List three risks of over-fitting on a small dataset.',
    'Explain gradient clipping to a first-year student.',
  ].map((prompt, i) => JSON.stringify({
    custom_id: `request-${i + 1}`,
    method: 'POST',
    url: '/v1/chat/completions',
    body: { model: modelId || 'MODEL_ID', messages: [{ role: 'user', content: prompt }] },
  })).join('\n');

  const sampleJsonl = buildSampleJsonl(sampleModelId);

  const buildSubmitSnippet = (modelId) => (
    `from openai import OpenAI\n` +
    `client = OpenAI(api_key="$SHESHNAG_API_KEY", base_url="${BACKEND}/v1")\n` +
    `\n` +
    `# 1. the file is uploaded first and gets an id\n` +
    `f = client.files.create(file=open("batch.jsonl", "rb"), purpose="batch")\n` +
    `\n` +
    `# 2. the batch references that id — it never carries the bytes\n` +
    `batch = client.batches.create(\n` +
    `    input_file_id=f.id,\n` +
    `    endpoint="/v1/chat/completions",\n` +
    `    completion_window="24h",\n` +
    `)\n` +
    `print(batch.id, batch.status)  # queued now, validated a moment later\n` +
    (modelId ? '' : `\n# every body.model must be an id from the Models tab\n`)
  );

  // A8 — the plaintext key exists in the browser exactly once, at reveal. That
  // is the only moment a snippet can carry it ready-to-run, so both of these
  // inline the real key rather than a placeholder. models.list() is the first
  // call worth making: it proves the key works and shows the ids that are
  // legal in body.model.

  const buildKeyPython = (key) => (
    `from openai import OpenAI\n` +
    `client = OpenAI(api_key="${key}", base_url="${BACKEND}/v1")\n` +
    `\n` +
    `print([m.id for m in client.models.list()])\n`
  );

  const buildKeyCurl = (key) => (
    `curl ${BACKEND}/v1/models \\\n` +
    `  -H "Authorization: Bearer ${key}"\n`
  );

  const handleDownloadSample = () => {
    const blob = new Blob([`${buildSampleJsonl(sampleModelId)}\n`], { type: 'application/jsonl' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'sample_batch.jsonl';
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  };

  const getPageTitle = () => {
    const titles = {
      home: 'Home',
      apikeys: 'API Keys',
      usage: 'Usage',
      models: 'Models',
      workers: 'Workers',
      files: 'Files',
      batches: 'Batches',
      settings: 'Settings'
    };
    return titles[activeTab] || 'Dashboard';
  };

  // ---- Fleet ------------------------------------------------------------
  // idle is a SUBSET of online (status online AND activity idle), so listing it
  // beside "online" as though the two were disjoint counts every idle worker
  // twice. Derive busy instead, so busy + idle = online and the three states
  // actually partition the fleet.
  const onlineWorkers = workers.filter(w => w.status === 'online');
  const activeWorkersCount = onlineWorkers.length;
  const idleWorkersCount = onlineWorkers.filter(w => w.activity === 'idle').length;
  const busyWorkersCount = activeWorkersCount - idleWorkersCount;
  const offlineWorkersCount = workers.filter(w => w.status === 'offline').length;

  // Demand against that fleet. Every other tile answers "what happened"; this is
  // the only one that answers "can it keep up".
  const pendingBatches = batches.filter(b => !['completed', 'failed'].includes(b.status));
  const pendingRequests = pendingBatches.reduce(
    (acc, b) => acc + Math.max((b.total || 0) - (b.done || 0) - (b.failed || 0), 0), 0,
  );

  // ---- Success rate -----------------------------------------------------
  // Over FINISHED requests only. The old form divided by b.total, which counts
  // every prompt that has not been attempted yet as a success — so submitting a
  // 10k batch drove the rate towards 100% precisely because none of it had run.
  // done and failed are disjoint (daemon/worker.py: failures = total - successes).
  const succeededRequests = batches.reduce((acc, b) => acc + (b.done || 0), 0);
  const failedRequests = batches.reduce((acc, b) => acc + (b.failed || 0), 0);
  const finishedRequests = succeededRequests + failedRequests;
  const successRate = finishedRequests > 0
    ? ((succeededRequests / finishedRequests) * 100).toFixed(1)
    : null;
  const totalRequestsAllTime = batches.reduce((acc, b) => acc + (b.total || 0), 0);

  // Helper: format date as local YYYY-MM-DD (avoids UTC timezone mismatch)
  const toLocalDateStr = (date) => {
    const y = date.getFullYear();
    const m = String(date.getMonth() + 1).padStart(2, '0');
    const d = String(date.getDate()).padStart(2, '0');
    return `${y}-${m}-${d}`;
  };

  // ---- Daily series -----------------------------------------------------
  // `days` back from today, ending `offsetDays` ago. The offset exists so the
  // same builder produces the previous window each delta is measured against.
  const buildDailySeries = (days, offsetDays = 0) => {
    const series = [...Array(days)].map((_, i) => {
      const d = new Date();
      d.setDate(d.getDate() - (days - 1 - i) - offsetDays);
      return {
        date: toLocalDateStr(d),
        displayDate: d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' }),
        requests: 0,
        successful: 0,
        failed: 0,
        promptTokens: 0,
        completionTokens: 0,
        totalTokens: 0,
        counted: 0,
        countedRequests: 0,
        awaitingCount: 0,
      };
    });

    const byDate = new Map(series.map(day => [day.date, day]));
    batches.forEach(b => {
      if (!b.created_at) return;
      const day = byDate.get(toLocalDateStr(new Date(b.created_at * 1000)));
      if (!day) return;
      day.requests += (b.total || 0);
      day.successful += (b.done || 0);
      day.failed += (b.failed || 0);
      if (b.usage) {
        day.promptTokens += b.usage.prompt_tokens || 0;
        day.completionTokens += b.usage.completion_tokens || 0;
        day.totalTokens += b.usage.total_tokens || 0;
        day.counted += 1;
        // Only the requests whose tokens are actually in the numerator, so a
        // day that mixes counted and uncounted batches does not understate
        // tokens-per-request.
        day.countedRequests += (b.done || 0) + (b.failed || 0);
      } else if (b.status === 'completed') {
        // Only completed batches are *missing* a count. A running batch has no
        // usage yet by definition, and flagging those would mark every active
        // day as incomplete.
        day.awaitingCount += 1;
      }
    });
    return series;
  };

  const sumSeries = (series, key) => series.reduce((acc, d) => acc + d[key], 0);

  // A window whose leading days are all empty renders as a run of zero-height
  // bars that reads "the platform is broken" rather than "you started on
  // Tuesday". Trim the empty head only — the window still ends today.
  const trimLeadingEmpty = (series) => {
    const first = series.findIndex(d => d.requests > 0 || d.totalTokens > 0);
    return first <= 0 ? series : series.slice(first);
  };

  // Percentage change against the preceding window of equal length. Returns
  // null when there is no baseline to compare against — "▲ ∞%" is noise.
  const windowDelta = (current, previous) => {
    if (previous === 0) return null;
    const pct = ((current - previous) / previous) * 100;
    if (Math.abs(pct) < 0.5) return { label: 'no change', tone: 'flat' };
    return {
      label: `${pct > 0 ? '\u25B2' : '\u25BC'} ${Math.abs(pct).toFixed(0)}% vs previous ${previous.toLocaleString()}`,
      tone: pct > 0 ? 'up' : 'down',
    };
  };

  const HOME_WINDOW = 14;
  const homeSeries = buildDailySeries(HOME_WINDOW);
  const homePrevSeries = buildDailySeries(HOME_WINDOW, HOME_WINDOW);
  const homeRequests = sumSeries(homeSeries, 'requests');
  const homeTokens = sumSeries(homeSeries, 'totalTokens');
  const homeRequestsDelta = windowDelta(homeRequests, sumSeries(homePrevSeries, 'requests'));
  const homeTokensDelta = windowDelta(homeTokens, sumSeries(homePrevSeries, 'totalTokens'));
  const homeCountedRequests = sumSeries(homeSeries, 'countedRequests');
  const homeTokensPerRequest = homeCountedRequests > 0
    ? Math.round(homeTokens / homeCountedRequests)
    : null;

  const usageSeries = buildDailySeries(usageRange);
  const usagePrevSeries = buildDailySeries(usageRange, usageRange);
  const usageRequests = sumSeries(usageSeries, 'requests');
  const usageFailed = sumSeries(usageSeries, 'failed');
  const usageSucceeded = sumSeries(usageSeries, 'successful');
  const usageTokens = sumSeries(usageSeries, 'totalTokens');
  const usageTokensDelta = windowDelta(usageTokens, sumSeries(usagePrevSeries, 'totalTokens'));
  const usageFinished = usageSucceeded + usageFailed;
  const usageFailureRate = usageFinished > 0
    ? ((usageFailed / usageFinished) * 100).toFixed(1)
    : null;
  const usageCountedRequests = sumSeries(usageSeries, 'countedRequests');
  const usageTokensPerRequest = usageCountedRequests > 0
    ? Math.round(usageTokens / usageCountedRequests)
    : null;
  const usageAwaiting = sumSeries(usageSeries, 'awaitingCount');
  const usageChartData = trimLeadingEmpty(usageSeries);
  const homeChartData = trimLeadingEmpty(homeSeries);

  return (
    <div className="app-layout">
      {/* ================= SIDEBAR ================= */}
      <aside className="sidebar">
        <div className="logo">
          <SheshnagLogo />
        </div>

        <nav className="nav">
          <div className={`nav-item ${activeTab === 'home' ? 'active' : ''}`} onClick={() => setActiveTab('home')}>
            <span className="ic">📊</span> Home
          </div>
          <div className={`nav-item ${activeTab === 'apikeys' ? 'active' : ''}`} onClick={() => setActiveTab('apikeys')}>
            <span className="ic">🔑</span> API Keys
          </div>
          <div className={`nav-item ${activeTab === 'models' ? 'active' : ''}`} onClick={() => setActiveTab('models')}>
            <span className="ic">🧠</span> Models
          </div>
          <div className={`nav-item ${activeTab === 'usage' ? 'active' : ''}`} onClick={() => setActiveTab('usage')}>
            <span className="ic">📈</span> Usage
          </div>
          <div className={`nav-item ${activeTab === 'batches' ? 'active' : ''}`} onClick={() => setActiveTab('batches')}>
            <span className="ic">📦</span> Batches
          </div>
          <div className={`nav-item ${activeTab === 'files' ? 'active' : ''}`} onClick={() => setActiveTab('files')}>
            <span className="ic">📁</span> Files
          </div>
        </nav>

        <div className="sidebar-bottom">
          <PortalSwitch to="provider" />
          <DocsLink page="using-sheshnag/" />
          <div className="profile-dropdown-wrap" style={{ position: 'relative' }}>
            <button
              className="profile-icon-btn"
              onClick={() => setIsOrgDropdownOpen(!isOrgDropdownOpen)}
              style={{
                display: 'flex', alignItems: 'center', gap: '10px',
                padding: '10px 14px', borderRadius: '10px', width: '100%',
                background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(255,255,255,0.08)',
                color: '#fff', cursor: 'pointer', transition: 'all 0.2s',
              }}
              onMouseEnter={e => e.currentTarget.style.background = 'rgba(255,255,255,0.08)'}
              onMouseLeave={e => e.currentTarget.style.background = 'rgba(255,255,255,0.04)'}
            >
              <div style={{
                width: '32px', height: '32px', borderRadius: '50%',
                background: '#1e3a5f', border: '1px solid #2d5a8a',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                fontSize: '13px', fontWeight: 600, color: '#60a5fa', flexShrink: 0,
              }}>
                {userProfile ? userProfile.full_name?.charAt(0).toUpperCase() : '?'}
              </div>
              <div style={{ flex: 1, textAlign: 'left', overflow: 'hidden' }}>
                <div style={{ fontSize: '13px', fontWeight: 600, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{userProfile ? userProfile.full_name : 'Loading...'}</div>
                <div style={{ fontSize: '11px', color: 'rgba(255,255,255,0.4)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{userProfile ? userProfile.email : ''}</div>
              </div>
              <span style={{ fontSize: '10px', opacity: 0.5 }}>⚙️</span>
            </button>
            {isOrgDropdownOpen && (
              <div style={{
                position: 'absolute', bottom: '100%', left: 0, right: 0,
                marginBottom: '6px', borderRadius: '10px',
                background: '#141720', border: '1px solid rgba(255,255,255,0.1)',
                padding: '6px', zIndex: 200,
                boxShadow: '0 -8px 24px rgba(0,0,0,0.5)',
              }}>
                <button
                  onClick={() => { setActiveTab('settings'); setIsOrgDropdownOpen(false); }}
                  style={{
                    display: 'flex', alignItems: 'center', gap: '8px', width: '100%',
                    padding: '10px 12px', borderRadius: '8px', border: 'none',
                    background: 'transparent', color: '#fff', cursor: 'pointer',
                    fontSize: '13px', transition: 'background 0.15s',
                  }}
                  onMouseEnter={e => e.currentTarget.style.background = 'rgba(255,255,255,0.07)'}
                  onMouseLeave={e => e.currentTarget.style.background = 'transparent'}
                >
                  ⚙️ Settings
                </button>
                <div style={{ height: '1px', background: 'rgba(255,255,255,0.08)', margin: '4px 0' }} />
                <button
                  onClick={handleSignOut}
                  style={{
                    display: 'flex', alignItems: 'center', gap: '8px', width: '100%',
                    padding: '10px 12px', borderRadius: '8px', border: 'none',
                    background: 'transparent', color: '#f87171', cursor: 'pointer',
                    fontSize: '13px', transition: 'background 0.15s',
                  }}
                  onMouseEnter={e => e.currentTarget.style.background = 'rgba(255,255,255,0.07)'}
                  onMouseLeave={e => e.currentTarget.style.background = 'transparent'}
                >
                  🚪 Signout
                </button>
              </div>
            )}
          </div>
        </div>
      </aside>

      {/* ================= MAIN COLUMN ================= */}
      <div className="main-content">
        <div className="header">
          <div className="breadcrumbs">
            Dashboard / <span className="current">{getPageTitle()}</span>
          </div>
          {/* Pool capacity — ambient, in the sticky header so every tab
              carries it, not just Batches: the question "is anyone online?"
              is just as live while picking a model or uploading a file.
              Aggregate only — hostnames and GPU names would identify whose
              machine is whose. No ETA either; a prediction over a volatile
              pool erodes trust faster than an honest count builds it. */}
          {poolCapacity && (
            <div className="pool-strip">
              {poolCapacity.workers_online === 0 ? (
                <span>No workers online — batches wait in the queue until one joins the pool.</span>
              ) : (
                <>
                  <span>
                    <strong>{poolCapacity.workers_online}</strong>
                    {' '}worker{poolCapacity.workers_online === 1 ? '' : 's'} online
                    {' · '}{poolCapacity.workers_idle} idle
                  </span>
                  {poolCapacity.gpus_online != null && (
                    <span><strong>{poolCapacity.gpus_online}</strong> GPU{poolCapacity.gpus_online === 1 ? '' : 's'}</span>
                  )}
                  {poolCapacity.vram_total_gb != null && (
                    <span><strong>{Math.round(poolCapacity.vram_total_gb).toLocaleString()} GB</strong> VRAM</span>
                  )}
                  <span title={poolCapacity.models_servable.map(m => m.display_name || m.id).join(', ')}>
                    <strong>{poolCapacity.models_servable.length}</strong>
                    {' '}model{poolCapacity.models_servable.length === 1 ? '' : 's'} ready to run
                  </span>
                </>
              )}
            </div>
          )}
        </div>

        <div className="content-body">
          {/* ============ HOME PAGE ============ */}
          <div className={`page-panel ${activeTab === 'home' ? 'active' : ''}`}>
            <h1 className="page-title">Home</h1>
            <p className="page-sub">Usage and recent activity.</p>

            <div className="grid-4">
              <div className="panel stat-card">
                <div className="stat-label">Workers</div>
                <div className="stat-value">
                  {activeWorkersCount} <span className="unit">online</span>
                </div>
                <div className="stat-sub">
                  {busyWorkersCount} busy · {idleWorkersCount} idle · {offlineWorkersCount} offline
                </div>
              </div>

              <div className="panel stat-card">
                <div className="stat-label">Queued</div>
                <div className="stat-value">
                  {pendingBatches.length} <span className="unit">batch{pendingBatches.length === 1 ? '' : 'es'}</span>
                </div>
                <div className="stat-sub">
                  {pendingRequests > 0
                    ? `${pendingRequests.toLocaleString()} requests waiting on ${activeWorkersCount} worker${activeWorkersCount === 1 ? '' : 's'}`
                    : activeWorkersCount > 0 ? 'nothing waiting' : 'nothing waiting · no workers online'}
                </div>
              </div>

              <div className="panel stat-card">
                <div className="stat-label">Requests · {HOME_WINDOW}d</div>
                <div className="stat-value">{homeRequests.toLocaleString()}</div>
                <div className="stat-sub">
                  {homeRequestsDelta ? homeRequestsDelta.label : `${totalRequestsAllTime.toLocaleString()} all time`}
                </div>
              </div>

              <div className="panel stat-card">
                <div className="stat-label">Tokens · {HOME_WINDOW}d</div>
                <div className="stat-value">{formatTokens(homeTokens)}</div>
                <div className="stat-sub">
                  {homeTokensPerRequest !== null
                    ? `${homeTokensPerRequest.toLocaleString()} per request`
                    : 'no counted batches yet'}
                  {homeTokensDelta && ` · ${homeTokensDelta.label}`}
                </div>
              </div>
            </div>

            <div className="grid-2" style={{ marginBottom: '1.5rem' }}>
              <div className="panel stat-card">
                <div className="stat-label">Success rate</div>
                <div className="stat-value">
                  {successRate === null ? '—' : <>{successRate}<span className="unit">%</span></>}
                </div>
                {/* Over finished requests. A batch still running is not evidence
                    of success, so it is not in the denominator. */}
                <div className="stat-sub">
                  {finishedRequests > 0
                    ? `${failedRequests.toLocaleString()} failed of ${finishedRequests.toLocaleString()} finished`
                    : 'nothing has finished yet'}
                </div>
              </div>
              <div className="panel stat-card">
                <div className="stat-label">All time</div>
                <div className="stat-value">{totalRequestsAllTime.toLocaleString()}</div>
                <div className="stat-sub">requests submitted across {batches.length} batch{batches.length === 1 ? '' : 'es'}</div>
              </div>
            </div>

            <div className="section-title">Requests per day, last {HOME_WINDOW} days</div>
            <div className="panel chart-wrap" style={{ height: '240px', padding: '1rem 1rem 0.5rem' }}>
              <ResponsiveContainer width="100%" height="100%">
                {/* Stacked, not a single "requests" line: the split is the point.
                    A line of totals hides a failure spike completely. */}
                <BarChart data={homeChartData} barCategoryGap="28%">
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
                    width={44}
                    tickFormatter={(value) => value >= 1000 ? `${(value / 1000).toFixed(1)}k` : value}
                  />
                  <Tooltip
                    cursor={{ fill: 'rgba(255,255,255,0.04)' }}
                    contentStyle={{ backgroundColor: CHART.surface, border: '1px solid var(--border)', borderRadius: '8px' }}
                    labelStyle={{ color: 'var(--fg)' }}
                  />
                  {/* stroke in the surface colour is the 2px gap between stacked
                      segments — mandatory secondary encoding for this pair. */}
                  <Bar dataKey="failed" name="Failed" stackId="r" fill={CHART.failed}
                       stroke={CHART.surface} strokeWidth={2} />
                  <Bar dataKey="successful" name="Successful" stackId="r" fill={CHART.ok}
                       stroke={CHART.surface} strokeWidth={2} radius={[4, 4, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>
            <div className="chart-legend">
              <span className="lg"><span className="sw" style={{ background: CHART.ok }} /> Successful</span>
              <span className="lg"><span className="sw" style={{ background: CHART.failed }} /> Failed</span>
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
                      <th>Tokens</th>
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
                        <td className="dim">{formatTokens(batch.usage?.total_tokens)}</td>
                        <td className="dim">{batch.filename}</td>
                      </tr>
                    ))}
                    {batches.length === 0 && (
                      /* A9 — "no data" is a dead end; name the next move instead. */
                      <tr>
                        <td colSpan={5} className="empty-hint">
                          Nothing here until a batch runs. The chart above stays flat until then —{' '}
                          <button className="teach-link" onClick={() => setActiveTab('batches')}>
                            submit your first batch
                          </button>.
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </div>
          </div>

          {/* ============ USAGE PAGE ============ */}
          <div className={`page-panel ${activeTab === 'usage' ? 'active' : ''}`}>
            <div className="page-actions">
              <div>
                <h1 className="page-title">Usage Details</h1>
                <p className="page-sub">Requests and token usage per day.</p>
              </div>
              {/* One control, above everything it scopes — both charts, the
                  tiles and the table all read the same slice. */}
              <div className="range-switch">
                {[7, 14, 30].map(days => (
                  <button
                    key={days}
                    className={`range-btn ${usageRange === days ? 'active' : ''}`}
                    onClick={() => setUsageRange(days)}
                  >
                    {days}d
                  </button>
                ))}
              </div>
            </div>

            {batches.length === 0 ? (
              <TeachingEmptyState title="No usage yet">
                <p className="teach-body" style={{ marginTop: '0.9rem', marginBottom: 0 }}>
                  This page charts requests and tokens per day. Everything would read zero right
                  now — usage appears once a batch has run, so start by{' '}
                  <button className="teach-link" onClick={() => setActiveTab('batches')}>
                    submitting your first batch
                  </button>.
                </p>
              </TeachingEmptyState>
            ) : (
              <>
                <div className="grid-3">
                  <div className="panel stat-card">
                    <div className="stat-label">Tokens · {usageRange}d</div>
                    <div className="stat-value">{formatTokens(usageTokens)}</div>
                    <div className="stat-sub">
                      {usageTokensDelta ? usageTokensDelta.label : 'no earlier window to compare'}
                    </div>
                  </div>
                  <div className="panel stat-card">
                    {/* The one number here you cannot read off the charts: it is
                        how prompt bloat or a model change shows up. */}
                    <div className="stat-label">Tokens per request</div>
                    <div className="stat-value">
                      {usageTokensPerRequest === null ? '—' : usageTokensPerRequest.toLocaleString()}
                    </div>
                    <div className="stat-sub">
                      {usageTokensPerRequest === null
                        ? 'no counted batches in this window'
                        : `over ${usageCountedRequests.toLocaleString()} counted request${usageCountedRequests === 1 ? '' : 's'}`}
                    </div>
                  </div>
                  <div className="panel stat-card">
                    <div className="stat-label">Failure rate · {usageRange}d</div>
                    <div className="stat-value">
                      {usageFailureRate === null ? '—' : <>{usageFailureRate}<span className="unit">%</span></>}
                    </div>
                    <div className="stat-sub">
                      {usageFinished > 0
                        ? `${usageFailed.toLocaleString()} failed of ${usageFinished.toLocaleString()} finished`
                        : 'nothing finished in this window'}
                    </div>
                  </div>
                </div>

                <div className="section-title">Requests per day</div>
                <div className="panel chart-wrap" style={{ height: '240px', padding: '1rem 1rem 0.5rem' }}>
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart data={usageChartData} barCategoryGap="28%">
                      <XAxis dataKey="displayDate" stroke={CHART.axis} fontSize={12} tickLine={false} axisLine={false} minTickGap={24} />
                      <YAxis
                        stroke={CHART.axis}
                        fontSize={12}
                        tickLine={false}
                        axisLine={false}
                        width={44}
                        tickFormatter={(value) => value >= 1000 ? `${(value / 1000).toFixed(1)}k` : value}
                      />
                      <Tooltip
                        cursor={{ fill: 'rgba(255,255,255,0.04)' }}
                        contentStyle={{ backgroundColor: CHART.surface, border: '1px solid var(--border)', borderRadius: '8px' }}
                        labelStyle={{ color: 'var(--fg)' }}
                      />
                      <Bar dataKey="failed" name="Failed" stackId="r" fill={CHART.failed}
                           stroke={CHART.surface} strokeWidth={2} />
                      <Bar dataKey="successful" name="Successful" stackId="r" fill={CHART.ok}
                           stroke={CHART.surface} strokeWidth={2} radius={[4, 4, 0, 0]} />
                    </BarChart>
                  </ResponsiveContainer>
                </div>
                <div className="chart-legend">
                  <span className="lg"><span className="sw" style={{ background: CHART.ok }} /> Successful</span>
                  <span className="lg"><span className="sw" style={{ background: CHART.failed }} /> Failed</span>
                </div>

                {/* Its own chart, never a second axis on the one above: requests
                    and tokens differ by ~3 orders of magnitude, and sharing a
                    plot would invent a correlation that is not in the data. */}
                <div className="section-title">Tokens per day</div>
                <div className="panel chart-wrap" style={{ height: '240px', padding: '1rem 1rem 0.5rem' }}>
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart data={usageChartData} barCategoryGap="28%">
                      <XAxis dataKey="displayDate" stroke={CHART.axis} fontSize={12} tickLine={false} axisLine={false} minTickGap={24} />
                      <YAxis
                        stroke={CHART.axis}
                        fontSize={12}
                        tickLine={false}
                        axisLine={false}
                        width={44}
                        tickFormatter={(value) => value >= 1000 ? `${(value / 1000).toFixed(1)}k` : value}
                      />
                      <Tooltip
                        cursor={{ fill: 'rgba(255,255,255,0.04)' }}
                        contentStyle={{ backgroundColor: CHART.surface, border: '1px solid var(--border)', borderRadius: '8px' }}
                        labelStyle={{ color: 'var(--fg)' }}
                      />
                      <Bar dataKey="promptTokens" name="Prompt" stackId="t" fill={CHART.prompt}
                           stroke={CHART.surface} strokeWidth={2} />
                      <Bar dataKey="completionTokens" name="Completion" stackId="t" fill={CHART.completion}
                           stroke={CHART.surface} strokeWidth={2} radius={[4, 4, 0, 0]} />
                    </BarChart>
                  </ResponsiveContainer>
                </div>
                <div className="chart-legend">
                  <span className="lg"><span className="sw" style={{ background: CHART.prompt }} /> Prompt</span>
                  <span className="lg"><span className="sw" style={{ background: CHART.completion }} /> Completion</span>
                  {usageAwaiting > 0 && (
                    <span className="lg" style={{ color: 'var(--dimmer)' }}>
                      {usageAwaiting} completed batch{usageAwaiting === 1 ? '' : 'es'} in this window not counted yet
                    </span>
                  )}
                </div>

                {/* The table is the charts' accessible twin — every value is
                    readable without colour — so it stays, just not first. */}
                <button
                  className="btn"
                  style={{ marginTop: '1.5rem' }}
                  onClick={() => setShowUsageTable(v => !v)}
                >
                  {showUsageTable ? 'Hide' : 'Show'} daily table ({usageChartData.length} day{usageChartData.length === 1 ? '' : 's'})
                </button>

                {showUsageTable && (
                  <div className="panel" style={{ padding: '0.5rem', marginTop: '0.8rem' }}>
                    <div className="table-container">
                      <table>
                        <thead>
                          <tr>
                            <th>Date</th>
                            <th>Total Requests</th>
                            <th>Successful</th>
                            <th>Failed</th>
                            <th>Prompt Tokens</th>
                            <th>Completion Tokens</th>
                            <th>Total Tokens</th>
                          </tr>
                        </thead>
                        <tbody>
                          {usageChartData.slice().reverse().map((day, idx) => (
                            <tr key={idx}>
                              <td className="mono">{day.displayDate}</td>
                              <td>{day.requests.toLocaleString()}</td>
                              <td style={{ color: CHART.ok }}>{day.successful.toLocaleString()}</td>
                              <td style={{ color: CHART.failed }}>{day.failed.toLocaleString()}</td>
                              {day.counted === 0 ? (
                                /* No batch that day has been counted yet, so three
                                   zeroes would read as "this day cost nothing"
                                   rather than "nothing has been counted". */
                                <td className="dim" colSpan={3}>
                                  {day.awaitingCount > 0 ? 'not counted yet' : '—'}
                                </td>
                              ) : (
                                <>
                                  <td>{formatTokens(day.promptTokens)}</td>
                                  <td>{formatTokens(day.completionTokens)}</td>
                                  <td>
                                    {formatTokens(day.totalTokens)}
                                    {day.awaitingCount > 0 && (
                                      <span
                                        className="dim"
                                        title={`${day.awaitingCount} completed batch${day.awaitingCount === 1 ? '' : 'es'} on this day ${day.awaitingCount === 1 ? 'has' : 'have'} no token count yet — this total is partial.`}
                                      > +</span>
                                    )}
                                  </td>
                                </>
                              )}
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                )}
              </>
            )}
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

            {/* Org worker key parked — relocating to the provider portal */}
            {false && (
            <>
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
            </>
            )}
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

            {/* A3 — result of the local shape check, shown before Upload is pressed. */}
            {uploadFile && preflight && (
              <div
                className="teach"
                style={{
                  borderColor: preflight.problems.length ? 'rgba(248,81,73,0.35)' : 'rgba(74,222,128,0.3)',
                  background: preflight.problems.length ? 'rgba(248,81,73,0.06)' : 'rgba(74,222,128,0.05)',
                }}
              >
                <div className="teach-title" style={{ color: preflight.problems.length ? '#F85149' : 'var(--online)' }}>
                  {preflight.problems.length
                    ? `${preflight.problems.length} problem${preflight.problems.length === 1 ? '' : 's'} in the first ${preflight.checked} line${preflight.checked === 1 ? '' : 's'}`
                    : `First ${preflight.checked} line${preflight.checked === 1 ? '' : 's'} look right`}
                </div>

                {groupValidationErrors(preflight.problems).map(g => (
                  <div key={g.key} style={{ marginTop: '0.5rem' }}>
                    <div className="mono" style={{ fontSize: '0.72rem', color: '#F85149', fontWeight: 600 }}>
                      {g.code}{g.field && <> · {g.field}</>}
                      {' · '}{g.count} line{g.count === 1 ? '' : 's'}
                      {g.lines.length > 0 && <> (first: {g.lines.join(', ')})</>}
                    </div>
                    {g.fix && <div className="teach-body" style={{ margin: '0.15rem 0 0' }}>{g.fix}</div>}
                  </div>
                ))}

                <p className="teach-body" style={{ margin: '0.6rem 0 0' }}>
                  {preflight.models.length === 1 && <>Model in this file: <span className="mono">{preflight.models[0]}</span>. </>}
                  {preflight.truncated
                    ? 'Only the start of the file was checked, and catalogue membership is checked server-side — this is a head start, not a guarantee.'
                    : 'Catalogue membership is still checked server-side after upload.'}
                </p>
              </div>
            )}

            {/* A2 — say what belongs in the file before it is uploaded, not after
                the backend rejects it. Sits outside .dropzone: that element opens
                the file picker on click, which would swallow the copy button. */}
            <div className="teach">
              <div className="teach-title">What goes in the file</div>
              <p className="teach-body">
                One JSON object per line — the OpenAI batch format. Every line needs
                <span className="mono"> custom_id</span>,<span className="mono"> method</span>,
                <span className="mono"> url</span> and<span className="mono"> body</span>.
              </p>
              <CopyableCode
                code={sampleJsonl}
                display={sampleJsonl.split('\n')[0]}
                copyLabel={modelsLoaded ? 'Copy 3-line sample' : 'Loading catalogue…'}
                disabled={!modelsLoaded}
              />
              <p className="teach-body" style={{ marginTop: '0.7rem' }}>
                Every line must use the same <span className="mono">body.model</span>, and it must be an
                id from the{' '}
                <button className="teach-link" onClick={() => setActiveTab('models')}>Models tab</button>
                {!modelsLoaded
                  ? <> — reading this deployment&apos;s catalogue to name a real one.</>
                  : sampleModelId
                    ? <> — this deployment currently serves <span className="mono">{sampleModelId}</span>.</>
                    : <> — no models are published yet, so the sample above says <span className="mono">MODEL_ID</span>.</>}
                {' '}A file that mixes models fails validation.
              </p>
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
                          <span style={{ display: 'inline-flex', gap: '0.5rem' }}>
                            <button className="btn" onClick={() => handleDownloadFile(file.id, file.filename)}>
                              Download
                            </button>
                            <button className="btn" style={{ color: 'var(--danger)' }} onClick={() => handleDeleteFile(file.id)}>
                              Delete
                            </button>
                          </span>
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

          {/* ============ MODELS PAGE ============ */}
          <div className={`page-panel ${activeTab === 'models' ? 'active' : ''}`}>
            <h1 className="page-title">Models</h1>
            <p className="page-sub">
              The platform model catalogue. Use the <span className="mono">ID</span> as <span className="mono">body.model</span> in your batch JSONL — anything else fails validation.
            </p>

            <div className="panel" style={{ padding: '0.5rem' }}>
              <div className="table-container">
                <table>
                  <thead>
                    <tr>
                      <th>ID</th>
                      <th>Name</th>
                      <th>Runtime</th>
                      <th>Params</th>
                      <th>Quantization</th>
                      <th>Context</th>
                      <th>VRAM</th>
                    </tr>
                  </thead>
                  <tbody>
                    {modelCatalog.map(m => (
                      <tr key={m.id}>
                        <td className="mono">
                          <span style={{ display: 'inline-flex', alignItems: 'center', gap: '0.5rem' }}>
                            {m.id}
                            <button
                              className="btn"
                              title="Copy model id"
                              onClick={() => handleCopyModelId(m.id)}
                              style={{ padding: '2px 8px', fontSize: '0.72rem', color: copiedModelId === m.id ? '#00D287' : undefined }}
                            >
                              {copiedModelId === m.id ? 'Copied ✓' : 'Copy'}
                            </button>
                          </span>
                        </td>
                        <td>{m.display_name || '—'}</td>
                        <td className="dim">{m.runtime || '—'}</td>
                        <td className="dim">{m.parameter_size || '—'}</td>
                        <td className="dim">{m.quantization || '—'}</td>
                        <td className="dim">{m.context_length ? m.context_length.toLocaleString() : '—'}</td>
                        <td className="dim">{m.vram_gb ? `${m.vram_gb} GB` : '—'}</td>
                      </tr>
                    ))}
                    {modelCatalog.length === 0 && (
                      <tr>
                        <td colSpan={7} className="empty-hint">No models in the catalogue yet.</td>
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
              <button className="btn primary" onClick={() => { loadModels(); loadFiles(); setIsNewBatchModalOpen(true); }}>
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
                    {/* Counts come from the output file, so they land a moment
                        after the batch completes rather than with it. Say which
                        state this is instead of showing a zero either way. */}
                    <div className="progress-meta">
                      {batch.usage ? (
                        <>
                          <span>{formatTokens(batch.usage.prompt_tokens)} prompt · {formatTokens(batch.usage.completion_tokens)} completion</span>
                          <span>{formatTokens(batch.usage.total_tokens)} tokens</span>
                        </>
                      ) : (
                        <span>
                          {batch.status === 'completed' ? 'Token usage not counted yet' : 'Token usage available once the batch completes'}
                        </span>
                      )}
                    </div>
                    {batch.status === 'failed' && batch.error_details && (() => {
                      /* the validator sends a code and a field per error and
                         persists both; this used to render the message alone, so
                         the reader learned what broke but never what to do. */
                      let groups = [];
                      let total = 0;
                      let shown = 0;
                      let fallback = null;
                      try {
                        const parsed = JSON.parse(batch.error_details);
                        if (Array.isArray(parsed?.data)) {
                          groups = groupValidationErrors(parsed.data);
                          shown = parsed.data.length;
                          total = parsed.total_errors || parsed.data.length;
                        } else if (parsed?.error) {
                          fallback = parsed.error;
                          total = 1;
                        }
                      } catch {
                        fallback = batch.error_details;
                        total = 1;
                      }
                      return (
                        <div style={{ marginTop: '0.8rem', padding: '0.7rem 0.85rem', borderRadius: '8px', background: 'rgba(248,81,73,0.08)', border: '1px solid rgba(248,81,73,0.3)' }}>
                          <div style={{ color: '#F85149', fontSize: '0.75rem', fontWeight: 600, marginBottom: '0.45rem' }}>
                            Validation failed — {total} error{total === 1 ? '' : 's'}
                            {groups.length > 1 && <> across {groups.length} problems</>}
                          </div>

                          {fallback && (
                            <div className="mono" style={{ color: '#F85149', fontSize: '0.72rem', opacity: 0.9, overflowWrap: 'anywhere' }}>{fallback}</div>
                          )}

                          {groups.map(g => (
                            <div key={g.key} style={{ marginBottom: '0.55rem' }}>
                              <div className="mono" style={{ color: '#F85149', fontSize: '0.72rem', fontWeight: 600 }}>
                                {g.code}
                                {g.field && <> · {g.field}</>}
                                {' · '}
                                {g.count} line{g.count === 1 ? '' : 's'}
                                {g.lines.length > 0 && <> (first: {g.lines.join(', ')})</>}
                              </div>
                              <div className="mono" style={{ color: '#F85149', fontSize: '0.72rem', opacity: 0.85, overflowWrap: 'anywhere' }}>
                                {g.sample}
                              </div>
                              {g.fix && (
                                <div style={{ color: 'var(--dim)', fontSize: '0.72rem', marginTop: '0.2rem', lineHeight: 1.5 }}>
                                  {g.fix}
                                </div>
                              )}
                            </div>
                          ))}

                          {/* The validator stores at most MAX_STORED_ERRORS but
                              reports the true total, so on a badly broken file
                              the header outruns the rows beneath it. Say so,
                              rather than leaving the reader to check the sum. */}
                          {shown > 0 && total > shown && (
                            <div style={{ color: 'var(--dim)', fontSize: '0.72rem', lineHeight: 1.5 }}>
                              Listing the first {shown} of {total} errors — the rest were not recorded.
                              Fix these and re-upload to see whether any remain.
                            </div>
                          )}
                        </div>
                      );
                    })()}
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
                /* A1 — the empty state is the only place a first-time user is
                   guaranteed to look, so it carries the whole path to a result. */
                <TeachingEmptyState title="Submit your first batch" style={{ gridColumn: 'span 2' }}>
                  <TeachingStep n={1}>
                    Build a <span className="mono">.jsonl</span> file — one request per line.
                    <CopyableCode
                      code={sampleJsonl}
                      style={{ marginTop: '0.5rem' }}
                      copyLabel={modelsLoaded ? 'Copy' : 'Loading catalogue…'}
                      disabled={!modelsLoaded}
                      actions={
                        <button
                          className="btn"
                          disabled={!modelsLoaded}
                          style={{
                            padding: '2px 10px',
                            fontSize: '0.72rem',
                            opacity: modelsLoaded ? undefined : 0.45,
                            cursor: modelsLoaded ? undefined : 'not-allowed',
                          }}
                          onClick={handleDownloadSample}
                        >
                          Download sample
                        </button>
                      }
                    />
                  </TeachingStep>

                  <TeachingStep n={2}>
                    Upload it on the{' '}
                    <button className="teach-link" onClick={() => setActiveTab('files')}>Files tab</button>, or from
                    code. The file is stored first and handed back an id; the batch references that
                    id rather than carrying the bytes.
                    <CopyableCode code={buildSubmitSnippet(sampleModelId)} style={{ marginTop: '0.5rem' }} />
                  </TeachingStep>

                  <TeachingStep n={3}>
                    Or click <strong>New Batch</strong> above and pick the uploaded file.
                    Either way the job is <em>accepted first and validated after</em> — a malformed
                    file is taken, then fails a moment later, and the reason appears on its card here.
                  </TeachingStep>
                </TeachingEmptyState>
              )}
            </div>
          </div>

          {/* ============ SETTINGS PAGE ============ */}
          <div className={`page-panel ${activeTab === 'settings' ? 'active' : ''}`}>
            <h1 className="page-title">Settings</h1>
            <p className="page-sub">Manage your profile and password.</p>

            <div className="grid-2">
              {/* Org settings parked — relocating to the provider portal */}
              {false && (
              <div className="panel">
                <div className="section-title" style={{ marginTop: 0 }}>Organization details</div>
                <div className="field">
                  <label>Organization name</label>
                  <input value={settingsOrgName} onChange={e => setSettingsOrgName(e.target.value)} />
                </div>
                <button className="btn primary" onClick={handleSaveOrgSettings} disabled={settingsLoading}>Save org settings</button>
              </div>
              )}

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
                <button className="btn primary" onClick={handleSaveProfile} disabled={settingsLoading}>Save profile</button>
              </div>

              <div className="panel">
                <div className="section-title" style={{ marginTop: 0 }}>Password</div>
                <div className="field">
                  <label>Current password</label>
                  <input
                    type="password"
                    autoComplete="current-password"
                    value={pwOld}
                    onChange={e => setPwOld(e.target.value)}
                  />
                </div>
                <div className="field">
                  <label>New password</label>
                  <input
                    type="password"
                    autoComplete="new-password"
                    value={pwNew}
                    onChange={e => setPwNew(e.target.value)}
                  />
                </div>
                <div className="field">
                  <label>Confirm new password</label>
                  <input
                    type="password"
                    autoComplete="new-password"
                    value={pwConfirm}
                    onChange={e => setPwConfirm(e.target.value)}
                  />
                </div>
                {pwError && (
                  <p style={{ color: 'var(--danger)', fontSize: '0.85rem', marginBottom: '0.75rem' }}>{pwError}</p>
                )}
                {pwStatus && (
                  <p style={{ color: 'var(--accent)', fontSize: '0.85rem', marginBottom: '0.75rem' }}>{pwStatus}</p>
                )}
                <button className="btn primary" onClick={handleChangePassword} disabled={pwLoading}>
                  {pwLoading ? 'Changing…' : 'Change password'}
                </button>
              </div>
            </div>
            
            {settingsStatus && (
              <p style={{ color: 'var(--accent)', fontSize: '0.85rem', marginTop: '1rem' }}>{settingsStatus}</p>
            )}

            {/* Org members & invites parked — relocating to the provider portal */}
            {false && (
            <>
            <div className="section-title" style={{ marginTop: '2rem' }}>Members</div>
            <div className="panel" style={{ padding: '0.5rem', marginBottom: '2rem' }}>
              <div className="table-container">
                <table>
                  <thead>
                    <tr>
                      <th>Name / Email</th>
                      <th>Role</th>
                      <th>Joined</th>
                      <th>Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {orgMembers.map(m => (
                      <tr key={m.membership_id}>
                        <td>
                          {m.full_name} <span className="dim">({m.email})</span>
                        </td>
                        <td>
                          <select 
                            value={m.role} 
                            onChange={(e) => handleUpdateRole(m.user_id, e.target.value)}
                            className="btn" style={{ padding: '2px 8px', fontSize: '0.8rem', background: 'transparent' }}
                            disabled={settingsLoading || userProfile?.email === m.email}
                          >
                            <option value="owner">Owner</option>
                            <option value="admin">Admin</option>
                            <option value="viewer">Viewer</option>
                          </select>
                        </td>
                        <td className="dim">
                          {new Date(m.joined_at * 1000).toLocaleDateString()}
                        </td>
                        <td>
                          {userProfile?.email !== m.email && (
                            <button className="btn" style={{ color: 'var(--danger)' }} onClick={() => handleRemoveMember(m.user_id)} disabled={settingsLoading}>
                              Remove
                            </button>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>

            {/* INVITES SECTION */}
            <div className="section-title">Pending Invites</div>
            <div className="panel" style={{ padding: '0.5rem', marginBottom: '2rem' }}>
              <div className="table-container">
                <table>
                  <thead>
                    <tr>
                      <th>Email</th>
                      <th>Role</th>
                      <th>Expires</th>
                      <th>Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {orgInvites.map(inv => (
                      <tr key={inv.id}>
                        <td>{inv.email}</td>
                        <td><span className="badge">{inv.role}</span></td>
                        <td className="dim">{new Date(inv.expires_at * 1000).toLocaleDateString()}</td>
                        <td>
                          <button className="btn" style={{ color: 'var(--danger)' }} onClick={() => handleRevokeInvite(inv.token)} disabled={settingsLoading}>
                            Revoke
                          </button>
                        </td>
                      </tr>
                    ))}
                    {orgInvites.length === 0 && (
                      <tr>
                        <td colSpan={4} className="empty-hint">No pending invites.</td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
              
              <div style={{ padding: '1rem', borderTop: '1px solid var(--border)' }}>
                <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'flex-end' }}>
                  <div className="field" style={{ margin: 0, flex: 1 }}>
                    <label>Email Address</label>
                    <input type="email" placeholder="colleague@example.com" value={inviteEmail} onChange={e => setInviteEmail(e.target.value)} />
                  </div>
                  <div className="field" style={{ margin: 0, width: '120px' }}>
                    <label>Role</label>
                    <select value={inviteRole} onChange={e => setInviteRole(e.target.value)}>
                      <option value="viewer">Viewer</option>
                      <option value="admin">Admin</option>
                    </select>
                  </div>
                  <button className="btn primary" onClick={handleInvite} disabled={!inviteEmail || settingsLoading}>
                    Send Invite
                  </button>
                </div>
              </div>
            </div>
            </>
            )}

          </div>
        </div>
      </div>

      {/* ================= MODALS ================= */}
      {isCreateKeyModalOpen && (
        <div className="modal-overlay open">
          <div className={`modal ${revealedKey ? 'modal-wide' : ''}`}>
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

                {/* A8 — a key with nothing to do next teaches nothing. These two
                    are runnable as-is, and list the ids body.model will accept. */}
                <div className="teach" style={{ marginBottom: 0 }}>
                  <CopyableCode
                    code={buildKeyPython(revealedKey)}
                    label="Use it"
                    copyLabel="Copy Python"
                  />
                  <CopyableCode
                    code={buildKeyCurl(revealedKey)}
                    label="Or from a shell"
                    copyLabel="Copy curl"
                    style={{ marginTop: '0.9rem' }}
                  />

                  <p className="teach-body" style={{ marginTop: '0.9rem', marginBottom: 0 }}>
                    Both carry the key inline so they run as-is. Anywhere but local testing, keep it
                    out of source — <span className="mono">export SHESHNAG_API_KEY=…</span> and read it
                    from the environment instead.
                  </p>
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {false && isRegenModalOpen && (
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
              <label>Model (from file)</label>
              {(() => {
                const selectedFile = uploadedFiles.find(f => f.id === batchFileId);
                const fileModel = selectedFile?.model || null;
                const inCatalogue = fileModel && availableModels.includes(fileModel);
                if (selectedFile?.mixed_models) {
                  return (
                    <div style={{ padding: '0.6rem 0.8rem', borderRadius: '8px', background: 'rgba(248,81,73,0.08)', border: '1px solid rgba(248,81,73,0.3)', fontSize: '0.85rem', color: '#F85149' }}>
                      File mixes models ({selectedFile.mixed_models.join(', ')}) — a batch must use one model; validation will fail
                    </div>
                  );
                }
                return (
                  <div style={{ padding: '0.6rem 0.8rem', borderRadius: '8px', background: '#141720', border: '1px solid rgba(255,255,255,0.1)', fontSize: '0.9rem' }}>
                    {fileModel ? (
                      <>
                        <span style={{ color: '#fff', fontFamily: 'monospace' }}>{fileModel}</span>
                        {availableModels.length > 0 && (
                          inCatalogue ? (
                            <span style={{ color: '#00D287', marginLeft: '0.6rem', fontSize: '0.8rem' }}>✓ in catalogue</span>
                          ) : (
                            <span style={{ color: '#F85149', marginLeft: '0.6rem', fontSize: '0.8rem' }}>not in catalogue — validation will fail</span>
                          )
                        )}
                      </>
                    ) : (
                      <span style={{ color: 'var(--dim)' }}>Read from body.model in the JSONL during validation</span>
                    )}
                  </div>
                );
              })()}
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
