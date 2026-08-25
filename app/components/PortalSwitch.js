'use client';

import Link from 'next/link';

/**
 * The one navigation control that spans both portals.
 *
 * It used to be built twice: a nav item at the bottom of the user portal's
 * <nav> (so it read as a seventh tab) and a dim inline-styled footer link in
 * the provider portal. Same control, different position, element and weight
 * depending on which side you were standing on. Switching portals is a change
 * of context rather than a change of tab, so both sidebars now render this in
 * the footer, above sign out, from here.
 */
export default function PortalSwitch({ to }) {
  const isProvider = to === 'provider';

  return (
    <Link
      className="portal-switch"
      href={isProvider ? '/provider' : '/dashboard'}
      title={
        isProvider
          ? "Manage your organization's workers"
          : 'Back to batches, files and API keys'
      }
    >
      <span className="portal-switch-label">
        {isProvider ? 'Provider portal' : 'User portal'}
      </span>
      <span className="portal-switch-arrow" aria-hidden="true">
        &rarr;
      </span>
    </Link>
  );
}
