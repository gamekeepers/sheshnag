'use client';

import React, { useState, useEffect, useRef, useMemo, useCallback } from 'react';
import LogprobStrip from './LogprobStrip';
import {
  POLL_MS, TERMINAL, buildRow, downloadText, extractAnswer, formatElapsed,
  parseOutputRows, sortByTier, modelOptionLabel, tierOf, firstDivergence, analyzeLogprobs,
  quantSweep,
} from './playgroundLib';

/**
 * One prompt set × several arms, answers side by side.
 *
 * A grid is a group of batches, not one batch: the validator allows one model
 * per file, so each arm is its own file and its own batch, tagged with a
 * shared `metadata.grid_id`. Arms are taken by whichever worker can serve
 * them, whenever, and the table fills in per arm. Nothing has to be resident
 * at the same time.
 */

const MAX_ARMS = 5;
const MAX_PROMPTS = 50;

const ARM_DEFAULTS = {
  label: '', model: '', system: '',
  temperature: '0.7', maxTokens: '512', topP: '', topK: '', seed: '', thinking: 'default',
};

let armSeq = 0;
function newArm(overrides = {}) {
  armSeq += 1;
  return { id: armSeq, ...ARM_DEFAULTS, ...overrides };
}

function armLabel(arm, i) {
  return arm.label.trim() || `Arm ${i + 1}`;
}

function cellId(gridId, armIndex, promptIndex) {
  return `${gridId}-a${armIndex}-p${promptIndex}`;
}

const sleep = (ms) => new Promise(r => setTimeout(r, ms));

export default function PlaygroundGrid({ backend, getHeaders, chatModels, servableIds, loadedIds, logprobsIds, servableRuntimes, onBatchCreated }) {
  const [promptsText, setPromptsText] = useState('');
  const [sharedSystem, setSharedSystem] = useState('');
  const [logprobsK, setLogprobsK] = useState('0');   // shared by every arm; 0 = off
  const [hiddenStrips, setHiddenStrips] = useState(() => new Set());   // answer strips the user collapsed
  const [arms, setArms] = useState(() => [newArm(), newArm({ temperature: '0' })]);
  const [grid, setGrid] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [baseArm, setBaseArm] = useState(0);

  const runRef = useRef(0);
  useEffect(() => () => { runRef.current += 1; }, []);

  const sortedModels = useMemo(() => sortByTier(chatModels, servableIds, loadedIds), [chatModels, servableIds, loadedIds]);
  const resident = useMemo(() => sortedModels.filter(m => servableIds.has(m.id)), [sortedModels, servableIds]);

  const prompts = useMemo(
    () => promptsText.split('\n').map(s => s.trim()).filter(Boolean).slice(0, MAX_PROMPTS),
    [promptsText]
  );
  const promptCount = promptsText.split('\n').map(s => s.trim()).filter(Boolean).length;

  // An arm with no chosen model takes the first resident one, so two fresh
  // arms compare two settings of one model rather than sitting empty.
  const effectiveArms = useMemo(
    () => arms.map(a => ({ ...a, model: a.model || resident[0]?.id || '' })),
    [arms, resident]
  );
  const modelById = useMemo(
    () => new Map((chatModels || []).map(m => [m.id, m])),
    [chatModels]
  );

  // The sweep reads the first arm: it is the model the user chose, and the
  // one whose settings every arm inherits.
  const sweep = useMemo(
    () => quantSweep(chatModels || [], effectiveArms[0]?.model,
      { servableRuntimes, max: MAX_ARMS }),
    [chatModels, effectiveArms, servableRuntimes]
  );

  // One arm per quantization, every other setting taken from the first arm,
  // so the table answers what quantization costs rather than what two
  // differently-configured runs do.
  const applySweep = () => {
    const src = effectiveArms[0];
    setArms(sweep.arms.map(m => newArm({
      system: src.system,
      temperature: src.temperature, maxTokens: src.maxTokens,
      topP: src.topP, topK: src.topK, seed: src.seed, thinking: src.thinking,
      model: m.id,
      label: m.quantization || m.id,
    })));
  };

  const wantLogprobs = Number(logprobsK) > 0;
  const armProblems = effectiveArms.map(a => {
    if (!a.model) return 'no model';
    if (!servableIds.has(a.model)) return 'model unavailable';
    if (wantLogprobs && !logprobsIds?.has(a.model)) return 'no online runtime for this model returns logprobs';
    return null;
  });
  const canRun = !busy && prompts.length > 0 && effectiveArms.length > 0 && armProblems.every(p => p === null);

  const updateArm = (id, patch) => setArms(as => as.map(a => (a.id === id ? { ...a, ...patch } : a)));
  const addArm = () => setArms(as => (as.length >= MAX_ARMS ? as : [...as, newArm()]));
  const duplicateArm = (id) => setArms(as => {
    if (as.length >= MAX_ARMS) return as;
    const src = as.find(a => a.id === id);
    const i = as.indexOf(src);
    // Strip the old id before the override spread: `id: undefined` would
    // clobber the fresh id `newArm` assigns (override wins the spread).
    const { id: _srcId, ...rest } = src;
    return [...as.slice(0, i + 1), newArm({ ...rest, label: '' }), ...as.slice(i + 1)];
  });
  const removeArm = (id) => setArms(as => (as.length <= 1 ? as : as.filter(a => a.id !== id)));

  const authOnlyHeaders = useCallback(() => {
    const h = { ...getHeaders() };
    delete h['Content-Type'];
    return h;
  }, [getHeaders]);

  const linesFor = (arm, armIndex, gridId) => prompts
    .map((p, j) => JSON.stringify(buildRow({
      customId: cellId(gridId, armIndex, j),
      model: arm.model,
      system: arm.system.trim() || sharedSystem,
      prompt: p,
      params: { ...arm, logprobs: logprobsK },
      responseFormat: null,
    })))
    .join('\n') + '\n';

  const run = async () => {
    if (!canRun) return;
    const runId = ++runRef.current;
    const gridId = `grid-${Date.now().toString(36)}`;
    const startedAt = Date.now();
    const armStates = effectiveArms.map((a, i) => ({
      ...a, index: i, label: armLabel(a, i), lines: linesFor(a, i, gridId),
      batch: null, answers: {}, error: null,
    }));

    setError(null);
    setBusy(true);
    setBaseArm(0);
    setGrid({ id: gridId, prompts, arms: armStates, startedAt, elapsedMs: null });

    const patchArm = (index, patch) => setGrid(g => (g && g.id === gridId
      ? { ...g, arms: g.arms.map(x => (x.index === index ? { ...x, ...patch } : x)), elapsedMs: Date.now() - startedAt }
      : g));

    try {
      for (const arm of armStates) {
        const fd = new FormData();
        fd.append('file', new File([arm.lines], `${gridId}-arm${arm.index}.jsonl`, { type: 'application/jsonl' }));
        const up = await fetch(`${backend}/v1/files`, { method: 'POST', headers: authOnlyHeaders(), body: fd });
        if (!up.ok) throw new Error(`${arm.label}: upload failed (${up.status}).`);
        const file = await up.json();

        const cr = await fetch(`${backend}/v1/batches`, {
          method: 'POST',
          headers: getHeaders(),
          body: JSON.stringify({
            input_file_id: file.id,
            endpoint: '/v1/chat/completions',
            completion_window: '24h',
            metadata: { grid_id: gridId, arm: String(arm.index), arm_label: arm.label, model: arm.model },
          }),
        });
        if (!cr.ok) {
          const detail = await cr.json().catch(() => ({}));
          throw new Error(`${arm.label}: ${detail.detail || `batch creation failed (${cr.status})`}.`);
        }
        arm.batch = await cr.json();
        patchArm(arm.index, { batch: arm.batch });
      }
      onBatchCreated?.();

      const pending = new Set(armStates.map(a => a.index));
      while (pending.size > 0) {
        await sleep(POLL_MS);
        if (runRef.current !== runId) return;
        for (const arm of armStates) {
          if (!pending.has(arm.index)) continue;
          const r = await fetch(`${backend}/v1/batches/${arm.batch.id}`, { headers: getHeaders() });
          if (!r.ok) continue;
          const b = await r.json();
          arm.batch = b;
          if (TERMINAL.has(b.status)) {
            pending.delete(arm.index);
            if (b.status === 'completed' && b.output_file_id) {
              const out = await fetch(`${backend}/v1/files/${b.output_file_id}/content`, { headers: authOnlyHeaders() });
              if (out.ok) {
                for (const row of parseOutputRows(await out.text())) {
                  const ans = extractAnswer(row);
                  arm.answers[row.custom_id] = { ...ans, analysis: ans.logprobs ? analyzeLogprobs(ans.logprobs) : null };
                }
              } else {
                arm.error = `Could not read the output file (${out.status}).`;
              }
            } else if (b.status === 'failed') {
              arm.error = b.error_details || 'The batch failed.';
            } else {
              arm.error = 'Completed, but no output file was recorded.';
            }
          }
          patchArm(arm.index, { batch: arm.batch, answers: { ...arm.answers }, error: arm.error });
        }
      }
    } catch (e) {
      if (runRef.current === runId) setError(e.message || String(e));
    } finally {
      if (runRef.current === runId) setBusy(false);
    }
  };

  // The settings come from the arm's own first line, not the form: that is
  // the body a worker received, so the export reproduces the run as it ran.
  const settingsOf = (arm) => {
    try {
      const { body } = JSON.parse(arm.lines.split('\n')[0]);
      const { messages, ...settings } = body;
      const system = messages.find(m => m.role === 'system')?.content ?? null;
      return { settings, system };
    } catch {
      return { settings: null, system: null };
    }
  };

  const exportResults = () => {
    if (!grid) return;
    const out = {
      grid_id: grid.id,
      exported_at: new Date().toISOString(),
      prompts: grid.prompts,
      arms: grid.arms.map(a => {
        const { settings, system } = settingsOf(a);
        return {
          label: a.label,
          quantization: modelById.get(a.model)?.quantization || null,
          lineage: modelById.get(a.model)?.lineage || null,
          batch_id: a.batch?.id || null,
          status: a.batch?.status || null,
          worker_id: a.batch?.worker_id || null,
          settings,
          system,
          answers: grid.prompts.map((_, j) => {
            const ans = a.answers[cellId(grid.id, a.index, j)];
            if (!ans) return null;
            return {
              text: ans.text,
              ...(ans.reasoning ? { reasoning: ans.reasoning } : {}),
              ...(ans.servedModel ? { served_model: ans.servedModel } : {}),
              usage: ans.usage,
              ...(ans.error ? { error: ans.error } : {}),
              ...(ans.analysis ? {
                flip_prone: ans.analysis.answerStats.flip,
                logprobs: ans.logprobs.map(t => ({ token: t.token, logprob: t.logprob, top: (t.top_logprobs || []).map(a => [a.token, a.logprob]) })),
              } : {}),
            };
          }),
        };
      }),
    };
    downloadText(`${grid.id}.json`, JSON.stringify(out, null, 2), 'application/json');
  };

  const armStatus = (a) => {
    if (a.error) return 'failed';
    if (!a.batch) return 'queued';
    return a.batch.status;
  };

  return (
    <div className="playground grid-mode">
      <div className="panel">
        <div className="grid-top">
          <div className="field grid-prompts">
            <label>Prompts <span className="dim">one per line · {promptCount}{promptCount > MAX_PROMPTS ? ` (first ${MAX_PROMPTS} used)` : ''}</span></label>
            <textarea
              rows={6}
              value={promptsText}
              onChange={e => setPromptsText(e.target.value)}
              placeholder={'Summarise the plot of Inception in one sentence.\nWhich hand does the Statue of Liberty hold the torch in?\nName three uses for a paperclip.'}
              disabled={busy}
            />
          </div>
          <div>
            <div className="field">
              <label>Shared system prompt <span className="dim">(optional, arms can override)</span></label>
              <textarea rows={4} value={sharedSystem} onChange={e => setSharedSystem(e.target.value)} placeholder="You are a concise assistant." disabled={busy} />
            </div>
            <div className="field">
              <label>Logprobs <span className="dim">(all arms)</span></label>
              <select value={logprobsK} onChange={e => setLogprobsK(e.target.value)} disabled={busy}>
                <option value="0">Off</option>
                <option value="5">Top 5 per token</option>
                <option value="10">Top 10 per token</option>
                <option value="20">Top 20 per token</option>
              </select>
              {wantLogprobs && <div className="playground-hint">Cells gain a token strip and a flip-prone count. Arms on a runtime without logprobs are refused.</div>}
            </div>
          </div>
        </div>

        <div className="grid-arms">
          {arms.map((arm, i) => {
            const eff = effectiveArms[i];
            const tier = tierOf(eff.model, servableIds, loadedIds);
            return (
              <div key={arm.id} className="grid-arm">
                <div className="grid-arm-head">
                  <input
                    className="grid-arm-label"
                    value={arm.label}
                    onChange={e => updateArm(arm.id, { label: e.target.value })}
                    placeholder={`Arm ${i + 1}`}
                    disabled={busy}
                  />
                  <span className="grid-arm-tools">
                    <button className="btn" onClick={() => duplicateArm(arm.id)} disabled={busy || arms.length >= MAX_ARMS} title="Duplicate">⧉</button>
                    <button className="btn" onClick={() => removeArm(arm.id)} disabled={busy || arms.length <= 1} title="Remove">✕</button>
                  </span>
                </div>
                <div className="field">
                  <label>Model</label>
                  <select value={eff.model} onChange={e => updateArm(arm.id, { model: e.target.value })} disabled={busy}>
                    {resident.length === 0 && <option value="">No model is resident on an online worker</option>}
                    {sortedModels.map(m => (
                      <option key={m.id} value={m.id} disabled={!servableIds.has(m.id)}>
                        {modelOptionLabel(m, servableIds, loadedIds)}
                      </option>
                    ))}
                  </select>
                  {tier === 'disk' && <div className="playground-hint">On disk, not loaded: the first prompt pays one model load.</div>}
                  {armProblems[i] && <div className="playground-hint warn">{armProblems[i]}</div>}
                </div>
                <div className="playground-params">
                  <div className="field"><label>Temp</label><input type="number" min="0" max="2" step="0.1" value={arm.temperature} onChange={e => updateArm(arm.id, { temperature: e.target.value })} disabled={busy} /></div>
                  <div className="field"><label>Top p</label><input type="number" min="0" max="1" step="0.05" placeholder="default" value={arm.topP} onChange={e => updateArm(arm.id, { topP: e.target.value })} disabled={busy} /></div>
                  <div className="field"><label>Top k</label><input type="number" min="1" step="1" placeholder="default" value={arm.topK} onChange={e => updateArm(arm.id, { topK: e.target.value })} disabled={busy} /></div>
                  <div className="field"><label>Seed</label><input type="number" step="1" placeholder="random" value={arm.seed} onChange={e => updateArm(arm.id, { seed: e.target.value })} disabled={busy} /></div>
                  <div className="field"><label>Max tok</label><input type="number" min="1" step="1" value={arm.maxTokens} onChange={e => updateArm(arm.id, { maxTokens: e.target.value })} disabled={busy} /></div>
                  <div className="field">
                    <label>Thinking</label>
                    <select value={arm.thinking} onChange={e => updateArm(arm.id, { thinking: e.target.value })} disabled={busy}>
                      <option value="default">Default</option>
                      <option value="on">On</option>
                      <option value="off">Off</option>
                    </select>
                  </div>
                </div>
                <div className="field">
                  <label>System override <span className="dim">(optional)</span></label>
                  <textarea rows={1} value={arm.system} onChange={e => updateArm(arm.id, { system: e.target.value })} placeholder="uses the shared one" disabled={busy} />
                </div>
              </div>
            );
          })}
          {arms.length < MAX_ARMS && (
            <button className="grid-arm grid-arm-add" onClick={addArm} disabled={busy}>+ Add arm</button>
          )}
        </div>

        {sweep.lineage && (
          <div className="playground-hint">
            <button className="btn" onClick={applySweep} disabled={busy || sweep.arms.length < 2}>
              Sweep quants · {sweep.arms.length}
            </button>{' '}
            {sweep.arms.length > 1
              ? `${sweep.arms.length} quantizations of these weights are servable on ${sweep.runtime}.`
              : 'Only one quantization of these weights is servable right now.'}
            {sweep.unservable.length > 0
              && ` Staged nowhere: ${sweep.unservable.map(m => m.quantization || m.id).join(', ')}.`}
            {sweep.overflow.length > 0
              && ` Past the ${MAX_ARMS}-arm limit: ${sweep.overflow.map(m => m.quantization || m.id).join(', ')}.`}
          </div>
        )}

        <div className="playground-actions">
          <button className="btn primary" onClick={run} disabled={!canRun}>
            {busy ? 'Running…' : `Run grid · ${prompts.length} × ${arms.length}`}
          </button>
          {grid && <button className="btn" onClick={exportResults} disabled={busy}>Export results</button>}
          {grid?.elapsedMs != null && <span className="dim grid-elapsed">{formatElapsed(grid.elapsedMs)}</span>}
        </div>
        {error && <div className="playground-hint warn">{error}</div>}
      </div>

      {grid && (
        <div className="panel grid-result">
          <div className="grid-result-head">
            <div className="section-title">Grid <span className="mono dim">{grid.id}</span></div>
            {grid.arms.length > 1 && (
              <label className="grid-base-pick">
                <span className="dim">Mark divergence from</span>
                <select value={baseArm} onChange={e => setBaseArm(Number(e.target.value))}>
                  {grid.arms.map(a => <option key={a.index} value={a.index}>{a.label}</option>)}
                </select>
              </label>
            )}
          </div>
          <div className="table-container grid-table-wrap">
            <table className="grid-table">
              <thead>
                <tr>
                  {grid.arms.map(a => {
                    const st = armStatus(a);
                    return (
                      <th key={a.index}>
                        <div className="grid-arm-title">{a.label}</div>
                        <div className="mono dim grid-arm-model">{a.model}</div>
                        <span className={`badge ${st}`}><span className="pip" />{st}</span>
                        {a.batch?.worker_id && <div className="mono dim grid-arm-worker">{a.batch.worker_id}</div>}
                        <div className="grid-arm-export">
                          <button className="btn" onClick={() => downloadText(`${grid.id}-arm${a.index}.jsonl`, a.lines)}>JSONL</button>
                        </div>
                      </th>
                    );
                  })}
                </tr>
              </thead>
              <tbody>
                {grid.prompts.map((p, j) => {
                  const base = grid.arms[baseArm]?.answers[cellId(grid.id, baseArm, j)];
                  return (
                    <React.Fragment key={j}>
                    <tr className="grid-prompt-row">
                      <td colSpan={grid.arms.length}><span className="dim mono">#{j + 1}</span> {p}</td>
                    </tr>
                    <tr>
                      {grid.arms.map(a => {
                        const ans = a.answers[cellId(grid.id, a.index, j)];
                        if (!ans) {
                          const st = armStatus(a);
                          return <td key={a.index} className="grid-cell dim">{a.error ? a.error : st === 'completed' ? 'no row' : st}</td>;
                        }
                        if (ans.error) return <td key={a.index} className="grid-cell warn">{ans.error}</td>;
                        const at = (a.index !== baseArm && base && !base.error) ? firstDivergence(base.text, ans.text) : -1;
                        return (
                          <td key={a.index} className="grid-cell">
                            <div className="grid-cell-answer">
                              {at < 0 ? ans.text : <>{ans.text.slice(0, at)}<mark className="grid-diverge">{ans.text.slice(at)}</mark></>}
                              {!ans.text && <span className="dim">(empty content)</span>}
                            </div>
                            {ans.reasoning && (
                              <details className="playground-reasoning">
                                <summary>Reasoning{ans.analysis?.reasoningStats?.n ? ` · ${ans.analysis.reasoningStats.flip} flip-prone of ${ans.analysis.reasoningStats.n}` : ''}</summary>
                                {ans.analysis?.reasoning?.length
                                  ? <LogprobStrip tokens={ans.analysis.reasoning} compact />
                                  : <div className="grid-cell-answer dim">{ans.reasoning}</div>}
                              </details>
                            )}
                            {ans.analysis && (() => {
                              const key = cellId(grid.id, a.index, j);
                              const open = !hiddenStrips.has(key);
                              return (
                                <>
                                  {open && <LogprobStrip tokens={ans.analysis.answer} stats={ans.analysis.answerStats} label="Answer tokens" compact />}
                                  <button
                                    type="button"
                                    className="btn lp-toggle"
                                    onClick={() => setHiddenStrips(s => { const n = new Set(s); n.has(key) ? n.delete(key) : n.add(key); return n; })}
                                  >
                                    {open ? 'Hide tokens' : `Show tokens · ${ans.analysis.answerStats.flip} flip-prone of ${ans.analysis.answerStats.n}`}
                                  </button>
                                </>
                              );
                            })()}
                            <div className="grid-cell-meta mono dim">
                              {ans.usage ? `${ans.usage.prompt_tokens ?? '?'} in · ${ans.usage.completion_tokens ?? '?'} out` : ''}
                              {a.index === baseArm && grid.arms.length > 1 ? ' · base' : at < 0 && a.index !== baseArm ? ' · identical' : ''}
                            </div>
                          </td>
                        );
                      })}
                    </tr>
                    </React.Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
          <div className="playground-hint">
            Each column is one batch on the Batches tab, tagged <span className="mono">grid_id={grid.id}</span>. Highlight = first word that differs from the base column.
          </div>
        </div>
      )}
    </div>
  );
}
