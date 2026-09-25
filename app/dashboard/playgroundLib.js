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
  // 0 / blank = off. The row then carries `logprobs` and the validator routes
  // it only to a runtime that advertises the capability.
  if (num(params.logprobs) > 0) {
    body.logprobs = true;
    body.top_logprobs = Math.min(20, num(params.logprobs));
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
  const logprobs = resp.choices?.[0]?.logprobs?.content;
  return {
    text: typeof message.content === 'string' ? message.content : JSON.stringify(message.content ?? ''),
    reasoning: message.reasoning_content || message.reasoning || '',
    usage: resp.usage || null,
    servedModel: resp.model || null,
    logprobs: Array.isArray(logprobs) && logprobs.length ? logprobs : null,
    error: null,
  };
}

// One bf16 ulp at the logit magnitudes these models run at (16–32): the
// finest gap two logits can have. A top-2 gap at or under it is a tie that
// any kernel or batch-shape change can resolve the other way.
export const FLIP_GAP = 0.125;

// Per-token view of `choices[0].logprobs.content`: probability, sorted
// alternatives, top-2 gap, and whether the position is flip-prone. Reasoning
// tokens come first in the array when the model thought; they are split off
// at the closing think tag so each strip shows one thing.
export function analyzeLogprobs(content) {
  const tokens = (content || []).map((t, i) => {
    const alts = (t.top_logprobs || []).slice().sort((a, b) => b.logprob - a.logprob);
    const gap = alts.length >= 2 ? alts[0].logprob - alts[1].logprob : null;
    return {
      i,
      token: t.token,
      logprob: t.logprob,
      p: Math.exp(t.logprob),
      alts,
      gap,
      flipProne: gap != null && gap <= FLIP_GAP,
      offArgmax: alts.length > 0 && alts[0].token !== t.token,
    };
  });
  const end = tokens.findIndex(t => t.token === '</think>');
  const reasoning = end >= 0 ? tokens.slice(0, end) : [];
  const answer = end >= 0 ? tokens.slice(end + 1) : tokens;
  const stats = (list) => {
    const n = list.length;
    if (!n) return { n: 0, flip: 0, offArgmax: 0, meanLogprob: null, minP: null };
    const flip = list.filter(t => t.flipProne).length;
    const offArgmax = list.filter(t => t.offArgmax).length;
    const meanLogprob = list.reduce((s, t) => s + t.logprob, 0) / n;
    const minP = list.reduce((m, t) => (t.p < m.p ? t : m), list[0]);
    return { n, flip, offArgmax, meanLogprob, minP };
  };
  return { reasoning, answer, reasoningStats: stats(reasoning), answerStats: stats(answer) };
}

export function pct(p) {
  if (p == null) return '—';
  if (p >= 0.9995) return '>99.9%';
  if (p < 0.0005) return '<0.05%';
  return `${(p * 100).toFixed(p < 0.1 ? 2 : 1)}%`;
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

// Effective bits per weight, which is the axis a quant sweep asks about and
// the order the result table has to read in. The names are not sortable as
// strings — Q2_K, Q4_K_M and Q8_0 happen to sort right, IQ4_XS and F16 do
// not — so the ladder is stated.
export const QUANT_BITS = {
  q2_k: 2.6,
  q3_k_s: 3.4, q3_k_m: 3.9, q3_k_l: 4.3,
  iq4_xs: 4.3, mxfp4: 4.3,
  q4_0: 4.5, q4_k_s: 4.6, q4_k_m: 4.8, q4_k_xl: 5.0,
  q5_0: 5.5, q5_k_s: 5.5, q5_k_m: 5.7,
  q6_k: 6.6,
  q8_0: 8.5, fp8: 8,
  f16: 16, fp16: 16, bf16: 16,
  f32: 32, fp32: 32,
};

export function quantRank(q) {
  if (!q) return Infinity;
  const key = String(q).toLowerCase();
  if (key in QUANT_BITS) return QUANT_BITS[key];
  // An unlisted name still sorts by the width in it — a new Q4_K_XXL lands
  // among the 4-bit entries rather than at the end.
  const digits = key.match(/(\d+)/);
  return digits ? Number(digits[1]) : Infinity;
}

/**
 * The quantizations of one model, as grid arms.
 *
 * Membership is `lineage` — the same weights — narrowed to one engine. Q4 on
 * one engine against Q8 on another measures both at once and the table gives
 * no hint that it happened, so the engine is held still and comparing engines
 * stays a separate study.
 *
 * The engine comes from `servableRuntimes`, which the pool derives from the
 * scheduler's own eligibility predicate. The catalogue's `runtime` column is
 * deprecated and names one engine for an entry that several serving profiles
 * may cover, so a sweep partitioned on it would hide arms the pool would
 * happily run. The pinned engine is the one the most of these weights can be
 * served on now, preferring one the selected model shares.
 *
 * `unservable` and `overflow` are returned rather than dropped: a sweep
 * missing its cheapest arm reads as a result about quality, and the reader
 * has to be told it is a result about what was loaded.
 */
export function quantSweep(models, modelId, { servableRuntimes, max = 5 } = {}) {
  const empty = { runtime: null, lineage: null, arms: [], unservable: [], overflow: [] };
  const base = (models || []).find(m => m.id === modelId);
  if (!base || !base.lineage) return empty;

  const family = (models || [])
    .filter(m => m.lineage === base.lineage)
    .sort((a, b) => quantRank(a.quantization) - quantRank(b.quantization)
      || String(a.id).localeCompare(String(b.id)));

  const runtimesOf = (id) => servableRuntimes?.get(id) || new Set();

  const counts = new Map();
  family.forEach(m => runtimesOf(m.id).forEach(
    r => counts.set(r, (counts.get(r) || 0) + 1)
  ));
  const shared = runtimesOf(base.id);
  const pinned = [...counts.keys()].sort((a, b) => (
    (shared.has(b) ? 1 : 0) - (shared.has(a) ? 1 : 0)
    || counts.get(b) - counts.get(a)
    || a.localeCompare(b)
  ))[0] || null;

  const servable = pinned ? family.filter(m => runtimesOf(m.id).has(pinned)) : [];
  const chosen = new Set(servable.slice(0, max).map(m => m.id));
  return {
    runtime: pinned,
    lineage: base.lineage,
    arms: servable.slice(0, max),
    unservable: family.filter(m => !runtimesOf(m.id).has(pinned)),
    overflow: servable.filter(m => !chosen.has(m.id)),
  };
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
