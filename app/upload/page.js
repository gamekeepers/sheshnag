'use client';

import { useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';

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

export default function UploadPage() {
  const [showModal, setShowModal] = useState(false);
  const [file, setFile] = useState(null);
  const [status, setStatus] = useState('');
  const router = useRouter();

  function handleFileChange(e) {
    setFile(e.target.files[0]);
  }

async function handleSubmit() {
    if (!file) {
      setStatus('Please select a file first.');
      return;
    }

    setStatus('Uploading file...');

    try {
      // Step 1 — Upload the file
      const fileData = new FormData();
      fileData.append('file', file);

      const fileRes = await fetch(`${process.env.NEXT_PUBLIC_API_URL}/v1/files`, {
        method: 'POST',
        body: fileData,
      });

      if (!fileRes.ok) {
        setStatus('File upload failed.');
        return;
      }

      const fileJson = await fileRes.json();
console.log('File uploaded:', fileJson);
// Save filename mapping to localStorage
const fileMap = JSON.parse(localStorage.getItem('falcon_file_map') || '{}');
fileMap[fileJson.id] = file.name;
localStorage.setItem('falcon_file_map', JSON.stringify(fileMap));

      setStatus('Creating batch job...');

      // Step 2 — Create the batch job
      const batchRes = await fetch(`${process.env.NEXT_PUBLIC_API_URL}/v1/batches`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          input_file_id: fileJson.id,
          endpoint: '/v1/chat/completions',
          completion_window: '24h',
        }),
      });

      if (!batchRes.ok) {
        setStatus('Batch creation failed.');
        return;
      }

      const batchJson = await batchRes.json();
      console.log('Batch created:', batchJson);

      setStatus('Job submitted!');
      router.push('/jobs');

    } catch (err) {
      console.error(err);
      setStatus('Could not reach server.');
    }
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
        <Link href="/jobs" className="mx-2 px-3 py-2 rounded-md text-sm text-[#aaa] hover:bg-[#1e1e1e] hover:text-white">
          📋 Jobs
        </Link>
        <div className="mx-2 px-3 py-2 rounded-md text-sm text-white bg-[#1e1e1e]">
          📁 Upload
        </div>
      </div>

      {/* Main */}
      <div className="flex-1 flex flex-col overflow-hidden">
        <div className="flex items-center justify-between px-6 py-3 border-b border-[#2a2a2a]">
          <h1 className="text-white font-medium text-base">Upload Batch</h1>
          <button
            onClick={() => setShowModal(true)}
            className="flex items-center gap-1 px-3 py-1.5 text-sm border border-[#3a3a3a] rounded-md hover:bg-[#1e1e1e] text-white"
          >
            + Create
          </button>
        </div>

        <div className="flex-1 flex items-center justify-center">
          <div className="text-center">
            <div className="w-12 h-12 border border-[#2a2a2a] rounded-xl flex items-center justify-center mx-auto mb-4 text-2xl">
              📁
            </div>
            <p className="text-white text-sm font-medium mb-1">No batches found</p>
            <p className="text-[#555] text-xs mb-6">Upload a JSONL file to create your first batch job.</p>
            <button
              onClick={() => setShowModal(true)}
              className="flex items-center gap-1 px-4 py-2 text-sm border border-[#3a3a3a] rounded-md hover:bg-[#1e1e1e] text-white mx-auto"
            >
              + Create
            </button>
          </div>
        </div>
      </div>

      {/* Modal */}
      {showModal && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50">
          <div className="bg-[#1a1a1a] border border-[#2a2a2a] rounded-xl p-6 w-96">
            <h2 className="text-white font-medium text-base mb-1">Create batch job</h2>
            <p className="text-[#666] text-xs mb-5">Upload a .jsonl file to submit a new batch.</p>

            <label className="block border border-dashed border-[#3a3a3a] rounded-lg p-6 text-center cursor-pointer hover:border-[#555] mb-4">
              <div className="text-2xl mb-2">📁</div>
              <p className="text-[#666] text-xs">
                <span className="text-purple-400">Click to upload</span> or drag and drop
                <br />.jsonl files only
              </p>
              <input
                type="file"
                accept=".jsonl"
                onChange={handleFileChange}
                className="hidden"
              />
            </label>

            {file && (
              <p className="text-green-400 text-xs mb-3">✓ {file.name} selected</p>
            )}

            {status && (
              <p className="text-yellow-400 text-xs mb-3">{status}</p>
            )}

            <div className="flex justify-end gap-2 mt-4">
              <button
                onClick={() => { setShowModal(false); setStatus(''); setFile(null); }}
                className="px-4 py-1.5 text-sm border border-[#3a3a3a] rounded-md text-[#aaa] hover:bg-[#222]"
              >
                Cancel
              </button>
              <button
                onClick={handleSubmit}
                className="px-4 py-1.5 text-sm bg-white text-black rounded-md font-medium hover:bg-gray-200"
              >
                Submit job
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}