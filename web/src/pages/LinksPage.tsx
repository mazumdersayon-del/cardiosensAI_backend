import { useEffect, useRef } from 'react';
import { useSearchParams } from 'react-router-dom';
import { api, type Conversation } from '../api';
import { useAuth } from '../auth';
import { LinkRequestBox, LinksTable, useLinks } from '../components/Links';
import { Thread } from '../components/Messages';
import { SetupChecklist } from '../components/Setup';
import { useLoad } from '../components/ui';

const TARGETS: Record<string, string> = {
  patient: 'Your doctor’s ID starts with 2, a clinic’s with 3. Linking lets them see all your readings, ECGs and reports until you end the link.',
  doctor: 'A patient’s ID starts with 1, a clinic’s with 3. Patients always approve before you see anything.',
  clinic: 'A patient’s ID starts with 1, a doctor’s with 2. Patients always approve before you see anything.',
};

/** Care team (patients) / Links (doctors, clinics). Patients message a linked doctor from here (`?with=` doctor's ID). */
export default function LinksPage() {
  const { user, status } = useAuth();
  const [links, err, reload] = useLinks();
  const [params, setParams] = useSearchParams();
  const patient = user?.role === 'patient';
  const [convs, , reloadConvs] = useLoad(() => (patient ? api<Conversation[]>('GET', '/v1/conversations') : Promise.resolve([])), [patient]);
  const chat = useRef<HTMLElement>(null);
  const party = patient ? params.get('with') : null;
  // the conversation for ?with= (a doctor's 16-digit ID, or a user id from a notification)
  const conv = party ? convs?.find((c) => c.partner.uniqueId === party || c.partner.userId === party) : undefined;
  const doctorLink = party ? links?.find((l) => l.other.uniqueId === party) : undefined;
  const name = conv?.partner.fullName ?? doctorLink?.other.name ?? 'Doctor';
  useEffect(() => {
    if (party) chat.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }, [party]);
  if (!user) return null;
  const pending = (links ?? []).filter((l) => l.status === 'pending');
  const active = (links ?? []).filter((l) => l.status === 'active');
  const unreadOf = (uid: string) => convs?.find((c) => c.partner.uniqueId === uid)?.unread ?? 0;
  // conversations with doctors who aren't linked any more: still readable
  const earlier = (convs ?? []).filter((c) => !c.canSend && !active.some((l) => l.other.uniqueId === c.partner.uniqueId));
  return (
    <div className="stack" style={{ gap: 16 }}>
      <h1>{patient ? 'Care team' : 'Links'}</h1>
      <SetupChecklist />
      {status?.canLink && (
        <section className="card">
          <h2>Add by ID</h2>
          <LinkRequestBox onDone={reload} targets={TARGETS[user.role]} allowDoctorOnBehalf={user.role === 'clinic'} />
        </section>
      )}
      {err && <p className="muted">{err}</p>}
      <section className="card">
        <h2>Requests</h2>
        <LinksTable links={pending} onChange={reload} emptyText="No pending requests." />
      </section>
      <section className="card">
        <h2>Active links</h2>
        {patient && active.some((l) => l.kind === 'patient_doctor') && (
          <p className="muted small" style={{ marginTop: -4 }}>
            Message a linked doctor with the Message button. For an emergency call your local emergency number, not a message.
          </p>
        )}
        <LinksTable
          links={active}
          onChange={reload}
          emptyText="No active links yet."
          actions={(l) =>
            patient && l.kind === 'patient_doctor' ? (
              <button
                type="button"
                className={`btn small ${party === l.other.uniqueId ? 'primary' : 'ghost'}`}
                aria-pressed={party === l.other.uniqueId}
                onClick={() => setParams({ with: l.other.uniqueId })}
              >
                Message{unreadOf(l.other.uniqueId) > 0 && <span className="nav-badge">{unreadOf(l.other.uniqueId)}</span>}
              </button>
            ) : null
          }
        />
        {earlier.length > 0 && (
          <p className="muted small">
            Earlier conversations (read-only):{' '}
            {earlier.map((c, i) => (
              <span key={c.partner.userId}>
                {i > 0 && ', '}
                <button type="button" className="link-btn" onClick={() => setParams({ with: c.partner.uniqueId ?? c.partner.userId })}>
                  {c.partner.fullName}
                </button>
              </span>
            ))}
          </p>
        )}
      </section>
      {party && (
        <section className="card" id="messages" ref={chat}>
          <div className="row spread" style={{ marginBottom: 8 }}>
            <h2 style={{ margin: 0 }}>Messages with {name}</h2>
            <button type="button" className="btn ghost small" onClick={() => setParams({})}>
              Close
            </button>
          </div>
          <Thread party={party} onRead={() => (conv?.unread ?? 0) > 0 && reloadConvs()} onSent={reloadConvs} />
        </section>
      )}
    </div>
  );
}
