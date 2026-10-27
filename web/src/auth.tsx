import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';
import { api, refreshSession, setAccessToken, setSessionLostHandler, type AccountStatus, type MfaRequired, type TokenOut, type User } from './api';

interface AuthState {
  ready: boolean;
  user: User | null;
  status: AccountStatus | null;
  /** Any sign-in or sign-up step that ends in tokens (code, password, Google, sign-up codes). null = signed in. */
  signIn: (path: string, body: Record<string, unknown>) => Promise<MfaRequired | null>;
  verifyMfa: (mfaToken: string, code: string) => Promise<void>;
  logout: () => Promise<void>;
  reload: () => Promise<void>;
}

const Ctx = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [ready, setReady] = useState(false);
  const [user, setUser] = useState<User | null>(null);
  const [status, setStatus] = useState<AccountStatus | null>(null);

  const adopt = useCallback(async (t: TokenOut) => {
    setAccessToken(t.accessToken);
    setUser(t.user);
    setStatus(await api<AccountStatus>('GET', '/v1/auth/status'));
  }, []);

  const reload = useCallback(async () => {
    const [u, s] = await Promise.all([api<User>('GET', '/v1/me'), api<AccountStatus>('GET', '/v1/auth/status')]);
    setUser(u);
    setStatus(s);
  }, []);

  useEffect(() => {
    setSessionLostHandler(() => {
      setAccessToken(null);
      setUser(null);
      setStatus(null);
    });
    refreshSession()
      .then((t) => (t ? adopt(t) : undefined))
      .catch(() => undefined)
      .finally(() => setReady(true));
  }, [adopt]);

  const value = useMemo<AuthState>(
    () => ({
      ready,
      user,
      status,
      reload,
      async signIn(path, body) {
        const out = await api<TokenOut | MfaRequired>('POST', path, { ...body, webSession: true });
        if ('mfaRequired' in out) return out;
        await adopt(out);
        return null;
      },
      async verifyMfa(mfaToken, code) {
        await adopt(await api<TokenOut>('POST', '/v1/auth/mfa/verify', { mfaToken, code, webSession: true }));
      },
      async logout() {
        try {
          await api('POST', '/v1/auth/logout');
        } finally {
          setAccessToken(null);
          setUser(null);
          setStatus(null);
        }
      },
    }),
    [ready, user, status, reload, adopt],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAuth(): AuthState {
  const v = useContext(Ctx);
  if (!v) throw new Error('useAuth outside AuthProvider');
  return v;
}

export function useTheme(): [string, () => void] {
  const [theme, setTheme] = useState<string>(() => document.documentElement.dataset.theme || 'system');
  const toggle = () => {
    const systemDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
    const current = theme === 'system' ? (systemDark ? 'dark' : 'light') : theme;
    const next = current === 'dark' ? 'light' : 'dark';
    document.documentElement.dataset.theme = next;
    try {
      localStorage.setItem('cs-theme', next);
    } catch {
      /* private mode: theme just won't persist */
    }
    setTheme(next);
  };
  return [theme, toggle];
}
