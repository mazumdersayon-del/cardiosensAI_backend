import { useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { api, type ClinicDoctor, type LinkedPatient } from '../api';
import { useAuth } from '../auth';
import EcgPanel from '../components/Ecg';
import IdCard from '../components/IdCard';
import { LinkRequestBox, useLinks } from '../components/Links';
import ReportsPanel from '../components/Reports';
import { SetupChecklist } from '../components/Setup';
import { Banner, useLoad } from '../components/ui';
import { History, LatestVitals } from '../components/Vitals';
import { ago, day, formatId, when } from '../format';

/** Doctor and clinic home: patients who approved a link. */
export function ProDashboard() {
  const { user, status } = useAuth();
  const [q, setQ] = useState('');
  const [patients, err] = useLoad(() => api<LinkedPatient[]>('GET', '/v1/linked-patients'), [status?.canViewPatients]);
  const [links] = useLinks();
  if (!user) return null;
  const waiting = (links ?? []).filter((l) => l.awaitingMe).length;
  const needle = q.trim().toLowerCase().replace(/\s/g, '');
  const shown = (patients ?? []).filter((p) => !needle || p.uniqueId.includes(needle) || (p.fullName ?? '').toLowerCase().replace(/\s/g, '').includes(needle));
  return (
    <div className="stack" style={{ gap: 16 }}>
      <IdCard uid={user.uniqueId} name={user.fullName} role={user.role} extra="Patients, doctors and clinics use this ID to send you link requests." />
      <SetupChecklist />
      {waiting > 0 && (
        <Banner kind="info">
          {waiting} link request{waiting > 1 ? 's' : ''} waiting for you. <Link to="/links">Review</Link>
        </Banner>
      )}
      <section className="card">
        <div className="row spread">
          <h2>Patients</h2>
          <input style={{ maxWidth: 280 }} placeholder="Search name or ID" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search patients" />
        </div>
        {err && <p className="muted">{err}</p>}
        {!status?.canViewPatients ? (
          <div className="empty">Your patient list appears here once your account is verified and two-step sign-in is on.</div>
        ) : patients && !patients.length ? (
          <div className="empty">No patients yet. Ask patients for their 16-digit ID and send a request under Links.</div>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Patient</th>
                  <th>ID</th>
                  <th>Born</th>
                  {user.role === 'clinic' && <th>Doctors</th>}
                  <th>Last reading</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {shown.map((p) => (
                  <tr key={p.patientId}>
                    <td>
                      <strong style={{ color: 'var(--heading)' }}>{p.fullName || 'Unnamed'}</strong>
                    </td>
                    <td className="mono">{formatId(p.uniqueId)}</td>
                    <td>{day(p.dateOfBirth)}</td>
                    {user.role === 'clinic' && <td>{p.doctors.length ? p.doctors.map((d) => d.name).join(', ') : <span className="muted">None</span>}</td>}
                    <td title={when(p.lastMeasurementAt)}>{ago(p.lastMeasurementAt)}</td>
                    <td>
                      <Link className="btn ghost small" to={`/patients/${p.patientId}`} state={p}>
                        Open
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}

export function PatientDetail() {
  const { patientId = '' } = useParams();
  const [patients] = useLoad(() => api<LinkedPatient[]>('GET', '/v1/linked-patients'), []);
  const p = patients?.find((x) => x.patientId === patientId);
  return (
    <div className="grid">
      <div className="span-12 row spread">
        <div>
          <Link to="/">← Patients</Link>
          <h1 style={{ marginTop: 8 }}>{p?.fullName ?? 'Patient'}</h1>
          {p && (
            <p className="muted mono">
              {formatId(p.uniqueId)} · born {day(p.dateOfBirth)} · linked {day(p.linkedAt)}
            </p>
          )}
        </div>
        <span className="pill info">Read-only · every view is logged in the patient’s access log</span>
      </div>
      <section className="card span-12">
        <h2>Latest readings</h2>
        <LatestVitals patientId={patientId} />
      </section>
      <section className="card span-12">
        <h2>History</h2>
        <History patientId={patientId} />
      </section>
      <section className="card span-6">
        <h2>ECG recordings</h2>
        <EcgPanel patientId={patientId} />
      </section>
      <section className="card span-6">
        <h2>Reports</h2>
        <ReportsPanel patientId={patientId} />
      </section>
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
