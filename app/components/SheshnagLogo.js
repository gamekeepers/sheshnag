'use client';

/**
 * The product wordmark.
 *
 * Was defined three times: the user dashboard and the provider portal held
 * byte-identical copies, and admin held a third that differed in weight,
 * tracking and typeface — so the one element on every authenticated screen
 * was the one element that did not match across them. One copy, here.
 */
export default function SheshnagLogo({ size = 22 }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
      <svg width={size} height={size} viewBox="0 0 32 32" fill="none">
        <path
          d="M22.5 6.5C11.5 5.5 9.5 13 15.5 15.6C21.5 18.2 23 25.5 11 26.5"
          stroke="#fff"
          strokeWidth="3.2"
          strokeLinecap="round"
        />
        <circle cx="23" cy="6.7" r="2.2" fill="#fff" />
      </svg>
      <span
        style={{
          color: '#fff',
          fontSize: size * 0.63,
          fontWeight: 700,
          letterSpacing: '0.12em',
          fontFamily: 'IBM Plex Mono, monospace',
        }}
      >
        SHESHNAG
      </span>
    </div>
  );
}
