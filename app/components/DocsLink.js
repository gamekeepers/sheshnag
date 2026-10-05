'use client';

/**
 * Link out to the documentation this deployment serves.
 *
 * Every deployment serves its own copy of the docs at /docs/ — see "Serve the
 * documentation" in the self-host guide — so the default is same-origin and
 * needs no configuration at all. Operators who would rather not build the site
 * point NEXT_PUBLIC_DOCS_URL somewhere else (the public site, an internal
 * mirror) and every link below follows.
 *
 * Two things this deliberately is not:
 *
 * - Not a next/link. /docs/ is not an app route, so the client router cannot
 *   resolve it; the browser has to issue a real request for nginx to answer
 *   with the static site.
 * - Not read at render time. NEXT_PUBLIC_* is inlined at build time, so the
 *   full process.env.NEXT_PUBLIC_DOCS_URL expression has to appear literally.
 */
const DOCS_BASE = process.env.NEXT_PUBLIC_DOCS_URL || '/docs/';

/** Absolute or root-relative URL of a documentation page, e.g. 'provider/'. */
export function docsUrl(page = '') {
  return DOCS_BASE.endsWith('/') ? `${DOCS_BASE}${page}` : `${DOCS_BASE}/${page}`;
}

/**
 * The sidebar-footer entry. Sibling of <PortalSwitch>, quieter on purpose:
 * switching portals moves you within the product, this leaves it.
 */
export default function DocsLink({ page = '', label = 'Documentation' }) {
  return (
    <DocsAnchor className="docs-link" page={page}>
      <span>{label}</span>
      <span className="docs-link-arrow" aria-hidden="true">&#8599;</span>
    </DocsAnchor>
  );
}

/**
 * A deep link into one documentation page, for use inline wherever the reader
 * is at the exact moment they need it. Opens in a new tab: the portals hold
 * their state in React, so navigating away would discard it.
 */
export function DocsAnchor({ page = '', className, children, ...rest }) {
  return (
    <a
      className={className}
      href={docsUrl(page)}
      target="_blank"
      rel="noreferrer"
      {...rest}
    >
      {children}
    </a>
  );
}
