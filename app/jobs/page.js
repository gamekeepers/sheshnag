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

function StatusBadge({ status }) {
  const styles = {
    completed: 'bg-green-900 text-green-300',
    running: 'bg-blue-900 text-blue-300',
    queued: 'bg-yellow-900 text-yellow-300',
    failed: 'bg-red-900 text-red-300',
  };
  return (
    <span className={`text-xs px-2 py-0.5 rounded-full ${styles[status] || 'bg-gray-800 text-gray-300'}`}>
      {status}
    </span>
  );
}

export default function JobsPage() {
  const [jobs, setJobs] = useState([]);
  const [selectedJob, setSelectedJob] = useState(null);

  useEffect(() => {
    const stored = JSON.parse(localStorage.getItem('falcon_jobs') || '[]');
    setJobs(stored);
  }, []);

  function clearJobs() {
    localStorage.removeItem('falcon_jobs');
    setJobs([]);
    setSelectedJob(null);
  }

  return (
    <div className="flex h-screen bg-[#0a0a0a] text-gray-300 font-sans">

      {/* Sidebar */}
      <div className="w-48 bg-[#111] border-r border-[#2a2a2a] flex flex-col py-4 flex-shrink-0">
        <div className="px-4 pb-4 mb-3 border-b border-[#2a2a2a]">
          <Link href="/"><FalconLogo size={24} /></Link>
        </div>
        <p className="px-4 text-[10px] text-[#555] uppercase tracking-widest mb-2">Manage</p>
        <Link href="/" className="mx-2 px-3 py-2 rounded-md text-sm text-[#aaa] hover:bg-[#1e1e1e] hover:text-white">
          🏠 Home
        </Link>
        <div className="mx-2 px-3 py-2 rounded-md text-sm text-white bg-[#1e1e1e]">
          📋 Jobs
        </div>
        <Link href="/upload" className="mx-2 px-3 py-2 rounded-md text-sm text-[#aaa] hover:bg-[#1e1e1e] hover:text-white">
          📁 Upload
        </Link>
      </div>

      {/* Main */}
      <div className="flex-1 flex flex-col overflow-hidden">

        {/* Topbar */}
        <div className="flex items-center justify-between px-6 py-3 border-b border-[#2a2a2a]">
          <h1 className="text-white font-medium text-base">Jobs</h1>
          <div className="flex gap-2">
            {jobs.length > 0 && (
              <button
                onClick={clearJobs}
                className="px-3 py-1.5 text-xs border border-[#3a3a3a] rounded-md hover:bg-[#1e1e1e] text-[#666]"
              >
                Clear all
              </button>
            )}
            <Link
              href="/upload"
              className="flex items-center gap-1 px-3 py-1.5 text-sm border border-[#3a3a3a] rounded-md hover:bg-[#1e1e1e] text-white"
            >
              + Create
            </Link>
          </div>
        </div>

        {/* Content */}
        <div className="flex flex-1 overflow-hidden">

          {/* Jobs List */}
          <div className="w-[45%] border-r border-[#2a2a2a] overflow-y-auto">
            {jobs.length === 0 ? (
              <div className="flex flex-col items-center justify-center h-full text-center px-6">
                <p className="text-white text-sm font-medium mb-1">No batches found</p>
                <p className="text-[#666] text-xs mb-4">Submit a JSONL file to get started.</p>
                <Link href="/upload" className="flex items-center gap-1 px-3 py-1.5 text-sm border border-[#3a3a3a] rounded-md hover:bg-[#1e1e1e] text-white">
                  + Create
                </Link>
              </div>
            ) : (
              <>
                <div className="flex items-center px-4 py-2 border-b border-[#1e1e1e] text-[#444] text-xs uppercase tracking-widest">
                  <span className="w-16">ID</span>
                  <span className="flex-1">File</span>
                  <span className="w-24 text-center">Status</span>
                </div>
                {jobs.map((job) => (
                  <div
                    key={job.id}
                    onClick={() => setSelectedJob(job)}
                    className={`flex items-center px-4 py-3 border-b border-[#1a1a1a] cursor-pointer hover:bg-[#141414] transition-colors ${selectedJob?.id === job.id ? 'bg-[#1a1a1a]' : ''}`}
                  >
                    <span className="w-16 text-[#555] text-xs font-mono">#{job.id}</span>
                    <span className="flex-1 text-sm text-white truncate">{job.filename}</span>
                    <span className="w-24 flex justify-center">
                      <StatusBadge status={job.status} />
                    </span>
                  </div>
                ))}
              </>
            )}
          </div>

          {/* Detail Panel */}
          <div className="flex-1 overflow-y-auto">
            {selectedJob ? (
              <div className="p-8">
                <div className="flex items-center justify-between mb-8">
                  <div>
                    <p className="text-[#555] text-xs mb-1">Job ID</p>
                    <p className="text-white font-medium text-lg font-mono">#{selectedJob.id}</p>
                  </div>
                  <StatusBadge status={selectedJob.status} />
                </div>
                <div className="space-y-4">
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
                  <button className="mt-8 w-full flex items-center justify-center gap-2 bg-white text-black py-2.5 rounded-lg text-sm font-medium hover:bg-gray-100">
                    📥 Download outputs.jsonl
                  </button>
                )}
              </div>
            ) : (
              <div className="flex items-center justify-center h-full">
                <p className="text-[#444] text-sm">Select a batch to view details.</p>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}