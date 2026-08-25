/**
 * Turning validator output into something a user can act on.
 *
 * backend/services/batch_validator.py emits a `code` and a `field` alongside
 * every message and persists all three, but the UI used to render only the
 * message — so the reader got told what was wrong and never what to do. The
 * code set is small and closed, so each one gets a fix written out once here.
 */

export const VALIDATION_FIXES = {
  invalid_json:
    'Each line must be one complete JSON object on a single line — no trailing commas, and no line breaks inside the object.',
  not_object:
    'The line is valid JSON but not an object. Every line must be an object with custom_id, method, url and body.',
  missing_field:
    'A required key is absent. Every line needs custom_id, method, url and body; body itself needs model and messages.',
  invalid_method:
    'Only POST is accepted. Set "method": "POST" on every line.',
  invalid_type:
    'A key has the wrong type — custom_id must be a string, and body.model must be a string.',
  duplicate_custom_id:
    'custom_id must be unique within the file. Results are matched back to it, so duplicates would be ambiguous.',
  unknown_fields:
    'Extra top-level keys are rejected rather than ignored. Keep only custom_id, method, url and body.',
  unsupported_endpoint:
    'That url is not served here. Use one of the endpoints named in the message.',
  model_mismatch:
    'One model per file. Either split the mixed lines into separate files, or set every body.model to the same catalogue id.',
};

/**
 * Collapse a flat error list into one entry per `code`.
 *
 * A malformed file usually breaks the same way on every line, so a flat list
 * shows the same sentence hundreds of times and truncates before reaching the
 * one error that differs. Grouping surfaces each distinct problem once.
 *
 * Returns entries sorted most-frequent first:
 *   { code, count, lines, sample, field, fix }
 */
export function groupValidationErrors(errors) {
  const byCode = new Map();

  for (const e of errors) {
    const code = e.code || 'error';
    if (!byCode.has(code)) {
      byCode.set(code, { code, count: 0, lines: [], sample: e.message, field: e.field || null });
    }
    const g = byCode.get(code);
    g.count += 1;
    if (g.lines.length < 3 && typeof e.line === 'number') g.lines.push(e.line);
  }

  return [...byCode.values()]
    .sort((a, b) => b.count - a.count)
    .map(g => ({ ...g, fix: VALIDATION_FIXES[g.code] || null }));
}

const ALLOWED_TOP_LEVEL = new Set(['custom_id', 'method', 'url', 'body']);

/**
 * Check line shape in the browser, before the upload starts.
 *
 * A deliberate subset of the server's rules — the ones decidable from the file
 * alone. Catalogue membership is not checked here; that needs the live model
 * list and the batch modal already covers it. This never *approves* a file,
 * because it only reads the first `maxLines`; it only catches the mistakes
 * that would otherwise cost a round trip and a minute of async validation.
 *
 * `truncated` says the text is a slice of a larger file, so the final line is
 * probably cut mid-object and must not be reported as malformed.
 */
export function preflightJsonl(text, { maxLines = 50, truncated = false } = {}) {
  const raw = text.split('\n');
  if (truncated) raw.pop();

  const problems = [];
  const models = [];
  const seenIds = new Set();
  let checked = 0;

  for (let i = 0; i < raw.length && checked < maxLines; i++) {
    const line = raw[i];
    if (!line.trim()) continue;
    checked += 1;
    const n = i + 1;

    let entry;
    try {
      entry = JSON.parse(line);
    } catch {
      problems.push({ line: n, code: 'invalid_json', message: 'Not valid JSON.' });
      continue;
    }

    if (entry === null || typeof entry !== 'object' || Array.isArray(entry)) {
      problems.push({ line: n, code: 'not_object', message: 'Line is not a JSON object.' });
      continue;
    }

    const extra = Object.keys(entry).filter(k => !ALLOWED_TOP_LEVEL.has(k));
    if (extra.length) {
      problems.push({ line: n, code: 'unknown_fields', message: `Unknown top-level fields: ${extra.sort().join(', ')}` });
    }

    for (const key of ['custom_id', 'method', 'url', 'body']) {
      if (entry[key] === undefined) {
        problems.push({ line: n, code: 'missing_field', field: key, message: `'${key}' is required.` });
      }
    }

    if (typeof entry.custom_id === 'string') {
      if (seenIds.has(entry.custom_id)) {
        problems.push({ line: n, code: 'duplicate_custom_id', field: 'custom_id', message: `'${entry.custom_id}' already used.` });
      }
      seenIds.add(entry.custom_id);
    } else if (entry.custom_id !== undefined) {
      problems.push({ line: n, code: 'invalid_type', field: 'custom_id', message: 'custom_id must be a string.' });
    }

    if (entry.method !== undefined && entry.method !== 'POST') {
      problems.push({ line: n, code: 'invalid_method', field: 'method', message: `method must be 'POST', got '${entry.method}'.` });
    }

    const model = entry.body?.model;
    if (typeof model === 'string' && !models.includes(model)) models.push(model);
  }

  if (models.length > 1) {
    problems.push({ code: 'model_mismatch', field: 'body.model', message: `File mixes models: ${models.join(', ')}` });
  }

  return { checked, problems, models, truncated };
}
