'use client';

import { useState } from 'react';

/**
 * Shared pieces for the in-product teaching layer.
 *
 * Extracted after the fact, not designed up front: the dropzone, the
 * first-batch empty state and the key reveal were each written by hand first,
 * and between them hand-rolled the same code-plus-copy-button five times. Only
 * what all three actually needed lives here.
 */

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    // Clipboard API is undefined on insecure origins — fall back to a
    // throwaway textarea, which still works over plain http.
    const ta = document.createElement('textarea');
    ta.value = text;
    document.body.appendChild(ta);
    ta.select();
    document.execCommand('copy');
    ta.remove();
  }
}

/**
 * A snippet with its own copy button and copied-state.
 *
 * `display` exists because the dropzone shows one line of a three-line sample
 * but copies all of it — what you read and what you get are allowed to differ,
 * so long as the copy is the complete, runnable thing.
 */
export function CopyableCode({ code, display, label, copyLabel = 'Copy', actions = null, style }) {
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    await copyText(code);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  return (
    <div style={style}>
      <div className="teach-head">
        {label ? <span className="teach-title">{label}</span> : <span />}
        <span style={{ display: 'inline-flex', gap: '0.5rem' }}>
          {actions}
          <button
            className="btn"
            style={{ padding: '2px 10px', fontSize: '0.72rem', color: copied ? '#00D287' : undefined }}
            onClick={handleCopy}
          >
            {copied ? 'Copied ✓' : copyLabel}
          </button>
        </span>
      </div>
      <pre className="teach-code">{display ?? code}</pre>
    </div>
  );
}

/** Panel shown in place of an empty table or a chart of zeroes. */
export function TeachingEmptyState({ title, children, style }) {
  return (
    <div className="panel teach-empty" style={style}>
      <div className="teach-title" style={{ fontSize: '1rem' }}>{title}</div>
      {children}
    </div>
  );
}

/** One numbered step inside a TeachingEmptyState. */
export function TeachingStep({ n, children }) {
  return (
    <div className="teach-step">
      <span className="teach-step-n">{n}</span>
      <div className="teach-body" style={{ margin: 0 }}>{children}</div>
    </div>
  );
}
