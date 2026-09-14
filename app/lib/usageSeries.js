// Daily buckets for the usage charts.
//
// Extracted from the user dashboard so the provider portal cannot quietly make
// a different call on the one rule that matters here: a batch's `usage` is null
// until its output file has been ingested, so a day can be fully counted,
// partly counted, or not counted at all — and only the first reads as
// throughput. Rendering the other two as zero says "this hardware produced
// nothing", which is a different and false claim.
//
// Callers normalise their own rows first (the two portals get different shapes
// from different endpoints); everything below works on `UsageRow`:
//
//   { at, total, done, failed, usage: { prompt_tokens, completion_tokens,
//                                       total_tokens } | null, status }
//
// `at` is unix seconds — whichever timestamp the caller considers the row's
// date. The dashboard uses created_at (when the user submitted it); the
// provider uses assigned_at (when its hardware picked it up), because those
// answer different questions and can fall on different days.

export const toLocalDateStr = (date) => {
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, '0');
  const d = String(date.getDate()).padStart(2, '0');
  return `${y}-${m}-${d}`;
};

export function emptyDay(date) {
  return {
    date: toLocalDateStr(date),
    displayDate: date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' }),
    requests: 0,
    successful: 0,
    failed: 0,
    promptTokens: 0,
    completionTokens: 0,
    totalTokens: 0,
    // Jobs whose tokens are in the sums above, and jobs that finished without
    // any. A day with counted === 0 and awaitingCount > 0 is uncounted, not idle.
    counted: 0,
    countedRequests: 0,
    awaitingCount: 0,
  };
}

/** `days` daily buckets ending today (or `offsetDays` before it). */
export function buildDailySeries(rows, days, offsetDays = 0) {
  const series = [...Array(days)].map((_, i) => {
    const d = new Date();
    d.setDate(d.getDate() - (days - 1 - i) - offsetDays);
    return emptyDay(d);
  });
  const byDate = new Map(series.map(d => [d.date, d]));

  rows.forEach(r => {
    if (!r.at) return;
    const day = byDate.get(toLocalDateStr(new Date(r.at * 1000)));
    if (!day) return;
    day.requests += r.total || 0;
    day.successful += r.done || 0;
    day.failed += r.failed || 0;
    if (r.usage) {
      day.promptTokens += r.usage.prompt_tokens || 0;
      day.completionTokens += r.usage.completion_tokens || 0;
      day.totalTokens += r.usage.total_tokens || 0;
      day.counted += 1;
      // Only the requests whose tokens are actually in the numerator, so a day
      // that mixes counted and uncounted jobs does not understate per-request.
      day.countedRequests += (r.done || 0) + (r.failed || 0);
    } else if (r.status === 'completed') {
      // Only a completed job is *missing* a count. A running one has none yet
      // by definition, and flagging those marks every active day incomplete.
      day.awaitingCount += 1;
    }
  });
  return series;
}

export const sumSeries = (series, key) => series.reduce((acc, d) => acc + d[key], 0);

// A window whose leading days are all empty renders as a run of zero-height
// bars that reads "the platform is broken" rather than "you started on
// Tuesday". Trim the empty head only — the window still ends today.
export function trimLeadingEmpty(series) {
  const first = series.findIndex(d => d.requests > 0 || d.totalTokens > 0);
  return first <= 0 ? series : series.slice(first);
}

/**
 * One daily series per distinct `keyOf(row)`, aligned on the same dates.
 *
 * Returns { keys, data } where `data` is a single array of day objects each
 * carrying `tok:<key>` and `req:<key>` fields — the shape a stacked chart
 * wants, since every series has to share one x-axis row.
 */
export function buildStackedSeries(rows, days, keyOf) {
  const keys = [...new Set(rows.map(keyOf).filter(Boolean))];
  const base = buildDailySeries(rows, days);
  const perKey = new Map(
    keys.map(k => [k, buildDailySeries(rows.filter(r => keyOf(r) === k), days)]),
  );
  const data = base.map((day, i) => {
    const merged = { ...day };
    keys.forEach(k => {
      const d = perKey.get(k)[i];
      merged[`tok:${k}`] = d.totalTokens;
      merged[`req:${k}`] = d.requests;
    });
    return merged;
  });
  return { keys, data };
}
