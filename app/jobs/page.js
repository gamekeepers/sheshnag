'use client';

import { useState, useEffect, useRef, useCallback } from 'react';
import Link from 'next/link';

const BACKEND = process.env.NEXT_PUBLIC_BACKEND_URL || 'http://localhost:8000';

/* Terminal statuses — leaf states in backend VALID_TRANSITIONS (empty allowed list) */
const TERMINAL_STATUSES = ['completed', 'failed'];

function MoonknightLogo({ size = 28 }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: size / 4 }}>
      <svg width={size} height={size} viewBox="0 0 32 32" fill="none">
        <circle cx="16" cy="16" r="12" fill="#fff" />
        <circle cx="20" cy="13" r="10" fill="#0a0a0a" />
      </svg>
      <span style={{ color: '#fff', fontSize: size * 0.45, fontWeight: 500, letterSpacing: '0.12em' }}>MOONKNIGHT</span>
    </div>
  );
}



function StatusBadge({ status }) {
  const styles = {
    completed: 'bg-green-900 text-green-400',
    running: 'bg-blue-900 text-blue-400',
    in_progress: 'bg-blue-900 text-blue-400',
    queued: 'bg-yellow-900 text-yellow-400',
    validated: 'bg-green-900 text-green-400',
    failed: 'bg-red-900 text-red-400',
    validating: 'bg-yellow-900 text-yellow-400 animate-pulse',
  };
  return (
    <span className={`text-xs px-2 py-0.5 rounded-full ${styles[status] || 'bg-gray-800 text-gray-300'}`}>
      {status.replace('_', ' ')}
    </span>
  );
}

export default function JobsPage() {
  const [jobs, setJobs] = useState([]);
  const [selectedJob, setSelectedJob] = useState(null);
  const [loading, setLoading] = useState(true);
  const [userName, setUserName] = useState('');

  useEffect(() => {
    const userRaw = localStorage.getItem('mk_user');
    if (userRaw) {
      try { setUserName(JSON.parse(userRaw).full_name || JSON.parse(userRaw).email || ''); } catch {}
    }
  }, []);

  /* ---- refs for the polling loop ---- */
  const pollTimerRef = useRef(null);        // holds the setTimeout handle
  const initialFetchDone = useRef(false);   // guard against loading flicker

  /* ---- fetch helper (returns raw backend data) ---- */
  const fetchJobs = useCallback(async () => {
    try {
      const controller = new AbortController();
      const token = localStorage.getItem('mk_token') || '';
      const res = await fetch(`${BACKEND}/v1/batches`, {
        headers: {
          'ngrok-skip-browser-warning': 'true',
          'Authorization': `Bearer ${token}`,
        },
        signal: controller.signal,
      });
      const data = await res.json();
      const raw = data.data || [];

      const fileMap = JSON.parse(localStorage.getItem('moonknight_file_map') || '{}');
      const mapped = raw.map((job) => ({
        id: job.id,
        filename: fileMap[job.input_file_id] || job.input_file_id || 'unknown.jsonl',
        status: job.status,
        error_details: job.error_details || null,
        created_at: job.created_at ? new Date(job.created_at * 1000).toLocaleString('en-IN') : 'N/A',
        total: job.request_counts?.total || 0,
        done: job.request_counts?.completed || 0,
      }));

      /* merge by id so React doesn't diff the entire array on every poll */
      setJobs(prev => {
        const map = new Map();
        prev.forEach(j => map.set(j.id, j));
        mapped.forEach(j => map.set(j.id, j));       // overwrite or insert
        // remove jobs that no longer exist
        const aliveIds = new Set(mapped.map(j => j.id));
        map.forEach((v, k) => { if (!aliveIds.has(k)) map.delete(k); });
        return [...map.values()];
      });

      /* preserve user's selection — only fall back to first job when nothing matches */
      setSelectedJob(old => mapped.find(j => j.id === old?.id) ?? (mapped[0] || null));

      /* silence the loading spinner after the first successful fetch */
      if (!initialFetchDone.current) {
        initialFetchDone.current = true;
        setLoading(false);
      }

      return mapped;
    } catch (err) {
      if (err.name !== 'AbortError') {
        console.error('Failed to fetch jobs:', err);
      }
      /* ensure spinner goes away on hard failure during initial load */
      if (!initialFetchDone.current) {
        initialFetchDone.current = true;
        setLoading(false);
      }
      return null;
    }
  }, []);

  /* ---- polling loop (recursive setTimeout, stops when all terminal) ---- */
  const ACTIVE_INTERVAL = 7000;

  useEffect(() => {
    let stopped = false;

    const poll = async () => {
      const result = await fetchJobs();

      if (stopped) return;

      /* stop when all jobs are terminal */
      const hasActive = result && result.some(j => !TERMINAL_STATUSES.includes(j.status));
      if (!hasActive) return;

      pollTimerRef.current = setTimeout(poll, ACTIVE_INTERVAL);
    };

    poll();

    return () => {
      stopped = true;
      if (pollTimerRef.current) clearTimeout(pollTimerRef.current);
    };
  }, [fetchJobs]);

  /* ---- SSE subscription for batches still validating ---- */
  const validatingIdsRef = useRef(new Set());

  useEffect(() => {
    const currentIds = new Set(jobs.filter(j => j.status === 'validating').map(j => j.id));
    const prevIds = validatingIdsRef.current;

    // Only recreate SSE connections when the set of validating job IDs changes
    const idsChanged = currentIds.size !== prevIds.size ||
      [...currentIds].some(id => !prevIds.has(id));

    if (!idsChanged) return;

    validatingIdsRef.current = currentIds;
    const eventSources = [];

    for (const job of jobs) {
      if (job.status !== 'validating') continue;

      try {
        const es = new EventSource(`${BACKEND}/v1/batches/${job.id}/events`);
        es.addEventListener('validation_complete', () => {
          fetchJobs();
          es.close();
        });
        es.addEventListener('error', () => {
          es.close();
        });
        eventSources.push(es);
      } catch (err) {
        console.warn('SSE not available for batch', job.id, err);
      }
    }

    return () => { eventSources.forEach((es) => es.close()); };
  }, [jobs, fetchJobs]);

  const total = jobs.length;
  const completed = jobs.filter(j => j.status === 'completed').length;
  const running = jobs.filter(j => j.status === 'running').length;
  const failed = jobs.filter(j => j.status === 'failed').length;

  return (
    <div className="flex h-screen bg-[#0a0a0a] text-gray-300 font-sans">

      {/* Sidebar */}
      <div className="w-48 bg-[#111] border-r border-[#2a2a2a] flex flex-col py-4 flex-shrink-0">
        <div className="px-4 pb-4 mb-3 border-b border-[#2a2a2a]">
          <Link href="/"><MoonknightLogo size={24} /></Link>
        </div>
        <div className="flex-1">
          <p className="px-4 text-[10px] text-[#555] uppercase tracking-widest mb-2">Manage</p>
          <Link href="/" className="block mx-2 px-3 py-2 rounded-md text-sm text-[#aaa] hover:bg-[#1e1e1e] hover:text-white">🏠 Home</Link>
          <div className="mx-2 px-3 py-2 rounded-md text-sm text-white bg-[#1e1e1e]">📋 Jobs</div>
          <Link href="/upload" className="block mx-2 px-3 py-2 rounded-md text-sm text-[#aaa] hover:bg-[#1e1e1e] hover:text-white">📁 Upload</Link>
        </div>
        <div className="px-4 pt-4 border-t border-[#2a2a2a] mt-auto">
          <button onClick={() => { localStorage.removeItem('mk_token'); localStorage.removeItem('mk_user'); router.push('/login'); }} className="flex items-center gap-2 w-full px-3 py-2 rounded-md text-sm text-red-400 hover:bg-[#1e1e1e]">🚪 Logout</button>
        </div>
      </div>

      {/* Main */}
      <div className="flex-1 flex flex-col overflow-hidden">

        {/* Topbar */}
        <div className="flex items-center justify-between px-6 py-3 border-b border-[#2a2a2a]">
          <h1 className="text-white font-medium text-base">Jobs</h1>
          <div className="flex items-center gap-3">
            {userName && (
              <div className="flex items-center gap-2">
                <div style={{
                  width: '28px', height: '28px', borderRadius: '50%',
                  backgroundColor: '#1e3a5f', border: '1px solid #2d5a8a',
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  fontSize: '12px', fontWeight: 600, color: '#60a5fa',
                }}>
                  {userName.charAt(0).toUpperCase()}
                </div>
                <span className="text-sm text-[#aaa]">{userName}</span>
              </div>
            )}
            <Link href="/upload" className="flex items-center gap-1 px-3 py-1.5 text-sm border border-[#3a3a3a] rounded-md hover:bg-[#1e1e1e] text-white">
              + New Job
            </Link>
          </div>
        </div>

        {/* Stats Cards */}
        <div className="grid grid-cols-4 gap-3 px-6 py-4 border-b border-[#1e1e1e]">
          <div className="bg-[#111] border border-[#2a2a2a] rounded-lg p-4">
            <p className="text-[#555] text-xs uppercase tracking-wider mb-2">Total jobs</p>
            <p className="text-white text-2xl font-medium">{total}</p>
          </div>
          <div className="bg-[#111] border border-[#2a2a2a] rounded-lg p-4">
            <p className="text-[#555] text-xs uppercase tracking-wider mb-2">Completed</p>
            <p className="text-green-400 text-2xl font-medium">{completed}</p>
          </div>
          <div className="bg-[#111] border border-[#2a2a2a] rounded-lg p-4">
            <p className="text-[#555] text-xs uppercase tracking-wider mb-2">Running</p>
            <p className="text-blue-400 text-2xl font-medium">{running}</p>
          </div>
          <div className="bg-[#111] border border-[#2a2a2a] rounded-lg p-4">
            <p className="text-[#555] text-xs uppercase tracking-wider mb-2">Failed</p>
            <p className="text-red-400 text-2xl font-medium">{failed}</p>
          </div>
        </div>

        {/* Content */}
        <div className="flex flex-1 overflow-hidden">

          {/* Jobs List */}
          <div className="w-[45%] border-r border-[#2a2a2a] overflow-y-auto">
            <div className="flex items-center px-4 py-2 border-b border-[#1e1e1e] text-[#444] text-xs uppercase tracking-widest">
              <span className="w-14">ID</span>
              <span className="flex-1">File</span>
              <span className="w-24 text-center">Status</span>
            </div>

            {loading ? (
              <div className="flex flex-col items-center justify-center py-20 text-[#444]">
                <div style={{ width: '28px', height: '28px', border: '2px solid #333', borderTopColor: '#fff', borderRadius: '50%', animation: 'spin 0.8s linear infinite' }} />
                <p className="text-xs mt-3">Loading your jobs...</p>
              </div>
            ) : jobs.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-20 text-center px-6">
                <div className="text-4xl mb-4">📭</div>
                <p className="text-white text-sm font-medium mb-1">No jobs yet</p>
                <p className="text-[#555] text-xs mb-5">Upload a JSONL file to submit your first batch job</p>
                <Link href="/upload" className="px-4 py-2 bg-white text-black text-xs rounded-full font-medium hover:bg-gray-100">
                  + Upload your first job
                </Link>
              </div>
            ) : (
              jobs.map((job) => (
                <div
                  key={job.id}
                  onClick={() => setSelectedJob(job)}
                  className={`flex items-center px-4 py-3 border-b border-[#1a1a1a] cursor-pointer hover:bg-[#141414] transition-colors ${selectedJob?.id === job.id ? 'bg-[#1a1a1a]' : ''}`}
                >
                  <span className="w-14 text-[#555] text-xs font-mono">#{String(job.id).slice(-4)}</span>
                  <span className="flex-1 text-sm text-white truncate pr-2">{job.filename}</span>
                  <span className="w-24 flex justify-center"><StatusBadge status={job.status} /></span>
                </div>
              ))
            )}
          </div>

          {/* Detail Panel */}
          <div className="flex-1 overflow-y-auto">
            {selectedJob ? (
              <div className="p-8">
                <div className="flex items-center justify-between mb-6">
                  <div>
                    <p className="text-[#555] text-xs mb-1">Job ID</p>
                    <p className="text-white font-medium text-lg font-mono">#{selectedJob.id}</p>
                  </div>
                  <StatusBadge status={selectedJob.status} />
                </div>

                {/* Progress bar for running */}
                {selectedJob.status === 'running' && (
                  <div className="mb-6">
                    <div className="flex justify-between text-xs text-[#555] mb-2">
                      <span>Progress</span>
                      <span>{selectedJob.done}/{selectedJob.total} prompts</span>
                    </div>
                    <div className="w-full bg-[#1e1e1e] rounded-full h-1.5">
                      <div
                        className="bg-blue-500 h-1.5 rounded-full"
                        style={{ width: `${(selectedJob.done / selectedJob.total) * 100}%` }}
                      />
                    </div>
                  </div>
                )}

                {/* Stats */}
                <div className="grid grid-cols-3 gap-3 mb-6">
                  <div className="border border-[#1e1e1e] rounded-lg p-3 text-center">
                    <p className="text-white text-lg font-medium">{selectedJob.total}</p>
                    <p className="text-[#555] text-xs mt-0.5">Total</p>
                  </div>
                  <div className="border border-[#1e1e1e] rounded-lg p-3 text-center">
                    <p className="text-green-400 text-lg font-medium">{selectedJob.done}</p>
                    <p className="text-[#555] text-xs mt-0.5">Done</p>
                  </div>
                  <div className="border border-[#1e1e1e] rounded-lg p-3 text-center">
                    <p className="text-yellow-400 text-lg font-medium">{Number(selectedJob?.total || 0) - Number(selectedJob?.done || 0)}</p>
                    <p className="text-[#555] text-xs mt-0.5">Remaining</p>
                  </div>
                </div>

                <div className="space-y-3">
                  <div className="border border-[#1e1e1e] rounded-lg p-4">
                    <p className="text-[#555] text-xs mb-1">Filename</p>
                    <p className="text-white text-sm">{selectedJob.filename}</p>
                  </div>
                  <div className="border border-[#1e1e1e] rounded-lg p-4">
                    <p className="text-[#555] text-xs mb-1">Created</p>
                    <p className="text-white text-sm">{selectedJob.created_at}</p>
                  </div>
                  <div className="border border-[#1e1e1e] rounded-lg p-4">
                    <p className="text-[#555] text-xs mb-1">Status</p>
                    <p className="text-white text-sm capitalize">{selectedJob.status}</p>
                  </div>
                </div>

                {selectedJob.status === 'completed' && (
                  <button className="mt-6 w-full flex items-center justify-center gap-2 bg-white text-black py-2.5 rounded-lg text-sm font-medium hover:bg-gray-100">
                    📥 Download outputs.jsonl
                  </button>
                )}

                {selectedJob.status === 'failed' && (
                  <div className="mt-6 border border-red-900 rounded-lg p-4">
                    <p className="text-red-400 text-xs font-medium mb-2">Job failed</p>
                    {selectedJob.error_details ? (() => {
                      try {
                        const details = JSON.parse(selectedJob.error_details);
                        if (details.data && details.data.length) {
                          return (
                            <div className="max-h-48 overflow-y-auto space-y-1">
                              {details.data.map((err, i) => {
                                // Handle new structured format (code + message) or legacy format (errors array)
                                const content = err.code
                                  ? <>
                                      <code className="text-yellow-300">{err.code}</code>{' '}
                                      {err.message}
                                    </>
                                  : err.errors.join('; ');
                                return (
                                  <div key={i} className="text-[#999] text-xs font-mono">
                                    <span className="text-red-300">Line {err.line}:</span>{' '}
                                    {content}
                                  </div>
                                );
                              })}
                              {details.total_errors > details.data.length && (
                                <p className="text-[#555] text-xs mt-2">
                                  …and {details.total_errors - details.data.length} more error(s)
                                </p>
                              )}
                            </div>
                          );
                        }
                      } catch {
                        /* fall through to raw display */
                      }
                      return <p className="text-[#666] text-xs font-mono">{selectedJob.error_details}</p>;
                    })() : (
                      <p className="text-[#666] text-xs">The input file may have been malformed. Please check your JSONL format and try again.</p>
                    )}
                  </div>
                )}
              </div>
            ) : (
              <div className="flex items-center justify-center h-full">
                <p className="text-[#444] text-sm">Select a job to view details.</p>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
