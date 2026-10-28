import { useAuth } from '../auth';
import { useCallback, useState } from 'react';
import { DocumentUpload } from '../components/Documents';
import { EcgRecordingsCard, LatestRecording, LiveEcg } from '../components/Ecg';
import FileHistory from '../components/FileHistory';
import { LinksTable, useLinks } from '../components/Links';
import ReportsPanel from '../components/Reports';
import { PhoneReminder } from '../components/Setup';
import { Banner } from '../components/ui';
import { History, LatestVitals } from '../components/Vitals';

/**
 * Patient home: a slim latest-readings strip; a big Live ECG card with the ECG recordings card beside it (click to
 * enlarge); one card with reports and documents on the left and the history of uploads and reports on the right.
 */
export default function PatientHome() {
  const { user } = useAuth();
  const [links, , reloadLinks] = useLinks();
  const [ecgKey, setEcgKey] = useState(0);
  const onSaved = useCallback(() => setEcgKey((k) => k + 1), []);
  const [filesKey, setFilesKey] = useState(0); // a new upload or report refreshes History
  const filesChanged = useCallback(() => setFilesKey((k) => k + 1), []);
  if (!user) return null;
  const waiting = (links ?? []).filter((l) => l.awaitingMe);
  return (
    <div className="grid">
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
      <section className="card span-12 readings-strip" aria-label="Latest readings">
        <h2>Latest readings</h2>
        <LatestVitals patientId="me" compact />
      </section>
      <section className="card span-8">
        <h2>Live ECG</h2>
        <LiveEcg patientId="me" onSaved={onSaved} idle={<LatestRecording patientId="me" reloadKey={ecgKey} />} />
      </section>
      <section className="card span-4">
        <h2>ECG recordings</h2>
        <EcgRecordingsCard patientId="me" reloadKey={ecgKey} />
      </section>
      <section className="card span-12 files-card">
        <div className="files-main">
          <h2>Reports and documents</h2>
          <p className="muted" style={{ marginTop: -4 }}>
            Upload lab reports, prescriptions and scans (PDF or JPEG). Your linked doctors and clinics can see them, add their own and comment.
          </p>
          <DocumentUpload patientId="me" onDone={filesChanged} />
          <h3 style={{ marginTop: 8 }}>PDF report of your readings</h3>
          <ReportsPanel patientId="me" showList={false} onCreated={filesChanged} />
        </div>
        <div className="files-history">
          <h2>History</h2>
          <FileHistory patientId="me" reloadKey={filesKey} />
        </div>
      </section>
      <section className="card span-12">
        <h2>Readings history</h2>
        <History patientId="me" />
      </section>
    </div>
  );
}
