import { useState } from 'react';
import { api } from '../api';
import { useAuth } from '../auth';
import { MfaSetup } from '../components/Setup';
import { Banner, errorText, StatusPill, useLoad } from '../components/ui';
import { day, formatId, ROLE_LABEL } from '../format';

interface Prof {
  userId: string;
  uniqueId: string | null;
  fullName: string | null;
  registrationNumber: string;
  council: string;
  specialty: string | null;
  verificationStatus: string;
  submittedAt: string;
}
interface Org {
  id: string;
  uniqueId: string;
  name: string;
  address: string | null;
  city: string | null;
  registrationNumber: string | null;
  phoneMasked: string | null;
  ownerName: string | null;
  verificationStatus: string;
  createdAt: string;
}
interface AdminUser {
  id: string;
  uniqueId: string | null;
  role: string;
  status: string;
  phoneMasked: string | null;
  emailMasked: string | null;
  emailVerified: boolean;
  hasPassword: boolean;
  fullName: string | null;
  createdAt: string;
}

function NeedsMfa({ children }: { children: React.ReactNode }) {
  const { user } = useAuth();
  if (user?.mfaEnabled) return <>{children}</>;
  return (
    <section className="card">
      <h2>Turn on two-step sign-in first</h2>
      <MfaSetup />
    </section>
  );
}

function Decide({ onDecide }: { onDecide: (d: 'verified' | 'rejected') => Promise<void> }) {
  const [busy, setBusy] = useState(false);
  const go = (d: 'verified' | 'rejected') => async () => {
    setBusy(true);
    try {
      await onDecide(d);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="row" style={{ justifyContent: 'flex-end' }}>
      <button type="button" className="btn primary small" disabled={busy} onClick={go('verified')}>
        Verify
      </button>
      <button type="button" className="btn danger small" disabled={busy} onClick={go('rejected')}>
        Reject
      </button>
    </div>
  );
}

export function AdminVerification() {
  const [status, setStatus] = useState<'pending' | 'verified' | 'rejected'>('pending');
  const [docs, dErr, reloadDocs] = useLoad(() => api<Prof[]>('GET', `/v1/admin/professionals?status=${status}`), [status]);
  const [orgs, oErr, reloadOrgs] = useLoad(() => api<Org[]>('GET', `/v1/admin/organizations?status=${status}`), [status]);
  const [err, setErr] = useState<string | null>(null);
  const decide = (path: string, reload: () => void) => async (decision: 'verified' | 'rejected') => {
    setErr(null);
    try {
      await api('POST', path, { decision });
      reload();
    } catch (e) {
      setErr(errorText(e));
    }
  };
  return (
    <NeedsMfa>
      <div className="stack" style={{ gap: 16 }}>
        <div className="row spread">
          <h1>Verification</h1>
          <div className="seg" role="group" aria-label="Status">
            {(['pending', 'verified', 'rejected'] as const).map((s) => (
              <button key={s} type="button" aria-pressed={s === status} onClick={() => setStatus(s)}>
                {s[0].toUpperCase() + s.slice(1)}
              </button>
            ))}
          </div>
        </div>
        <Banner kind="info">Check each registration number against the medical council or the state clinical-establishment register before verifying. Verified accounts can see the data of patients who approve them.</Banner>
        {err && <Banner kind="error">{err}</Banner>}
        <section className="card">
          <h2>Doctors</h2>
          {dErr && <p className="muted">{dErr}</p>}
          {docs && !docs.length && <div className="empty">Nothing here.</div>}
          {docs && docs.length > 0 && (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Doctor</th>
                    <th>Registration</th>
                    <th>Council</th>
                    <th>Submitted</th>
                    <th>Status</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {docs.map((d) => (
                    <tr key={d.userId}>
                      <td>
                        <strong>{d.fullName}</strong>
                        <div className="muted mono">{formatId(d.uniqueId)}</div>
                        {d.specialty && <small>{d.specialty}</small>}
                      </td>
                      <td className="mono">{d.registrationNumber}</td>
                      <td>{d.council}</td>
                      <td>{day(d.submittedAt)}</td>
                      <td>
                        <StatusPill status={d.verificationStatus} />
                      </td>
                      <td>
                        <Decide onDecide={decide(`/v1/admin/professionals/${d.userId}/verify`, reloadDocs)} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
        <section className="card">
          <h2>Clinics</h2>
          {oErr && <p className="muted">{oErr}</p>}
          {orgs && !orgs.length && <div className="empty">Nothing here.</div>}
          {orgs && orgs.length > 0 && (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Clinic</th>
                    <th>Registration</th>
                    <th>Address</th>
                    <th>Contact</th>
                    <th>Status</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {orgs.map((o) => (
                    <tr key={o.id}>
                      <td>
                        <strong>{o.name}</strong>
                        <div className="muted mono">{formatId(o.uniqueId)}</div>
                      </td>
                      <td className="mono">{o.registrationNumber}</td>
                      <td>
                        {o.address}, {o.city}
                      </td>
                      <td>
                        {o.ownerName}
                        <div className="muted mono">{o.phoneMasked}</div>
                      </td>
                      <td>
                        <StatusPill status={o.verificationStatus} />
                      </td>
                      <td>
                        <Decide onDecide={decide(`/v1/admin/organizations/${o.id}/verify`, reloadOrgs)} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      </div>
    </NeedsMfa>
  );
}

export function AdminUsers() {
  const [q, setQ] = useState('');
  const [search, setSearch] = useState('');
  const [users, err, reload] = useLoad(() => api<AdminUser[]>('GET', `/v1/admin/users?limit=100${search ? `&q=${encodeURIComponent(search)}` : ''}`), [search]);
  const [editing, setEditing] = useState<string | null>(null);
  const [email, setEmail] = useState('');
  const [msg, setMsg] = useState<{ kind: 'ok' | 'error'; text: string } | null>(null);
  return (
    <NeedsMfa>
      <div className="stack" style={{ gap: 16 }}>
        <h1>Users</h1>
        <Banner kind="info">
          Accounts created in the app before email sign-in have no password. Set their email here and they receive a link to choose one. Health data is never shown in this console.
        </Banner>
        <form
          className="row"
          onSubmit={(e) => {
            e.preventDefault();
            setSearch(q.trim());
          }}
        >
          <input style={{ flex: '1 1 260px' }} placeholder="Name, 16-digit ID or last digits of phone" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search users" />
          <button type="submit" className="btn ghost">
            Search
          </button>
        </form>
        {msg && <Banner kind={msg.kind}>{msg.text}</Banner>}
        <section className="card">
          {err && <p className="muted">{err}</p>}
          {users && (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>User</th>
                    <th>Role</th>
                    <th>Email</th>
                    <th>Phone</th>
                    <th>Since</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {users.map((u) => (
                    <tr key={u.id}>
                      <td>
                        <strong>{u.fullName ?? '—'}</strong>
                        <div className="muted mono">{formatId(u.uniqueId)}</div>
                      </td>
                      <td>
                        {ROLE_LABEL[u.role] ?? u.role} {u.status !== 'active' && <StatusPill status={u.status} />}
                      </td>
                      <td>
                        {u.emailMasked ?? <span className="muted">none</span>}
                        <div>{u.emailMasked && (u.hasPassword ? (u.emailVerified ? <span className="pill ok">Ready</span> : <span className="pill warn">Unconfirmed</span>) : <span className="pill warn">No password yet</span>)}</div>
                      </td>
                      <td className="mono">{u.phoneMasked ?? '—'}</td>
                      <td>{day(u.createdAt)}</td>
                      <td>
                        {u.role !== 'admin' &&
                          (editing === u.id ? (
                            <form
                              className="row"
                              onSubmit={async (e) => {
                                e.preventDefault();
                                try {
                                  await api('POST', `/v1/admin/users/${u.id}/email`, { email });
                                  setMsg({ kind: 'ok', text: `Set-password link sent to ${email}.` });
                                  setEditing(null);
                                  reload();
                                } catch (ex) {
                                  setMsg({ kind: 'error', text: errorText(ex) });
                                }
                              }}
                            >
                              <input type="email" required value={email} onChange={(e) => setEmail(e.target.value)} placeholder="name@example.com" style={{ minWidth: 200 }} />
                              <button type="submit" className="btn primary small">
                                Send link
                              </button>
                              <button type="button" className="btn ghost small" onClick={() => setEditing(null)}>
                                Cancel
                              </button>
                            </form>
                          ) : (
                            <button
                              type="button"
                              className="btn ghost small"
                              onClick={() => {
                                setEditing(u.id);
                                setEmail('');
                              }}
                            >
                              {u.hasPassword ? 'Change email…' : 'Set email…'}
                            </button>
                          ))}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      </div>
    </NeedsMfa>
  );
}
