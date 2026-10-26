import { useState } from 'react';
import { api, type Link, type Party } from '../api';
import { useAuth } from '../auth';
import { formatId, isValidId, ROLE_LABEL, ROLE_OF_PREFIX, when } from '../format';
import { Banner, errorText, StatusPill, useLoad } from './ui';

const KIND_LABEL: Record<string, string> = { patient_doctor: 'Patient ↔ Doctor', patient_clinic: 'Patient ↔ Clinic', doctor_clinic: 'Doctor ↔ Clinic' };

function PartyCell({ p }: { p: Party }) {
  return (
    <div className="stack" style={{ gap: 2 }}>
      <strong style={{ color: 'var(--heading)' }}>{p.name || 'Unnamed'}</strong>
      <span className="muted mono" style={{ fontSize: '0.85rem' }}>
        {ROLE_LABEL[p.role]} · {formatId(p.uniqueId)}
        {p.detail ? ` · ${p.detail}` : ''}
      </span>
      {p.verified === false && <span className="pill warn">Not yet verified by CardiosensAI</span>}
    </div>
  );
}

/** Ask to link by 16-digit ID. Looks the ID up first so the user confirms who they are linking with. */
export function LinkRequestBox({ onDone, allowDoctorOnBehalf = false, targets }: { onDone: () => void; allowDoctorOnBehalf?: boolean; targets: string }) {
  const [raw, setRaw] = useState('');
  const [doctorId, setDoctorId] = useState('');
  const [found, setFound] = useState<Party | null>(null);
  const [msg, setMsg] = useState<{ kind: 'error' | 'ok' | 'warn'; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const digits = raw.replace(/\D/g, '');

  async function lookup(e: React.FormEvent) {
    e.preventDefault();
    setMsg(null);
    setFound(null);
    if (!isValidId(digits)) {
      setMsg({ kind: 'error', text: 'That isn’t a valid 16-digit CardiosensAI ID. Check the digits.' });
      return;
    }
    try {
      setFound(await api<Party>('GET', `/v1/directory/${digits}`));
    } catch (err) {
      setMsg({ kind: 'error', text: errorText(err) });
    }
  }

  async function send() {
    setBusy(true);
    try {
      const body: Record<string, string> = { targetId: digits };
      if (allowDoctorOnBehalf && doctorId.trim()) body.onBehalfOfDoctorId = doctorId.replace(/\D/g, '');
      await api('POST', '/v1/links', body);
      setMsg({ kind: 'ok', text: `Request sent to ${found?.name ?? 'them'}. The link becomes active once they approve.` });
      setRaw('');
      setDoctorId('');
      setFound(null);
      onDone();
    } catch (err) {
      setMsg({ kind: 'error', text: errorText(err) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="stack">
      <form className="row" onSubmit={lookup} style={{ alignItems: 'flex-end' }}>
        <label className="field" style={{ flex: '1 1 260px' }}>
          16-digit ID
          <small>{targets}</small>
          <input
            inputMode="numeric"
            autoComplete="off"
            className="mono"
            placeholder="1234 5678 9012 3456"
            value={formatId(digits) === '—' ? '' : formatId(digits)}
            maxLength={19}
            onChange={(e) => {
              setRaw(e.target.value);
              setFound(null);
            }}
          />
        </label>
        <button type="submit" className="btn ghost" disabled={digits.length !== 16}>
          Look up
        </button>
      </form>
      {digits.length === 16 && !found && isValidId(digits) && <small className="muted">This is a {ROLE_OF_PREFIX[digits[0]]} ID.</small>}
      {found && (
        <div className="card" style={{ background: 'var(--card-2)' }}>
          <div className="row spread">
            <PartyCell p={found} />
            <div className="row">
              <button type="button" className="btn ghost small" onClick={() => setFound(null)}>
                Cancel
              </button>
              <button type="button" className="btn primary small" disabled={busy} onClick={send}>
                Send link request
              </button>
            </div>
          </div>
          {allowDoctorOnBehalf && found.role === 'patient' && (
            <label className="field" style={{ marginTop: 12 }}>
              Optional: link this patient with one of your doctors instead
              <small>Enter the doctor’s ID. The patient approves; the clinic itself won’t get access through this request.</small>
              <input className="mono" inputMode="numeric" value={doctorId} onChange={(e) => setDoctorId(e.target.value)} placeholder="2xxx xxxx xxxx xxxx" />
            </label>
          )}
        </div>
      )}
      {msg && <Banner kind={msg.kind}>{msg.text}</Banner>}
    </div>
  );
}

export function LinksTable({ links, onChange, emptyText }: { links: Link[]; onChange: () => void; emptyText: string }) {
  const { user } = useAuth();
  const [err, setErr] = useState<string | null>(null);
  const [confirmEnd, setConfirmEnd] = useState<string | null>(null);
  async function act(id: string, action: 'approve' | 'decline' | 'end') {
    setErr(null);
    try {
      await api('POST', `/v1/links/${id}/${action}`);
      setConfirmEnd(null);
      onChange();
    } catch (e) {
      setErr(errorText(e));
    }
  }
  if (!links.length) return <div className="empty">{emptyText}</div>;
  return (
    <div className="stack">
      {err && <Banner kind="error">{err}</Banner>}
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>With</th>
              <th>Link</th>
              <th>Status</th>
              <th>Since</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {links.map((l) => (
              <tr key={l.id}>
                <td>
                  <PartyCell p={l.other} />
                  {l.viaClinic && <small className="muted">Requested by {l.viaClinic.name}</small>}
                </td>
                <td>{KIND_LABEL[l.kind]}</td>
                <td>
                  <StatusPill status={l.status} />
                  {l.status === 'pending' && <div className="muted" style={{ fontSize: '0.8rem' }}>{l.awaitingMe ? 'Waiting for you' : 'Waiting for them'}</div>}
                </td>
                <td>{when(l.decidedAt ?? l.createdAt)}</td>
                <td>
                  <div className="row" style={{ justifyContent: 'flex-end' }}>
                    {l.status === 'pending' && l.awaitingMe && (
                      <>
                        <button type="button" className="btn primary small" onClick={() => act(l.id, 'approve')}>
                          Approve
                        </button>
                        <button type="button" className="btn ghost small" onClick={() => act(l.id, 'decline')}>
                          Decline
                        </button>
                      </>
                    )}
                    {l.status === 'pending' && !l.awaitingMe && (
                      <button type="button" className="btn ghost small" onClick={() => act(l.id, 'decline')}>
                        Cancel request
                      </button>
                    )}
                    {l.status === 'active' &&
                      (confirmEnd === l.id ? (
                        <>
                          <span className="muted" style={{ fontSize: '0.85rem' }}>
                            {user?.role === 'patient' && l.kind !== 'doctor_clinic' ? 'They lose access to your data at once.' : 'End this link?'}
                          </span>
                          <button type="button" className="btn danger small" onClick={() => act(l.id, 'end')}>
                            End link
                          </button>
                          <button type="button" className="btn ghost small" onClick={() => setConfirmEnd(null)}>
                            Keep
                          </button>
                        </>
                      ) : (
                        <button type="button" className="btn ghost small" onClick={() => setConfirmEnd(l.id)}>
                          End link…
                        </button>
                      ))}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export function useLinks() {
  return useLoad(() => api<Link[]>('GET', '/v1/links'), []);
}
