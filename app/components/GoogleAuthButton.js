'use client';

import { GoogleLogin } from '@react-oauth/google';
import { useRouter } from 'next/navigation';
import confetti from 'canvas-confetti';
import { completeLogin } from '../lib/completeLogin';

const BACKEND = process.env.NEXT_PUBLIC_BACKEND_URL || 'http://localhost:8005';

export default function GoogleAuthButton({ setError, setLoading, loading }) {
  const router = useRouter();

  const handleSuccess = async (credentialResponse) => {
    setLoading(true);
    setError('');
    
    try {
      const res = await fetch(`${BACKEND}/v1/auth/google`, {
        method: 'POST',
        headers: { 
          'Content-Type': 'application/json',
          ...(process.env.NEXT_PUBLIC_NGROK_ENABLED === 'true' && { 'ngrok-skip-browser-warning': 'true' })
        },
        body: JSON.stringify({ id_token: credentialResponse.credential }),
      });
      
      const data = await res.json();
      
      if (!res.ok) {
        setError(data?.detail || 'Google sign-in failed.');
        // A 403 here is usually the sign-up domain restriction, and the fix is
        // to choose a different Google account. Google auto-selects the last
        // one used, so without this the next attempt silently reuses the
        // rejected account and appears to fail for no reason.
        if (res.status === 403) {
          window.google?.accounts?.id?.disableAutoSelect?.();
        }
        setLoading(false);
        return;
      }

      // Trigger confetti on success
      confetti({
        particleCount: 100,
        spread: 70,
        origin: { y: 0.6 },
        colors: ['#fff', '#cfdbfa', '#8896d3', '#302b5e']
      });

      await completeLogin(data.access_token, router);
    } catch (err) {
      console.error('Google auth error:', err);
      setError('An error occurred during Google sign-in.');
      setLoading(false);
    }
  };

  const handleError = () => {
    setError('Google sign-in was unsuccessful. Please try again.');
  };

  // Without a client id the provider isn't mounted — render nothing rather
  // than a button that errors on click.
  if (!process.env.NEXT_PUBLIC_GOOGLE_CLIENT_ID) return null;

  return (
    <>
    <div style={{ display: 'flex', alignItems: 'center', margin: '20px 0' }}>
      <div style={{ flex: 1, height: '1px', background: 'rgba(22,24,45,0.12)' }} />
      <span style={{ padding: '0 10px', fontSize: '12px', color: 'rgba(22,24,45,0.45)' }}>OR</span>
      <div style={{ flex: 1, height: '1px', background: 'rgba(22,24,45,0.12)' }} />
    </div>
    <div style={{ position: 'relative', width: '100%', marginTop: '12px' }}>
      {/* Custom Button styling to match "Create account" / "Log in" */}
      <button
        disabled={loading}
        style={{
          width: '100%',
          padding: '12px',
          background: '#fff',
          border: '1px solid rgba(22,24,45,0.2)',
          color: loading ? 'rgba(22,24,45,0.35)' : '#16182d',
          borderRadius: '999px',
          fontSize: '14px',
          fontWeight: 500,
          cursor: loading ? 'default' : 'pointer',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          gap: '8px'
        }}
      >
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
          <path d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z" fill="#4285F4"/>
          <path d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" fill="#34A853"/>
          <path d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z" fill="#FBBC05"/>
          <path d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" fill="#EA4335"/>
        </svg>
        Continue with Google
      </button>

      {/* Invisible GoogleLogin overlay. Unmounted while loading so the
          disabled state of the styled button underneath is actually
          enforced (clicks would otherwise hit the live iframe). Note:
          hiding Google's rendered button under a custom one sits in a
          gray zone of Google's branding guidelines — if it ever breaks
          or gets flagged, swap in the real button. */}
      {!loading && (
        <div style={{ position: 'absolute', top: 0, left: 0, width: '100%', height: '100%', opacity: 0, overflow: 'hidden', cursor: 'pointer' }}>
          <GoogleLogin
            onSuccess={handleSuccess}
            onError={handleError}
            useOneTap={false}
            theme="outline"
            shape="rectangular"
            text="continue_with"
            width="1000" // Make it very wide so it covers the button completely
          />
        </div>
      )}
    </div>
    </>
  );
}
