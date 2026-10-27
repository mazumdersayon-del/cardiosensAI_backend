import { useEffect, useRef, useState } from 'react';
import { authOptions } from '../api';

/* "Sign in / Sign up with Google" through Google Identity Services. Google hands us an ID token (the
   "credential"); the server verifies it. The button shows only when the server has GOOGLE_CLIENT_ID. */

interface GsiId {
  initialize(cfg: { client_id: string; callback: (r: { credential: string }) => void; ux_mode?: 'popup'; auto_select?: boolean; context?: string }): void;
  renderButton(el: HTMLElement, opts: Record<string, unknown>): void;
}
declare global {
  interface Window {
    google?: { accounts: { id: GsiId } };
  }
}

let scriptPromise: Promise<void> | null = null;
function loadGsi(): Promise<void> {
  scriptPromise ??= new Promise<void>((resolve, reject) => {
    const s = document.createElement('script');
    s.src = 'https://accounts.google.com/gsi/client';
    s.async = true;
    s.onload = () => resolve();
    s.onerror = () => {
      scriptPromise = null;
      reject(new Error('gsi'));
    };
    document.head.appendChild(s);
  });
  return scriptPromise;
}

/** The name and email inside a Google credential, for showing on the sign-up form (the server verifies the token). */
export function credentialProfile(credential: string): { email?: string; name?: string } {
  try {
    const part = credential.split('.')[1].replace(/-/g, '+').replace(/_/g, '/');
    const bin = atob(part.padEnd(part.length + ((4 - (part.length % 4)) % 4), '='));
    const json = JSON.parse(new TextDecoder().decode(Uint8Array.from(bin, (c) => c.charCodeAt(0))));
    return { email: json.email, name: json.name };
  } catch {
    return {};
  }
}

export default function GoogleButton({ mode, onCredential }: { mode: 'signin' | 'signup'; onCredential: (credential: string) => void }) {
  const box = useRef<HTMLDivElement>(null);
  const cb = useRef(onCredential);
  const [state, setState] = useState<'loading' | 'ready' | 'off' | 'failed'>('loading');

  useEffect(() => {
    cb.current = onCredential;
  }, [onCredential]);

  useEffect(() => {
    let live = true;
    authOptions()
      .then(async (o) => {
        if (!o.googleClientId) return live && setState('off');
        await loadGsi();
        if (!live || !box.current || !window.google) return;
        window.google.accounts.id.initialize({ client_id: o.googleClientId, callback: (r) => cb.current(r.credential), ux_mode: 'popup', context: mode });
        const dark = document.documentElement.dataset.theme === 'dark' || (!document.documentElement.dataset.theme && matchMedia('(prefers-color-scheme: dark)').matches);
        window.google.accounts.id.renderButton(box.current, {
          type: 'standard',
          theme: dark ? 'filled_black' : 'outline',
          size: 'large',
          text: mode === 'signup' ? 'signup_with' : 'continue_with',
          shape: 'pill',
          width: Math.min(box.current.clientWidth || 360, 400),
        });
        setState('ready');
      })
      .catch(() => live && setState('failed'));
    return () => {
      live = false;
    };
  }, [mode]);

  return (
    <div className="stack" style={{ gap: 6, alignItems: 'center' }}>
      <div ref={box} style={{ width: '100%', display: 'flex', justifyContent: 'center', minHeight: state === 'ready' ? 44 : 0 }} />
      {state !== 'ready' && state !== 'loading' && (
        <>
          <button type="button" className="btn ghost" disabled style={{ width: '100%' }}>
            <GoogleMark /> {mode === 'signup' ? 'Sign up with Google' : 'Continue with Google'}
          </button>
        </>
      )}
    </div>
  );
}

function GoogleMark() {
  return (
    <svg width="18" height="18" viewBox="0 0 48 48" aria-hidden="true" style={{ verticalAlign: '-4px', marginRight: 6 }}>
      <path fill="#FFC107" d="M43.6 20.5H42V20H24v8h11.3C33.7 32.7 29.2 36 24 36c-6.6 0-12-5.4-12-12s5.4-12 12-12c3.1 0 5.8 1.2 7.9 3.1l5.7-5.7C34 6.1 29.3 4 24 4 12.9 4 4 12.9 4 24s8.9 20 20 20 20-8.9 20-20c0-1.3-.1-2.4-.4-3.5z" />
      <path fill="#FF3D00" d="M6.3 14.7l6.6 4.8C14.7 15.1 19 12 24 12c3.1 0 5.8 1.2 7.9 3.1l5.7-5.7C34 6.1 29.3 4 24 4 16.3 4 9.7 8.3 6.3 14.7z" />
      <path fill="#4CAF50" d="M24 44c5.2 0 9.9-2 13.4-5.2l-6.2-5.2C29.2 35.1 26.7 36 24 36c-5.2 0-9.6-3.3-11.3-7.9l-6.5 5C9.5 39.6 16.2 44 24 44z" />
      <path fill="#1976D2" d="M43.6 20.5H42V20H24v8h11.3c-.8 2.2-2.2 4.2-4.1 5.6l6.2 5.2C37 39.2 44 34 44 24c0-1.3-.1-2.4-.4-3.5z" />
    </svg>
  );
}
