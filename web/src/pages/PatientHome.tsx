import { Link } from 'react-router-dom';
import { useAuth } from '../auth';
import { useCallback, useState } from 'react';
import DocumentsPanel from '../components/Documents';
import EcgPanel, { LiveEcg } from '../components/Ecg';
import IdCard from '../components/IdCard';
import { LinksTable, useLinks } from '../components/Links';
import ReportsPanel from '../components/Reports';
import { PhoneReminder } from '../components/Setup';
import { Banner } from '../components/ui';
import { History, LatestVitals } from '../components/Vitals';
import { day } from '../format';

export default function PatientHome() {
  const { user } = useAuth();
  const [links, , reloadLinks] = useLinks();
  const [ecgKey, setEcgKey] = useState(0);
  const onSaved = useCallback(() => setEcgKey((k) => k + 1), []);
  if (!user) return null;
  const waiting = (links ?? []).filter((l) => l.awaitingMe);
  const team = (links ?? []).filter((l) => l.status === 'active');
  return (
    <div className="grid">
      <div className="span-12">
        <IdCard uid={user.uniqueId} name={user.fullName} role="patient" extra={[user.patientCode && `Report code ${user.patientCode}`, user.dateOfBirth && `Born ${day(user.dateOfBirth)}`].filter(Boolean).join(' · ')} />
      </div>
      {user.phone && !user.phoneVerified && (
        <div className="span-12">
          <PhoneReminder />
        </div>
      )}
      {waiting.length > 0 && (
        <div className="span-12">
          <Banner kind="info">
            <strong>
              {waiting.length} link request{waiting.length > 1 ? 's' : ''} waiting for you.
            </strong>{' '}
            Nobody sees your data until you approve.
          </Banner>
          <div className="card" style={{ marginTop: 12 }}>
            <LinksTable links={waiting} onChange={reloadLinks} emptyText="" />
          </div>
        </div>
      )}
      <section className="card span-8">
        <h2>Latest readings</h2>
        <LatestVitals patientId="me" />
      </section>
      <section className="card span-4">
        <div className="row spread">
          <h2>Care team</h2>
          <Link to="/links" className="btn ghost small">
            Manage
          </Link>
        </div>
        {team.length ? (
          <ul className="checklist">
            {team.map((l) => (
              <li key={l.id}>
                <span className={`pill ${l.other.role === 'clinic' ? 'info' : 'ok'}`}>{l.other.role === 'clinic' ? 'Clinic' : 'Doctor'}</span>
                <span>{l.other.name}</span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="muted">No doctor or clinic linked yet. Share your ID with them, or add theirs under Care team.</p>
        )}
      </section>
      <section className="card span-12">
        <h2>History</h2>
        <History patientId="me" />
      </section>
      <section className="card span-12">
        <h2>ECG recordings</h2>
        <div className="stack">
          <LiveEcg patientId="me" onSaved={onSaved} hideWhenIdle />
          <EcgPanel patientId="me" reloadKey={ecgKey} />
        </div>
      </section>
      <section className="card span-12">
        <h2>Reports and documents</h2>
        <p className="muted" style={{ marginTop: -4 }}>
          Upload lab reports, prescriptions and scans (PDF or JPEG). Your linked doctors and clinics can see them, add their own and comment.
        </p>
        <DocumentsPanel patientId="me" />
        <h3 style={{ marginTop: 24 }}>Generated PDF report of your readings</h3>
        <ReportsPanel patientId="me" />
      </section>
    </div>
  );
}
