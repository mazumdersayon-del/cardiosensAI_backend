import QRCode from 'qrcode';
import { useEffect, useRef, useState } from 'react';
import { Link as RouterLink } from 'react-router-dom';
import { api } from '../api';
import { useAuth } from '../auth';
import { Banner, errorText } from './ui';

/** Doctors, clinics and staff: what is left before they can see patient data. */
export function SetupChecklist() {
  const { status } = useAuth();
  if (!status || !status.missing.length) return null;
  const items = [
    { key: 'verify_contact', text: 'Confirm your mobile number or email address' },
    { key: 'upload_licence', text: 'Upload your registration / licence certificate under Account' },
    {
      key: 'verification',
      text:
        status.verificationStatus === 'rejected' || status.verificationStatus === 'revoked'
          ? `CardiosensAI ${status.verificationStatus === 'revoked' ? 'withdrew your verification' : 'could not verify your registration'}${
              status.verificationNote ? `: ${status.verificationNote}` : ''
            }. Fix it under Account and submit again.`
          : 'CardiosensAI verifies your registration details (usually within one working day)',
    },
    { key: 'enable_mfa', text: 'Turn on two-step sign-in with an authenticator app' },
  ];
  return (
    <Banner kind="warn">
      <strong>Finish setting up before you can link with patients</strong>
      <ul className="checklist" style={{ marginTop: 8 }}>
        {items.map((i) => (
          <li key={i.key}>
            <span aria-hidden="true">{status.missing.includes(i.key) ? '○' : '●'}</span>
            <span style={{ textDecoration: status.missing.includes(i.key) ? 'none' : 'line-through' }}>{i.text}</span>
          </li>
        ))}
      </ul>
      {(status.missing.includes('enable_mfa') || status.missing.includes('upload_licence') || status.verificationNote) && (
        <p style={{ margin: '8px 0 0' }}>
          <RouterLink to="/settings">Go to Account →</RouterLink>
        </p>
      )}
    </Banner>
  );
}

export function MfaSetup() {
  const { user, reload, status } = useAuth();
  const [setup, setSetup] = useState<{ secret: string; otpauthUri: string } | null>(null);
  const [code, setCode] = useState('');
  const [msg, setMsg] = useState<{ kind: 'error' | 'ok'; text: string } | null>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    if (setup && canvas.current) QRCode.toCanvas(canvas.current, setup.otpauthUri, { width: 180, margin: 1 }).catch(() => undefined);
  }, [setup]);

  if (user?.mfaEnabled)
    return (
      <Banner kind="ok">
        Two-step sign-in is on. You’ll be asked for a code from your authenticator app each time you sign in.
        {status?.mfaRequired && ' It is required for your account type.'}
      </Banner>
    );

  return (
    <div className="stack">
      <p>
        Use an authenticator app (Google Authenticator, Microsoft Authenticator, 1Password, Aegis). Each sign-in then needs your password and a 6-digit code.
        {status?.mfaRequired && <strong> Required for doctor, clinic and staff accounts.</strong>}
      </p>
      {!setup ? (
        <div>
          <button
            type="button"
            className="btn primary"
            onClick={async () => {
              try {
                setSetup(await api('POST', '/v1/auth/mfa/setup'));
              } catch (e) {
                setMsg({ kind: 'error', text: errorText(e) });
              }
            }}
          >
            Set up two-step sign-in
          </button>
        </div>
      ) : (
        <div className="row" style={{ alignItems: 'flex-start' }}>
          <canvas ref={canvas} width={180} height={180} style={{ background: '#fff', borderRadius: 8 }} aria-label="QR code for your authenticator app" />
          <form
            className="stack"
            style={{ flex: '1 1 240px' }}
            onSubmit={async (e) => {
              e.preventDefault();
              try {
                await api('POST', '/v1/auth/mfa/enable', { code });
                setMsg({ kind: 'ok', text: 'Two-step sign-in is on.' });
                await reload();
              } catch (err) {
                setMsg({ kind: 'error', text: errorText(err) });
              }
            }}
          >
            <p style={{ margin: 0 }}>Scan the code, or enter this key by hand:</p>
            <code className="mono" style={{ wordBreak: 'break-all' }}>
              {setup.secret}
            </code>
            <label className="field">
              6-digit code from the app
              <input inputMode="numeric" autoComplete="one-time-code" pattern="\d{6}" maxLength={6} value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, ''))} required />
            </label>
            <div>
              <button type="submit" className="btn primary" disabled={code.length !== 6}>
                Turn on
              </button>
            </div>
          </form>
        </div>
      )}
      {msg && <Banner kind={msg.kind}>{msg.text}</Banner>}
    </div>
  );
}
