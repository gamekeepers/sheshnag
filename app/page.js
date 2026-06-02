import Link from 'next/link';

function FalconLogo({ size = 32 }) {
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

export default function Home() {
  return (
    <div className="bg-[#0a0a0a] min-h-screen flex flex-col font-sans">

      {/* Navbar */}
      <nav className="flex items-center justify-between px-10 py-4 border-b border-[#1e1e1e]">
        <FalconLogo size={28} />
        <div className="flex gap-8">
          <span className="text-[#666] text-sm cursor-pointer hover:text-white transition-colors">Docs</span>
          <Link href="/upload" className="text-[#666] text-sm cursor-pointer hover:text-white transition-colors">Jobs</Link>
          <span className="text-[#666] text-sm cursor-pointer hover:text-white transition-colors">Logs</span>
        </div>
      </nav>

      {/* Hero */}
      <div className="flex flex-col items-center justify-center text-center px-8 pt-24 pb-16">
        <div className="flex items-center gap-2 px-4 py-1.5 border border-[#2a2a2a] rounded-full text-[#666] text-xs mb-8 tracking-wide">
          ⚡ AI Batch Processing Platform
        </div>

        <h1 className="text-6xl font-medium text-white leading-tight mb-6 tracking-tight max-w-3xl">
          Process thousands of<br />
          <span className="text-[#444]">prompts in one shot.</span>
        </h1>

        <p className="text-sm text-[#555] max-w-md leading-relaxed mb-10">
          Upload a JSONL file, submit a batch job, and let FALCON handle the rest.
          Fast, simple, and reliable AI batch processing.
        </p>

        <div className="flex gap-4">
          <Link
            href="/upload"
            className="flex items-center gap-2 bg-white text-black px-7 py-3 rounded-lg text-sm font-medium hover:bg-gray-100 transition-colors"
          >
            📁 Upload a batch
          </Link>
          <Link
            href="/upload"
            className="text-[#888] border border-[#2a2a2a] px-7 py-3 rounded-lg text-sm hover:bg-[#111] transition-colors"
          >
            View jobs →
          </Link>
        </div>
      </div>

      {/* Stats Bar */}
      <div className="flex justify-center gap-16 py-10 border-y border-[#1a1a1a] mx-10 mb-16">
        <div className="text-center">
          <p className="text-white font-medium text-xl">JSONL</p>
          <p className="text-[#444] text-xs mt-1 tracking-wide">Input format</p>
        </div>
        <div className="text-center">
          <p className="text-white font-medium text-xl">vLLM</p>
          <p className="text-[#444] text-xs mt-1 tracking-wide">AI runtime</p>
        </div>
        <div className="text-center">
          <p className="text-white font-medium text-xl">Real-time</p>
          <p className="text-[#444] text-xs mt-1 tracking-wide">Status updates</p>
        </div>
        <div className="text-center">
          <p className="text-white font-medium text-xl">1-click</p>
          <p className="text-[#444] text-xs mt-1 tracking-wide">Output download</p>
        </div>
      </div>

      {/* How it works */}
      <div className="px-16 mb-20">
        <p className="text-[#444] text-xs uppercase tracking-widest text-center mb-10">How it works</p>
        <div className="grid grid-cols-4 gap-4">
          {[
            { step: '01', title: 'Upload', desc: 'Upload your JSONL file with all your prompts.', icon: '📁' },
            { step: '02', title: 'Submit', desc: 'Submit the batch job with one click.', icon: '🚀' },
            { step: '03', title: 'Process', desc: 'FALCON runs your prompts through vLLM automatically.', icon: '⚙️' },
            { step: '04', title: 'Download', desc: 'Download your outputs.jsonl when complete.', icon: '📥' },
          ].map((item) => (
            <div key={item.step} className="border border-[#1e1e1e] rounded-xl p-6 hover:border-[#2a2a2a] transition-colors">
              <p className="text-[#333] text-xs mb-4 font-mono">{item.step}</p>
              <div className="text-2xl mb-3">{item.icon}</div>
              <p className="text-white text-sm font-medium mb-2">{item.title}</p>
              <p className="text-[#555] text-xs leading-relaxed">{item.desc}</p>
            </div>
          ))}
        </div>
      </div>

      {/* CTA */}
      <div className="mx-16 mb-16 border border-[#1e1e1e] rounded-2xl p-12 flex flex-col items-center text-center">
        <FalconLogo size={40} />
        <h2 className="text-white text-2xl font-medium mt-6 mb-3">Ready to run your first batch?</h2>
        <p className="text-[#555] text-sm mb-8 max-w-sm">Upload a JSONL file and get results in minutes.</p>
        <Link
          href="/upload"
          className="bg-white text-black px-8 py-3 rounded-lg text-sm font-medium hover:bg-gray-100 transition-colors"
        >
          Get started →
        </Link>
      </div>

      {/* Footer */}
      <div className="flex items-center justify-between px-16 py-6 border-t border-[#1a1a1a] mt-auto">
        <FalconLogo size={20} />
        <p className="text-[#333] text-xs">Built for speed. Built for scale.</p>
      </div>

    </div>
  );
}