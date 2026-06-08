'use client';

import { useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';

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

const GPU_OPTIONS = ['NVIDIA A100 80GB', 'NVIDIA A100 40GB', 'NVIDIA H100 80GB', 'NVIDIA RTX 4090', 'NVIDIA RTX 3090', 'Other'];
const MODEL_OPTIONS = ['Llama 3.1 70B', 'Llama 3.1 8B', 'Mistral 7B', 'Mixtral 8x7B', 'Gemma 2 27B', 'Other'];

export default function ProviderSignupPage() {
  const [step, setStep] = useState(1);
  const [form, setForm] = useState({
    firstName: '', lastName: '', email: '', password: '', confirm: '',
    gpuType: '', gpuCount: '', modelName: '', maxConcurrency: '', endpoint: '', orgName: '',
  });
  const [error, setError] = useState('');
  const router = useRouter();

  function update(field, value) {
    setForm(prev => ({ ...prev, [field]: value }));
  }

  function handleStep1() {
    if (!form.firstName || !form.lastName || !form.email || !form.password || !form.confirm) {
      setError('Please fill in all fields.');
      return;
    }
    if (form.password !== form.confirm) {
      setError('Passwords do not match.');
      return;
    }
    setError('');
    setStep(2);
  }

  function handleStep2() {
    if (!form.gpuType || !form.gpuCount || !form.modelName || !form.maxConcurrency) {
      setError('Please fill in all GPU/model fields.');
      return;
    }
    setError('');
    setStep(3);
  }

  function handleSubmit() {
    if (!form.endpoint || !form.orgName) {
      setError('Please fill in all fields.');
      return;
    }
    setError('');
    setStep(4);
  }

  return (
    <div className="bg-[#0a0a0a] min-h-screen flex flex-col font-sans">

      {/* Top bar */}
      <div className="px-7 py-5 flex items-center justify-between border-b border-[#1a1a1a]">
        <Link href="/"><MoonknightLogo size={26} /></Link>
        <span className="text-xs px-3 py-1 rounded-full border border-[#2d5a2d] bg-[#1a3a1a] text-[#4ade80]">
          ⚡ Provider Portal
        </span>
      </div>

      <div className="flex-1 flex items-center justify-center px-6 py-10">
        <div className="w-full max-w-md">

          {/* Progress bar */}
          {step < 4 && (
            <div className="mb-8">
              <div className="flex justify-between text-[10px] text-[#555] mb-2 uppercase tracking-widest">
                <span className={step >= 1 ? 'text-[#4ade80]' : ''}>Account</span>
                <span className={step >= 2 ? 'text-[#4ade80]' : ''}>GPU Setup</span>
                <span className={step >= 3 ? 'text-[#4ade80]' : ''}>Configuration</span>
              </div>
              <div className="h-1 bg-[#1a1a1a] rounded-full overflow-hidden">
                <div
                  className="h-full bg-[#22c55e] rounded-full transition-all duration-500"
                  style={{ width: step === 1 ? '33%' : step === 2 ? '66%' : '100%' }}
                />
              </div>
            </div>
          )}

          {/* ── STEP 1: Account details ── */}
          {step === 1 && (
            <>
              <h1 className="text-white text-2xl font-medium text-center mb-1">Join as a Provider</h1>
              <p className="text-[#555] text-sm text-center mb-7">
                Contribute your GPU to the MOONKNIGHT network
              </p>

              <div className="flex gap-3 mb-4">
                <Field label="First name" value={form.firstName} onChange={v => update('firstName', v)} />
                <Field label="Last name" value={form.lastName} onChange={v => update('lastName', v)} />
              </div>

              <div className="mb-4">
                <Field label="Work email" value={form.email} onChange={v => update('email', v)} type="email" full />
              </div>

              <div className="mb-4">
                <Field label="Organisation / Company" value={form.orgName} onChange={v => update('orgName', v)} full />
              </div>

              <div className="mb-4">
                <Field label="Password" value={form.password} onChange={v => update('password', v)} type="password" full />
              </div>

              <div className="mb-4">
                <Field label="Confirm password" value={form.confirm} onChange={v => update('confirm', v)} type="password" full />
              </div>

              {error && <p className="text-red-400 text-xs mb-3">{error}</p>}

              <button
                onClick={handleStep1}
                className="w-full bg-[#22c55e] text-black py-3 rounded-full text-sm font-semibold hover:bg-[#16a34a] transition-colors"
              >
                Continue →
              </button>

              <p className="text-center text-[#666] text-sm mt-5">
                Already a provider?{' '}
                <Link href="/provider/login" className="text-[#4ade80]">Log in</Link>
              </p>
            </>
          )}

          {/* ── STEP 2: GPU Setup ── */}
          {step === 2 && (
            <>
              <h1 className="text-white text-2xl font-medium text-center mb-1">GPU Setup</h1>
              <p className="text-[#555] text-sm text-center mb-7">
                Tell us about your hardware
              </p>

              {/* GPU Type */}
              <div className="mb-4">
                <label className="text-[11px] text-[#4ade80] block mb-2">GPU Type</label>
                <div className="grid grid-cols-2 gap-2">
                  {GPU_OPTIONS.map(opt => (
                    <button
                      key={opt}
                      onClick={() => update('gpuType', opt)}
                      className={`text-xs py-2.5 px-3 rounded-lg border text-left transition-all ${form.gpuType === opt ? 'border-[#22c55e] bg-[#1a3a1a] text-white' : 'border-[#2a2a2a] text-[#666] hover:border-[#444]'}`}
                    >
                      {opt}
                    </button>
                  ))}
                </div>
              </div>

              {/* GPU Count */}
              <div className="mb-4">
                <Field label="Number of GPUs" value={form.gpuCount} onChange={v => update('gpuCount', v)} type="number" full green />
              </div>

              {/* Model */}
              <div className="mb-4">
                <label className="text-[11px] text-[#4ade80] block mb-2">Model to serve</label>
                <div className="grid grid-cols-2 gap-2">
                  {MODEL_OPTIONS.map(opt => (
                    <button
                      key={opt}
                      onClick={() => update('modelName', opt)}
                      className={`text-xs py-2.5 px-3 rounded-lg border text-left transition-all ${form.modelName === opt ? 'border-[#22c55e] bg-[#1a3a1a] text-white' : 'border-[#2a2a2a] text-[#666] hover:border-[#444]'}`}
                    >
                      {opt}
                    </button>
                  ))}
                </div>
              </div>

              {/* Max Concurrency */}
              <div className="mb-4">
                <Field label="Max concurrency (parallel jobs)" value={form.maxConcurrency} onChange={v => update('maxConcurrency', v)} type="number" full green />
              </div>

              {error && <p className="text-red-400 text-xs mb-3">{error}</p>}

              <div className="flex gap-3">
                <button
                  onClick={() => { setStep(1); setError(''); }}
                  className="flex-1 py-3 rounded-full text-sm border border-[#2a2a2a] text-[#888] hover:bg-[#1a1a1a]"
                >
                  ← Back
                </button>
                <button
                  onClick={handleStep2}
                  className="flex-1 bg-[#22c55e] text-black py-3 rounded-full text-sm font-semibold hover:bg-[#16a34a] transition-colors"
                >
                  Continue →
                </button>
              </div>
            </>
          )}

          {/* ── STEP 3: Endpoint config ── */}
          {step === 3 && (
            <>
              <h1 className="text-white text-2xl font-medium text-center mb-1">Configuration</h1>
              <p className="text-[#555] text-sm text-center mb-7">
                Connect your vLLM server endpoint
              </p>

              <div className="mb-4">
                <Field
                  label="vLLM Endpoint URL"
                  value={form.endpoint}
                  onChange={v => update('endpoint', v)}
                  placeholder="https://your-endpoint.cfargotunnel.com"
                  full green
                />
              </div>

              {/* Summary box */}
              <div className="border border-[#2a2a2a] rounded-xl p-4 mb-6 space-y-2 bg-[#111]">
                <p className="text-[#555] text-xs uppercase tracking-widest mb-3">Review your setup</p>
                <SummaryRow label="Name" value={`${form.firstName} ${form.lastName}`} />
                <SummaryRow label="Email" value={form.email} />
                <SummaryRow label="GPU" value={`${form.gpuCount}× ${form.gpuType}`} />
                <SummaryRow label="Model" value={form.modelName} />
                <SummaryRow label="Max concurrency" value={form.maxConcurrency} />
              </div>

              {error && <p className="text-red-400 text-xs mb-3">{error}</p>}

              <p className="text-[#444] text-xs text-center mb-5 leading-relaxed">
                By registering you agree to our{' '}
                <Link href="#" className="underline text-[#555]">Provider Terms</Link>. Your application will be reviewed by the platform admin.
              </p>

              <div className="flex gap-3">
                <button
                  onClick={() => { setStep(2); setError(''); }}
                  className="flex-1 py-3 rounded-full text-sm border border-[#2a2a2a] text-[#888] hover:bg-[#1a1a1a]"
                >
                  ← Back
                </button>
                <button
                  onClick={handleSubmit}
                  className="flex-1 bg-[#22c55e] text-black py-3 rounded-full text-sm font-semibold hover:bg-[#16a34a] transition-colors"
                >
                  Submit application
                </button>
              </div>
            </>
          )}

          {/* ── STEP 4: Success ── */}
          {step === 4 && (
            <div className="text-center">
              <div className="text-5xl mb-5">✅</div>
              <h1 className="text-white text-2xl font-medium mb-2">Application submitted!</h1>
              <p className="text-[#555] text-sm mb-3 leading-relaxed max-w-xs mx-auto">
                Your provider application has been sent for review. You'll receive an email at{' '}
                <span className="text-white">{form.email}</span> once approved.
              </p>
              <div className="border border-[#2d5a2d] bg-[#1a3a1a] rounded-xl p-4 mb-8 text-left space-y-1 max-w-xs mx-auto">
                <SummaryRow label="GPU" value={`${form.gpuCount}× ${form.gpuType}`} green />
                <SummaryRow label="Model" value={form.modelName} green />
                <SummaryRow label="Concurrency" value={form.maxConcurrency} green />
              </div>
              <Link
                href="/provider/login"
                className="inline-block bg-[#22c55e] text-black px-8 py-3 rounded-full text-sm font-semibold hover:bg-[#16a34a]"
              >
                Go to Provider Login
              </Link>
            </div>
          )}

        </div>
      </div>

      {/* Footer */}
      <div className="text-center py-4 border-t border-[#1a1a1a]">
        <Link href="#" className="text-[#444] text-xs underline mx-2">Terms of Use</Link>
        <span className="text-[#444] text-xs">|</span>
        <Link href="#" className="text-[#444] text-xs underline mx-2">Privacy Policy</Link>
      </div>
    </div>
  );
}

function Field({ label, value, onChange, type = 'text', full, placeholder, green }) {
  const color = green ? '#4ade80' : '#2d7dd6';
  const border = green ? '#2d5a2d' : '#2d7dd6';
  const focusBorder = green ? '#4ade80' : '#4d9cf8';
  return (
    <div className={`relative ${full ? 'w-full' : 'flex-1'}`}>
      <label style={{ color }} className="absolute -top-2 left-3 bg-[#0a0a0a] px-1 text-[11px]">
        {label}
      </label>
      <input
        type={type}
        value={value}
        onChange={e => onChange(e.target.value)}
        placeholder={placeholder || ''}
        style={{ borderColor: border }}
        className="w-full bg-transparent border rounded-full px-4 py-3 text-white text-sm outline-none placeholder-[#333]"
        onFocus={e => e.target.style.borderColor = focusBorder}
        onBlur={e => e.target.style.borderColor = border}
      />
    </div>
  );
}

function SummaryRow({ label, value, green }) {
  return (
    <div className="flex justify-between items-center py-0.5">
      <span className="text-[#555] text-xs">{label}</span>
      <span className={`text-xs ${green ? 'text-[#4ade80]' : 'text-white'}`}>{value}</span>
    </div>
  );
}
