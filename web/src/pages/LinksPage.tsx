import { useAuth } from '../auth';
import { LinkRequestBox, LinksTable, useLinks } from '../components/Links';
import { SetupChecklist } from '../components/Setup';

const TARGETS: Record<string, string> = {
  patient: 'Your doctor’s ID starts with 2, a clinic’s with 3. Linking lets them see all your readings, ECGs and reports until you end the link.',
  doctor: 'A patient’s ID starts with 1, a clinic’s with 3. Patients always approve before you see anything.',
  clinic: 'A patient’s ID starts with 1, a doctor’s with 2. Patients always approve before you see anything.',
};

export default function LinksPage() {
  const { user, status } = useAuth();
  const [links, err, reload] = useLinks();
  if (!user) return null;
  const pending = (links ?? []).filter((l) => l.status === 'pending');
  const active = (links ?? []).filter((l) => l.status === 'active');
  return (
    <div className="stack" style={{ gap: 16 }}>
      <h1>{user.role === 'patient' ? 'Care team' : 'Links'}</h1>
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
        <LinksTable links={active} onChange={reload} emptyText="No active links yet." />
      </section>
    </div>
  );
}
