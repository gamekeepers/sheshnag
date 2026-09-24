/**
 * Shared by the single-prompt Playground and the Grid: how a request becomes
 * a JSONL line, how an output row becomes an answer, and how a catalogue
 * model's availability is read off the pool.
 */

export const POLL_MS = 2500;
export const TERMINAL = new Set(['completed', 'failed']);

// Blank means "runtime default": the field is left out of the body rather
// than sent as 0 or null, so the exported line stays a faithful record of what
// was asked for.
export function num(v) {
  if (v === '' || v == null) return null;
  const n = Number(v);
  return Number.isNaN(n) ? null : n;
}

// json_object mode constrains the *shape* only. Without a sentence saying
// what the JSON should contain, a model fills the object with whatever JSON it
// has seen most — an API error envelope is a common pick. Add the sentence
// when neither prompt has one, and keep it in the exported line.
export const JSON_OBJECT_NUDGE = 'Respond with a single JSON object that answers the request.';

export function needsJsonNudge(responseFormat, system, prompt) {
  return responseFormat?.type === 'json_object' && !/json/i.test(`${system}\n${prompt}`);
}

export function buildRow({ customId, model, system, prompt, params, responseFormat }) {
  const messages = [];
  let sys = (system || '').trim();
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

export const SCHEMA_SAMPLE = JSON.stringify({
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
export function buildResponseFormat(mode, schemaText) {
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
export function prettyJson(text) {
  try {
    return { text: JSON.stringify(JSON.parse(text), null, 2), ok: true };
  } catch {
    return { text, ok: false };
  }
}

export function downloadText(filename, text, type = 'application/jsonl') {
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

export function parseOutputRows(text) {
  return text.split('\n').filter(Boolean).map(l => { try { return JSON.parse(l); } catch { return null; } }).filter(Boolean);
}

// Output rows are `{custom_id, response, error}` with the runtime's
// OpenAI-style completion directly under `response`. Reasoning models may put
// their text under `reasoning_content` and leave `content` empty; show both
// rather than an empty box.
export function extractAnswer(row) {
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

export function formatElapsed(ms) {
  if (ms == null) return '—';
  return ms < 10_000 ? `${(ms / 1000).toFixed(1)} s` : `${Math.round(ms / 1000)} s`;
}

// Availability, from the pool: `loaded` is in memory on an online worker,
// `disk` is on an online worker but not loaded (Ollama loads on first use,
// costing one model load), `unavailable` is no online worker at all. Only
// the last is refused; the middle tier is a wait, not a failure.
export const TIER_ORDER = { loaded: 0, disk: 1, unavailable: 2 };
export const TIER_LABEL = {
  loaded: 'loaded',
  disk: 'on disk, loads on first use',
  unavailable: 'unavailable',
};

export function tierOf(id, servableIds, loadedIds) {
  if (loadedIds?.has(id)) return 'loaded';
  if (servableIds?.has(id)) return 'disk';
  return 'unavailable';
}

export function sortByTier(models, servableIds, loadedIds) {
  return models
    .slice()
    .sort((a, b) => {
      const ta = TIER_ORDER[tierOf(a.id, servableIds, loadedIds)];
      const tb = TIER_ORDER[tierOf(b.id, servableIds, loadedIds)];
      return ta - tb || String(a.display_name || a.id).localeCompare(String(b.display_name || b.id));
    });
}

export function modelOptionLabel(m, servableIds, loadedIds) {
  const tier = tierOf(m.id, servableIds, loadedIds);
  return `${m.display_name || m.id}${m.runtime ? ` · ${m.runtime}` : ''} · ${TIER_LABEL[tier]}`;
}

// Word-level: the character offset where `text` first differs from `base`,
// or -1 when they match. Tokeniser-level divergence is the logprobs view's
// job; whitespace tokens are enough to point a reader at the right place.
export function firstDivergence(base, text) {
  if (base === text) return -1;
  const re = /\S+|\s+/g;
  const a = base.match(re) || [];
  const b = text.match(re) || [];
  let offset = 0;
  for (let i = 0; i < Math.max(a.length, b.length); i += 1) {
    if (a[i] !== b[i]) return offset;
    offset += b[i].length;
  }
  return -1;
}
