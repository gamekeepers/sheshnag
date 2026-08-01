'use client';

import { GoogleOAuthProvider } from '@react-oauth/google';

export default function GoogleAuthProviderWrapper({ children }) {
  const clientId = process.env.NEXT_PUBLIC_GOOGLE_CLIENT_ID;
  // No client id configured → skip the provider entirely; GoogleAuthButton
  // hides itself in the same condition, so auth degrades to email/password.
  if (!clientId) return children;
  return (
    <GoogleOAuthProvider clientId={clientId}>
      {children}
    </GoogleOAuthProvider>
  );
}
