'use client';

import { useState, useRef, useEffect, useLayoutEffect, useMemo } from 'react';
import ShareCard from './ShareCard';
import { pickCallouts, tokenWindow } from './playgroundLib';

const WIDTHS = [
  { value: 1200, label: '1200 · blog' },
  { value: 1080, label: '1080 · social' },
];
const CARD_BG = { dark: '#0B0B10', light: '#FFFFFF' };

function fileName(result) {
  const model = String(result.servedModel || result.model || 'run').replace(/[^a-z0-9._-]+/gi, '-');
  return `sheshnag-${model}-${new Date().toISOString().slice(0, 10)}.png`;
}

/**
 * Export a finished run as a PNG. The preview is the card itself, scaled to
 * fit; the export renders the unscaled node at 2× so text stays sharp on a
 * retina screen. With logprobs on, clicking a token in the preview marks or
 * unmarks it for a callout.
 */
export default function ShareDialog({ result, onClose }) {
  const answerTokens = result.logprobs?.answer?.length ? result.logprobs.answer : null;
  const [opts, setOpts] = useState({
    theme: 'light',
    width: 1200,
    length: 'full',
    lines: 12,
    includeSystem: Boolean(result.system?.trim()),
    includeReasoning: false,
    showLogprobs: Boolean(answerTokens),
  });
  const [marked, setMarked] = useState(() => (answerTokens ? pickCallouts(answerTokens) : []));
  const [status, setStatus] = useState(null);   // { kind: 'busy' | 'ok' | 'error', text }

  const set = (k) => (e) => {
    const v = e.target.type === 'checkbox' ? e.target.checked : e.target.value;
    setOpts(o => ({ ...o, [k]: k === 'width' || k === 'lines' ? Number(v) : v }));
  };

  const toggleMark = (i) => setMarked(m => (m.includes(i) ? m.filter(x => x !== i) : [...m, i].sort((a, b) => a - b)));
  const autoPick = () => {
    if (!answerTokens) return;
    const visible = opts.length === 'lines' ? tokenWindow(answerTokens, { mode: 'lines', lines: opts.lines }).tokens : answerTokens;
    setMarked(pickCallouts(visible));
  };

  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  // Scale the full-size card into the preview box; the wrapper takes the
  // scaled height so the dialog scrolls by what is actually visible.
  const boxRef = useRef(null);
  const cardRef = useRef(null);
  const [fit, setFit] = useState({ scale: 1, height: 0 });
  useLayoutEffect(() => {
    const box = boxRef.current;
    const card = cardRef.current;
    if (!box || !card) return undefined;
    const measure = () => {
      const scale = Math.min(1, box.clientWidth / opts.width);
      setFit({ scale, height: card.offsetHeight * scale });
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(box);
    ro.observe(card);
    return () => ro.disconnect();
  }, [opts.width]);

  const render = async () => {
    const { toBlob } = await import('html-to-image');
    await document.fonts.ready;
    const blob = await toBlob(cardRef.current, { pixelRatio: 2, backgroundColor: CARD_BG[opts.theme] });
    if (!blob) throw new Error('The browser returned an empty image.');
    return blob;
  };

  const download = async () => {
    setStatus({ kind: 'busy', text: 'Rendering…' });
    try {
      const blob = await render();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = fileName(result);
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      setStatus({ kind: 'ok', text: 'Downloaded.' });
    } catch (e) {
      setStatus({ kind: 'error', text: `Could not render the image: ${e.message || e}` });
    }
  };

  const copy = async () => {
    if (typeof ClipboardItem === 'undefined' || !navigator.clipboard?.write) {
      setStatus({ kind: 'error', text: 'This browser cannot put images on the clipboard. Use Download.' });
      return;
    }
    setStatus({ kind: 'busy', text: 'Rendering…' });
    try {
      // The blob goes in as a promise so the write starts inside the click;
      // Safari drops clipboard access once the gesture has returned.
      await navigator.clipboard.write([new ClipboardItem({ 'image/png': render() })]);
      setStatus({ kind: 'ok', text: 'Copied to the clipboard.' });
    } catch (e) {
      setStatus({ kind: 'error', text: `Could not copy the image: ${e.message || e}` });
    }
  };

  const busy = status?.kind === 'busy';
  const lengthOptions = useMemo(() => [
    { value: 'full', label: 'Whole answer' },
    { value: 'lines', label: 'First N lines' },
    ...(opts.showLogprobs && answerTokens ? [{ value: 'around', label: 'Around marked tokens' }] : []),
  ], [opts.showLogprobs, answerTokens]);
  const length = lengthOptions.some(o => o.value === opts.length) ? opts.length : 'full';

  return (
    <div className="modal-overlay open" onClick={e => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="modal share-modal" role="dialog" aria-label="Share run as image">
        <h3>Share as image</h3>
        <p className="modal-sub">
          A PNG of this run for a post or a slide. Worker and batch id are left off.
          {opts.showLogprobs && ' Click a token in the preview to add or remove a callout.'}
        </p>

        <div className="share-controls">
          <div className="field">
            <label>Theme</label>
            <select value={opts.theme} onChange={set('theme')}>
              <option value="light">Light</option>
              <option value="dark">Dark</option>
            </select>
          </div>
          <div className="field">
            <label>Width</label>
            <select value={opts.width} onChange={set('width')}>
              {WIDTHS.map(w => <option key={w.value} value={w.value}>{w.label}</option>)}
            </select>
          </div>
          <div className="field">
            <label>Answer</label>
            <select value={length} onChange={set('length')}>
              {lengthOptions.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
            </select>
          </div>
          {length === 'lines' && (
            <div className="field">
              <label>Lines</label>
              <input type="number" min="1" max="200" value={opts.lines} onChange={set('lines')} />
            </div>
          )}
        </div>

        <div className="share-toggles">
          {result.system?.trim() && (
            <label><input type="checkbox" checked={opts.includeSystem} onChange={set('includeSystem')} /> System prompt</label>
          )}
          {result.reasoning && (
            <label><input type="checkbox" checked={opts.includeReasoning} onChange={set('includeReasoning')} /> Reasoning</label>
          )}
          <label title={answerTokens ? '' : 'This run was made without logprobs.'}>
            <input type="checkbox" checked={opts.showLogprobs} onChange={set('showLogprobs')} disabled={!answerTokens} /> Logprobs
          </label>
          {opts.showLogprobs && answerTokens && (
            <>
              <button type="button" className="btn share-mini" onClick={autoPick}>Auto-pick callouts</button>
              <button type="button" className="btn share-mini" onClick={() => setMarked([])} disabled={!marked.length}>Clear callouts</button>
            </>
          )}
        </div>

        <div className="share-preview" ref={boxRef} style={{ height: fit.height || undefined }}>
          <div style={{ transform: `scale(${fit.scale})`, transformOrigin: 'top left', width: opts.width }}>
            <ShareCard
              ref={cardRef}
              result={result}
              opts={{ ...opts, length }}
              marked={marked}
              onTokenClick={opts.showLogprobs ? toggleMark : undefined}
            />
          </div>
        </div>

        <div className="modal-actions">
          {status && <span className={`share-status ${status.kind}`}>{status.text}</span>}
          <button className="btn" onClick={onClose}>Close</button>
          <button className="btn" onClick={copy} disabled={busy}>Copy image</button>
          <button className="btn primary" onClick={download} disabled={busy}>Download PNG</button>
        </div>
      </div>
    </div>
  );
}
