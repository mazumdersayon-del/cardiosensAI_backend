import { useEffect, useRef, useState, type ReactNode } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { api, ApiError } from '../api';
import { useAuth } from '../auth';
import { ThemeButton } from '../components/Layout';
import { Banner, errorText } from '../components/ui';

function AuthShell({ title, children, sub }: { title: string; sub?: ReactNode; children: ReactNode }) {
  return (
    <div className="auth-wrap">
      <div className="auth-card stack">
        <div className="card stack">
          <div className="logo">
            <img src="/logos/cardiosensai-logo.png" alt="CardiosensAI" />
          </div>
          <div>
            <h1 style={{ textAlign: 'center' }}>{title}</h1>
            {sub && <p className="muted" style={{ textAlign: 'center' }}>{sub}</p>}
          </div>
          {children}
        </div>
        <div className="row spread">
          <small>Not a diagnostic service. No clinical interpretation is shown.</small>
          <ThemeButton className="btn ghost small" />
        </div>
      </div>
    </div>
  );
}

export function LoginPage() {
  const { login, verifyMfa } = useAuth();
  const nav = useNavigate();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [mfaToken, setMfaToken] = useState<string | null>(null);
  const [code, setCode] = useState('');
  const [err, setErr] = useState<ApiError | string | null>(null);
  const [busy, setBusy] = useState(false);
  const [resent, setResent] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      if (mfaToken) {
        await verifyMfa(mfaToken, code);
        nav('/', { replace: true });
      } else {
        const mfa = await login(email, password);
        if (mfa) setMfaToken(mfa.mfaToken);
        else nav('/', { replace: true });
      }
    } catch (ex) {
      setErr(ex instanceof ApiError ? ex : errorText(ex));
      if (ex instanceof ApiError && ex.status === 401 && mfaToken) setMfaToken(null);
    } finally {
      setBusy(false);
    }
  }

  const unverified = err instanceof ApiError && err.code === 'email_not_verified';
  return (
    <AuthShell title={mfaToken ? 'Two-step sign-in' : 'Sign in'} sub={mfaToken ? 'Enter the 6-digit code from your authenticator app.' : 'Patients, doctors and clinics all sign in here.'}>
      <form className="stack" onSubmit={submit}>
        {!mfaToken ? (
          <>
            <label className="field">
              Email
              <input type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} required autoFocus />
            </label>
            <label className="field">
              Password
              <input type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required />
            </label>
          </>
        ) : (
          <label className="field">
            Authenticator code
            <input inputMode="numeric" autoComplete="one-time-code" maxLength={6} value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, ''))} required autoFocus />
          </label>
        )}
        {err && <Banner kind="error">{typeof err === 'string' ? err : err.message}</Banner>}
        {unverified && (
          <button
            type="button"
            className="btn ghost"
            disabled={resent}
            onClick={async () => {
              await api('POST', '/v1/auth/email/resend', { email }).catch(() => undefined);
              setResent(true);
            }}
          >
            {resent ? 'Sent. Check your inbox' : 'Send the confirmation email again'}
          </button>
        )}
        <button type="submit" className="btn primary" disabled={busy}>
          {busy ? 'Signing in…' : mfaToken ? 'Verify' : 'Sign in'}
        </button>
      </form>
      {!mfaToken && (
        <div className="row spread">
          <Link to="/forgot-password">Forgot password?</Link>
          <Link to="/register">Create an account</Link>
        </div>
      )}
    </AuthShell>
  );
}

const ROLES = [
  { key: 'patient', label: 'Patient', hint: 'Track your own vitals and share them with your doctor or clinic' },
  { key: 'doctor', label: 'Doctor', hint: 'Registered medical practitioner. Verified by CardiosensAI before you can see patients' },
  { key: 'clinic', label: 'Clinic', hint: 'Clinic or hospital. Verified by CardiosensAI before you can see patients' },
] as const;

export function RegisterPage() {
  const [role, setRole] = useState<'patient' | 'doctor' | 'clinic'>('patient');
  const [f, setF] = useState<Record<string, string>>({});
  const [err, setErr] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const set = (k: string) => (e: React.ChangeEvent<HTMLInputElement>) => setF((s) => ({ ...s, [k]: e.target.value }));
  const field = (k: string, label: string, props: React.InputHTMLAttributes<HTMLInputElement> = {}, hint?: string) => (
    <label className="field">
      {label}
      {hint && <small>{hint}</small>}
      <input value={f[k] ?? ''} onChange={set(k)} {...props} />
    </label>
  );

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setErr(null);
    if (f.password !== f.password2) {
      setErr('The two passwords don’t match.');
      return;
    }
    setBusy(true);
    const body: Record<string, unknown> = { role, email: f.email, password: f.password, fullName: f.fullName, phone: f.phone || null };
    if (role === 'patient') body.dateOfBirth = f.dob || null;
    if (role === 'doctor') body.doctor = { registrationNumber: f.reg, council: f.council, specialty: f.specialty || null };
    if (role === 'clinic') body.clinic = { name: f.clinicName, address: f.address, city: f.city, registrationNumber: f.reg, phone: f.clinicPhone };
    try {
      const out = await api<{ message: string }>('POST', '/v1/auth/register', body);
      setDone(out.message);
    } catch (ex) {
      setErr(errorText(ex));
    } finally {
      setBusy(false);
    }
  }

  if (done)
    return (
      <AuthShell title="Check your email">
        <Banner kind="ok">{done}</Banner>
        <p className="muted">The confirmation link works once and expires in 24 hours.</p>
        <Link to="/" className="btn ghost">
          Back to sign in
        </Link>
      </AuthShell>
    );

  return (
    <AuthShell title="Create an account" sub="Every account gets a 16-digit CardiosensAI ID that links patients, doctors and clinics.">
      <div className="seg" role="group" aria-label="Account type" style={{ alignSelf: 'center' }}>
        {ROLES.map((r) => (
          <button key={r.key} type="button" aria-pressed={role === r.key} onClick={() => setRole(r.key)}>
            {r.label}
          </button>
        ))}
      </div>
      <small className="muted" style={{ textAlign: 'center' }}>
        {ROLES.find((r) => r.key === role)!.hint}
      </small>
      <form className="stack" onSubmit={submit}>
        {field('fullName', role === 'clinic' ? 'Your name (account manager)' : 'Full name', { required: true, minLength: 2, autoComplete: 'name' })}
        {field('email', 'Email', { type: 'email', required: true, autoComplete: 'email' })}
        {field('password', 'Password', { type: 'password', required: true, minLength: 10, autoComplete: 'new-password' }, 'At least 10 characters. Avoid common passwords.')}
        {field('password2', 'Repeat password', { type: 'password', required: true, autoComplete: 'new-password' })}
        {role === 'patient' && field('dob', 'Date of birth', { type: 'date', max: new Date().toISOString().slice(0, 10) })}
        {role !== 'clinic' && field('phone', 'Mobile (optional)', { type: 'tel', placeholder: '+919812345678', pattern: '\\+[1-9]\\d{7,14}' }, 'International format, starting with +91')}
        {role === 'doctor' && (
          <>
            {field('reg', 'Medical registration number', { required: true, minLength: 3 })}
            {field('council', 'Medical council', { required: true, minLength: 2, placeholder: 'e.g. West Bengal Medical Council' })}
            {field('specialty', 'Specialty (optional)', { placeholder: 'e.g. Cardiology' })}
          </>
        )}
        {role === 'clinic' && (
          <>
            {field('clinicName', 'Clinic name', { required: true, minLength: 2 })}
            {field('address', 'Address', { required: true, minLength: 3 })}
            {field('city', 'City', { required: true, minLength: 2 })}
            {field('reg', 'Clinical establishment registration / licence number', { required: true, minLength: 3 })}
            {field('clinicPhone', 'Clinic phone', { type: 'tel', required: true, placeholder: '+913340001111', pattern: '\\+[1-9]\\d{7,14}' })}
          </>
        )}
        {err && <Banner kind="error">{err}</Banner>}
        <button type="submit" className="btn primary" disabled={busy}>
          {busy ? 'Creating…' : 'Create account'}
        </button>
      </form>
      <p style={{ textAlign: 'center', margin: 0 }}>
        Already registered? <Link to="/">Sign in</Link>
      </p>
    </AuthShell>
  );
}

export function VerifyEmailPage() {
  const [params] = useSearchParams();
  const [state, setState] = useState<{ kind: 'ok' | 'error' | 'info'; text: string }>({ kind: 'info', text: 'Confirming…' });
  const sent = useRef(false); // tokens are single-use; never send twice
  useEffect(() => {
    if (sent.current) return;
    sent.current = true;
    const token = params.get('token') ?? '';
    api<{ message: string }>('POST', '/v1/auth/email/verify', { token })
      .then((r) => setState({ kind: 'ok', text: r.message }))
      .catch((e) => setState({ kind: 'error', text: errorText(e) }));
  }, [params]);
  return (
    <AuthShell title="Email confirmation">
      <Banner kind={state.kind}>{state.text}</Banner>
      <Link to="/" className="btn primary">
        Go to sign in
      </Link>
    </AuthShell>
  );
}

export function ForgotPasswordPage() {
  const [email, setEmail] = useState('');
  const [sent, setSent] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  return (
    <AuthShell title="Reset your password">
      {sent ? (
        <Banner kind="ok">If an account uses {email}, we’ve sent it a reset link. The link expires in 1 hour.</Banner>
      ) : (
        <form
          className="stack"
          onSubmit={async (e) => {
            e.preventDefault();
            try {
              await api('POST', '/v1/auth/password/forgot', { email });
              setSent(true);
            } catch (ex) {
              setErr(errorText(ex));
            }
          }}
        >
          <label className="field">
            Email
            <input type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} required autoFocus />
          </label>
          {err && <Banner kind="error">{err}</Banner>}
          <button type="submit" className="btn primary">
            Send reset link
          </button>
        </form>
      )}
      <Link to="/">Back to sign in</Link>
    </AuthShell>
  );
}

export function ResetPasswordPage() {
  const [params] = useSearchParams();
  const [pw, setPw] = useState('');
  const [pw2, setPw2] = useState('');
  const [msg, setMsg] = useState<{ kind: 'ok' | 'error'; text: string } | null>(null);
  return (
    <AuthShell title="Choose a password" sub="At least 10 characters. Signing in again will be needed on every device.">
      {msg?.kind === 'ok' ? (
        <>
          <Banner kind="ok">{msg.text}</Banner>
          <Link to="/" className="btn primary">
            Sign in
          </Link>
        </>
      ) : (
        <form
          className="stack"
          onSubmit={async (e) => {
            e.preventDefault();
            if (pw !== pw2) {
              setMsg({ kind: 'error', text: 'The two passwords don’t match.' });
              return;
            }
            try {
              const r = await api<{ message: string }>('POST', '/v1/auth/password/reset', { token: params.get('token') ?? '', password: pw });
              setMsg({ kind: 'ok', text: r.message });
            } catch (ex) {
              setMsg({ kind: 'error', text: errorText(ex) });
            }
          }}
        >
          <label className="field">
            New password
            <input type="password" autoComplete="new-password" minLength={10} value={pw} onChange={(e) => setPw(e.target.value)} required autoFocus />
          </label>
          <label className="field">
            Repeat password
            <input type="password" autoComplete="new-password" value={pw2} onChange={(e) => setPw2(e.target.value)} required />
          </label>
          {msg && <Banner kind={msg.kind}>{msg.text}</Banner>}
          <button type="submit" className="btn primary">
            Set password
          </button>
        </form>
      )}
    </AuthShell>
  );
}
