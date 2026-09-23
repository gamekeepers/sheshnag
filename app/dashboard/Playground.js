'use client';

import { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { CopyableCode } from '../components/Teaching';

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

const POLL_MS = 2500;
const TERMINAL = new Set(['completed', 'failed']);
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

// Blank means "runtime default": the field is left out of the body rather
// than sent as 0 or null, so the exported line stays a faithful record of what
// was asked for.
function num(v) {
  if (v === '' || v == null) return null;
  const n = Number(v);
  return Number.isNaN(n) ? null : n;
}

// json_object mode constrains the *shape* only. Without a sentence saying
// what the JSON should contain, a model fills the object with whatever JSON it
// has seen most — an API error envelope is a common pick. Add the sentence
// when neither prompt has one, and keep it in the exported line.
const JSON_OBJECT_NUDGE = 'Respond with a single JSON object that answers the request.';

function needsJsonNudge(responseFormat, system, prompt) {
  return responseFormat?.type === 'json_object' && !/json/i.test(`${system}\n${prompt}`);
}

function buildRow({ customId, model, system, prompt, params, responseFormat }) {
  const messages = [];
  let sys = system.trim();
  if (needsJsonNudge(responseFormat, system, prompt)) sys = sys ? `${sys}\n${JSON_OBJECT_NUDGE}` : JSON_OBJECT_NUDGE;
  if (sys) messages.push({ role: 'system', content: sys });
  messages.push({ role: 'user', content: prompt });
  const body = { model, messages, temperature: num(params.temperature) ?? 0.7 };
  if (num(params.maxTokens) > 0) body.max_tokens = num(params.maxTokens);
  if (num(params.topP) != null) body.top_p = num(params.topP);
  if (num(params.topK) != null) body.top_k = num(params.topK);
  if (num(params.seed) != null) body.seed = num(params.seed);
  // One spelling for both runtimes: vLLM takes chat_template_kwargs natively,
  // the Ollama executor translates it to `think`. Left out on "default".
  if (params.thinking === 'on' || params.thinking === 'off') {
    body.chat_template_kwargs = { enable_thinking: params.thinking === 'on' };
  }
  if (responseFormat) body.response_format = responseFormat;
  return { custom_id: customId, method: 'POST', url: '/v1/chat/completions', body };
}

const SCHEMA_SAMPLE = JSON.stringify({
  type: 'object',
  properties: {
    title: { type: 'string' },
    year: { type: 'integer' },
    genres: { type: 'array', items: { type: 'string' } },
  },
  required: ['title', 'year'],
}, null, 2);

// OpenAI's response_format, as both executors read it: `json_object` for
// "any JSON", `json_schema` with the schema under json_schema.schema.
function buildResponseFormat(mode, schemaText) {
  if (mode === 'json_object') return { format: { type: 'json_object' }, error: null };
  if (mode === 'json_schema') {
    try {
      const schema = JSON.parse(schemaText);
      if (!schema || typeof schema !== 'object' || Array.isArray(schema)) {
        return { format: null, error: 'Schema must be a JSON object.' };
      }
      return { format: { type: 'json_schema', json_schema: { name: 'playground', schema } }, error: null };
    } catch (e) {
      return { format: null, error: `Schema is not valid JSON: ${e.message}` };
    }
  }
  return { format: null, error: null };
}

// Structured output is only useful if it parses; show it formatted when it
// does and raw when it does not, so a model that ignored the format is visible.
function prettyJson(text) {
  try {
    return { text: JSON.stringify(JSON.parse(text), null, 2), ok: true };
  } catch {
    return { text, ok: false };
  }
}

function downloadText(filename, text, type = 'application/jsonl') {
  const blob = new Blob([text], { type });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

// Output rows are `{custom_id, response, error}` with the runtime's
// OpenAI-style completion directly under `response`. Reasoning models may put
// their text under `reasoning_content` and leave `content` empty; show both
// rather than an empty box.
function extractAnswer(row) {
  if (!row) return { text: '', reasoning: '', usage: null, servedModel: null, error: 'No output row found.' };
  if (row.error) return { text: '', reasoning: '', usage: null, servedModel: null, error: String(row.error) };
  const resp = row.response || {};
  const message = resp.choices?.[0]?.message || {};
  return {
    text: typeof message.content === 'string' ? message.content : JSON.stringify(message.content ?? ''),
    reasoning: message.reasoning_content || message.reasoning || '',
    usage: resp.usage || null,
    servedModel: resp.model || null,
    error: null,
  };
}

function formatElapsed(ms) {
  if (ms == null) return '—';
  return ms < 10_000 ? `${(ms / 1000).toFixed(1)} s` : `${Math.round(ms / 1000)} s`;
}

export default function Playground({ backend, getHeaders, catalog, servableIds, modelsLoaded, onBatchCreated }) {
  const chatModels = useMemo(
    () => catalog
      .filter(m => m.task_type !== 'embedding')
      .slice()
      .sort((a, b) => {
        const sa = servableIds.has(a.id) ? 0 : 1;
        const sb = servableIds.has(b.id) ? 0 : 1;
        return sa - sb || String(a.display_name || a.id).localeCompare(String(b.display_name || b.id));
      }),
    [catalog, servableIds]
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

  const params = useMemo(
    () => ({ temperature, maxTokens, topP, topK, seed, thinking }),
    [temperature, maxTokens, topP, topK, seed, thinking]
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
      const rows = text.split('\n').filter(Boolean).map(l => { try { return JSON.parse(l); } catch { return null; } });
      const mine = rows.find(x => x && x.custom_id === customId) || rows.find(Boolean);
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

  return (
    <div className="playground">
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
                  {(m.display_name || m.id)}{m.runtime ? ` · ${m.runtime}` : ''}{servableIds.has(m.id) ? '' : ' · not resident'}
                </option>
              ))}
            </select>
            {residentModels.length === 0 && modelsLoaded && chatModels.length > 0 && (
              <div className="playground-hint warn">
                Nothing can run right now: no online worker has a catalogue model loaded. Greyed entries become selectable when a worker that serves them comes online.
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
                  <summary>Reasoning</summary>
                  <pre className="playground-output">{result.reasoning}</pre>
                </details>
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
