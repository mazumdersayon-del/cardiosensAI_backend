import { useEffect, useState } from 'react';
import { api, ApiError, type Organization, type ProfessionalProfile } from '../api';
import { useAuth } from '../auth';
import PhoneInput from '../components/PhoneInput';
import { MfaSetup } from '../components/Setup';
import { Banner, errorText, StatusPill } from '../components/ui';
import { formatId, ROLE_LABEL } from '../format';

type Msg = { kind: 'ok' | 'error'; text: string } | null;

function Profile() {
  const { user, reload } = useAuth();
  const [name, setName] = useState(user?.fullName ?? '');
  const [dob, setDob] = useState(user?.dateOfBirth ?? '');
  const [emergency, setEmergency] = useState(user?.emergencyContact ?? '');
  const [msg, setMsg] = useState<Msg>(null);
  if (!user) return null;
  return (
    <form
      className="stack"
      onSubmit={async (e) => {
        e.preventDefault();
        try {
          const body: Record<string, unknown> = { fullName: name };
          if (user.role === 'patient') Object.assign(body, { dateOfBirth: dob || null, emergencyContact: emergency || null });
          await api('PATCH', '/v1/me', body);
          await reload();
          setMsg({ kind: 'ok', text: 'Saved.' });
        } catch (ex) {
          setMsg({ kind: 'error', text: errorText(ex) });
        }
      }}
    >
      <dl className="row" style={{ gap: 24, margin: 0 }}>
        <div>
          <dt className="muted">ID</dt>
          <dd className="mono" style={{ margin: 0 }}>
            {formatId(user.uniqueId)}
          </dd>
        </div>
        <div>
          <dt className="muted">Account type</dt>
          <dd style={{ margin: 0 }}>{ROLE_LABEL[user.role]}</dd>
        </div>
        <div>
          <dt className="muted">Sign-in email</dt>
          <dd style={{ margin: 0 }}>{user.email}</dd>
        </div>
      </dl>
      <label className="field">
        {user.role === 'clinic' ? 'Your name' : 'Full name'}
        <input value={name} onChange={(e) => setName(e.target.value)} minLength={2} required />
      </label>
      {user.role === 'patient' && (
        <>
          <label className="field">
            Date of birth
            <input type="date" value={dob} onChange={(e) => setDob(e.target.value)} />
          </label>
          <label className="field">
            Emergency contact
            <input value={emergency} onChange={(e) => setEmergency(e.target.value)} placeholder="Name and phone" />
          </label>
        </>
      )}
      {msg && <Banner kind={msg.kind}>{msg.text}</Banner>}
      <div>
        <button type="submit" className="btn primary">
          Save
        </button>
      </div>
    </form>
  );
}

function DoctorDetails() {
  const { reload } = useAuth();
  const [p, setP] = useState<ProfessionalProfile | null>(null);
  const [f, setF] = useState({ registrationNumber: '', council: '', specialty: '' });
  const [msg, setMsg] = useState<Msg>(null);
  useEffect(() => {
    api<ProfessionalProfile>('GET', '/v1/me/professional-profile')
      .then((x) => {
        setP(x);
        setF({ registrationNumber: x.registrationNumber, council: x.council, specialty: x.specialty ?? '' });
      })
      .catch((e) => !(e instanceof ApiError && e.status === 404) && setMsg({ kind: 'error', text: errorText(e) }));
  }, []);
  return (
    <form
      className="stack"
      onSubmit={async (e) => {
        e.preventDefault();
        try {
          setP(await api('PUT', '/v1/me/professional-profile', { ...f, specialty: f.specialty || null }));
          await reload();
          setMsg({ kind: 'ok', text: 'Submitted. CardiosensAI will verify the new details.' });
        } catch (ex) {
          setMsg({ kind: 'error', text: errorText(ex) });
        }
      }}
    >
      <div className="row">
        Verification: <StatusPill status={p?.verificationStatus ?? 'missing'} />
      </div>
      <label className="field">
        Medical registration number
        <input value={f.registrationNumber} onChange={(e) => setF({ ...f, registrationNumber: e.target.value })} required minLength={3} />
      </label>
      <label className="field">
        Medical council
        <input value={f.council} onChange={(e) => setF({ ...f, council: e.target.value })} required minLength={2} />
      </label>
      <label className="field">
        Specialty
        <input value={f.specialty} onChange={(e) => setF({ ...f, specialty: e.target.value })} />
      </label>
      <Banner kind="info">Changing these details sends your account back for verification. You can’t see patient data until it is verified again.</Banner>
      {msg && <Banner kind={msg.kind}>{msg.text}</Banner>}
      <div>
        <button type="submit" className="btn primary">
          Submit for verification
        </button>
      </div>
    </form>
  );
}

function ClinicDetails() {
  const { reload } = useAuth();
  const [o, setO] = useState<Organization | null>(null);
  const [f, setF] = useState({ name: '', address: '', city: '', registrationNumber: '', phone: '' });
  const [msg, setMsg] = useState<Msg>(null);
  useEffect(() => {
    api<Organization>('GET', '/v1/me/organization')
      .then((x) => {
        setO(x);
        setF({ name: x.name, address: x.address ?? '', city: x.city ?? '', registrationNumber: x.registrationNumber ?? '', phone: x.phone ?? '' });
      })
      .catch((e) => setMsg({ kind: 'error', text: errorText(e) }));
  }, []);
  const input = (k: keyof typeof f, label: string, props: React.InputHTMLAttributes<HTMLInputElement> = {}) => (
    <label className="field">
      {label}
      <input value={f[k]} onChange={(e) => setF({ ...f, [k]: e.target.value })} required {...props} />
    </label>
  );
  return (
    <form
      className="stack"
      onSubmit={async (e) => {
        e.preventDefault();
        try {
          setO(await api('PUT', '/v1/me/organization', f));
          await reload();
          setMsg({ kind: 'ok', text: 'Submitted. CardiosensAI will verify the new details.' });
        } catch (ex) {
          setMsg({ kind: 'error', text: errorText(ex) });
        }
      }}
    >
      <div className="row">
        Verification: <StatusPill status={o?.verificationStatus} /> <span className="muted mono">Clinic ID {formatId(o?.uniqueId)}</span>
      </div>
      {input('name', 'Clinic name', { minLength: 2 })}
      {input('address', 'Address', { minLength: 3 })}
      {input('city', 'City', { minLength: 2 })}
      {input('registrationNumber', 'Registration / licence number', { minLength: 3 })}
      <PhoneInput label="Clinic phone" value={f.phone} onChange={(v) => setF((x) => ({ ...x, phone: v }))} required />
      <Banner kind="info">Changing these details sends the clinic back for verification. The clinic can’t see patient data until it is verified again.</Banner>
      {msg && <Banner kind={msg.kind}>{msg.text}</Banner>}
      <div>
        <button type="submit" className="btn primary">
          Submit for verification
        </button>
      </div>
    </form>
  );
}

function ChangePassword() {
  const { logout } = useAuth();
  const [cur, setCur] = useState('');
  const [pw, setPw] = useState('');
  const [msg, setMsg] = useState<Msg>(null);
  return (
    <form
      className="stack"
      onSubmit={async (e) => {
        e.preventDefault();
        try {
          await api('POST', '/v1/auth/password/change', { currentPassword: cur, newPassword: pw });
          setMsg({ kind: 'ok', text: 'Password changed. Signing you out of every device…' });
          setTimeout(() => logout(), 1500);
        } catch (ex) {
          setMsg({ kind: 'error', text: errorText(ex) });
        }
      }}
    >
      <label className="field">
        Current password
        <input type="password" autoComplete="current-password" value={cur} onChange={(e) => setCur(e.target.value)} required />
      </label>
      <label className="field">
        New password
        <input type="password" autoComplete="new-password" minLength={10} value={pw} onChange={(e) => setPw(e.target.value)} required />
      </label>
      {msg && <Banner kind={msg.kind}>{msg.text}</Banner>}
      <div>
        <button type="submit" className="btn primary">
          Change password
        </button>
      </div>
    </form>
  );
}

export default function Settings() {
  const { user } = useAuth();
  if (!user) return null;
  return (
    <div className="grid">
      <h1 className="span-12">Account</h1>
      <section className="card span-6">
        <h2>Profile</h2>
        <Profile />
      </section>
      <section className="card span-6">
        <h2>Two-step sign-in</h2>
        <MfaSetup />
      </section>
      {user.role === 'doctor' && (
        <section className="card span-6">
          <h2>Registration details</h2>
          <DoctorDetails />
        </section>
      )}
      {user.role === 'clinic' && (
        <section className="card span-6">
          <h2>Clinic details</h2>
          <ClinicDetails />
        </section>
      )}
      <section className="card span-6">
        <h2>Password</h2>
        <ChangePassword />
      </section>
    </div>
  );
}
