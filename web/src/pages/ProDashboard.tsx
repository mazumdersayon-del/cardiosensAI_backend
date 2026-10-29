import { useEffect, useState } from 'react';
import { Link, useLocation, useParams } from 'react-router-dom';
import { api, type Appointment, type ClinicDoctor, type LinkedPatient } from '../api';
import { MODE_LABEL, STATUS_LABEL, STATUS_TONE, whenLabel } from '../calendar';
import { useAuth } from '../auth';
import DocumentsPanel from '../components/Documents';
import { EcgCards } from '../components/EcgCards';
import IdCard from '../components/IdCard';
import { LinkRequestBox, useLinks } from '../components/Links';
import ReportsPanel from '../components/Reports';
import { PhoneReminder, SetupChecklist } from '../components/Setup';
import { Thread } from '../components/Messages';
import { Banner, useLoad } from '../components/ui';
import { History, LatestVitals } from '../components/Vitals';
import { ago, day, formatId, when } from '../format';
import { PatientDetailsCard } from '../components/PatientDetails';

const BROWSER_TZ = Intl.DateTimeFormat().resolvedOptions().timeZone;

/** Tap-to-call / tap-to-mail; only confirmed contact details come from the server. */
export function ContactCell({ phone, email }: { phone: string | null; email: string | null }) {
  if (!phone && !email) return <span className="muted">—</span>;
  return (
    <span className="contact-cell">
      {phone && (
        <a href={`tel:${phone}`} className="mono">
          {phone}
        </a>
      )}
      {email && (
        <a href={`mailto:${email}`} title={email} style={{ overflow: 'hidden', textOverflow: 'ellipsis' }}>
          {email}
        </a>
      )}
    </span>
  );
}

function NextCell({ n }: { n: LinkedPatient['nextAppointment'] }) {
  if (!n) return <span className="muted">None booked</span>;
  return (
    <span>
      <strong>{whenLabel(n.startsAt, BROWSER_TZ)}</strong>
      <br />
      <small className="muted">
        {MODE_LABEL[n.mode]} · <span className={`pill ${STATUS_TONE[n.status]}`}>{STATUS_LABEL[n.status]}</span>
      </small>
    </span>
  );
}

function activity(p: LinkedPatient): number {
  if (p.unreadMessages > 0 || p.ecgLive) return Number.MAX_SAFE_INTEGER - (p.ecgLive ? 0 : 1);
  const times = [p.lastMeasurementAt, p.latestEcg?.startedAt].filter(Boolean).map((t) => Date.parse(t as string));
  return times.length ? Math.max(...times) : 0;
}

/** Doctor Patients tab / clinic home: patients who approved a link, with contact, next appointment, last ECG and messages. */
export function ProDashboard() {
  const { user, status } = useAuth();
  const [q, setQ] = useState('');
  const [tick, setTick] = useState(0);
  const [patients, err] = useLoad(() => api<LinkedPatient[]>('GET', '/v1/linked-patients'), [status?.canViewPatients, tick]);
  const [links] = useLinks();
  useEffect(() => {
    const t = setInterval(() => setTick((x) => x + 1), 15000); // live badges and new readings
    return () => clearInterval(t);
  }, []);
  if (!user) return null;
  const waiting = (links ?? []).filter((l) => l.awaitingMe).length;
  const needle = q.trim().toLowerCase().replace(/\s/g, '');
  const shown = (patients ?? [])
    .filter((p) => !needle || p.uniqueId.includes(needle) || (p.fullName ?? '').toLowerCase().replace(/\s/g, '').includes(needle))
    .sort((a, b) => activity(b) - activity(a));
  const liveCount = (patients ?? []).filter((p) => p.ecgLive).length;
  return (
    <div className="stack" style={{ gap: 16 }}>
      <PhoneReminder />
      <SetupChecklist />
      {waiting > 0 && (
        <Banner kind="info">
          {waiting} link request{waiting > 1 ? 's' : ''} waiting for you. <Link to="/links">Review</Link>
        </Banner>
      )}
      <section className="card">
        <div className="row spread">
          <div>
            <h2 style={{ marginBottom: 2 }}>Patients</h2>
            <small className="muted">
              {liveCount > 0 ? `${liveCount} ECG${liveCount > 1 ? 's' : ''} recording now · ` : ''}New messages and newest activity first · refreshes every 15 s
            </small>
          </div>
          <input style={{ maxWidth: 260 }} placeholder="Search name or ID" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search patients" />
        </div>
        {err && <p className="muted">{err}</p>}
        {!status?.canViewPatients ? (
          <div className="empty">Your patients’ recordings appear here once your account is verified and two-step sign-in is on.</div>
        ) : patients && !patients.length ? (
          <div className="empty">No patients yet. Ask patients for their 16-digit ID and send a request under Links.</div>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Patient</th>
                  <th>Contact</th>
                  {user.role === 'doctor' && <th>Next appointment</th>}
                  <th>Last ECG</th>
                  {user.role === 'clinic' && <th>Doctors</th>}
                  <th />
                </tr>
              </thead>
              <tbody>
                {shown.map((p) => (
                  <tr key={p.patientId}>
                    <td>
                      <strong style={{ color: 'var(--heading)' }}>{p.fullName || 'Unnamed'}</strong>
                      <div className="muted mono" style={{ fontSize: '0.8rem' }}>
                        {formatId(p.uniqueId)}
                        {p.dateOfBirth ? ` · born ${day(p.dateOfBirth)}` : ''}
                      </div>
                    </td>
                    <td>
                      <ContactCell phone={p.phone} email={p.email} />
                    </td>
                    {user.role === 'doctor' && (
                      <td>
                        <NextCell n={p.nextAppointment} />
                      </td>
                    )}
                    <td>
                      {p.ecgLive ? (
                        <span className={`pill ${p.ecgLive === 'live' ? 'error live-dot' : 'warn'}`}>● {p.ecgLive === 'live' ? 'Recording now' : 'Signal lost'}</span>
                      ) : p.latestEcg ? (
                        <span title={when(p.latestEcg.startedAt)}>
                          {p.latestEcg.leadCount === 12 ? <span className="pill info">12-lead</span> : p.latestEcg.lead} {p.latestEcg.durationS.toFixed(0)} s
                          <br />
                          <small className="muted">{ago(p.latestEcg.startedAt)}</small>
                        </span>
                      ) : (
                        <span className="muted">—</span>
                      )}
                    </td>
                    {user.role === 'clinic' && <td>{p.doctors.length ? p.doctors.map((d) => d.name).join(', ') : <span className="muted">None</span>}</td>}
                    <td>
                      <div className="row" style={{ gap: 6, flexWrap: 'nowrap' }}>
                        {user.role === 'doctor' && (
                          <Link className="btn ghost small unread-btn" to={`/patients/${p.patientId}#messages`} aria-label={p.unreadMessages ? `Messages, ${p.unreadMessages} new` : 'Messages'}>
                            Message
                            {p.unreadMessages > 0 && <span className="nav-badge">{p.unreadMessages}</span>}
                          </Link>
                        )}
                        <Link className="btn ghost small" to={`/patients/${p.patientId}`}>
                          Open
                        </Link>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
      {user.role === 'clinic' && (
        <IdCard uid={user.uniqueId} name={user.fullName} role={user.role} extra="Patients, doctors and clinics use this ID to send you link requests." />
      )}
    </div>
  );
}

export function PatientDetail() {
  const { patientId = '' } = useParams();
  const { user } = useAuth();
  const { hash } = useLocation();
  const [patients] = useLoad(() => api<LinkedPatient[]>('GET', '/v1/linked-patients'), []);
  const [showHistory, setShowHistory] = useState(false);
  const p = patients?.find((x) => x.patientId === patientId);
  const doctor = user?.role === 'doctor';
  useEffect(() => {
    if (hash === '#messages' && p) document.getElementById('messages')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }, [hash, p]);
  return (
    <div className="grid">
      <div className="span-12 row spread">
        <div>
          <Link to={doctor ? '/patients' : '/'}>← Patients</Link>
          <h1 style={{ marginTop: 8 }}>{p?.fullName ?? 'Patient'}</h1>
          {p && (
            <p className="muted mono">
              {formatId(p.uniqueId)} · born {day(p.dateOfBirth)} · linked {day(p.linkedAt)}
            </p>
          )}
          {p && (p.phone || p.email) && (
            <div className="row" style={{ gap: 8 }}>
              {p.phone && (
                <a className="btn ghost small" href={`tel:${p.phone}`}>
                  Call {p.phone}
                </a>
              )}
              {p.email && (
                <a className="btn ghost small" href={`mailto:${p.email}`}>
                  Email {p.email}
                </a>
              )}
            </div>
          )}
        </div>
        <span className="pill info">Every view is logged in the patient’s access log</span>
      </div>
      {doctor && p && (
        <>
          <section className="card span-6" id="messages">
            <h2>
              Messages {p.unreadMessages > 0 && <span className="pill warn">{p.unreadMessages} new</span>}
            </h2>
            <Thread party={patientId} height={320} />
          </section>
          <section className="card span-6">
            <PatientAppointmentsCard patientId={patientId} />
          </section>
        </>
      )}
      {p && (
        <section className="card span-12">
          <PatientDetailsCard patientId={patientId} />
        </section>
      )}
      <EcgCards patientId={patientId} />
      <section className="card span-12">
        <h2>Latest readings</h2>
        <LatestVitals patientId={patientId} />
      </section>
      <section className="card span-12">
        <h2>Reports and documents</h2>
        <DocumentsPanel patientId={patientId} />
      </section>
      <section className="card span-12">
        <button type="button" className="btn ghost history-toggle" aria-expanded={showHistory} onClick={() => setShowHistory(!showHistory)}>
          <span>History: trends, all readings and PDF reports</span>
          <span aria-hidden="true">{showHistory ? '▴ Hide' : '▾ Show'}</span>
        </button>
        {showHistory && (
          <div className="stack" style={{ marginTop: 16, gap: 24 }}>
            <History patientId={patientId} />
            <div>
              <h3>Generated PDF reports</h3>
              <ReportsPanel patientId={patientId} />
            </div>
          </div>
        )}
      </section>
    </div>
  );
}

/** Doctor: this patient's appointments with me (upcoming, then the last few). */
function PatientAppointmentsCard({ patientId }: { patientId: string }) {
  const [up, upErr] = useLoad(() => api<Appointment[]>('GET', `/v1/appointments?scope=upcoming&with=${patientId}`), [patientId]);
  const [past] = useLoad(() => api<Appointment[]>('GET', `/v1/appointments?scope=past&with=${patientId}`), [patientId]);
  const upcoming = (up ?? []).filter((a) => a.status === 'requested' || a.status === 'accepted');
  const row = (a: Appointment) => (
    <li key={a.id} className="appt-row">
      <div className="appt-main">
        <strong>{whenLabel(a.startsAt, BROWSER_TZ)}</strong>
        <span className="muted small">
          {MODE_LABEL[a.mode]}
          {a.reason ? ` · “${a.reason}”` : ''}
        </span>
      </div>
      <span className={`pill ${STATUS_TONE[a.status]}`}>{STATUS_LABEL[a.status]}</span>
    </li>
  );
  return (
    <div className="stack">
      <div className="row spread">
        <h2 style={{ margin: 0 }}>Appointments</h2>
        <Link to="/" className="small">
          Calendar
        </Link>
      </div>
      {upErr && <Banner kind="error">{upErr}</Banner>}
      <h3 style={{ margin: 0 }}>Upcoming</h3>
      {up && upcoming.length === 0 && <p className="muted" style={{ margin: 0 }}>Nothing booked.</p>}
      <ul className="appt-list">{upcoming.map(row)}</ul>
      {past && past.length > 0 && (
        <>
          <h3 style={{ margin: '8px 0 0' }}>Past</h3>
          <ul className="appt-list">{past.slice(0, 5).map(row)}</ul>
        </>
      )}
    </div>
  );
}

/** Clinic: the doctors linked to this clinic. */
export function ClinicDoctors() {
  const { status } = useAuth();
  const [doctors, err, reload] = useLoad(() => api<ClinicDoctor[]>('GET', '/v1/clinic/doctors'), []);
  const [confirm, setConfirm] = useState<string | null>(null);
  return (
    <div className="stack" style={{ gap: 16 }}>
      <h1>Doctors</h1>
      <SetupChecklist />
      {status?.canLink && (
        <section className="card">
          <h2>Invite a doctor</h2>
          <LinkRequestBox onDone={reload} targets="The doctor’s 16-digit ID (starts with 2). They approve the request from their own account." />
        </section>
      )}
      <section className="card">
        <h2>Linked doctors</h2>
        {err && <p className="muted">{err}</p>}
        {doctors && !doctors.length && <div className="empty">No doctors linked to this clinic yet.</div>}
        {doctors && doctors.length > 0 && (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Doctor</th>
                  <th>ID</th>
                  <th>Specialty</th>
                  <th>Clinic patients they follow</th>
                  <th>Linked</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {doctors.map((d) => (
                  <tr key={d.linkId}>
                    <td>
                      <strong style={{ color: 'var(--heading)' }}>{d.doctor.name}</strong> {d.doctor.verified ? <span className="pill ok">Verified</span> : <span className="pill warn">Unverified</span>}
                    </td>
                    <td className="mono">{formatId(d.doctor.uniqueId)}</td>
                    <td>{d.doctor.detail ?? '—'}</td>
                    <td>{d.patientCount}</td>
                    <td>{day(d.linkedAt)}</td>
                    <td>
                      {confirm === d.linkId ? (
                        <div className="row">
                          <button
                            type="button"
                            className="btn danger small"
                            onClick={async () => {
                              await api('POST', `/v1/links/${d.linkId}/end`);
                              setConfirm(null);
                              reload();
                            }}
                          >
                            Remove
                          </button>
                          <button type="button" className="btn ghost small" onClick={() => setConfirm(null)}>
                            Keep
                          </button>
                        </div>
                      ) : (
                        <button type="button" className="btn ghost small" onClick={() => setConfirm(d.linkId)}>
                          Remove…
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <p className="muted" style={{ marginTop: 12 }}>
          Removing a doctor ends their link with the clinic only. Their own links with patients stay until the patient or the doctor ends them.
        </p>
      </section>
    </div>
  );
}
