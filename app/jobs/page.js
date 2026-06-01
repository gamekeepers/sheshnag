'use client';

import { useState, useEffect } from 'react';
import Link from 'next/link';

function FalconLogo({ size = 28 }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: size / 4 }}>
      <svg width={size} height={size} viewBox="0 0 36 36" fill="none">
        <line x1="4" y1="28" x2="20" y2="8" stroke="#fff" strokeWidth="2.5" strokeLinecap="round"/>
        <line x1="12" y1="28" x2="28" y2="8" stroke="#fff" strokeWidth="2.5" strokeLinecap="round" opacity="0.5"/>
        <line x1="20" y1="28" x2="32" y2="12" stroke="#fff" strokeWidth="2.5" strokeLinecap="round" opacity="0.25"/>
      </svg>
      <span style={{ color: '#fff', fontSize: size * 0.55, fontWeight: 500, letterSpacing: '0.15em' }}>FALCON</span>
    </div>
  );
}

const DEMO_JOBS = [
  { id: 1, filename: 'prompts_batch1.jsonl', status: 'completed', created_at: '24 May 2026, 08:00 AM', total: 120, done: 120 },
  { id: 2, filename: 'qa_test_run.jsonl', status: 'running', created_at: '24 May 2026, 09:30 AM', total: 80, done: 45 },
  { id: 3, filename: 'large_batch.jsonl', status: 'queued', created_at: '24 May 2026, 10:15 AM', total: 500, done: 0 },
  { id: 4, filename: 'marketing_prompts.jsonl', status: 'completed', created_at: '24 May 2026, 11:00 AM', total: 60, done: 60 },
  { id: 5, filename: 'broken_input.jsonl', status: 'failed', created_at: '24 May 2026, 11:45 AM', total: 30, done: 0 },
];

function StatusBadge({ status }) {
  const styles = {
    completed: 'bg-green-900 text-green-400',
    running: 'bg-blue-900 text-blue-400',
    queued: 'bg-yellow-900 text-yellow-400',
    failed: 'bg-red-900 text-red-400',
  };
  return (
    <span className={`text-xs px-2 py-0.5 rounded-full ${styles[status] || 'bg-gray-800 text-gray-300'}`}>
      {status}
    </span>
  );
}

export default function JobsPage() {
  const [jobs, setJobs] = useState(DEMO_JOBS);
  const [selectedJob, setSelectedJob] = useState(DEMO_JOBS[0]);

  useEffect(() => {
    const stored = JSON.parse(localStorage.getItem('falcon_jobs') || '[]');
    if (stored.length > 0) {
  const merged = [...DEMO_JOBS, ...stored.map((j, i) => ({ ...j, id: DEMO_JOBS.length + i + 1 }))];
  setJobs(merged);
}
  }, []);

  const total = jobs.length;
  const completed = jobs.filter(j => j.status === 'completed').length;
  const running = jobs.filter(j => j.status === 'running').length;
  const failed = jobs.filter(j => j.status === 'failed').length;

  return (
    <div className="flex h-screen bg-[#0a0a0a] text-gray-300 font-sans">

      {/* Sidebar */}
      <div className="w-48 bg-[#111] border-r border-[#2a2a2a] flex flex-col py-4 flex-shrink-0">
        <div className="px-4 pb-4 mb-3 border-b border-[#2a2a2a]">
          <Link href="/"><FalconLogo size={24} /></Link>
        </div>
        <p className="px-4 text-[10px] text-[#555] uppercase tracking-widest mb-2">Manage</p>
        <Link href="/" className="mx-2 px-3 py-2 rounded-md text-sm text-[#aaa] hover:bg-[#1e1e1e] hover:text-white">🏠 Home</Link>
        <div className="mx-2 px-3 py-2 rounded-md text-sm text-white bg-[#1e1e1e]">📋 Jobs</div>
        <Link href="/upload" className="mx-2 px-3 py-2 rounded-md text-sm text-[#aaa] hover:bg-[#1e1e1e] hover:text-white">📁 Upload</Link>
      </div>

      {/* Main */}
      <div className="flex-1 flex flex-col overflow-hidden">

        {/* Topbar */}
        <div className="flex items-center justify-between px-6 py-3 border-b border-[#2a2a2a]">
          <h1 className="text-white font-medium text-base">Jobs</h1>
          <Link href="/upload" className="flex items-center gap-1 px-3 py-1.5 text-sm border border-[#3a3a3a] rounded-md hover:bg-[#1e1e1e] text-white">
            + Create
          </Link>
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
            {jobs.map((job) => (
              <div
                key={job.id}
                onClick={() => setSelectedJob(job)}
                className={`flex items-center px-4 py-3 border-b border-[#1a1a1a] cursor-pointer hover:bg-[#141414] transition-colors ${selectedJob?.id === job.id ? 'bg-[#1a1a1a]' : ''}`}
              >
                <span className="w-14 text-[#555] text-xs font-mono">#{job.id}</span>
                <span className="flex-1 text-sm text-white truncate pr-2">{job.filename}</span>
                <span className="w-24 flex justify-center"><StatusBadge status={job.status} /></span>
              </div>
            ))}
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
                    <p className="text-yellow-400 text-lg font-medium">{selectedJob.total - selectedJob.done}</p>
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
                    <p className="text-red-400 text-xs font-medium mb-1">Job failed</p>
                    <p className="text-[#666] text-xs">The input file may have been malformed. Please check your JSONL format and try again.</p>
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