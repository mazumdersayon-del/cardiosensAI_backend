import { useState } from 'react';
import { api, type User } from '../api';
import { useAuth } from '../auth';
import PhoneInput from './PhoneInput';
import { Banner, errorText } from './ui';

interface CodeSent {
  challengeId: string;
  destinationMasked: string;
  devCode?: string | null;
}

/** Sign-in mobile number and email. An account that has only one can add the other, confirmed with a code. */
export function ContactDetails() {
  const { user, reload } = useAuth();
  const [adding, setAdding] = useState<'email' | 'phone' | null>(null);
  const [value, setValue] = useState('');
  const [sent, setSent] = useState<CodeSent | null>(null);
  const [code, setCode] = useState('');
  const [msg, setMsg] = useState<{ kind: 'ok' | 'error'; text: string } | null>(null);
  if (!user) return null;

  const reset = () => (setAdding(null), setValue(''), setSent(null), setCode(''));
  const row = (label: string, v: string | null, ok: boolean, kind: 'email' | 'phone') => (
    <div className="row spread">
      <span>
        <span className="muted">{label}</span>
        <br />
        {v ? <span className="mono">{v}</span> : <span className="muted">none</span>}{' '}
        {v && <span className={`pill ${ok ? 'ok' : 'warn'}`}>{ok ? 'Confirmed' : 'Not confirmed'}</span>}
      </span>
      {!v && !adding && (
        <button type="button" className="btn ghost small" onClick={() => (setAdding(kind), setMsg(null))}>
          Add {kind === 'email' ? 'email' : 'mobile number'}
        </button>
      )}
      {v && !ok && kind === 'phone' && !adding && (
        <button type="button" className="btn ghost small" onClick={() => (setAdding('phone'), setValue(v), setMsg(null))}>
          Confirm mobile number
        </button>
      )}
    </div>
  );

  return (
    <div className="stack">
      {row('Mobile number', user.phone, user.phoneVerified, 'phone')}
      {row('Email', user.email, user.emailVerified, 'email')}
      {user.googleLinked && <small className="muted">Google sign-in is linked to this account.</small>}
      {adding && (
        <form
          className="stack upload-box"
          onSubmit={async (e) => {
            e.preventDefault();
            setMsg(null);
            try {
              if (!sent) {
                setSent(await api<CodeSent>('POST', '/v1/me/contact/code', { kind: adding, value }));
              } else {
                await api<User>('POST', '/v1/me/contact/verify', { challengeId: sent.challengeId, code });
                await reload();
                setMsg({ kind: 'ok', text: adding === 'email' ? 'Email added and confirmed.' : 'Mobile number confirmed.' });
                reset();
              }
            } catch (ex) {
              setMsg({ kind: 'error', text: errorText(ex) });
            }
          }}
        >
          {!sent ? (
            adding === 'email' ? (
              <label className="field">
                Email
                <input type="email" value={value} onChange={(e) => setValue(e.target.value)} required autoFocus />
              </label>
            ) : (
              <PhoneInput label="Mobile number" value={value} onChange={setValue} required />
            )
          ) : (
            <label className="field">
              Code sent to {sent.destinationMasked}
              <input className="mono" inputMode="numeric" autoComplete="one-time-code" maxLength={6} value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, ''))} required autoFocus />
              {sent.devCode && <small className="muted">Development: the code is {sent.devCode}</small>}
            </label>
          )}
          <div className="row">
            <button type="submit" className="btn primary small" disabled={sent ? code.length !== 6 : !value}>
              {sent ? 'Confirm' : 'Send code'}
            </button>
            <button type="button" className="btn ghost small" onClick={reset}>
              Cancel
            </button>
          </div>
        </form>
      )}
      {msg && <Banner kind={msg.kind}>{msg.text}</Banner>}
      <small className="muted">Your sign-in mobile number and email can’t be changed here. Contact CardiosensAI support to change them.</small>
    </div>
  );
}
