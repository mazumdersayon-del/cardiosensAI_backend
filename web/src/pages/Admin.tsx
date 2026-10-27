import { useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { api, apiDownload, type AdminUser, type AdminUserPage, type ClinicReview, type DoctorReview, type LicenceFile, type PlatformStats } from '../api';
import { useAuth } from '../auth';
import { LicenceFileRow } from '../components/Licence';
import { MfaSetup } from '../components/Setup';
import { Banner, errorText, StatusPill, useLoad } from '../components/ui';
import { ago, day, formatId, ROLE_LABEL } from '../format';

/* The /admin portal for CardiosensAI staff. Counts and account details only — never health data. */

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

function Seg<T extends string | number>({ value, options, onChange, label }: { value: T; options: [T, string][]; onChange: (v: T) => void; label: string }) {
  return (
    <div className="seg" role="group" aria-label={label}>
      {options.map(([v, text]) => (
        <button key={String(v)} type="button" aria-pressed={v === value} onClick={() => onChange(v)}>
          {text}
        </button>
      ))}
    </div>
  );
}

const ROLES = ['patient', 'doctor', 'clinic'] as const;
const n = (x: number | undefined) => (x ?? 0).toLocaleString('en-IN');

// ---------------------------------------------------------------- overview

function SignupChart({ s }: { s: PlatformStats }) {
  const pts = s.growth.points;
  const max = Math.max(1, ...pts.map((p) => p.patient + p.doctor + p.clinic));
  const W = 720;
  const H = 180;
  const pad = 24;
  const bw = (W - pad) / Math.max(1, pts.length);
  const color = { patient: 'var(--primary)', doctor: 'var(--info-fg)', clinic: 'var(--accent)' };
  const label = (iso: string) => new Date(`${iso}T00:00:00Z`).toLocaleDateString('en-IN', { day: 'numeric', month: 'short', timeZone: 'UTC' });
  return (
    <figure style={{ margin: 0 }}>
      <svg className="chart" viewBox={`0 0 ${W} ${H + 22}`} role="img" aria-label={`Sign-ups per ${s.growth.bucket}, highest ${max}`}>
        <line x1={pad} x2={W} y1={H} y2={H} stroke="var(--line-strong)" />
        <text x={0} y={12} fontSize="11" fill="var(--muted)">
          {max}
        </text>
        {pts.map((p, i) => {
          let y = H;
          return (
            <g key={p.start}>
              <title>{`${label(p.start)}: ${p.patient} patients, ${p.doctor} doctors, ${p.clinic} clinics`}</title>
              {ROLES.map((r) => {
                const h = (p[r] / max) * (H - 16);
                y -= h;
                return h > 0 ? <rect key={r} x={pad + i * bw + bw * 0.15} y={y} width={Math.max(1, bw * 0.7)} height={h} fill={color[r]} rx={1.5} /> : null;
              })}
            </g>
          );
        })}
        {pts.length > 0 && (
          <>
            <text x={pad} y={H + 16} fontSize="11" fill="var(--muted)">
              {label(pts[0].start)}
            </text>
            <text x={W} y={H + 16} fontSize="11" fill="var(--muted)" textAnchor="end">
              {label(pts[pts.length - 1].start)}
            </text>
          </>
        )}
      </svg>
      <figcaption className="row" style={{ gap: 16, fontSize: '0.85rem' }}>
        {ROLES.map((r) => (
          <span key={r} className="row" style={{ gap: 6 }}>
            <span aria-hidden="true" style={{ width: 10, height: 10, borderRadius: 2, background: color[r], display: 'inline-block' }} />
            {ROLE_LABEL[r]}s: {n(s.growth.newInPeriod[r])}
          </span>
        ))}
        <span className="muted">per {s.growth.bucket}</span>
      </figcaption>
    </figure>
  );
}

function Tile({ label, value, to, hint }: { label: string; value: number; to?: string; hint?: string }) {
  const body = (
    <>
      <div className="muted" style={{ fontSize: '0.85rem', fontWeight: 600 }}>
        {label}
      </div>
      <div className="v">{n(value)}</div>
      {hint && <small className="muted">{hint}</small>}
    </>
  );
  return to ? (
    <Link to={to} className="vital" style={{ textDecoration: 'none', display: 'block' }}>
      {body}
    </Link>
  ) : (
    <div className="vital">{body}</div>
  );
}

export function AdminOverview() {
  const [days, setDays] = useState(30);
  const [s, err] = useLoad(() => api<PlatformStats>('GET', `/v1/admin/stats?days=${days}`), [days]);
  const [msg, setMsg] = useState<string | null>(null);
  const live = (role: string) => (s ? (s.users.byRole[role]?.active ?? 0) + (s.users.byRole[role]?.suspended ?? 0) : 0);
  const pendingReview = s ? s.verification.doctors.pending + s.verification.clinics.pending : 0;
  const deleting = s ? ROLES.reduce((t, r) => t + (s.users.byRole[r]?.deleting ?? 0), 0) : 0;
  const VOLUME: [keyof PlatformStats['volume'], string, string][] = [
    ['measurements', 'Readings', 'uploaded'],
    ['ecgRecordings', 'ECG recordings', 'recorded'],
    ['liveEcgSessions', 'Live ECG sessions', 'started'],
    ['documents', 'Documents', 'uploaded'],
    ['reports', 'PDF reports', 'created'],
    ['devices', 'Active devices', 'registered'],
    ['careLinks', 'Active care links', 'requested'],
    ['familyShares', 'Active family shares', 'created'],
  ];
  return (
    <NeedsMfa>
      <div className="stack" style={{ gap: 16 }}>
        <div className="row spread">
          <h1>Overview</h1>
          <div className="row">
            <Seg
              label="Period"
              value={days}
              onChange={setDays}
              options={[
                [7, '7 days'],
                [30, '30 days'],
                [90, '90 days'],
                [365, '1 year'],
              ]}
            />
            <button
              type="button"
              className="btn ghost small"
              onClick={() => apiDownload(`/v1/admin/stats.csv?days=${days}`, 'cardiosensai-stats.csv').catch((e) => setMsg(errorText(e)))}
            >
              Export CSV
            </button>
          </div>
        </div>
        {(err || msg) && <Banner kind="error">{err || msg}</Banner>}
        {!s && !err && <p className="muted">Loading…</p>}
        {s && (
          <>
            <div className="vitals">
              <Tile label="Patients" value={live('patient')} to="/admin/users?role=patient" hint={`${n(s.growth.newInPeriod.patient)} new`} />
              <Tile label="Doctors" value={live('doctor')} to="/admin/users?role=doctor" hint={`${n(s.verification.doctors.verified)} verified`} />
              <Tile label="Clinics" value={live('clinic')} to="/admin/users?role=clinic" hint={`${n(s.verification.clinics.verified)} verified`} />
              <Tile label="Waiting for review" value={pendingReview} to="/admin/verification" hint={`${n(s.verification.doctors.awaitingDocuments + s.verification.clinics.awaitingDocuments)} without documents`} />
              <Tile label="Active in period" value={ROLES.reduce((t, r) => t + s.activity.activeInPeriod[r], 0)} hint="signed in" />
              <Tile label="Scheduled deletion" value={deleting} to="/admin/users?status=deleting" />
            </div>
            <div className="grid">
              <section className="card span-8">
                <h2>Sign-ups</h2>
                <SignupChart s={s} />
              </section>
              <section className="card span-4">
                <h2>Patients using it</h2>
                <ul className="checklist">
                  <li>
                    <strong>{n(s.activity.patientsWithReadings)}</strong> uploaded readings in the period
                  </li>
                  <li>
                    <strong>{n(s.activity.patientsWithEcg)}</strong> recorded an ECG in the period
                  </li>
                  <li>
                    <strong>{n(s.activity.patientsWithDevices)}</strong> have an active device
                  </li>
                </ul>
              </section>
              <section className="card span-6">
                <h2>Accounts</h2>
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th>Role</th>
                        <th>Active</th>
                        <th>Suspended</th>
                        <th>Deleting</th>
                        <th>Erased</th>
                        <th>Email confirmed</th>
                        <th>Two-step on</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(['patient', 'doctor', 'clinic', 'admin'] as const).map((r) => (
                        <tr key={r}>
                          <td>{ROLE_LABEL[r]}</td>
                          <td>{n(s.users.byRole[r]?.active)}</td>
                          <td>{n(s.users.byRole[r]?.suspended)}</td>
                          <td>{n(s.users.byRole[r]?.deleting)}</td>
                          <td>{n(s.users.byRole[r]?.deleted)}</td>
                          <td>{n(s.users.emailVerified[r])}</td>
                          <td>{n(s.users.mfaEnabled[r])}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>
              <section className="card span-6">
                <h2>Signed in</h2>
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th>Role</th>
                        <th>Last 24 h</th>
                        <th>Last 7 days</th>
                        <th>Last 30 days</th>
                      </tr>
                    </thead>
                    <tbody>
                      {ROLES.map((r) => (
                        <tr key={r}>
                          <td>{ROLE_LABEL[r]}s</td>
                          <td>{n(s.activity.activeDay[r])}</td>
                          <td>{n(s.activity.activeWeek[r])}</td>
                          <td>{n(s.activity.activeMonth[r])}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>
              <section className="card span-6">
                <h2>Platform volume</h2>
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th>What</th>
                        <th>Total</th>
                        <th>In the period</th>
                      </tr>
                    </thead>
                    <tbody>
                      {VOLUME.map(([k, label, verb]) => (
                        <tr key={k}>
                          <td>{label}</td>
                          <td>{n(s.volume[k].total)}</td>
                          <td>
                            {n(s.volume[k].period)} <small className="muted">{verb}</small>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>
              <section className="card span-6">
                <h2>Verification</h2>
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th />
                        <th>Pending</th>
                        <th>No documents</th>
                        <th>Verified</th>
                        <th>Rejected</th>
                        <th>Revoked</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(
                        [
                          ['Doctors', s.verification.doctors],
                          ['Clinics', s.verification.clinics],
                        ] as const
                      ).map(([label, v]) => (
                        <tr key={label}>
                          <td>{label}</td>
                          <td>{n(v.pending)}</td>
                          <td>{n(v.awaitingDocuments)}</td>
                          <td>{n(v.verified)}</td>
                          <td>{n(v.rejected)}</td>
                          <td>{n(v.revoked)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>
            </div>
            <p className="muted" style={{ margin: 0 }}>
              Counts only — the admin portal never shows health values. Updated {ago(s.generatedAt)}; “active” means signed in.
            </p>
          </>
        )}
      </div>
    </NeedsMfa>
  );
}

// ---------------------------------------------------------------- verification

type Decision = 'verified' | 'rejected' | 'revoked';

function Review({ status, files, onDecide }: { status: string; files: LicenceFile[]; onDecide: (d: Decision, note?: string) => Promise<void> }) {
  const [asking, setAsking] = useState<'rejected' | 'revoked' | null>(null);
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const go = async (d: Decision, text?: string) => {
    setBusy(true);
    try {
      await onDecide(d, text);
      setAsking(null);
      setNote('');
    } finally {
      setBusy(false);
    }
  };
  if (asking)
    return (
      <form
        className="stack"
        onSubmit={(e) => {
          e.preventDefault();
          void go(asking, note.trim());
        }}
      >
        <label className="field">
          {asking === 'revoked' ? 'Why is the verification withdrawn?' : 'Why can’t it be verified?'} (emailed to them)
          <textarea value={note} onChange={(e) => setNote(e.target.value)} minLength={3} maxLength={300} required rows={2} autoFocus />
        </label>
        <div className="row">
          <button type="submit" className="btn danger small" disabled={busy || note.trim().length < 3}>
            {asking === 'revoked' ? 'Revoke verification' : 'Reject'}
          </button>
          <button type="button" className="btn ghost small" onClick={() => setAsking(null)}>
            Cancel
          </button>
        </div>
      </form>
    );
  return (
    <div className="row">
      {status !== 'verified' && (
        <button type="button" className="btn primary small" disabled={busy || files.length === 0} onClick={() => go('verified')} title={files.length ? undefined : 'No licence document uploaded yet'}>
          Verify
        </button>
      )}
      {status === 'pending' && (
        <button type="button" className="btn danger small" disabled={busy} onClick={() => setAsking('rejected')}>
          Reject…
        </button>
      )}
      {status === 'verified' && (
        <button type="button" className="btn danger small" disabled={busy} onClick={() => setAsking('revoked')}>
          Revoke…
        </button>
      )}
      {status !== 'verified' && files.length === 0 && <small className="muted">Waiting for their licence document</small>}
    </div>
  );
}

function ReviewCard({
  title,
  id,
  facts,
  status,
  note,
  files,
  onDecide,
}: {
  title: string;
  id: string | null;
  facts: [string, React.ReactNode][];
  status: string;
  note: string | null;
  files: LicenceFile[];
  onDecide: (d: Decision, note?: string) => Promise<void>;
}) {
  return (
    <div className="doc-card">
      <div className="row spread">
        <div>
          <strong>{title}</strong> <span className="muted mono">{formatId(id)}</span>
        </div>
        <StatusPill status={status} />
      </div>
      <dl className="row" style={{ margin: 0, gap: 18 }}>
        {facts.map(([k, v]) => (
          <div key={k}>
            <dt className="muted" style={{ fontSize: '0.8rem' }}>
              {k}
            </dt>
            <dd style={{ margin: 0 }}>{v || '—'}</dd>
          </div>
        ))}
      </dl>
      {note && (
        <Banner kind="warn">
          <strong>Reason given:</strong> {note}
        </Banner>
      )}
      {files.length > 0 ? (
        <ul className="stack" style={{ listStyle: 'none', padding: 0, margin: 0, gap: 6 }}>
          {files.map((f) => (
            <LicenceFileRow key={f.id} file={f} link={() => api('POST', `/v1/admin/verification-files/${f.id}/link`)} />
          ))}
        </ul>
      ) : (
        <small className="muted">No licence document uploaded.</small>
      )}
      <Review status={status} files={files} onDecide={onDecide} />
    </div>
  );
}

export function AdminVerification() {
  const [status, setStatus] = useState<'pending' | 'verified' | 'rejected' | 'revoked' | 'all'>('pending');
  const [docs, dErr, reloadDocs] = useLoad(() => api<DoctorReview[]>('GET', `/v1/admin/professionals?status=${status}`), [status]);
  const [orgs, oErr, reloadOrgs] = useLoad(() => api<ClinicReview[]>('GET', `/v1/admin/organizations?status=${status}`), [status]);
  const [msg, setMsg] = useState<{ kind: 'ok' | 'error'; text: string } | null>(null);
  const decide = (path: string, name: string, reload: () => void) => async (decision: Decision, note?: string) => {
    setMsg(null);
    try {
      await api('POST', path, { decision, note: note || null });
      setMsg({ kind: 'ok', text: `${name}: ${decision}. They have been emailed.` });
      reload();
    } catch (e) {
      setMsg({ kind: 'error', text: errorText(e) });
    }
  };
  return (
    <NeedsMfa>
      <div className="stack" style={{ gap: 16 }}>
        <div className="row spread">
          <h1>Verification</h1>
          <Seg
            label="Status"
            value={status}
            onChange={setStatus}
            options={[
              ['pending', 'Pending'],
              ['verified', 'Verified'],
              ['rejected', 'Rejected'],
              ['revoked', 'Revoked'],
              ['all', 'All'],
            ]}
          />
        </div>
        <Banner kind="info">
          Open the licence document and check the registration number with the medical council (doctors) or the clinical-establishment register (clinics) before verifying. Verified accounts with two-step
          sign-in can see the data of patients who link with them. Every decision is emailed and audited.
        </Banner>
        {msg && <Banner kind={msg.kind}>{msg.text}</Banner>}
        <section className="card stack">
          <h2>Doctors {docs && <small className="muted">({docs.length})</small>}</h2>
          {dErr && <Banner kind="error">{dErr}</Banner>}
          {docs && !docs.length && <div className="empty">Nothing here.</div>}
          {docs?.map((d) => (
            <ReviewCard
              key={d.userId}
              title={d.fullName ?? 'Unnamed doctor'}
              id={d.uniqueId}
              status={d.verificationStatus}
              note={d.reviewNote}
              files={d.files}
              facts={[
                ['Registration no.', <span className="mono">{d.registrationNumber}</span>],
                ['Council', d.council],
                ['Specialty', d.specialty],
                ['Email', d.emailMasked],
                ['Submitted', day(d.submittedAt)],
                ...(d.accountStatus === 'active' ? [] : [['Account', <StatusPill status={d.accountStatus} />] as [string, React.ReactNode]]),
              ]}
              onDecide={decide(`/v1/admin/professionals/${d.userId}/verify`, d.fullName ?? 'Doctor', reloadDocs)}
            />
          ))}
        </section>
        <section className="card stack">
          <h2>Clinics {orgs && <small className="muted">({orgs.length})</small>}</h2>
          {oErr && <Banner kind="error">{oErr}</Banner>}
          {orgs && !orgs.length && <div className="empty">Nothing here.</div>}
          {orgs?.map((o) => (
            <ReviewCard
              key={o.id}
              title={o.name}
              id={o.uniqueId}
              status={o.verificationStatus}
              note={o.reviewNote}
              files={o.files}
              facts={[
                ['Registration no.', <span className="mono">{o.registrationNumber}</span>],
                ['Address', [o.address, o.city].filter(Boolean).join(', ')],
                ['Contact', o.ownerName],
                ['Email', o.ownerEmailMasked],
                ['Phone', <span className="mono">{o.phoneMasked}</span>],
                ['Registered', day(o.createdAt)],
              ]}
              onDecide={decide(`/v1/admin/organizations/${o.id}/verify`, o.name, reloadOrgs)}
            />
          ))}
        </section>
      </div>
    </NeedsMfa>
  );
}

// ---------------------------------------------------------------- users

type Act = { kind: 'delete' | 'erase' | 'email' } | null;

function UserActions({ u, onDone }: { u: AdminUser; onDone: (text: string, kind?: 'ok' | 'error') => void }) {
  const [act, setAct] = useState<Act>(null);
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const run = async (fn: () => Promise<unknown>, ok: string) => {
    setBusy(true);
    try {
      await fn();
      setAct(null);
      setText('');
      onDone(ok);
    } catch (e) {
      onDone(errorText(e), 'error');
    } finally {
      setBusy(false);
    }
  };
  const who = u.fullName ?? formatId(u.uniqueId);
  if (u.role === 'admin' || u.status === 'deleted') return null;
  if (act?.kind === 'delete')
    return (
      <form className="stack" style={{ minWidth: 240 }} onSubmit={(e) => (e.preventDefault(), void run(() => api('POST', `/v1/admin/users/${u.id}/delete`, { reason: text.trim() }), `${who} is closed and will be erased in 30 days.`))}>
        <small>Blocks sign-in and hides the account from everyone now. Data is erased after 30 days unless restored. They are emailed.</small>
        <input value={text} onChange={(e) => setText(e.target.value)} placeholder="Reason (kept in the audit log)" minLength={3} maxLength={200} required autoFocus />
        <div className="row">
          <button type="submit" className="btn danger small" disabled={busy || text.trim().length < 3}>
            Delete account
          </button>
          <button type="button" className="btn ghost small" onClick={() => setAct(null)}>
            Cancel
          </button>
        </div>
      </form>
    );
  if (act?.kind === 'erase')
    return (
      <form className="stack" style={{ minWidth: 240 }} onSubmit={(e) => (e.preventDefault(), void run(() => api('POST', `/v1/admin/users/${u.id}/erase`, { confirmUniqueId: text }), `${who} was erased.`))}>
        <small>
          <strong>Irreversible.</strong> Type the 16-digit ID <span className="mono">{formatId(u.uniqueId)}</span> to erase all data now.
        </small>
        <input value={text} onChange={(e) => setText(e.target.value)} inputMode="numeric" placeholder="16-digit ID" required autoFocus />
        <div className="row">
          <button type="submit" className="btn danger small" disabled={busy || text.replace(/\D/g, '') !== u.uniqueId}>
            Erase now
          </button>
          <button type="button" className="btn ghost small" onClick={() => setAct(null)}>
            Cancel
          </button>
        </div>
      </form>
    );
  if (act?.kind === 'email')
    return (
      <form className="row" onSubmit={(e) => (e.preventDefault(), void run(() => api('POST', `/v1/admin/users/${u.id}/email`, { email: text }), `Set-password link sent to ${text}.`))}>
        <input type="email" required value={text} onChange={(e) => setText(e.target.value)} placeholder="name@example.com" style={{ minWidth: 200 }} autoFocus />
        <button type="submit" className="btn primary small" disabled={busy}>
          Send link
        </button>
        <button type="button" className="btn ghost small" onClick={() => setAct(null)}>
          Cancel
        </button>
      </form>
    );
  if (u.status === 'deleting')
    return (
      <div className="row" style={{ gap: 6 }}>
        <button type="button" className="btn ghost small" disabled={busy} onClick={() => run(() => api('POST', `/v1/admin/users/${u.id}/restore`, {}), `${who} is restored and was emailed.`)}>
          Restore
        </button>
        <button type="button" className="btn danger small" onClick={() => (setText(''), setAct({ kind: 'erase' }))}>
          Erase now…
        </button>
      </div>
    );
  return (
    <div className="row" style={{ gap: 6 }}>
      {u.status === 'suspended' && (
        <button
          type="button"
          className="btn ghost small"
          disabled={busy}
          onClick={() => run(() => api('PATCH', `/v1/admin/users/${u.id}/status`, { status: 'active', reason: 'Reactivated in the admin portal' }), `${who} is active again.`)}
        >
          Reactivate
        </button>
      )}
      <button type="button" className="btn ghost small" onClick={() => (setText(''), setAct({ kind: 'email' }))}>
        {u.hasPassword ? 'Change email…' : 'Set email…'}
      </button>
      <button type="button" className="btn danger small" onClick={() => (setText(''), setAct({ kind: 'delete' }))}>
        Delete…
      </button>
    </div>
  );
}

const PAGE = 50;

export function AdminUsers() {
  const [params, setParams] = useSearchParams();
  const role = params.get('role') ?? '';
  const status = params.get('status') ?? '';
  const verification = params.get('verification') ?? '';
  const search = params.get('q') ?? '';
  const offset = Number(params.get('offset') ?? 0) || 0;
  const [q, setQ] = useState(search);
  const [fullEmails, setFullEmails] = useState(false);
  const [msg, setMsg] = useState<{ kind: 'ok' | 'error'; text: string } | null>(null);
  const query = new URLSearchParams(Object.entries({ role, status, verification, q: search }).filter(([, v]) => v));
  const [page, err, reload] = useLoad(() => api<AdminUserPage>('GET', `/v1/admin/users?${query}&limit=${PAGE}&offset=${offset}`), [query.toString(), offset]);
  const set = (k: string, v: string) => {
    const next = new URLSearchParams(params);
    if (v) next.set(k, v);
    else next.delete(k);
    next.delete('offset');
    setParams(next, { replace: true });
  };
  const goPage = (o: number) => {
    const next = new URLSearchParams(params);
    next.set('offset', String(o));
    setParams(next, { replace: true });
  };
  return (
    <NeedsMfa>
      <div className="stack" style={{ gap: 16 }}>
        <h1>Users</h1>
        <form
          className="row filters"
          onSubmit={(e) => {
            e.preventDefault();
            set('q', q.trim());
          }}
        >
          <input placeholder="Name, email, 16-digit ID or last digits of phone" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search users" />
          <select value={role} onChange={(e) => set('role', e.target.value)} aria-label="Role">
            <option value="">All roles</option>
            <option value="patient">Patients</option>
            <option value="doctor">Doctors</option>
            <option value="clinic">Clinics</option>
            <option value="admin">Staff</option>
          </select>
          <select value={status} onChange={(e) => set('status', e.target.value)} aria-label="Account status">
            <option value="">Not erased</option>
            <option value="active">Active</option>
            <option value="suspended">Suspended</option>
            <option value="deleting">Deletion scheduled</option>
            <option value="deleted">Erased</option>
            <option value="all">All</option>
          </select>
          <select value={verification} onChange={(e) => set('verification', e.target.value)} aria-label="Verification">
            <option value="">Any verification</option>
            <option value="pending">Pending</option>
            <option value="verified">Verified</option>
            <option value="rejected">Rejected</option>
            <option value="revoked">Revoked</option>
          </select>
          <button type="submit" className="btn ghost">
            Search
          </button>
        </form>
        <div className="row spread">
          <span className="muted">{page ? `${n(page.total)} account${page.total === 1 ? '' : 's'}` : ' '}</span>
          <div className="row">
            <label className="row" style={{ gap: 6, fontSize: '0.9rem' }}>
              <input type="checkbox" checked={fullEmails} onChange={(e) => setFullEmails(e.target.checked)} /> Full email addresses
            </label>
            <button
              type="button"
              className="btn ghost small"
              onClick={() => apiDownload(`/v1/admin/users.csv?${query}${fullEmails ? '&fullEmails=true' : ''}`, 'cardiosensai-users.csv').catch((e) => setMsg({ kind: 'error', text: errorText(e) }))}
            >
              Export CSV
            </button>
          </div>
        </div>
        {msg && <Banner kind={msg.kind}>{msg.text}</Banner>}
        <section className="card">
          {err && <Banner kind="error">{err}</Banner>}
          {page && !page.items.length && <div className="empty">No accounts match.</div>}
          {page && page.items.length > 0 && (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Account</th>
                    <th>Role</th>
                    <th>Status</th>
                    <th>Email</th>
                    <th>Last active</th>
                    <th>Joined</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {page.items.map((u) => (
                    <tr key={u.id}>
                      <td>
                        <strong>{u.organizationName ?? u.fullName ?? '—'}</strong>
                        {u.organizationName && u.fullName && <div className="muted">{u.fullName}</div>}
                        <div className="muted mono">{formatId(u.uniqueId)}</div>
                      </td>
                      <td>
                        {ROLE_LABEL[u.role] ?? u.role}
                        {u.verificationStatus && (
                          <div>
                            <StatusPill status={u.verificationStatus} />
                          </div>
                        )}
                      </td>
                      <td>
                        <StatusPill status={u.status} />
                        {u.eraseAfter && <div className="muted">erases on {day(u.eraseAfter)}</div>}
                        {u.mfaEnabled && <div className="muted">two-step on</div>}
                      </td>
                      <td>
                        {u.emailMasked ?? <span className="muted">none</span>}
                        <div>{u.emailMasked && (u.hasPassword ? (u.emailVerified ? <span className="pill ok">Ready</span> : <span className="pill warn">Unconfirmed</span>) : <span className="pill warn">No password yet</span>)}</div>
                      </td>
                      <td>{u.lastActiveAt ? ago(u.lastActiveAt) : <span className="muted">never</span>}</td>
                      <td>{day(u.createdAt)}</td>
                      <td>
                        <UserActions
                          u={u}
                          onDone={(text, kind = 'ok') => {
                            setMsg({ kind, text });
                            if (kind === 'ok') reload();
                          }}
                        />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {page && page.total > PAGE && (
            <div className="row spread" style={{ marginTop: 12 }}>
              <button type="button" className="btn ghost small" disabled={offset === 0} onClick={() => goPage(Math.max(0, offset - PAGE))}>
                ← Previous
              </button>
              <span className="muted">
                {offset + 1}–{Math.min(offset + PAGE, page.total)} of {n(page.total)}
              </span>
              <button type="button" className="btn ghost small" disabled={offset + PAGE >= page.total} onClick={() => goPage(offset + PAGE)}>
                Next →
              </button>
            </div>
          )}
        </section>
      </div>
    </NeedsMfa>
  );
}
