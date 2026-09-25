'use client';

import Link from 'next/link';

/**
 * The one navigation control that spans the portals.
 *
 * Switching portals is a change of context rather than a change of tab, so
 * every sidebar renders this in the footer, above sign out, from here. The
 * user and provider portals link to each other; the admin panel links back to
 * the user portal and is offered only to superadmins, which the calling page
 * decides from the user's platform role.
 */
const PORTALS = {
  user: {
    href: '/dashboard',
    label: 'User portal',
    title: 'Back to batches, files and API keys',
  },
  provider: {
    href: '/provider',
    label: 'Provider portal',
    title: "Manage your organization's workers",
  },
  admin: {
    href: '/admin',
    label: 'Admin panel',
    title: 'Users, workers and sign-up domains across every organization',
  },
};

export default function PortalSwitch({ to }) {
  const portal = PORTALS[to] ?? PORTALS.user;

  return (
    <Link className="portal-switch" href={portal.href} title={portal.title}>
      <span>{portal.label}</span>
      <span className="portal-switch-arrow" aria-hidden="true">
        &rarr;
      </span>
    </Link>
  );
}
