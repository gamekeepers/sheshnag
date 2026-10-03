'use client';

import { useState } from 'react';
import { FLIP_GAP, pct } from './playgroundLib';

/**
 * Tokens as the model saw them: each one shaded by its probability, flagged
 * when the top-2 gap is within one bf16 ulp, and clickable for the top-k
 * alternatives at that position. The number a hosted API never shows: how
 * many positions were a coin toss.
 *
 * With `interactive={false}` the strip is a picture of itself — spans, no
 * hover titles, no detail panel — for the share card, where `markers` numbers
 * the positions its callouts explain and `onTokenClick` lets the preview
 * choose them.
 */

function shade(p) {
  // Confident tokens fade into the background; uncertain ones light up.
  const heat = Math.min(1, Math.max(0, 1 - p));
  return `rgba(251, 191, 36, ${(0.08 + heat * 0.55).toFixed(3)})`;
}

function glyph(token) {
  return token === '\n' ? '⏎' : token === '\n\n' ? '⏎⏎' : token;
}

/** One position's alternatives: the chosen token, its probability, and the top-k it beat. */
export function TokenDetail({ tok, mark }) {
  return (
    <div className="lp-detail">
      <div className="lp-detail-head mono">
        {mark != null && <span className="lp-mark lp-mark-head">{mark}</span>}
        #{tok.i} {JSON.stringify(tok.token)} · {pct(tok.p)} · logprob {tok.logprob.toFixed(4)}
        {tok.gap != null && <> · top-2 gap {tok.gap.toFixed(4)}{tok.flipProne ? ` (≤ ${FLIP_GAP}, flip-prone)` : ''}</>}
        {tok.offArgmax && ' · sampled, not the argmax'}
      </div>
      <table className="lp-alts">
        <tbody>
          {tok.alts.map((a, k) => (
            <tr key={k} className={a.token === tok.token ? 'lp-alt-chosen' : ''}>
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
  );
}

export default function LogprobStrip({
  tokens, stats, label, compact = false,
  interactive = true, markers, onTokenClick, cutStart = false, cutEnd = false,
}) {
  const [picked, setPicked] = useState(null);
  if (!tokens || tokens.length === 0) return null;
  const sel = interactive && picked != null ? tokens.find(t => t.i === picked) : null;

  const tokClass = (t) => `lp-tok${t.flipProne ? ' lp-flip' : ''}${t.offArgmax ? ' lp-off' : ''}`;

  return (
    <div className={`lp${compact ? ' lp-compact' : ''}${interactive ? '' : ' lp-static'}`}>
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
        {cutStart && <span className="lp-cut">…</span>}
        {interactive ? tokens.map(t => (
          <button
            key={t.i}
            type="button"
            className={`${tokClass(t)}${picked === t.i ? ' lp-picked' : ''}`}
            style={{ background: shade(t.p) }}
            title={`${JSON.stringify(t.token)} · ${pct(t.p)}${t.gap != null ? ` · gap ${t.gap.toFixed(3)}` : ''}`}
            onClick={() => setPicked(picked === t.i ? null : t.i)}
          >
            {glyph(t.token)}
          </button>
        )) : tokens.map(t => {
          const mark = markers?.get(t.i);
          const className = `${tokClass(t)}${mark != null ? ' lp-marked' : ''}`;
          const body = (
            <>
              {glyph(t.token)}
              {mark != null && <span className="lp-mark">{mark}</span>}
            </>
          );
          return onTokenClick ? (
            <button
              key={t.i}
              type="button"
              className={`${className} lp-pickable`}
              style={{ background: shade(t.p) }}
              aria-pressed={mark != null}
              onClick={() => onTokenClick(t.i)}
            >
              {body}
            </button>
          ) : (
            <span key={t.i} className={className} style={{ background: shade(t.p) }}>{body}</span>
          );
        })}
        {cutEnd && <span className="lp-cut">…</span>}
      </div>
      {sel && <TokenDetail tok={sel} />}
    </div>
  );
}
