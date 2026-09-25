'use client';

import { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { CopyableCode } from '../components/Teaching';
import PlaygroundGrid from './PlaygroundGrid';
import LogprobStrip from './LogprobStrip';
import {
  POLL_MS, TERMINAL, num, JSON_OBJECT_NUDGE, needsJsonNudge, buildRow, SCHEMA_SAMPLE,
  buildResponseFormat, prettyJson, downloadText, parseOutputRows, extractAnswer, formatElapsed,
  sortByTier, modelOptionLabel, tierOf, analyzeLogprobs,
} from './playgroundLib';

const HISTORY_LIMIT = 8;

// The lifecycle as the user sees it. `uploading` and `submitting` are local
// phases before a batch id exists; the rest are the batch's own status values.
const STEPS = [
  { key: 'uploading', label: 'Upload', note: 'Sending the one-line file.' },
  { key: 'validating', label: 'Validate', note: 'Checking the line, same as any batch.' },
  { key: 'validated', label: 'Queue', note: 'Waiting for a worker that serves this model to poll.' },
  { key: 'in_progress', label: 'Run', note: 'A worker is generating the answer.' },
  { key: 'completed', label: 'Done', note: '' },
];
const STEP_INDEX = Object.fromEntries(STEPS.map((s, i) => [s.key, i]));
STEP_INDEX.submitting = STEP_INDEX.uploading;

/**
 * One prompt, one model, one answer — and the JSONL line that produced it.
 *
 * There is no synchronous inference path: workers pull batches on a ~5 s poll
 * and nothing else reaches a GPU. So a playground run *is* a batch — a one-line
 * file uploaded, submitted, polled to a terminal state and its output file
 * read back. The steps are shown rather than hidden because they are the
 * mental model the user needs for the next thousand prompts, and every result
 * hands back the exact line to put in that file.
 *
 * The batch status stream (`/v1/batches/{id}/events`) is not used: EventSource
 * cannot carry the bearer header the route requires, and it only announces
 * validation anyway. Polling `GET /v1/batches/{id}` covers the whole lifecycle.
 */

export default function Playground({ backend, getHeaders, catalog, servableIds, loadedIds, logprobsIds, servableRuntimes, modelsLoaded, onBatchCreated }) {
  const [mode, setMode] = useState('single');   // single | grid
  const chatModels = useMemo(
    () => sortByTier(catalog.filter(m => m.task_type !== 'embedding'), servableIds, loadedIds),
    [catalog, servableIds, loadedIds]
  );

  // Empty until the user picks; the effective model below falls back to the
  // first entry an online worker can serve, so the form is never blank.
  const [chosenModel, setChosenModel] = useState('');
  const [system, setSystem] = useState('');
  const [prompt, setPrompt] = useState('');
  const [temperature, setTemperature] = useState('0.7');
  const [maxTokens, setMaxTokens] = useState('512');
  const [topP, setTopP] = useState('');
  const [topK, setTopK] = useState('');
  const [seed, setSeed] = useState('');
  const [thinking, setThinking] = useState('default'); // default | on | off — reasoning models only
  const [logprobsK, setLogprobsK] = useState('0');    // 0 = off; else top_logprobs per position
  const [rfMode, setRfMode] = useState('text');          // text | json_object | json_schema
  const [schemaText, setSchemaText] = useState(SCHEMA_SAMPLE);

  const [phase, setPhase] = useState('idle');        // idle | uploading | submitting | <batch status>
  const [errorAt, setErrorAt] = useState(null);      // phase when the run errored, for the step row
  const [batch, setBatch] = useState(null);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const [elapsed, setElapsed] = useState(null);
  const [history, setHistory] = useState([]);

  const runRef = useRef(0);          // bumps per run so a stale poll loop exits
  const startedRef = useRef(null);
  const phaseRef = useRef('idle');   // last lifecycle phase reached; phase itself becomes 'error'

  // Record the phase for the step row, but only values that name a step: a
  // server-side 'failed' status should not move the marker past the last step
  // actually reached (the run failed *while in* that step).
  const toPhase = (p) => {
    if (STEP_INDEX[p] != null) phaseRef.current = p;
    setPhase(p);
  };

  // A model is resident when an online worker can serve it now; only those
  // are selectable. The default is the first resident one, never a
  // catalogue entry that would leave the run queued indefinitely.
  const residentModels = useMemo(() => chatModels.filter(m => servableIds.has(m.id)), [chatModels, servableIds]);
  const model = chosenModel || residentModels[0]?.id || '';

  useEffect(() => () => { runRef.current += 1; }, []);

  const busy = phase !== 'idle' && !TERMINAL.has(phase) && phase !== 'error';
  const selected = chatModels.find(m => m.id === model);
  const servable = selected ? servableIds.has(selected.id) : false;

  const canLogprobs = Boolean(model) && logprobsIds?.has(model);
  const params = useMemo(
    () => ({ temperature, maxTokens, topP, topK, seed, thinking, logprobs: canLogprobs ? logprobsK : '0' }),
    [temperature, maxTokens, topP, topK, seed, thinking, logprobsK, canLogprobs]
  );
  const { format: responseFormat, error: schemaError } = useMemo(
    () => buildResponseFormat(rfMode, schemaText),
    [rfMode, schemaText]
  );
  const row = useMemo(
    () => buildRow({ customId: 'playground-1', model, system, prompt, params, responseFormat }),
    [model, system, prompt, params, responseFormat]
  );
  const canRun = Boolean(model) && servable && prompt.trim().length > 0 && !schemaError;
  const runtimeNotes = [];
  if (selected?.runtime === 'vllm' && num(topK) != null) {
    runtimeNotes.push('top_k is dropped by the vLLM executor today (issue #49); the line still records it.');
  }
  const rowJsonl = `${JSON.stringify(row)}\n`;

  const authOnlyHeaders = useCallback(() => {
    // Multipart needs the browser to set Content-Type with its boundary.
    const h = { ...getHeaders() };
    delete h['Content-Type'];
    return h;
  }, [getHeaders]);

  const sleep = (ms) => new Promise(r => setTimeout(r, ms));

  const run = async () => {
    if (!canRun || busy) return;
    const runId = ++runRef.current;
    const customId = `playground-${Date.now()}`;
    const line = `${JSON.stringify(buildRow({ customId, model, system, prompt, params, responseFormat }))}\n`;

    setError(null);
    setResult(null);
    setErrorAt(null);
    setBatch(null);
    setElapsed(null);
    startedRef.current = Date.now();

    try {
      toPhase('uploading');
      const fd = new FormData();
      fd.append('file', new File([line], `${customId}.jsonl`, { type: 'application/jsonl' }));
      const up = await fetch(`${backend}/v1/files`, { method: 'POST', headers: authOnlyHeaders(), body: fd });
      if (!up.ok) throw new Error(`Upload failed (${up.status}).`);
      const file = await up.json();

      toPhase('submitting');
      const cr = await fetch(`${backend}/v1/batches`, {
        method: 'POST',
        headers: getHeaders(),
        body: JSON.stringify({ input_file_id: file.id, endpoint: '/v1/chat/completions', completion_window: '24h' }),
      });
      if (!cr.ok) {
        const detail = await cr.json().catch(() => ({}));
        throw new Error(detail.detail || `Batch creation failed (${cr.status}).`);
      }
      let b = await cr.json();
      setBatch(b);
      toPhase(b.status);
      onBatchCreated?.();

      while (!TERMINAL.has(b.status)) {
        await sleep(POLL_MS);
        if (runRef.current !== runId) return;
        const r = await fetch(`${backend}/v1/batches/${b.id}`, { headers: getHeaders() });
        if (!r.ok) throw new Error(`Could not read batch status (${r.status}).`);
        b = await r.json();
        setBatch(b);
        toPhase(b.status);
        setElapsed(Date.now() - startedRef.current);
      }
      const took = Date.now() - startedRef.current;
      setElapsed(took);

      if (b.status === 'failed') {
        throw new Error(b.error_details || 'The batch failed. See the Batches tab for details.');
      }
      if (!b.output_file_id) throw new Error('Completed, but no output file was recorded.');

      const out = await fetch(`${backend}/v1/files/${b.output_file_id}/content`, { headers: authOnlyHeaders() });
      if (!out.ok) throw new Error(`Could not read the output file (${out.status}).`);
      const text = await out.text();
      const rows = parseOutputRows(text);
      const mine = rows.find(x => x.custom_id === customId) || rows[0];
      const answer = extractAnswer(mine);
      if (answer.error) throw new Error(answer.error);

      const entry = {
        id: b.id,
        model,
        servedModel: answer.servedModel,
        worker: b.worker_id || null,
        prompt,
        system,
        text: answer.text,
        reasoning: answer.reasoning,
        usage: answer.usage,
        logprobs: answer.logprobs ? analyzeLogprobs(answer.logprobs) : null,
        elapsedMs: took,
        line,
        structured: rfMode !== 'text',
      };
      setResult(entry);
      setHistory(h => [entry, ...h].slice(0, HISTORY_LIMIT));
    } catch (e) {
      if (runRef.current !== runId) return;
      setError(e.message || String(e));
      setErrorAt(phaseRef.current);
      setPhase('error');
    }
  };

  const stepIndex = STEP_INDEX[phase === 'error' ? errorAt : phase] ?? -1;

  const modeSwitch = (
    <div className="playground-mode" role="tablist">
      <button role="tab" aria-selected={mode === 'single'} className={mode === 'single' ? 'active' : ''} onClick={() => setMode('single')}>Single prompt</button>
      <button role="tab" aria-selected={mode === 'grid'} className={mode === 'grid' ? 'active' : ''} onClick={() => setMode('grid')}>Grid</button>
    </div>
  );

  if (mode === 'grid') {
    return (
      <div className="playground">
        {modeSwitch}
        <PlaygroundGrid
          backend={backend}
          getHeaders={getHeaders}
          chatModels={chatModels}
          servableIds={servableIds}
          loadedIds={loadedIds}
          logprobsIds={logprobsIds}
          servableRuntimes={servableRuntimes}
          onBatchCreated={onBatchCreated}
        />
      </div>
    );
  }

  return (
    <div className="playground">
      {modeSwitch}
      <div className="playground-grid">
        {/* ── Left: the request ─────────────────────────────────────── */}
        <div className="panel">
          <div className="field">
            <label>Model</label>
            <select value={model} onChange={e => setChosenModel(e.target.value)} disabled={busy}>
              {residentModels.length === 0 && (
                <option value="">
                  {!modelsLoaded ? 'Loading catalogue…'
                    : chatModels.length === 0 ? 'No chat models in the catalogue'
                    : 'No model is resident on an online worker'}
                </option>
              )}
              {chatModels.map(m => (
                <option key={m.id} value={m.id} disabled={!servableIds.has(m.id)}>
                  {modelOptionLabel(m, servableIds, loadedIds)}
                </option>
              ))}
            </select>
            {selected && tierOf(selected.id, servableIds, loadedIds) === 'disk' && (
              <div className="playground-hint">On disk, not loaded: the first run pays one model load, then it stays warm for a few minutes.</div>
            )}
            {residentModels.length === 0 && modelsLoaded && chatModels.length > 0 && (
              <div className="playground-hint warn">
                Nothing can run right now: no online worker has a catalogue model. Greyed entries become selectable when a worker that serves them comes online.
              </div>
            )}
            {selected && !servable && (
              <div className="playground-hint warn">
                <span className="mono">{selected.id}</span> is no longer resident on an online worker. Pick another model.
              </div>
            )}
          </div>

          <div className="field">
            <label>System prompt <span className="dim">(optional)</span></label>
            <textarea
              rows={2}
              value={system}
              onChange={e => setSystem(e.target.value)}
              placeholder="You are a concise assistant."
              disabled={busy}
            />
          </div>

          <div className="field">
            <label>Prompt</label>
            <textarea
              rows={7}
              value={prompt}
              onChange={e => setPrompt(e.target.value)}
              placeholder="Summarise the plot of Inception in three sentences."
              disabled={busy}
            />
          </div>

          <div className="playground-params">
            <div className="field">
              <label>Temperature</label>
              <input type="number" min="0" max="2" step="0.1" value={temperature} onChange={e => setTemperature(e.target.value)} disabled={busy} />
            </div>
            <div className="field">
              <label>Top p</label>
              <input type="number" min="0" max="1" step="0.05" placeholder="default" value={topP} onChange={e => setTopP(e.target.value)} disabled={busy} />
            </div>
            <div className="field">
              <label>Top k</label>
              <input type="number" min="1" step="1" placeholder="default" value={topK} onChange={e => setTopK(e.target.value)} disabled={busy} />
            </div>
            <div className="field">
              <label>Seed</label>
              <input type="number" step="1" placeholder="random" value={seed} onChange={e => setSeed(e.target.value)} disabled={busy} />
            </div>
            <div className="field">
              <label>Max tokens</label>
              <input type="number" min="1" step="1" value={maxTokens} onChange={e => setMaxTokens(e.target.value)} disabled={busy} />
            </div>
          </div>

          <div className="playground-params">
            <div className="field">
              <label>Thinking</label>
              <select value={thinking} onChange={e => setThinking(e.target.value)} disabled={busy}>
                <option value="default">Model default</option>
                <option value="on">On</option>
                <option value="off">Off</option>
              </select>
              <div className="playground-hint">Reasoning models only; others ignore it. The trace shows above the answer.</div>
            </div>
            <div className="field">
              <label>Logprobs</label>
              <select value={canLogprobs ? logprobsK : '0'} onChange={e => setLogprobsK(e.target.value)} disabled={busy || !canLogprobs}>
                <option value="0">Off</option>
                <option value="5">Top 5 per token</option>
                <option value="10">Top 10 per token</option>
                <option value="20">Top 20 per token</option>
              </select>
              <div className="playground-hint">
                {canLogprobs
                  ? 'Each token shaded by its probability, with the alternatives the model weighed. Flip-prone = top-2 within one bf16 ulp.'
                  : model ? 'No online runtime serving this model returns log-probabilities (vLLM, llama.cpp, or Ollama ≥ 0.12.11 do).' : ''}
              </div>
            </div>
          </div>

          <div className="field">
            <label>Response format</label>
            <select value={rfMode} onChange={e => setRfMode(e.target.value)} disabled={busy}>
              <option value="text">Free text</option>
              <option value="json_object">JSON object (any shape)</option>
              <option value="json_schema">JSON matching a schema</option>
            </select>
            {rfMode === 'json_object' && (
              <div className="playground-hint">
                {needsJsonNudge(responseFormat, system, prompt)
                  ? `Neither prompt mentions JSON, so "${JSON_OBJECT_NUDGE}" is added to the system prompt. Say what the object should contain for a useful answer.`
                  : 'The model decides the keys. Use "JSON matching a schema" to fix them.'}
              </div>
            )}
          </div>
          {rfMode === 'json_schema' && (
            <div className="field">
              <label>JSON schema</label>
              <textarea
                rows={8}
                className="mono"
                value={schemaText}
                onChange={e => setSchemaText(e.target.value)}
                spellCheck={false}
                disabled={busy}
              />
              {schemaError && <div className="playground-hint warn">{schemaError}</div>}
            </div>
          )}
          {runtimeNotes.map(n => <div key={n} className="playground-hint warn">{n}</div>)}

          <div className="playground-actions">
            <button className="btn primary" onClick={run} disabled={busy || !canRun}>
              {busy ? 'Running…' : 'Run'}
            </button>
            <button className="btn" onClick={() => downloadText('playground.jsonl', rowJsonl)} disabled={!canRun}>
              Export as JSONL
            </button>
          </div>

          <div className="playground-steps" aria-live="polite">
            {STEPS.map((s, i) => {
              const state = phase === 'error' ? (i < stepIndex ? 'done' : i === stepIndex ? 'failed' : '')
                : i < stepIndex ? 'done' : i === stepIndex ? 'active' : '';
              return (
                <div key={s.key} className={`playground-step ${state}`}>
                  <span className="playground-step-dot" />
                  <span className="playground-step-label">{s.label}</span>
                </div>
              );
            })}
          </div>
          {busy && STEPS[stepIndex]?.note && (
            <div className="playground-hint">
              {STEPS[stepIndex].note}{elapsed != null && ` · ${formatElapsed(elapsed)}`}
            </div>
          )}
        </div>

        {/* ── Right: the answer ─────────────────────────────────────── */}
        <div className="panel">
          {error && (
            <div className="playground-error">
              <div className="section-title">Failed</div>
              <pre className="playground-output">{error}</pre>
              {batch && <div className="playground-hint">Batch <span className="mono">{batch.id}</span> — open it on the Batches tab for the full report.</div>}
            </div>
          )}

          {!error && !result && (
            <div className="teach-empty playground-empty">
              {busy
                ? 'Waiting for the answer. A one-prompt batch usually settles in 10–20 seconds once a worker is free.'
                : 'Pick a model, write a prompt, press Run. The answer lands here, with the batch line that produced it.'}
            </div>
          )}

          {result && (
            <>
              <div className="section-title">Answer</div>
              {result.reasoning && (
                <details className="playground-reasoning">
                  <summary>Reasoning{result.logprobs?.reasoningStats?.n ? ` · ${result.logprobs.reasoningStats.flip} flip-prone of ${result.logprobs.reasoningStats.n}` : ''}</summary>
                  {result.logprobs?.reasoning?.length
                    ? <LogprobStrip tokens={result.logprobs.reasoning} stats={result.logprobs.reasoningStats} />
                    : <pre className="playground-output">{result.reasoning}</pre>}
                </details>
              )}
              {result.logprobs?.answer?.length > 0 && (
                <LogprobStrip tokens={result.logprobs.answer} stats={result.logprobs.answerStats} label="Answer tokens" />
              )}
              {result.structured ? (() => {
                const shown = prettyJson(result.text);
                return (
                  <>
                    {!shown.ok && <div className="playground-hint warn">Structured output was requested but the answer is not valid JSON.</div>}
                    <pre className={`playground-output${shown.ok ? ' mono' : ''}`}>{shown.text || '(empty content)'}</pre>
                  </>
                );
              })() : (
                <pre className="playground-output">{result.text || '(empty content)'}</pre>
              )}

              <dl className="batch-facts playground-facts">
                <div className="batch-fact"><dt>Model</dt><dd className="mono">{result.servedModel || result.model}</dd></div>
                <div className="batch-fact"><dt>Worker</dt><dd className="mono">{result.worker || '—'}</dd></div>
                <div className="batch-fact"><dt>Time</dt><dd>{formatElapsed(result.elapsedMs)}</dd></div>
                <div className="batch-fact">
                  <dt>Tokens</dt>
                  <dd>
                    {result.usage
                      ? `${result.usage.prompt_tokens ?? '?'} in · ${result.usage.completion_tokens ?? '?'} out`
                      : '—'}
                  </dd>
                </div>
                <div className="batch-fact"><dt>Batch</dt><dd className="mono">{result.id}</dd></div>
              </dl>

              <div className="teach playground-scale">
                <div className="teach-head"><span className="teach-title">Run this at scale</span></div>
                <div className="teach-body">
                  This answer came from a one-line batch file. Put one line per prompt in the same shape,
                  upload it on the Files tab, and submit it as a batch — same model, same parameters, any number of rows.
                </div>
                <CopyableCode
                  code={result.line}
                  label="The line that produced it"
                  actions={
                    <button className="btn" style={{ padding: '2px 10px', fontSize: '0.72rem' }} onClick={() => downloadText(`${result.id}.jsonl`, result.line)}>
                      Download
                    </button>
                  }
                />
              </div>
            </>
          )}
        </div>
      </div>

      {history.length > 1 && (
        <div className="panel">
          <div className="section-title">Earlier runs this session</div>
          <div className="playground-history">
            {history.slice(1).map(h => (
              <button key={h.id} className="playground-history-row" onClick={() => { setResult(h); setError(null); }}>
                <span className="mono dim">{h.servedModel || h.model}</span>
                <span className="playground-history-prompt">{h.prompt}</span>
                <span className="dim">{formatElapsed(h.elapsedMs)}</span>
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
