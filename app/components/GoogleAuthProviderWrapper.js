'use client';

import { GoogleOAuthProvider } from '@react-oauth/google';

export default function GoogleAuthProviderWrapper({ children }) {
  const clientId = process.env.NEXT_PUBLIC_GOOGLE_CLIENT_ID || '';
  return (
    <GoogleOAuthProvider clientId={clientId}>
      {children}
    </GoogleOAuthProvider>
  );
}
