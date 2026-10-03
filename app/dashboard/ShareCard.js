'use client';

import LogprobStrip, { TokenDetail } from './LogprobStrip';
import { paramsFromLine, tokenWindow, clipLines, prettyJson, formatElapsed } from './playgroundLib';

/**
 * One finished playground run, laid out to be exported as an image.
 *
 * The card carries what a reader needs to trust the example — model, the
 * parameters on the line, prompt, answer, token counts — and nothing that
 * names the deployment: no worker, no batch id. Its colours are tokens on the
 * card's own root, so the same markup renders light or dark and the
 * LogprobStrip inside it follows.
 *
 * `opts.length` is `full`, `lines` (first `opts.lines` lines) or `around`
 * (the tokens either side of the marked ones; logprob view only). The line
 * cut applies to the reasoning too. Callouts render for marked tokens that
 * survive the cut.
 */
export default function ShareCard({ ref, result, opts, marked = [], onTokenClick }) {
  const chips = paramsFromLine(result.line);
  const lp = opts.showLogprobs ? result.logprobs : null;
  const answerTokens = lp?.answer?.length ? lp.answer : null;

  let answer;
  if (answerTokens) {
    const win = tokenWindow(answerTokens, { mode: opts.length, lines: opts.lines, marked });
    const shown = new Set(win.tokens.map(t => t.i));
    const visibleMarks = marked.filter(i => shown.has(i));
    const markers = new Map(visibleMarks.map((i, k) => [i, k + 1]));
    const byI = new Map(answerTokens.map(t => [t.i, t]));
    answer = (
      <>
        <LogprobStrip
          tokens={win.tokens}
          stats={lp.answerStats}
          interactive={false}
          markers={markers}
          onTokenClick={onTokenClick}
          cutStart={win.cutStart}
          cutEnd={win.cutEnd}
        />
        <div className="sc-legend mono">
          <span className="sc-ramp" /> confident → uncertain
          <span className="sc-key sc-key-flip">tok</span> flip-prone
          <span className="sc-key sc-key-off">tok</span> sampled off-argmax
        </div>
        {visibleMarks.length > 0 && (
          <div className="sc-callouts">
            {visibleMarks.map((i, k) => <TokenDetail key={i} tok={byI.get(i)} mark={k + 1} />)}
          </div>
        )}
      </>
    );
  } else {
    const raw = result.structured ? prettyJson(result.text).text : result.text;
    const clip = opts.length === 'lines' ? clipLines(raw, opts.lines) : { text: raw, cut: false };
    answer = (
      <pre className={`sc-text${result.structured ? ' mono' : ''}`}>
        {clip.text || '(empty content)'}{clip.cut && '\n…'}
      </pre>
    );
  }

  const reasoning = opts.includeReasoning && result.reasoning
    ? clipLines(result.reasoning, opts.length === 'lines' ? opts.lines : 0)
    : null;
  const usage = result.usage;

  return (
    <div ref={ref} className="share-card" data-theme={opts.theme} style={{ width: opts.width }}>
      <header className="sc-head">
        <span className="sc-model mono">{result.servedModel || result.model}</span>
        <span className="sc-chips">
          {chips.map(([k, v]) => (
            <span key={k} className="sc-chip mono"><span className="sc-chip-k">{k}</span> {v}</span>
          ))}
        </span>
      </header>

      {opts.includeSystem && result.system?.trim() && (
        <section className="sc-block">
          <div className="sc-label">System</div>
          <pre className="sc-text sc-dim">{result.system}</pre>
        </section>
      )}

      <section className="sc-block">
        <div className="sc-label">Prompt</div>
        <pre className="sc-text sc-prompt">{result.prompt}</pre>
      </section>

      {reasoning && (
        <section className="sc-block">
          <div className="sc-label">Reasoning</div>
          <pre className="sc-text sc-dim">{reasoning.text}{reasoning.cut && '\n…'}</pre>
        </section>
      )}

      <section className="sc-block">
        <div className="sc-label">Answer</div>
        {answer}
      </section>

      <footer className="sc-foot">
        <span className="mono">
          {usage ? `${usage.prompt_tokens ?? '?'} in · ${usage.completion_tokens ?? '?'} out · ` : ''}
          {formatElapsed(result.elapsedMs)} batch round trip
        </span>
        <span className="sc-brand"><span className="sc-brand-dot" />Sheshnag</span>
      </footer>
    </div>
  );
}
