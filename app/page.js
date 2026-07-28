'use client';

import { useEffect, useRef } from 'react';
import Link from 'next/link';

export default function Home() {
  const canvasRef = useRef(null);

  useEffect(() => {
    if (!canvasRef.current) return;
    const destroy = fluidSimulation(canvasRef.current);
    return () => destroy && destroy();
  }, []);

  return (
    <>
      <style jsx global>{`
        @import url('https://fonts.googleapis.com/css2?family=Onest:wght@400;500&display=swap');
        html { font-size: 16px; }
        @media (max-width: 1920px) { html { font-size: 0.833333vw; } }
        @media (max-width: 1440px) { html { font-size: 1.111111vw; } }
        @media (max-width: 1024px) { html { font-size: 1.5625vw; } }
        @media (max-width: 640px)  { html { font-size: 4.444444vw; } }
        * { margin: 0; box-sizing: border-box; }
        body { background: #04050c; color: #eef0f6; overflow-x: hidden; font-family: 'Onest', sans-serif; }
        a { text-decoration: none; color: inherit; }
        button { border: none; cursor: pointer; font-family: inherit; }

        .mk-hero { position: relative; display: flex; flex-direction: column; align-items: center; justify-content: center; min-height: 100lvh; width: 100vw; overflow: hidden; background: #04050c; text-align: center; padding: 0 1.25rem; }
        @media (min-width: 640px) { .mk-hero { padding: 0 2.5rem; } }
        .mk-canvas { position: absolute; inset: 0; width: 100%; height: 100%; z-index: 0; pointer-events: none; }
        .mk-scrim { position: absolute; inset: 0; z-index: 1; pointer-events: none;
          background: radial-gradient(115% 95% at 50% 46%, rgba(4,5,12,0.68) 0%, rgba(4,5,12,0.68) 24%, rgba(4,5,12,0.46) 52%, rgba(4,5,12,0.12) 100%); }

        .mk-nav { position: absolute; inset-inline: 0; top: 0; display: flex; align-items: center; justify-content: space-between; padding: 1.25rem; z-index: 20;
          opacity: 0; transform: translateY(-0.75rem); transition: opacity 0.7s cubic-bezier(0.2,0,0,1), transform 0.7s cubic-bezier(0.2,0,0,1); transition-delay: 150ms; }
        @media (min-width: 640px) { .mk-nav { padding: 1.75rem 2.5rem; } }
        .mk-nav.mk-in { opacity: 1; transform: translateY(0); }

        .mk-brand { display: flex; align-items: center; gap: 0.6rem; font-weight: 500; font-size: 1.15rem; color: #ffffff; letter-spacing: -0.01em; }
        @media (min-width: 640px) { .mk-brand { font-size: 1.375rem; } }
        .mk-brand svg { width: 1.35rem; height: 1.35rem; }
        @media (min-width: 640px) { .mk-brand svg { width: 1.5rem; height: 1.5rem; } }

        .mk-navlinks { display: none; position: absolute; left: 50%; transform: translateX(-50%); height: 3rem; align-items: center; gap: 2.25rem; border-radius: 9999px; border: 1px solid rgba(255,255,255,0.16); background: rgba(255,255,255,0.08); padding: 0 1.75rem; backdrop-filter: blur(12px); }
        @media (min-width: 640px) { .mk-navlinks { display: flex; } }
        .mk-navlinks a { font-size: 0.95rem; color: #b9becf; transition: color 150ms cubic-bezier(0.2,0,0,1); white-space: nowrap; }
        .mk-navlinks a:hover { color: #eef0f6; }

        .mk-pill { display: inline-flex; align-items: center; justify-content: center; height: 2.5rem; border-radius: 9999px; background: #ffffff; padding: 0 1.125rem; font-size: 0.85rem; font-weight: 500; color: #2f2f33; box-shadow: 0 1px 2px rgba(0,0,0,.05); transition: background 150ms cubic-bezier(0.2,0,0,1); }
        @media (min-width: 640px) { .mk-pill { height: 2.75rem; padding: 0 1.375rem; font-size: 0.95rem; } }
        .mk-pill:hover { background: rgba(255,255,255,0.85); }

        .mk-center { position: relative; z-index: 10; display: flex; flex-direction: column; align-items: center; width: 100%; max-width: 22rem; }
        @media (min-width: 640px) { .mk-center { max-width: 40rem; } }
        @media (min-width: 1024px) { .mk-center { max-width: 52rem; } }

        .mk-badge { display: inline-flex; align-items: center; border-radius: 9999px; border: 1px solid rgba(255,255,255,0.16); background: rgba(255,255,255,0.08); padding: 0.4rem 0.875rem; font-size: 0.72rem; color: #b9becf; backdrop-filter: blur(12px);
          opacity: 0; transform: translateY(1.25rem); transition: opacity 0.7s cubic-bezier(0.2,0,0,1), transform 0.7s cubic-bezier(0.2,0,0,1); transition-delay: 320ms; }
        @media (min-width: 640px) { .mk-badge { font-size: 0.8rem; } }
        .mk-badge.mk-in { opacity: 1; transform: translateY(0); }

        .mk-heading { margin-top: 1.25rem; max-width: 20rem; font-size: 2rem; font-weight: 500; line-height: 1.1; letter-spacing: -0.02em; color: #eef0f6; text-align: center; }
        @media (min-width: 640px) { .mk-heading { margin-top: 1.75rem; max-width: 34rem; font-size: 3.5rem; } }
        @media (min-width: 1024px) { .mk-heading { max-width: 46rem; font-size: 5rem; } }
        .mk-word { display: inline-block; opacity: 0; transform: translateY(26px); transition: opacity 720ms cubic-bezier(0.33,1,0.68,1), transform 720ms cubic-bezier(0.33,1,0.68,1); }
        .mk-word.mk-in { opacity: 1; transform: translateY(0); }

        .mk-sub { margin-top: 1rem; max-width: 20rem; font-size: 1rem; line-height: 1.5; color: #b9becf; }
        @media (min-width: 640px) { .mk-sub { margin-top: 1.25rem; max-width: 34rem; font-size: 1.1rem; } }
        @media (min-width: 1024px) { .mk-sub { max-width: none; font-size: 1.2rem; } }
        .mk-subword { display: inline-block; opacity: 0; transform: translateY(14px); transition: opacity 600ms cubic-bezier(0.33,1,0.68,1), transform 600ms cubic-bezier(0.33,1,0.68,1); }
        .mk-subword.mk-in { opacity: 1; transform: translateY(0); }

        .mk-formwrap { margin-top: 1.75rem; display: flex; justify-content: center; width: 100%;
          opacity: 0; transform: translateY(1.25rem); transition: opacity 0.7s cubic-bezier(0.2,0,0,1), transform 0.7s cubic-bezier(0.2,0,0,1); transition-delay: 1450ms; }
        @media (min-width: 640px) { .mk-formwrap { margin-top: 2.5rem; } }
        .mk-formwrap.mk-in { opacity: 1; transform: translateY(0); }
        .mk-form { width: 30rem; max-width: 100%; }
        .mk-bar { display: flex; align-items: center; height: 3.5rem; border-radius: 9999px; border: 1px solid rgba(255,255,255,0.16); background: rgba(255,255,255,0.08); backdrop-filter: blur(12px); box-shadow: 0 1px 2px rgba(0,0,0,.05); padding-left: 1.25rem; padding-right: 0.35rem; }
        @media (min-width: 640px) { .mk-bar { height: 4rem; padding-left: 1.5rem; padding-right: 0.4rem; } }
        .mk-bar button { flex: 1; min-width: 0; height: 100%; background: transparent; color: #eef0f6; font-size: 0.95rem; text-align: left; }
        @media (min-width: 640px) { .mk-bar button { font-size: 1.15rem; } }

        .mk-footer { position: absolute; inset-inline: 0; bottom: 0; display: flex; justify-content: center; padding: 1.25rem; font-size: 0.72rem; color: #b9becf; z-index: 20;
          opacity: 0; transform: translateY(1.25rem); transition: opacity 0.7s cubic-bezier(0.2,0,0,1), transform 0.7s cubic-bezier(0.2,0,0,1); transition-delay: 1650ms; }
        @media (min-width: 640px) { .mk-footer { padding: 1.5rem 2.5rem; font-size: 0.8rem; } }
        .mk-footer.mk-in { opacity: 1; transform: translateY(0); }

        /* ---- Info sections below hero ---- */
        .mk-section { background: #04050c; padding: 6rem 2rem; }
        .mk-section-inner { max-width: 72rem; margin: 0 auto; }
        .mk-section-title { font-size: 2.25rem; font-weight: 500; color: #eef0f6; letter-spacing: -0.02em; text-align: center; margin-bottom: 0.75rem; }
        .mk-section-sub { font-size: 1rem; color: #b9becf; text-align: center; max-width: 34rem; margin: 0 auto 3.5rem; line-height: 1.6; }
        .mk-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 1.25rem; }
        @media (max-width: 900px) { .mk-grid { grid-template-columns: 1fr; } }
        .mk-card { border-radius: 16px; border: 1px solid rgba(255,255,255,0.1); background: rgba(255,255,255,0.03); padding: 2rem; }
        .mk-card-icon { font-size: 1.5rem; margin-bottom: 1rem; }
        .mk-card-title { font-size: 1.1rem; font-weight: 500; color: #eef0f6; margin-bottom: 0.5rem; }
        .mk-card-body { font-size: 0.9rem; color: #b9becf; line-height: 1.6; }

        .mk-steps { display: flex; flex-direction: column; gap: 0; max-width: 40rem; margin: 0 auto; }
        .mk-step { display: flex; gap: 1.25rem; padding: 1.5rem 0; border-bottom: 1px solid rgba(255,255,255,0.08); }
        .mk-step:last-child { border-bottom: none; }
        .mk-step-num { font-size: 0.8rem; color: #6a6f85; font-weight: 500; flex-shrink: 0; width: 2rem; }
        .mk-step-title { font-size: 1rem; font-weight: 500; color: #eef0f6; margin-bottom: 0.25rem; }
        .mk-step-body { font-size: 0.9rem; color: #b9becf; line-height: 1.6; }

        .mk-roles { display: grid; grid-template-columns: repeat(3, 1fr); gap: 1.25rem; }
        @media (max-width: 900px) { .mk-roles { grid-template-columns: 1fr; } }
        .mk-role-badge { display: inline-flex; padding: 0.3rem 0.75rem; border-radius: 9999px; background: rgba(255,255,255,0.08); font-size: 0.72rem; color: #b9becf; margin-bottom: 1rem; }
      `}</style>

      {/* ===== HERO ===== */}
      <section className="mk-hero">
        <canvas ref={canvasRef} className="mk-canvas" aria-hidden="true" />
        <div className="mk-scrim" aria-hidden="true" />

        <header className="mk-nav" id="mk-nav">
          <Link href="/" className="mk-brand">
            <svg viewBox="0 0 32 32" fill="none" aria-hidden="true">
              <circle cx="16" cy="16" r="12" fill="#fff" />
              <circle cx="20" cy="13" r="10" fill="#04050c" />
            </svg>
            MOONKNIGHT
          </Link>

          <div className="mk-navlinks">
            <a href="#how-it-works">How it works</a>
            <a href="#roles">Who it's for</a>
            <a href="#pricing">Pricing</a>
            <Link href="/login">Log in</Link>
          </div>

          <Link href="/signup" className="mk-pill">Get started</Link>
        </header>

        <div className="mk-center">
          <p className="mk-badge" id="mk-badge">Distributed GPU inference for everyone</p>

          <h1 className="mk-heading" id="mk-heading">
            <WordSpan text="Process thousands of prompts in one shot" delay={480} stagger={85} dur={720} from={26} cls="mk-word" />
          </h1>

          <p className="mk-sub" id="mk-sub">
            <WordSpan text="Upload a JSONL file, submit a batch job, and let MOONKNIGHT route it across distributed AI workers running vLLM and Ollama." delay={1150} stagger={22} dur={600} from={14} cls="mk-subword" />
          </p>

          <div className="mk-formwrap" id="mk-formwrap">
            <div className="mk-form">
              <div className="mk-bar">
                <Link href="/signup" style={{ flex: 1 }}>
                  <button>Upload a batch job →</button>
                </Link>
                <Link href="/signup" className="mk-pill">Get started</Link>
              </div>
            </div>
          </div>
        </div>

        <footer className="mk-footer" id="mk-footer">© 2026 MOONKNIGHT — distributed AI, on demand.</footer>
      </section>

      {/* ===== WHAT IT DOES ===== */}
      <section className="mk-section" id="how-it-works">
        <div className="mk-section-inner">
          <h2 className="mk-section-title">How it works</h2>
          <p className="mk-section-sub">Four steps from raw prompts to processed output.</p>
          <div className="mk-steps">
            <div className="mk-step">
              <span className="mk-step-num">01</span>
              <div>
                <p className="mk-step-title">Upload your JSONL file</p>
                <p className="mk-step-body">Bring a file of prompts formatted as JSONL — one request per line, same as OpenAI's batch API.</p>
              </div>
            </div>
            <div className="mk-step">
              <span className="mk-step-num">02</span>
              <div>
                <p className="mk-step-title">Submit the batch job</p>
                <p className="mk-step-body">MOONKNIGHT queues your job and assigns it to an available GPU worker running your chosen model.</p>
              </div>
            </div>
            <div className="mk-step">
              <span className="mk-step-num">03</span>
              <div>
                <p className="mk-step-title">Track it in real time</p>
                <p className="mk-step-body">Watch prompts complete live from your dashboard — status, progress, and per-request results.</p>
              </div>
            </div>
            <div className="mk-step">
              <span className="mk-step-num">04</span>
              <div>
                <p className="mk-step-title">Download your outputs</p>
                <p className="mk-step-body">Once complete, download a single outputs.jsonl file with every response matched to its request.</p>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* ===== FEATURES ===== */}
      <section className="mk-section">
        <div className="mk-section-inner">
          <h2 className="mk-section-title">Built for scale</h2>
          <p className="mk-section-sub">Everything you need to run inference at volume, without managing infrastructure yourself.</p>
          <div className="mk-grid">
            <div className="mk-card">
              <div className="mk-card-icon">⚡</div>
              <p className="mk-card-title">Distributed workers</p>
              <p className="mk-card-body">Jobs are routed across a pool of GPU-backed workers running vLLM and Ollama, so throughput scales with demand.</p>
            </div>
            <div className="mk-card">
              <div className="mk-card-icon">📋</div>
              <p className="mk-card-title">Real-time job tracking</p>
              <p className="mk-card-body">Live status, progress bars, and per-prompt completion counts — no polling, no guessing.</p>
            </div>
            <div className="mk-card">
              <div className="mk-card-icon">🔑</div>
              <p className="mk-card-title">Simple REST API</p>
              <p className="mk-card-body">Upload files, create batches, and poll status with a small, predictable set of endpoints.</p>
            </div>
            <div className="mk-card">
              <div className="mk-card-icon">🖥️</div>
              <p className="mk-card-title">Bring your own GPU</p>
              <p className="mk-card-body">Providers register their machines and models, then get routed real jobs from the platform.</p>
            </div>
            <div className="mk-card">
              <div className="mk-card-icon">📊</div>
              <p className="mk-card-title">Full admin visibility</p>
              <p className="mk-card-body">See every job, user, and provider on the platform from one dashboard, with live system logs.</p>
            </div>
            <div className="mk-card">
              <div className="mk-card-icon">🔒</div>
              <p className="mk-card-title">Role-based access</p>
              <p className="mk-card-body">Separate flows and permissions for Users, Providers, and Admins, each with their own dashboard.</p>
            </div>
          </div>
        </div>
      </section>

      {/* ===== ROLES ===== */}
      <section className="mk-section" id="roles">
        <div className="mk-section-inner">
          <h2 className="mk-section-title">Built for three kinds of people</h2>
          <p className="mk-section-sub">Whoever you are on the platform, there's a dashboard built for you.</p>
          <div className="mk-roles">
            <div className="mk-card">
              <span className="mk-role-badge">User</span>
              <p className="mk-card-title">Run inference at scale</p>
              <p className="mk-card-body">Upload prompts, submit batches, and download results — without provisioning a single GPU yourself.</p>
            </div>
            <div className="mk-card">
              <span className="mk-role-badge">Provider</span>
              <p className="mk-card-title">Monetize your compute</p>
              <p className="mk-card-body">Register your GPU, go online, and start receiving jobs. Track throughput, uptime, and earnings live.</p>
            </div>
            <div className="mk-card">
              <span className="mk-role-badge">Admin</span>
              <p className="mk-card-title">Oversee the whole platform</p>
              <p className="mk-card-body">Monitor every job, user, and provider, approve new machines, and keep the system running smoothly.</p>
            </div>
          </div>
        </div>
      </section>

      <ScrollReveal />
    </>
  );
}

function WordSpan({ text, delay, stagger, dur, from, cls }) {
  const words = text.split(' ');
  return words.map((w, i) => (
    <span
      key={i}
      className={cls}
      data-delay={delay + i * stagger}
      data-dur={dur}
      data-from={from}
    >
      {w}{i < words.length - 1 ? '\u00A0' : ''}
    </span>
  ));
}

function ScrollReveal() {
  useEffect(() => {
    const reveal = (id) => {
      const el = document.getElementById(id);
      if (el) el.classList.add('mk-in');
    };
    setTimeout(() => reveal('mk-nav'), 0);
    setTimeout(() => reveal('mk-badge'), 0);
    setTimeout(() => reveal('mk-formwrap'), 0);
    setTimeout(() => reveal('mk-footer'), 0);

    document.querySelectorAll('.mk-word, .mk-subword').forEach((el) => {
      const delay = parseInt(el.dataset.delay, 10);
      el.style.transitionDelay = delay + 'ms';
      setTimeout(() => el.classList.add('mk-in'), 10);
    });
  }, []);
  return null;
}

/* ============ WebGL fluid engine (verbatim, unmodified logic) ============ */
function fluidSimulation(canvas) {
  canvas.width = canvas.clientWidth;
  canvas.height = canvas.clientHeight;

  let config = {
    SIM_RESOLUTION: 200,
    DYE_RESOLUTION: 512,
    DENSITY_DISSIPATION: 0.958,
    VELOCITY_DISSIPATION: 0.96,
    PRESSURE_DISSIPATION: 0.8,
    PRESSURE_ITERATIONS: 20,
    CURL: 42,
    SPLAT_RADIUS: 0.22,
    SHADING: true,
    COLORFUL: true,
    PAUSED: false,
    BACK_COLOR: { r: 4, g: 5, b: 12 },
    TRANSPARENT: false,
    BLOOM: false,
    BLOOM_ITERATIONS: 8,
    BLOOM_RESOLUTION: 256,
    BLOOM_INTENSITY: 0.8,
    BLOOM_THRESHOLD: 0.8,
    BLOOM_SOFT_KNEE: 0.7,
  };

  function pointerPrototype() {
    this.id = -1; this.x = 0; this.y = 0; this.dx = 0; this.dy = 0;
    this.down = false; this.moved = false; this.color = [30, 0, 300];
  }

  let pointers = [];
  let splatStack = [];
  let bloomFramebuffers = [];
  pointers.push(new pointerPrototype());

  const { gl, ext } = getWebGLContext(canvas);

  function isMobile() { return /Mobi|Android/i.test(navigator.userAgent); }
  if (isMobile()) config.SHADING = false;
  if (!ext.supportLinearFiltering) { config.SHADING = false; config.BLOOM = false; }

  function getWebGLContext(canvas) {
    const params = { alpha: true, depth: false, stencil: false, antialias: false, preserveDrawingBuffer: false };
    let gl = canvas.getContext('webgl2', params);
    const isWebGL2 = !!gl;
    if (!isWebGL2) gl = canvas.getContext('webgl', params) || canvas.getContext('experimental-webgl', params);
    let halfFloat, supportLinearFiltering;
    if (isWebGL2) {
      gl.getExtension('EXT_color_buffer_float');
      supportLinearFiltering = gl.getExtension('OES_texture_float_linear');
    } else {
      halfFloat = gl.getExtension('OES_texture_half_float');
      supportLinearFiltering = gl.getExtension('OES_texture_half_float_linear');
    }
    gl.clearColor(0.0, 0.0, 0.0, 1.0);
    const halfFloatTexType = isWebGL2 ? gl.HALF_FLOAT : halfFloat.HALF_FLOAT_OES;
    let formatRGBA, formatRG, formatR;
    if (isWebGL2) {
      formatRGBA = getSupportedFormat(gl, gl.RGBA16F, gl.RGBA, halfFloatTexType);
      formatRG = getSupportedFormat(gl, gl.RG16F, gl.RG, halfFloatTexType);
      formatR = getSupportedFormat(gl, gl.R16F, gl.RED, halfFloatTexType);
    } else {
      formatRGBA = getSupportedFormat(gl, gl.RGBA, gl.RGBA, halfFloatTexType);
      formatRG = getSupportedFormat(gl, gl.RGBA, gl.RGBA, halfFloatTexType);
      formatR = getSupportedFormat(gl, gl.RGBA, gl.RGBA, halfFloatTexType);
    }
    return { gl, ext: { formatRGBA, formatRG, formatR, halfFloatTexType, supportLinearFiltering } };
  }

  function getSupportedFormat(gl, internalFormat, format, type) {
    if (!supportRenderTextureFormat(gl, internalFormat, format, type)) {
      switch (internalFormat) {
        case gl.R16F: return getSupportedFormat(gl, gl.RG16F, gl.RG, type);
        case gl.RG16F: return getSupportedFormat(gl, gl.RGBA16F, gl.RGBA, type);
        default: return null;
      }
    }
    return { internalFormat, format };
  }

  function supportRenderTextureFormat(gl, internalFormat, format, type) {
    let texture = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, texture);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    gl.texImage2D(gl.TEXTURE_2D, 0, internalFormat, 4, 4, 0, format, type, null);
    let fbo = gl.createFramebuffer();
    gl.bindFramebuffer(gl.FRAMEBUFFER, fbo);
    gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, texture, 0);
    return gl.checkFramebufferStatus(gl.FRAMEBUFFER) == gl.FRAMEBUFFER_COMPLETE;
  }

  class GLProgram {
    constructor(vertexShader, fragmentShader) {
      this.uniforms = {};
      this.program = gl.createProgram();
      gl.attachShader(this.program, vertexShader);
      gl.attachShader(this.program, fragmentShader);
      gl.linkProgram(this.program);
      if (!gl.getProgramParameter(this.program, gl.LINK_STATUS)) throw gl.getProgramInfoLog(this.program);
      const uniformCount = gl.getProgramParameter(this.program, gl.ACTIVE_UNIFORMS);
      for (let i = 0; i < uniformCount; i++) {
        const uniformName = gl.getActiveUniform(this.program, i).name;
        this.uniforms[uniformName] = gl.getUniformLocation(this.program, uniformName);
      }
    }
    bind() { gl.useProgram(this.program); }
  }

  function compileShader(type, source) {
    const shader = gl.createShader(type);
    gl.shaderSource(shader, source);
    gl.compileShader(shader);
    if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) throw gl.getShaderInfoLog(shader);
    return shader;
  }

  const baseVertexShader = compileShader(gl.VERTEX_SHADER, `
    precision highp float;
    attribute vec2 aPosition;
    varying vec2 vUv; varying vec2 vL; varying vec2 vR; varying vec2 vT; varying vec2 vB;
    uniform vec2 texelSize;
    void main () {
      vUv = aPosition * 0.5 + 0.5;
      vL = vUv - vec2(texelSize.x, 0.0); vR = vUv + vec2(texelSize.x, 0.0);
      vT = vUv + vec2(0.0, texelSize.y); vB = vUv - vec2(0.0, texelSize.y);
      gl_Position = vec4(aPosition, 0.0, 1.0);
    }
  `);

  const clearShader = compileShader(gl.FRAGMENT_SHADER, `
    precision mediump float; precision mediump sampler2D;
    varying highp vec2 vUv; uniform sampler2D uTexture; uniform float value;
    void main () { gl_FragColor = value * texture2D(uTexture, vUv); }
  `);

  const colorShader = compileShader(gl.FRAGMENT_SHADER, `
    precision mediump float; uniform vec4 color;
    void main () { gl_FragColor = color; }
  `);

  const backgroundShader = compileShader(gl.FRAGMENT_SHADER, `
    precision highp float; precision highp sampler2D;
    varying vec2 vUv; uniform sampler2D uTexture; uniform float aspectRatio;
    #define SCALE 25.0
    void main () {
      vec2 uv = floor(vUv * SCALE * vec2(aspectRatio, 1.0));
      float v = mod(uv.x + uv.y, 2.0); v = v * 0.1 + 0.8;
      gl_FragColor = vec4(vec3(v), 1.0);
    }
  `);

  const displayShader = compileShader(gl.FRAGMENT_SHADER, `
    precision highp float; precision highp sampler2D;
    varying vec2 vUv; uniform sampler2D uTexture;
    void main () {
      vec3 C = texture2D(uTexture, vUv).rgb;
      float a = max(C.r, max(C.g, C.b));
      gl_FragColor = vec4(C, a);
    }
  `);

  const displayBloomShader = compileShader(gl.FRAGMENT_SHADER, `
    precision highp float; precision highp sampler2D;
    varying vec2 vUv; uniform sampler2D uTexture; uniform sampler2D uBloom;
    uniform sampler2D uDithering; uniform vec2 ditherScale;
    void main () {
      vec3 C = texture2D(uTexture, vUv).rgb; vec3 bloom = texture2D(uBloom, vUv).rgb;
      vec3 noise = texture2D(uDithering, vUv * ditherScale).rgb; noise = noise * 2.0 - 1.0;
      bloom += noise / 800.0; bloom = pow(bloom.rgb, vec3(1.0 / 2.2)); C += bloom;
      float a = max(C.r, max(C.g, C.b)); gl_FragColor = vec4(C, a);
    }
  `);

  const displayShadingShader = compileShader(gl.FRAGMENT_SHADER, `
    precision highp float; precision highp sampler2D;
    varying vec2 vUv; varying vec2 vL; varying vec2 vR; varying vec2 vT; varying vec2 vB;
    uniform sampler2D uTexture; uniform vec2 texelSize;
    void main () {
      vec3 L = texture2D(uTexture, vL).rgb; vec3 R = texture2D(uTexture, vR).rgb;
      vec3 T = texture2D(uTexture, vT).rgb; vec3 B = texture2D(uTexture, vB).rgb;
      vec3 C = texture2D(uTexture, vUv).rgb;
      float dx = length(R) - length(L); float dy = length(T) - length(B);
      vec3 n = normalize(vec3(dx, dy, length(texelSize))); vec3 l = vec3(0.0, 0.0, 1.0);
      float diffuse = clamp(dot(n, l) + 0.7, 0.7, 1.0); C.rgb *= diffuse;
      float a = max(C.r, max(C.g, C.b)); gl_FragColor = vec4(C, a);
    }
  `);

  const displayBloomShadingShader = compileShader(gl.FRAGMENT_SHADER, `
    precision highp float; precision highp sampler2D;
    varying vec2 vUv; varying vec2 vL; varying vec2 vR; varying vec2 vT; varying vec2 vB;
    uniform sampler2D uTexture; uniform sampler2D uBloom; uniform sampler2D uDithering;
    uniform vec2 ditherScale; uniform vec2 texelSize;
    void main () {
      vec3 L = texture2D(uTexture, vL).rgb; vec3 R = texture2D(uTexture, vR).rgb;
      vec3 T = texture2D(uTexture, vT).rgb; vec3 B = texture2D(uTexture, vB).rgb;
      vec3 C = texture2D(uTexture, vUv).rgb;
      float dx = length(R) - length(L); float dy = length(T) - length(B);
      vec3 n = normalize(vec3(dx, dy, length(texelSize))); vec3 l = vec3(0.0, 0.0, 1.0);
      float diffuse = clamp(dot(n, l) + 0.7, 0.7, 1.0); C *= diffuse;
      vec3 bloom = texture2D(uBloom, vUv).rgb; vec3 noise = texture2D(uDithering, vUv * ditherScale).rgb;
      noise = noise * 2.0 - 1.0; bloom += noise / 800.0; bloom = pow(bloom.rgb, vec3(1.0 / 2.2)); C += bloom;
      float a = max(C.r, max(C.g, C.b)); gl_FragColor = vec4(C, a);
    }
  `);

  const bloomPrefilterShader = compileShader(gl.FRAGMENT_SHADER, `
    precision mediump float; precision mediump sampler2D;
    varying vec2 vUv; uniform sampler2D uTexture; uniform vec3 curve; uniform float threshold;
    void main () {
      vec3 c = texture2D(uTexture, vUv).rgb; float br = max(c.r, max(c.g, c.b));
      float rq = clamp(br - curve.x, 0.0, curve.y); rq = curve.z * rq * rq;
      c *= max(rq, br - threshold) / max(br, 0.0001); gl_FragColor = vec4(c, 0.0);
    }
  `);

  const bloomBlurShader = compileShader(gl.FRAGMENT_SHADER, `
    precision mediump float; precision mediump sampler2D;
    varying vec2 vL; varying vec2 vR; varying vec2 vT; varying vec2 vB; uniform sampler2D uTexture;
    void main () {
      vec4 sum = vec4(0.0); sum += texture2D(uTexture, vL); sum += texture2D(uTexture, vR);
      sum += texture2D(uTexture, vT); sum += texture2D(uTexture, vB); sum *= 0.25;
      gl_FragColor = sum;
    }
  `);

  const bloomFinalShader = compileShader(gl.FRAGMENT_SHADER, `
    precision mediump float; precision mediump sampler2D;
    varying vec2 vL; varying vec2 vR; varying vec2 vT; varying vec2 vB;
    uniform sampler2D uTexture; uniform float intensity;
    void main () {
      vec4 sum = vec4(0.0); sum += texture2D(uTexture, vL); sum += texture2D(uTexture, vR);
      sum += texture2D(uTexture, vT); sum += texture2D(uTexture, vB); sum *= 0.25;
      gl_FragColor = sum * intensity;
    }
  `);

  const splatShader = compileShader(gl.FRAGMENT_SHADER, `
    precision highp float; precision highp sampler2D;
    varying vec2 vUv; uniform sampler2D uTarget; uniform float aspectRatio;
    uniform vec3 color; uniform vec2 point; uniform float radius;
    void main () {
      vec2 p = vUv - point.xy; p.x *= aspectRatio;
      vec3 splat = exp(-dot(p, p) / radius) * color;
      vec3 base = texture2D(uTarget, vUv).xyz; gl_FragColor = vec4(base + splat, 1.0);
    }
  `);

  const advectionManualFilteringShader = compileShader(gl.FRAGMENT_SHADER, `
    precision highp float; precision highp sampler2D;
    varying vec2 vUv; uniform sampler2D uVelocity; uniform sampler2D uSource;
    uniform vec2 texelSize; uniform vec2 dyeTexelSize; uniform float dt; uniform float dissipation;
    vec4 bilerp (sampler2D sam, vec2 uv, vec2 tsize) {
      vec2 st = uv / tsize - 0.5; vec2 iuv = floor(st); vec2 fuv = fract(st);
      vec4 a = texture2D(sam, (iuv + vec2(0.5, 0.5)) * tsize);
      vec4 b = texture2D(sam, (iuv + vec2(1.5, 0.5)) * tsize);
      vec4 c = texture2D(sam, (iuv + vec2(0.5, 1.5)) * tsize);
      vec4 d = texture2D(sam, (iuv + vec2(1.5, 1.5)) * tsize);
      return mix(mix(a, b, fuv.x), mix(c, d, fuv.x), fuv.y);
    }
    void main () {
      vec2 coord = vUv - dt * bilerp(uVelocity, vUv, texelSize).xy * texelSize;
      gl_FragColor = dissipation * bilerp(uSource, coord, dyeTexelSize); gl_FragColor.a = 1.0;
    }
  `);

  const advectionShader = compileShader(gl.FRAGMENT_SHADER, `
    precision highp float; precision highp sampler2D;
    varying vec2 vUv; uniform sampler2D uVelocity; uniform sampler2D uSource;
    uniform vec2 texelSize; uniform float dt; uniform float dissipation;
    void main () {
      vec2 coord = vUv - dt * texture2D(uVelocity, vUv).xy * texelSize;
      gl_FragColor = dissipation * texture2D(uSource, coord); gl_FragColor.a = 1.0;
    }
  `);

  const divergenceShader = compileShader(gl.FRAGMENT_SHADER, `
    precision mediump float; precision mediump sampler2D;
    varying highp vec2 vUv; varying highp vec2 vL; varying highp vec2 vR; varying highp vec2 vT; varying highp vec2 vB;
    uniform sampler2D uVelocity;
    void main () {
      float L = texture2D(uVelocity, vL).x; float R = texture2D(uVelocity, vR).x;
      float T = texture2D(uVelocity, vT).y; float B = texture2D(uVelocity, vB).y;
      vec2 C = texture2D(uVelocity, vUv).xy;
      if (vL.x < 0.0) { L = -C.x; } if (vR.x > 1.0) { R = -C.x; }
      if (vT.y > 1.0) { T = -C.y; } if (vB.y < 0.0) { B = -C.y; }
      float div = 0.5 * (R - L + T - B); gl_FragColor = vec4(div, 0.0, 0.0, 1.0);
    }
  `);

  const curlShader = compileShader(gl.FRAGMENT_SHADER, `
    precision mediump float; precision mediump sampler2D;
    varying highp vec2 vUv; varying highp vec2 vL; varying highp vec2 vR; varying highp vec2 vT; varying highp vec2 vB;
    uniform sampler2D uVelocity;
    void main () {
      float L = texture2D(uVelocity, vL).y; float R = texture2D(uVelocity, vR).y;
      float T = texture2D(uVelocity, vT).x; float B = texture2D(uVelocity, vB).x;
      float vorticity = R - L - T + B; gl_FragColor = vec4(0.5 * vorticity, 0.0, 0.0, 1.0);
    }
  `);

  const vorticityShader = compileShader(gl.FRAGMENT_SHADER, `
    precision highp float; precision highp sampler2D;
    varying vec2 vUv; varying vec2 vL; varying vec2 vR; varying vec2 vT; varying vec2 vB;
    uniform sampler2D uVelocity; uniform sampler2D uCurl; uniform float curl; uniform float dt;
    void main () {
      float L = texture2D(uCurl, vL).x; float R = texture2D(uCurl, vR).x;
      float T = texture2D(uCurl, vT).x; float B = texture2D(uCurl, vB).x; float C = texture2D(uCurl, vUv).x;
      vec2 force = 0.5 * vec2(abs(T) - abs(B), abs(R) - abs(L));
      force /= length(force) + 0.0001; force *= curl * C; force.y *= -1.0;
      vec2 vel = texture2D(uVelocity, vUv).xy; gl_FragColor = vec4(vel + force * dt, 0.0, 1.0);
    }
  `);

  const pressureShader = compileShader(gl.FRAGMENT_SHADER, `
    precision mediump float; precision mediump sampler2D;
    varying highp vec2 vUv; varying highp vec2 vL; varying highp vec2 vR; varying highp vec2 vT; varying highp vec2 vB;
    uniform sampler2D uPressure; uniform sampler2D uDivergence;
    vec2 boundary (vec2 uv) { return uv; }
    void main () {
      float L = texture2D(uPressure, boundary(vL)).x; float R = texture2D(uPressure, boundary(vR)).x;
      float T = texture2D(uPressure, boundary(vT)).x; float B = texture2D(uPressure, boundary(vB)).x;
      float C = texture2D(uPressure, vUv).x; float divergence = texture2D(uDivergence, vUv).x;
      float pressure = (L + R + B + T - divergence) * 0.25; gl_FragColor = vec4(pressure, 0.0, 0.0, 1.0);
    }
  `);

  const gradientSubtractShader = compileShader(gl.FRAGMENT_SHADER, `
    precision mediump float; precision mediump sampler2D;
    varying highp vec2 vUv; varying highp vec2 vL; varying highp vec2 vR; varying highp vec2 vT; varying highp vec2 vB;
    uniform sampler2D uPressure; uniform sampler2D uVelocity;
    vec2 boundary (vec2 uv) { return uv; }
    void main () {
      float L = texture2D(uPressure, boundary(vL)).x; float R = texture2D(uPressure, boundary(vR)).x;
      float T = texture2D(uPressure, boundary(vT)).x; float B = texture2D(uPressure, boundary(vB)).x;
      vec2 velocity = texture2D(uVelocity, vUv).xy; velocity.xy -= vec2(R - L, T - B);
      gl_FragColor = vec4(velocity, 0.0, 1.0);
    }
  `);

  const blit = (() => {
    gl.bindBuffer(gl.ARRAY_BUFFER, gl.createBuffer());
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, -1, 1, 1, 1, 1, -1]), gl.STATIC_DRAW);
    gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, gl.createBuffer());
    gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, new Uint16Array([0, 1, 2, 0, 2, 3]), gl.STATIC_DRAW);
    gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0);
    gl.enableVertexAttribArray(0);
    return (destination) => {
      gl.bindFramebuffer(gl.FRAMEBUFFER, destination);
      gl.drawElements(gl.TRIANGLES, 6, gl.UNSIGNED_SHORT, 0);
    };
  })();

  let simWidth, simHeight, dyeWidth, dyeHeight, density, velocity, divergence, curl, pressure, bloom;
  let ditheringTexture = createNoiseTexture(256);

  const clearProgram = new GLProgram(baseVertexShader, clearShader);
  const colorProgram = new GLProgram(baseVertexShader, colorShader);
  const backgroundProgram = new GLProgram(baseVertexShader, backgroundShader);
  const displayProgram = new GLProgram(baseVertexShader, displayShader);
  const displayBloomProgram = new GLProgram(baseVertexShader, displayBloomShader);
  const displayShadingProgram = new GLProgram(baseVertexShader, displayShadingShader);
  const displayBloomShadingProgram = new GLProgram(baseVertexShader, displayBloomShadingShader);
  const bloomPrefilterProgram = new GLProgram(baseVertexShader, bloomPrefilterShader);
  const bloomBlurProgram = new GLProgram(baseVertexShader, bloomBlurShader);
  const bloomFinalProgram = new GLProgram(baseVertexShader, bloomFinalShader);
  const splatProgram = new GLProgram(baseVertexShader, splatShader);
  const advectionProgram = new GLProgram(baseVertexShader, ext.supportLinearFiltering ? advectionShader : advectionManualFilteringShader);
  const divergenceProgram = new GLProgram(baseVertexShader, divergenceShader);
  const curlProgram = new GLProgram(baseVertexShader, curlShader);
  const vorticityProgram = new GLProgram(baseVertexShader, vorticityShader);
  const pressureProgram = new GLProgram(baseVertexShader, pressureShader);
  const gradienSubtractProgram = new GLProgram(baseVertexShader, gradientSubtractShader);

  function initFramebuffers() {
    let simRes = getResolution(config.SIM_RESOLUTION);
    let dyeRes = getResolution(config.DYE_RESOLUTION);
    simWidth = simRes.width; simHeight = simRes.height; dyeWidth = dyeRes.width; dyeHeight = dyeRes.height;
    const texType = ext.halfFloatTexType, rgba = ext.formatRGBA, rg = ext.formatRG, r = ext.formatR;
    const filtering = ext.supportLinearFiltering ? gl.LINEAR : gl.NEAREST;
    if (density == null) density = createDoubleFBO(dyeWidth, dyeHeight, rgba.internalFormat, rgba.format, texType, filtering);
    else density = resizeDoubleFBO(density, dyeWidth, dyeHeight, rgba.internalFormat, rgba.format, texType, filtering);
    if (velocity == null) velocity = createDoubleFBO(simWidth, simHeight, rg.internalFormat, rg.format, texType, filtering);
    else velocity = resizeDoubleFBO(velocity, simWidth, simHeight, rg.internalFormat, rg.format, texType, filtering);
    divergence = createFBO(simWidth, simHeight, r.internalFormat, r.format, texType, gl.NEAREST);
    curl = createFBO(simWidth, simHeight, r.internalFormat, r.format, texType, gl.NEAREST);
    pressure = createDoubleFBO(simWidth, simHeight, r.internalFormat, r.format, texType, gl.NEAREST);
    initBloomFramebuffers();
  }

  function initBloomFramebuffers() {
    let res = getResolution(config.BLOOM_RESOLUTION);
    const texType = ext.halfFloatTexType, rgba = ext.formatRGBA;
    const filtering = ext.supportLinearFiltering ? gl.LINEAR : gl.NEAREST;
    bloom = createFBO(res.width, res.height, rgba.internalFormat, rgba.format, texType, filtering);
    bloomFramebuffers.length = 0;
    for (let i = 0; i < config.BLOOM_ITERATIONS; i++) {
      let width = res.width >> (i + 1), height = res.height >> (i + 1);
      if (width < 2 || height < 2) break;
      bloomFramebuffers.push(createFBO(width, height, rgba.internalFormat, rgba.format, texType, filtering));
    }
  }

  function createFBO(w, h, internalFormat, format, type, param) {
    gl.activeTexture(gl.TEXTURE0);
    let texture = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, texture);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, param);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, param);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    gl.texImage2D(gl.TEXTURE_2D, 0, internalFormat, w, h, 0, format, type, null);
    let fbo = gl.createFramebuffer();
    gl.bindFramebuffer(gl.FRAMEBUFFER, fbo);
    gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, texture, 0);
    gl.viewport(0, 0, w, h); gl.clear(gl.COLOR_BUFFER_BIT);
    return { texture, fbo, width: w, height: h, attach(id) { gl.activeTexture(gl.TEXTURE0 + id); gl.bindTexture(gl.TEXTURE_2D, texture); return id; } };
  }

  function createDoubleFBO(w, h, internalFormat, format, type, param) {
    let fbo1 = createFBO(w, h, internalFormat, format, type, param);
    let fbo2 = createFBO(w, h, internalFormat, format, type, param);
    return {
      get read() { return fbo1; }, set read(value) { fbo1 = value; },
      get write() { return fbo2; }, set write(value) { fbo2 = value; },
      swap() { let temp = fbo1; fbo1 = fbo2; fbo2 = temp; },
    };
  }

  function resizeFBO(target, w, h, internalFormat, format, type, param) {
    let newFBO = createFBO(w, h, internalFormat, format, type, param);
    clearProgram.bind();
    gl.uniform1i(clearProgram.uniforms.uTexture, target.attach(0));
    gl.uniform1f(clearProgram.uniforms.value, 1);
    blit(newFBO.fbo);
    return newFBO;
  }

  function resizeDoubleFBO(target, w, h, internalFormat, format, type, param) {
    target.read = resizeFBO(target.read, w, h, internalFormat, format, type, param);
    target.write = createFBO(w, h, internalFormat, format, type, param);
    return target;
  }

  function createNoiseTexture(size) {
    const data = new Uint8Array(size * size * 3);
    for (let i = 0; i < data.length; i++) data[i] = Math.floor(Math.random() * 256);
    let texture = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, texture);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.REPEAT);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.REPEAT);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGB, size, size, 0, gl.RGB, gl.UNSIGNED_BYTE, data);
    return { texture, width: size, height: size, attach(id) { gl.activeTexture(gl.TEXTURE0 + id); gl.bindTexture(gl.TEXTURE_2D, texture); return id; } };
  }

  initFramebuffers();
  multipleSplats(34);
  for (let i = 0; i < 8; i++) splatStack.push(10 + parseInt(Math.random() * 10));

  let lastColorChangeTime = Date.now();
  let virtualSeeded = false, orbitAngle = 0, vPrevX = 0, vPrevY = 0, virtualColor = null, lastVColorTime = 0;
  const engineStart = Date.now();
  const ORBIT_RADIUS = 300, ORBIT_SPEED = 0.026, ORBIT_START_DELAY = 700;
  let rafHandle = 0, destroyed = false;

  update();

  function update() {
    if (destroyed) return;
    resizeCanvas();
    driveVirtualPointer();
    input();
    if (!config.PAUSED) step(0.016);
    render(null);
    rafHandle = requestAnimationFrame(update);
  }

  function driveVirtualPointer() {
    if (Date.now() - engineStart < ORBIT_START_DELAY) return;
    const cx = canvas.width / 2, cy = canvas.height / 2;
    const base = Math.min(ORBIT_RADIUS, canvas.width * 0.35, canvas.height * 0.35);
    const r = base * (0.72 + 0.28 * Math.sin(orbitAngle * 0.37));
    orbitAngle += ORBIT_SPEED;
    const x = cx + Math.cos(orbitAngle) * r, y = cy + Math.sin(orbitAngle) * r;
    if (!virtualSeeded) { virtualSeeded = true; vPrevX = x; vPrevY = y; return; }
    if (!virtualColor || Date.now() - lastVColorTime > 120) {
      virtualColor = generateColor();
      virtualColor.r *= 3.2; virtualColor.g *= 3.2; virtualColor.b *= 3.2;
      lastVColorTime = Date.now();
    }
    const dx = (x - vPrevX) * 9.0, dy = (y - vPrevY) * 9.0;
    vPrevX = x; vPrevY = y;
    splat(x, y, dx, dy, virtualColor);
  }

  function input() {
    if (splatStack.length > 0) multipleSplats(splatStack.pop());
    for (let i = 0; i < pointers.length; i++) {
      const p = pointers[i];
      if (p.moved) { splat(p.x, p.y, p.dx, p.dy, p.color); p.moved = false; }
    }
    if (!config.COLORFUL) return;
    if (lastColorChangeTime + 100 < Date.now()) {
      lastColorChangeTime = Date.now();
      for (let i = 0; i < pointers.length; i++) pointers[i].color = generateColor();
    }
  }

  function step(dt) {
    gl.disable(gl.BLEND);
    gl.viewport(0, 0, simWidth, simHeight);

    curlProgram.bind();
    gl.uniform2f(curlProgram.uniforms.texelSize, 1.0 / simWidth, 1.0 / simHeight);
    gl.uniform1i(curlProgram.uniforms.uVelocity, velocity.read.attach(0));
    blit(curl.fbo);

    vorticityProgram.bind();
    gl.uniform2f(vorticityProgram.uniforms.texelSize, 1.0 / simWidth, 1.0 / simHeight);
    gl.uniform1i(vorticityProgram.uniforms.uVelocity, velocity.read.attach(0));
    gl.uniform1i(vorticityProgram.uniforms.uCurl, curl.attach(1));
    gl.uniform1f(vorticityProgram.uniforms.curl, config.CURL);
    gl.uniform1f(vorticityProgram.uniforms.dt, dt);
    blit(velocity.write.fbo); velocity.swap();

    divergenceProgram.bind();
    gl.uniform2f(divergenceProgram.uniforms.texelSize, 1.0 / simWidth, 1.0 / simHeight);
    gl.uniform1i(divergenceProgram.uniforms.uVelocity, velocity.read.attach(0));
    blit(divergence.fbo);

    clearProgram.bind();
    gl.uniform1i(clearProgram.uniforms.uTexture, pressure.read.attach(0));
    gl.uniform1f(clearProgram.uniforms.value, config.PRESSURE_DISSIPATION);
    blit(pressure.write.fbo); pressure.swap();

    pressureProgram.bind();
    gl.uniform2f(pressureProgram.uniforms.texelSize, 1.0 / simWidth, 1.0 / simHeight);
    gl.uniform1i(pressureProgram.uniforms.uDivergence, divergence.attach(0));
    for (let i = 0; i < config.PRESSURE_ITERATIONS; i++) {
      gl.uniform1i(pressureProgram.uniforms.uPressure, pressure.read.attach(1));
      blit(pressure.write.fbo); pressure.swap();
    }

    gradienSubtractProgram.bind();
    gl.uniform2f(gradienSubtractProgram.uniforms.texelSize, 1.0 / simWidth, 1.0 / simHeight);
    gl.uniform1i(gradienSubtractProgram.uniforms.uPressure, pressure.read.attach(0));
    gl.uniform1i(gradienSubtractProgram.uniforms.uVelocity, velocity.read.attach(1));
    blit(velocity.write.fbo); velocity.swap();

    advectionProgram.bind();
    gl.uniform2f(advectionProgram.uniforms.texelSize, 1.0 / simWidth, 1.0 / simHeight);
    if (!ext.supportLinearFiltering) gl.uniform2f(advectionProgram.uniforms.dyeTexelSize, 1.0 / simWidth, 1.0 / simHeight);
    let velocityId = velocity.read.attach(0);
    gl.uniform1i(advectionProgram.uniforms.uVelocity, velocityId);
    gl.uniform1i(advectionProgram.uniforms.uSource, velocityId);
    gl.uniform1f(advectionProgram.uniforms.dt, dt);
    gl.uniform1f(advectionProgram.uniforms.dissipation, config.VELOCITY_DISSIPATION);
    blit(velocity.write.fbo); velocity.swap();

    gl.viewport(0, 0, dyeWidth, dyeHeight);
    if (!ext.supportLinearFiltering) gl.uniform2f(advectionProgram.uniforms.dyeTexelSize, 1.0 / dyeWidth, 1.0 / dyeHeight);
    gl.uniform1i(advectionProgram.uniforms.uVelocity, velocity.read.attach(0));
    gl.uniform1i(advectionProgram.uniforms.uSource, density.read.attach(1));
    gl.uniform1f(advectionProgram.uniforms.dissipation, config.DENSITY_DISSIPATION);
    blit(density.write.fbo); density.swap();
  }

  function render(target) {
    if (config.BLOOM) applyBloom(density.read, bloom);
    if (target == null || !config.TRANSPARENT) { gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA); gl.enable(gl.BLEND); }
    else gl.disable(gl.BLEND);
    let width = target == null ? gl.drawingBufferWidth : dyeWidth;
    let height = target == null ? gl.drawingBufferHeight : dyeHeight;
    gl.viewport(0, 0, width, height);
    if (!config.TRANSPARENT) {
      colorProgram.bind();
      let bc = config.BACK_COLOR;
      gl.uniform4f(colorProgram.uniforms.color, bc.r / 255, bc.g / 255, bc.b / 255, 1);
      blit(target);
    }
    if (target == null && config.TRANSPARENT) {
      backgroundProgram.bind();
      gl.uniform1f(backgroundProgram.uniforms.aspectRatio, canvas.width / canvas.height);
      blit(null);
    }
    if (config.SHADING) {
      let program = config.BLOOM ? displayBloomShadingProgram : displayShadingProgram;
      program.bind();
      gl.uniform2f(program.uniforms.texelSize, 1.0 / width, 1.0 / height);
      gl.uniform1i(program.uniforms.uTexture, density.read.attach(0));
      if (config.BLOOM) {
        gl.uniform1i(program.uniforms.uBloom, bloom.attach(1));
        gl.uniform1i(program.uniforms.uDithering, ditheringTexture.attach(2));
        let scale = getTextureScale(ditheringTexture, width, height);
        gl.uniform2f(program.uniforms.ditherScale, scale.x, scale.y);
      }
    } else {
      let program = config.BLOOM ? displayBloomProgram : displayProgram;
      program.bind();
      gl.uniform1i(program.uniforms.uTexture, density.read.attach(0));
      if (config.BLOOM) {
        gl.uniform1i(program.uniforms.uBloom, bloom.attach(1));
        gl.uniform1i(program.uniforms.uDithering, ditheringTexture.attach(2));
        let scale = getTextureScale(ditheringTexture, width, height);
        gl.uniform2f(program.uniforms.ditherScale, scale.x, scale.y);
      }
    }
    blit(target);
  }

  function applyBloom(source, destination) {
    if (bloomFramebuffers.length < 2) return;
    let last = destination;
    gl.disable(gl.BLEND);
    bloomPrefilterProgram.bind();
    let knee = config.BLOOM_THRESHOLD * config.BLOOM_SOFT_KNEE + 0.0001;
    let curve0 = config.BLOOM_THRESHOLD - knee, curve1 = knee * 2, curve2 = 0.25 / knee;
    gl.uniform3f(bloomPrefilterProgram.uniforms.curve, curve0, curve1, curve2);
    gl.uniform1f(bloomPrefilterProgram.uniforms.threshold, config.BLOOM_THRESHOLD);
    gl.uniform1i(bloomPrefilterProgram.uniforms.uTexture, source.attach(0));
    gl.viewport(0, 0, last.width, last.height); blit(last.fbo);
    bloomBlurProgram.bind();
    for (let i = 0; i < bloomFramebuffers.length; i++) {
      let dest = bloomFramebuffers[i];
      gl.uniform2f(bloomBlurProgram.uniforms.texelSize, 1.0 / last.width, 1.0 / last.height);
      gl.uniform1i(bloomBlurProgram.uniforms.uTexture, last.attach(0));
      gl.viewport(0, 0, dest.width, dest.height); blit(dest.fbo); last = dest;
    }
    gl.blendFunc(gl.ONE, gl.ONE); gl.enable(gl.BLEND);
    for (let i = bloomFramebuffers.length - 2; i >= 0; i--) {
      let baseTex = bloomFramebuffers[i];
      gl.uniform2f(bloomBlurProgram.uniforms.texelSize, 1.0 / last.width, 1.0 / last.height);
      gl.uniform1i(bloomBlurProgram.uniforms.uTexture, last.attach(0));
      gl.viewport(0, 0, baseTex.width, baseTex.height); blit(baseTex.fbo); last = baseTex;
    }
    gl.disable(gl.BLEND);
    bloomFinalProgram.bind();
    gl.uniform2f(bloomFinalProgram.uniforms.texelSize, 1.0 / last.width, 1.0 / last.height);
    gl.uniform1i(bloomFinalProgram.uniforms.uTexture, last.attach(0));
    gl.uniform1f(bloomFinalProgram.intensity, config.BLOOM_INTENSITY);
    gl.viewport(0, 0, destination.width, destination.height); blit(destination.fbo);
  }

  function splat(x, y, dx, dy, color) {
    gl.viewport(0, 0, simWidth, simHeight);
    splatProgram.bind();
    gl.uniform1i(splatProgram.uniforms.uTarget, velocity.read.attach(0));
    gl.uniform1f(splatProgram.uniforms.aspectRatio, canvas.width / canvas.height);
    gl.uniform2f(splatProgram.uniforms.point, x / canvas.width, 1.0 - y / canvas.height);
    gl.uniform3f(splatProgram.uniforms.color, dx, -dy, 1.0);
    gl.uniform1f(splatProgram.uniforms.radius, config.SPLAT_RADIUS / 100.0);
    blit(velocity.write.fbo); velocity.swap();
    gl.viewport(0, 0, dyeWidth, dyeHeight);
    gl.uniform1i(splatProgram.uniforms.uTarget, density.read.attach(0));
    gl.uniform3f(splatProgram.uniforms.color, color.r, color.g, color.b);
    blit(density.write.fbo); density.swap();
  }

  function multipleSplats(amount) {
    for (let i = 0; i < amount; i++) {
      const color = generateColor();
      color.r *= 10.0; color.g *= 10.0; color.b *= 10.0;
      const x = canvas.width * Math.random(), y = canvas.height * Math.random();
      const dx = 1000 * (Math.random() - 0.5), dy = 1000 * (Math.random() - 0.5);
      splat(x, y, dx, dy, color);
    }
  }

  function resizeCanvas() {
    if (canvas.width != canvas.clientWidth || canvas.height != canvas.clientHeight) {
      canvas.width = canvas.clientWidth; canvas.height = canvas.clientHeight; initFramebuffers();
    }
  }

  function pointerPos(clientX, clientY) {
    const rect = canvas.getBoundingClientRect();
    return { x: clientX - rect.left, y: clientY - rect.top };
  }

  const teardown = [];
  function on(target, type, handler, opts) {
    target.addEventListener(type, handler, opts);
    teardown.push(() => target.removeEventListener(type, handler, opts));
  }

  on(window, 'mousemove', (e) => {
    const { x, y } = pointerPos(e.clientX, e.clientY);
    const p = pointers[0];
    if (!p.everMoved) { p.everMoved = true; p.x = x; p.y = y; p.down = true; return; }
    p.down = true; p.moved = true;
    p.dx = (x - p.x) * 5.0; p.dy = (y - p.y) * 5.0;
    p.x = x; p.y = y; p.color = generateColor();
  });

  on(window, 'touchmove', (e) => {
    const touches = e.targetTouches;
    for (let i = 0; i < touches.length; i++) {
      if (i >= pointers.length) pointers.push(new pointerPrototype());
      const p = pointers[i];
      const { x, y } = pointerPos(touches[i].clientX, touches[i].clientY);
      p.down = true; p.moved = p.everMoved === true; p.everMoved = true;
      p.dx = (x - p.x) * 8.0; p.dy = (y - p.y) * 8.0; p.x = x; p.y = y;
    }
  }, { passive: true });

  on(window, 'touchstart', (e) => {
    const touches = e.targetTouches;
    for (let i = 0; i < touches.length; i++) {
      if (i >= pointers.length) pointers.push(new pointerPrototype());
      const p = pointers[i];
      const { x, y } = pointerPos(touches[i].clientX, touches[i].clientY);
      p.id = touches[i].identifier; p.down = true; p.x = x; p.y = y; p.color = generateColor();
    }
  }, { passive: true });

  on(window, 'mouseup', () => { pointers[0].down = false; });

  on(window, 'touchend', (e) => {
    const touches = e.changedTouches;
    for (let i = 0; i < touches.length; i++)
      for (let j = 0; j < pointers.length; j++)
        if (touches[i].identifier == pointers[j].id) pointers[j].down = false;
  });

  return function destroy() {
    destroyed = true;
    if (rafHandle) cancelAnimationFrame(rafHandle);
    for (const off of teardown) off();
  };

  function generateColor() {
    const h = 0.5 + Math.random() * 0.42;
    let c = HSVtoRGB(h, 0.95, 1.0);
    c.r *= 0.92; c.g *= 0.92; c.b *= 0.92;
    return c;
  }

  function HSVtoRGB(h, s, v) {
    let r, g, b, i, f, p, q, t;
    i = Math.floor(h * 6); f = h * 6 - i;
    p = v * (1 - s); q = v * (1 - f * s); t = v * (1 - (1 - f) * s);
    switch (i % 6) {
      case 0: r = v; g = t; b = p; break;
      case 1: r = q; g = v; b = p; break;
      case 2: r = p; g = v; b = t; break;
      case 3: r = p; g = q; b = v; break;
      case 4: r = t; g = p; b = v; break;
      case 5: r = v; g = p; b = q; break;
    }
    return { r, g, b };
  }

  function getResolution(resolution) {
    let aspectRatio = gl.drawingBufferWidth / gl.drawingBufferHeight;
    if (aspectRatio < 1) aspectRatio = 1.0 / aspectRatio;
    let max = Math.round(resolution * aspectRatio), min = Math.round(resolution);
    return gl.drawingBufferWidth > gl.drawingBufferHeight ? { width: max, height: min } : { width: min, height: max };
  }

  function getTextureScale(texture, width, height) {
    return { x: width / texture.width, y: height / texture.height };
  }
}