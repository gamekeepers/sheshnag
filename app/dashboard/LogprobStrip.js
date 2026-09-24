'use client';

import { useState } from 'react';
import { FLIP_GAP, pct } from './playgroundLib';

/**
 * Tokens as the model saw them: each one shaded by its probability, flagged
 * when the top-2 gap is within one bf16 ulp, and clickable for the top-k
 * alternatives at that position. The number a hosted API never shows: how
 * many positions were a coin toss.
 */

function shade(p) {
  // Confident tokens fade into the background; uncertain ones light up.
  const heat = Math.min(1, Math.max(0, 1 - p));
  return `rgba(251, 191, 36, ${(0.08 + heat * 0.55).toFixed(3)})`;
}

export default function LogprobStrip({ tokens, stats, label, compact = false }) {
  const [picked, setPicked] = useState(null);
  if (!tokens || tokens.length === 0) return null;
  const sel = picked != null ? tokens.find(t => t.i === picked) : null;

  return (
    <div className={`lp ${compact ? 'lp-compact' : ''}`}>
      {(label || stats) && (
        <div className="lp-head">
          {label && <span className="lp-label">{label}</span>}
          {stats && stats.n > 0 && (
            <span className="lp-stats mono dim">
              {stats.n} tokens · <span className={stats.flip ? 'lp-stat-flip' : ''}>{stats.flip} flip-prone</span>
              {stats.offArgmax ? ` · ${stats.offArgmax} sampled off-argmax` : ''}
              {stats.meanLogprob != null ? ` · mean logprob ${stats.meanLogprob.toFixed(3)}` : ''}
            </span>
          )}
        </div>
      )}
      <div className="lp-strip">
        {tokens.map(t => (
          <button
            key={t.i}
            type="button"
            className={`lp-tok${t.flipProne ? ' lp-flip' : ''}${t.offArgmax ? ' lp-off' : ''}${picked === t.i ? ' lp-picked' : ''}`}
            style={{ background: shade(t.p) }}
            title={`${JSON.stringify(t.token)} · ${pct(t.p)}${t.gap != null ? ` · gap ${t.gap.toFixed(3)}` : ''}`}
            onClick={() => setPicked(picked === t.i ? null : t.i)}
          >
            {t.token === '\n' ? '⏎' : t.token === '\n\n' ? '⏎⏎' : t.token}
          </button>
        ))}
      </div>
      {sel && (
        <div className="lp-detail">
          <div className="lp-detail-head mono">
            #{sel.i} {JSON.stringify(sel.token)} · {pct(sel.p)} · logprob {sel.logprob.toFixed(4)}
            {sel.gap != null && <> · top-2 gap {sel.gap.toFixed(4)}{sel.flipProne ? ` (≤ ${FLIP_GAP}, flip-prone)` : ''}</>}
            {sel.offArgmax && ' · sampled, not the argmax'}
          </div>
          <table className="lp-alts">
            <tbody>
              {sel.alts.map((a, k) => (
                <tr key={k} className={a.token === sel.token ? 'lp-alt-chosen' : ''}>
                  <td className="mono">{JSON.stringify(a.token)}</td>
                  <td className="mono">{a.logprob.toFixed(4)}</td>
                  <td>
                    <span className="lp-bar" style={{ width: `${Math.max(1, Math.exp(a.logprob) * 100)}%` }} />
                    <span className="mono dim">{pct(Math.exp(a.logprob))}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
