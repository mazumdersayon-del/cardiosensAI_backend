import { useCallback, useState, type ReactNode } from 'react';
import { Link, useLocation, useNavigate, useSearchParams } from 'react-router-dom';
import { api, ApiError, parseIdentifier, type LoginStarted, type MfaRequired, type SignupStarted } from '../api';
import { useAuth } from '../auth';
import GoogleButton, { credentialProfile } from '../components/GoogleButton';
import { Tagline, ThemeButton } from '../components/Layout';
import PhoneInput from '../components/PhoneInput';
import SiteFooter from '../components/SiteFooter';
import { Banner, errorText } from '../components/ui';

function AuthShell({ title, children, sub }: { title: string; sub?: ReactNode; children: ReactNode }) {
  return (
    <div className="auth-wrap">
      <div className="auth-card stack">
        <div className="card stack">
          <div className="logo">
            <img src="/logos/cardiosensai-logo.png" alt="CardiosensAI" />
            <Tagline />
          </div>
          <div>
            <h1 style={{ textAlign: 'center' }}>{title}</h1>
            {sub && <p className="muted" style={{ textAlign: 'center' }}>{sub}</p>}
          </div>
          {children}
        </div>
        <div className="row" style={{ justifyContent: 'flex-end' }}>
          <ThemeButton className="btn ghost small" />
        </div>
      </div>
      <SiteFooter />
    </div>
  );
}

function Or() {
  return (
    <div className="row" style={{ gap: 10 }} aria-hidden="true">
      <span style={{ flex: 1, borderTop: '1px solid var(--line)' }} />
      <small className="muted">or</small>
      <span style={{ flex: 1, borderTop: '1px solid var(--line)' }} />
    </div>
  );
}

/** A 6-digit one-time code field. */
function CodeField({ label, value, onChange, autoFocus }: { label: ReactNode; value: string; onChange: (v: string) => void; autoFocus?: boolean }) {
  return (
    <label className="field">
      {label}
      <input
        className="mono"
        style={{ fontSize: '1.4rem', letterSpacing: '0.4em', textAlign: 'center' }}
        inputMode="numeric"
        autoComplete="one-time-code"
        pattern="\d{6}"
        maxLength={6}
        value={value}
        onChange={(e) => onChange(e.target.value.replace(/\D/g, '').slice(0, 6))}
        required
        autoFocus={autoFocus}
      />
    </label>
  );
}

/** Doctors, clinics, staff (and patients who turned it on): the authenticator-app step after any sign-in. */
function MfaStep({ mfaToken, onBack }: { mfaToken: string; onBack: () => void }) {
  const { verifyMfa } = useAuth();
  const nav = useNavigate();
  const [code, setCode] = useState('');
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  return (
    <AuthShell title="Two-step sign-in" sub="Enter the 6-digit code from your authenticator app.">
      <form
        className="stack"
        onSubmit={async (e) => {
          e.preventDefault();
          setBusy(true);
          setErr(null);
          try {
            await verifyMfa(mfaToken, code);
            nav('/', { replace: true });
          } catch (ex) {
            setErr(errorText(ex));
            if (ex instanceof ApiError && ex.status === 401) onBack();
          } finally {
            setBusy(false);
          }
        }}
      >
        <CodeField label="Authenticator code" value={code} onChange={setCode} autoFocus />
        {err && <Banner kind="error">{err}</Banner>}
        <button type="submit" className="btn primary" disabled={busy || code.length !== 6}>
          {busy ? 'Checking…' : 'Verify'}
        </button>
      </form>
      <button type="button" className="linklike" onClick={onBack}>
        Start again
      </button>
    </AuthShell>
  );
}

/** Google's credential → signed in, or (no account yet) on to sign-up with the Google details filled in. */
function useGoogleSignIn(setErr: (e: string | null) => void) {
  const { signIn } = useAuth();
  const nav = useNavigate();
  const [mfa, setMfa] = useState<MfaRequired | null>(null);
  const onCredential = useCallback(
    async (credential: string) => {
      setErr(null);
      try {
        const m = await signIn('/v1/auth/google', { credential });
        if (m) setMfa(m);
        else nav('/', { replace: true });
      } catch (ex) {
        if (ex instanceof ApiError && ex.code === 'no_account') nav('/register', { state: { googleCredential: credential } });
        else setErr(errorText(ex));
      }
    },
    [signIn, nav, setErr],
  );
  return { onCredential, mfa, clearMfa: () => setMfa(null) };
}

// ---------------------------------------------------------------- sign-in: step 1

export function LoginPage() {
  const nav = useNavigate();
  const [who, setWho] = useState('');
  const [err, setErr] = useState<string | null>(null);
  const [smsDown, setSmsDown] = useState<string | null>(null); // the number typed while text messages are down
  const [busy, setBusy] = useState(false);
  const google = useGoogleSignIn(setErr);
  if (google.mfa) return <MfaStep mfaToken={google.mfa.mfaToken} onBack={google.clearMfa} />;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setErr(null);
    setSmsDown(null);
    const id = parseIdentifier(who);
    if ('error' in id) return setErr(id.error);
    setBusy(true);
    try {
      const started = await api<LoginStarted>('POST', '/v1/auth/login/start', { identifier: id.value });
      nav('/login/code', { state: { started, identifier: id.value } });
    } catch (ex) {
      setErr(errorText(ex));
      if (ex instanceof ApiError && ex.code === 'sms_unavailable') setSmsDown(id.value);
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthShell title="Sign in">
      <form className="stack" onSubmit={submit}>
        <label className="field">
          Email or phone number
          <input autoComplete="username" inputMode="email" value={who} onChange={(e) => setWho(e.target.value)} required autoFocus />
        </label>
        {err && <Banner kind="error">{err}</Banner>}
        {smsDown && (
          <div className="row">
            <button type="button" className="btn ghost small" onClick={() => nav('/login/code', { state: { identifier: smsDown, passwordOnly: true } })}>
              Sign in with password
            </button>
            <button type="button" className="btn ghost small" onClick={() => (setWho(''), setErr(null), setSmsDown(null))}>
              Use my email instead
            </button>
          </div>
        )}
        <button type="submit" className="btn primary" disabled={busy}>
          {busy ? 'Sending a code…' : 'Continue'}
        </button>
      </form>
      <Or />
      <GoogleButton mode="signin" onCredential={google.onCredential} />
      <p style={{ textAlign: 'center', margin: 0 }}>
        New here? <Link to="/register">Create an account</Link>
      </p>
    </AuthShell>
  );
}

// ---------------------------------------------------------------- sign-in: step 2 (code, or password instead)

export function LoginCodePage() {
  const { signIn } = useAuth();
  const nav = useNavigate();
  // passwordOnly: text messages were down for a mobile number, so there is no code challenge, only the password
  const loc = useLocation() as { state?: { started?: LoginStarted; identifier?: string; passwordOnly?: boolean } };
  const [started, setStarted] = useState<LoginStarted | undefined>(loc.state?.started);
  const identifier = loc.state?.identifier ?? '';
  const [mode, setMode] = useState<'code' | 'password'>(loc.state?.passwordOnly ? 'password' : 'code');
  const [code, setCode] = useState('');
  const [password, setPassword] = useState('');
  const [mfa, setMfa] = useState<MfaRequired | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (!identifier || (!started && mode === 'code'))
    return (
      <AuthShell title="Sign in">
        <Banner kind="info">Start by entering your email or mobile number.</Banner>
        <Link to="/" className="btn primary">
          Go to sign in
        </Link>
      </AuthShell>
    );
  if (mfa) return <MfaStep mfaToken={mfa.mfaToken} onBack={() => nav('/', { replace: true })} />;

  const viaSms = started?.channel === 'sms';
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      const m =
        mode === 'code'
          ? await signIn('/v1/auth/login/verify', { challengeId: started!.challengeId, code })
          : await signIn('/v1/auth/login', { identifier, password });
      if (m) setMfa(m);
      else nav('/', { replace: true });
    } catch (ex) {
      setErr(errorText(ex));
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthShell
      title={mode === 'code' ? 'Enter your code' : 'Enter your password'}
      sub={
        mode === 'code' && started ? (
          <>
            We sent a 6-digit code {viaSms ? 'by SMS to' : 'to'} <strong>{started.destinationMasked}</strong>, and another to your account&apos;s confirmed{' '}
            {viaSms ? 'email' : 'mobile number'} if it has one. Either code works; they expire in {Math.round(started.expiresInS / 60)} minutes.
          </>
        ) : (
          <>
            Signing in as <strong>{started?.destinationMasked ?? identifier}</strong>
          </>
        )
      }
    >
      <form className="stack" onSubmit={submit}>
        {mode === 'code' ? (
          <>
            <CodeField label="Code from the SMS or the email" value={code} onChange={setCode} autoFocus />
            {started?.devCode && (
              <small className="muted">
                Development: the code is {started.devCode}
                {started.devOtherCode && ` (or ${started.devOtherCode})`}
              </small>
            )}
          </>
        ) : (
          <label className="field">
            Password
            <input type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required autoFocus />
          </label>
        )}
        {err && <Banner kind="error">{err}</Banner>}
        {note && <Banner kind="ok">{note}</Banner>}
        <button type="submit" className="btn primary" disabled={busy || (mode === 'code' && code.length !== 6)}>
          {busy ? 'Signing in…' : 'Sign in'}
        </button>
      </form>
      <div className="row spread">
        {mode === 'code' ? (
          <>
            <button type="button" className="linklike" onClick={() => (setMode('password'), setErr(null))}>
              Use password instead
            </button>
            <button
              type="button"
              className="linklike"
              disabled={busy}
              onClick={async () => {
                setErr(null);
                try {
                  setStarted(await api<LoginStarted>('POST', '/v1/auth/login/start', { identifier }));
                  setCode('');
                  setNote('New codes are on their way.');
                } catch (ex) {
                  setErr(errorText(ex));
                }
              }}
            >
              Send a new code
            </button>
          </>
        ) : (
          <>
            {started ? (
              <button type="button" className="linklike" onClick={() => (setMode('code'), setErr(null))}>
                Use a one-time code instead
              </button>
            ) : (
              <Link to="/">Get a code by email instead</Link>
            )}
            <Link to="/forgot-password">Forgot password?</Link>
          </>
        )}
      </div>
      <Link to="/" style={{ textAlign: 'center' }}>
        Use a different email or number
      </Link>
    </AuthShell>
  );
}

// ---------------------------------------------------------------- sign-up

const ROLES = [
  { key: 'patient', label: 'Patient' },
  { key: 'doctor', label: 'Doctor' },
  { key: 'clinic', label: 'Clinic' },
] as const;

export function RegisterPage() {
  const nav = useNavigate();
  const loc = useLocation() as { state?: { googleCredential?: string } };
  const [google, setGoogle] = useState<string | null>(loc.state?.googleCredential ?? null);
  const fromGoogle = google ? credentialProfile(google) : null;
  const [role, setRole] = useState<'patient' | 'doctor' | 'clinic'>('patient');
  const [f, setF] = useState<Record<string, string>>(() => ({ fullName: fromGoogle?.name ?? '' }));
  const [err, setErr] = useState<string | null>(null);
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
    if (!f.phone) return setErr('Enter your mobile number. We send it a code to activate the account.');
    if (!google && f.password !== f.password2) return setErr('The two passwords don’t match.');
    setBusy(true);
    const body: Record<string, unknown> = { role, fullName: f.fullName, phone: f.phone };
    if (google) body.googleCredential = google;
    else {
      body.password = f.password;
      if (f.email) body.email = f.email;
    }
    if (role === 'patient') body.dateOfBirth = f.dob || null;
    if (role === 'doctor') body.doctor = { registrationNumber: f.reg, council: f.council, specialty: f.specialty || null };
    if (role === 'clinic') body.clinic = { name: f.clinicName, address: f.address, city: f.city, registrationNumber: f.reg, phone: f.clinicPhone };
    try {
      const started = await api<SignupStarted>('POST', '/v1/auth/register', body);
      nav('/register/verify', { state: { started } });
    } catch (ex) {
      setErr(errorText(ex));
      if (ex instanceof ApiError && ex.code.startsWith('google')) setGoogle(null);
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthShell title="Create an account">
      <div className="seg" role="group" aria-label="Account type" style={{ alignSelf: 'center' }}>
        {ROLES.map((r) => (
          <button key={r.key} type="button" aria-pressed={role === r.key} onClick={() => setRole(r.key)}>
            {r.label}
          </button>
        ))}
      </div>
      {google ? (
        <Banner kind="ok">
          Signing up with Google as <strong>{fromGoogle?.email}</strong>.{' '}
          <button type="button" className="linklike" onClick={() => setGoogle(null)}>
            Use email and password instead
          </button>
        </Banner>
      ) : (
        <>
          <GoogleButton
            mode="signup"
            onCredential={(c) => {
              setGoogle(c);
              const p = credentialProfile(c);
              setF((x) => ({ ...x, fullName: x.fullName || p.name || '' }));
            }}
          />
          <Or />
        </>
      )}
      <form className="stack" onSubmit={submit}>
        {field('fullName', role === 'clinic' ? 'Your name (account manager)' : 'Full name', { required: true, minLength: 2, autoComplete: 'name' })}
        <PhoneInput label="Mobile number" value={f.phone ?? ''} onChange={(v) => setF((x) => ({ ...x, phone: v }))} required />
        {!google && (
          <>
            {field('email', 'Email (optional)', { type: 'email', autoComplete: 'email' })}
            {field('password', 'Password', { type: 'password', required: true, minLength: 10, autoComplete: 'new-password' })}
            {field('password2', 'Repeat password', { type: 'password', required: true, autoComplete: 'new-password' })}
          </>
        )}
        {role === 'patient' && field('dob', 'Date of birth', { type: 'date', max: new Date().toISOString().slice(0, 10) })}
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
            <PhoneInput label="Clinic phone" value={f.clinicPhone ?? ''} onChange={(v) => setF((x) => ({ ...x, clinicPhone: v }))} required />
          </>
        )}
        {err && <Banner kind="error">{err}</Banner>}
        <button type="submit" className="btn primary" disabled={busy}>
          {busy ? 'Sending codes…' : 'Continue'}
        </button>
      </form>
      <p style={{ textAlign: 'center', margin: 0 }}>
        Already registered? <Link to="/">Sign in</Link>
      </p>
    </AuthShell>
  );
}

export function RegisterCodePage() {
  const { signIn } = useAuth();
  const nav = useNavigate();
  const loc = useLocation() as { state?: { started?: SignupStarted } };
  const [started, setStarted] = useState<SignupStarted | undefined>(loc.state?.started);
  const [phoneCode, setPhoneCode] = useState('');
  const [emailCode, setEmailCode] = useState('');
  const [err, setErr] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  if (!started)
    return (
      <AuthShell title="Create an account">
        <Banner kind="info">Start by filling in the sign-up form.</Banner>
        <Link to="/register" className="btn primary">
          Go to sign-up
        </Link>
      </AuthShell>
    );
  const needEmail = !!started.emailMasked;
  const needPhone = started.phoneCodeSent !== false;
  return (
    <AuthShell
      title="Activate your account"
      sub={
        needPhone ? (
          <>
            Enter the code we sent by SMS to <strong>{started.phoneMasked}</strong>
            {needEmail && (
              <>
                {' '}
                and the one we emailed to <strong>{started.emailMasked}</strong>
              </>
            )}
            .
          </>
        ) : (
          <>
            Enter the code we emailed to <strong>{started.emailMasked}</strong>.
          </>
        )
      }
    >
      {!needPhone && (
        <Banner kind="info">
          We can’t send text messages right now, so your mobile number <strong>{started.phoneMasked}</strong> will be confirmed later. Once text
          messages work again, confirm it in Settings (you’ll be reminded on your home page).
        </Banner>
      )}
      <form
        className="stack"
        onSubmit={async (e) => {
          e.preventDefault();
          setBusy(true);
          setErr(null);
          try {
            await signIn('/v1/auth/register/verify', { signupId: started.signupId, phoneCode: needPhone ? phoneCode : null, emailCode: needEmail ? emailCode : null });
            nav('/', { replace: true });
          } catch (ex) {
            setErr(errorText(ex));
          } finally {
            setBusy(false);
          }
        }}
      >
        {needPhone && (
          <>
            <CodeField label="Code from the SMS" value={phoneCode} onChange={setPhoneCode} autoFocus />
            {started.devPhoneCode && <small className="muted">Development: SMS code {started.devPhoneCode}</small>}
          </>
        )}
        {needEmail && (
          <>
            <CodeField label="Code from the email" value={emailCode} onChange={setEmailCode} autoFocus={!needPhone} />
            {started.devEmailCode && <small className="muted">Development: email code {started.devEmailCode}</small>}
          </>
        )}
        {err && <Banner kind="error">{err}</Banner>}
        {note && <Banner kind="ok">{note}</Banner>}
        <button type="submit" className="btn primary" disabled={busy || (needPhone && phoneCode.length !== 6) || (needEmail && emailCode.length !== 6)}>
          {busy ? 'Activating…' : 'Activate and sign in'}
        </button>
      </form>
      <div className="row spread">
        <button
          type="button"
          className="linklike"
          onClick={async () => {
            setErr(null);
            try {
              setStarted(await api<SignupStarted>('POST', '/v1/auth/register/resend', { signupId: started.signupId }));
              setPhoneCode('');
              setEmailCode('');
              setNote('New codes are on their way. The old ones no longer work.');
            } catch (ex) {
              setErr(errorText(ex));
            }
          }}
        >
          Send new codes
        </button>
        <Link to="/register">Change details</Link>
      </div>
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
