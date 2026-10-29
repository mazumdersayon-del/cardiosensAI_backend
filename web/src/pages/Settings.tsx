import { useEffect, useRef, useState, type ReactNode } from 'react';
import { api, ApiError, type DoctorListing, type Organization, type ProfessionalProfile } from '../api';
import { useAuth } from '../auth';
import { AvatarEditor } from '../components/Avatar';
import { ContactDetails } from '../components/Contact';
import { DevicesSection } from '../components/Devices';
import IdCard from '../components/IdCard';
import { LicenceSection } from '../components/Licence';
import PhoneInput from '../components/PhoneInput';
import { AboutYouForm } from '../components/PatientDetails';
import { MfaSetup } from '../components/Setup';
import { Banner, errorText, StatusPill } from '../components/ui';
import { day, formatId, ROLE_LABEL } from '../format';

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
      <AvatarEditor />
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
  const { reload, status } = useAuth();
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
        Verification: <StatusPill status={status?.verificationStatus ?? p?.verificationStatus ?? 'missing'} />
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

/** Doctors: the card patients see in their Doctors list, and whether to be listed at all. */
function DoctorListingSection() {
  const [f, setF] = useState({ listed: false, degrees: '', experienceYears: '', languages: '', practice: '', city: '', pinCode: '', about: '' });
  const [l, setL] = useState<DoctorListing | null>(null);
  const [msg, setMsg] = useState<Msg>(null);
  const fill = (x: DoctorListing) => {
    setL(x);
    setF({ listed: x.listed, degrees: x.degrees ?? '', experienceYears: x.experienceYears?.toString() ?? '', languages: x.languages ?? '', practice: x.practice ?? '', city: x.city ?? '', pinCode: x.pinCode ?? '', about: x.about ?? '' });
  };
  useEffect(() => {
    api<DoctorListing>('GET', '/v1/me/doctor-listing').then(fill, (e) => setMsg({ kind: 'error', text: errorText(e) }));
  }, []);
  const text = (key: 'degrees' | 'languages' | 'practice' | 'city' | 'pinCode', label: string, hint?: string, max = 160) => (
    <label className="field">
      {label}
      {hint && <small className="muted">{hint}</small>}
      <input value={f[key]} maxLength={max} onChange={(e) => setF({ ...f, [key]: e.target.value })} />
    </label>
  );
  return (
    <form
      className="stack"
      onSubmit={async (e) => {
        e.preventDefault();
        try {
          const n = f.experienceYears.trim();
          fill(await api<DoctorListing>('PUT', '/v1/me/doctor-listing', { ...f, pinCode: f.pinCode.replace(/\s/g, ''), experienceYears: n ? Number(n) : null }));
          setMsg({ kind: 'ok', text: 'Saved.' });
        } catch (ex) {
          setMsg({ kind: 'error', text: errorText(ex) });
        }
      }}
    >
      <label className="row" style={{ gap: 10, fontWeight: 600 }}>
        <input type="checkbox" checked={f.listed} onChange={(e) => setF({ ...f, listed: e.target.checked })} />
        Show me in the patients’ Doctors list
      </label>
      {l && (l.visible ? <Banner kind="ok">Patients can see your card now.</Banner> : l.listed && l.hiddenReason ? <Banner kind="info">{l.hiddenReason}</Banner> : null)}
      <p className="muted" style={{ margin: 0 }}>
        Patients see your name, picture, specialty ({l?.specialty || 'set it under Registration details'}), registration council and the details below. Your mobile number and email are never shown. Saving these doesn’t affect your verification.
      </p>
      {text('degrees', 'Degrees', 'For example: MBBS, MD (Medicine), DM (Cardiology)')}
      <label className="field">
        Years of experience
        <input type="number" min={0} max={70} value={f.experienceYears} onChange={(e) => setF({ ...f, experienceYears: e.target.value })} />
      </label>
      {text('practice', 'Hospital or clinic', 'Where you see patients')}
      {text('city', 'City', undefined, 80)}
      {text('pinCode', 'Practice PIN code', 'Lets patients find you with “Near me”. Only the area is shown, not the address.', 7)}
      {text('languages', 'Languages', 'For example: English, Hindi, Bengali')}
      <label className="field">
        About you
        <small className="muted">What you treat and how patients can work with you (up to 800 characters).</small>
        <textarea rows={4} maxLength={800} value={f.about} onChange={(e) => setF({ ...f, about: e.target.value })} />
      </label>
      {msg && <Banner kind={msg.kind}>{msg.text}</Banner>}
      <div>
        <button type="submit" className="btn primary">
          Save
        </button>
      </div>
    </form>
  );
}

function ClinicDetails() {
  const { reload, status } = useAuth();
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
        Verification: <StatusPill status={status?.verificationStatus ?? o?.verificationStatus} /> <span className="muted mono">Clinic ID {formatId(o?.uniqueId)}</span>
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
  const { logout, user } = useAuth();
  const has = user?.hasPassword !== false;
  const [cur, setCur] = useState('');
  const [pw, setPw] = useState('');
  const [again, setAgain] = useState('');
  const [msg, setMsg] = useState<Msg>(null);
  const mismatch = again.length > 0 && again !== pw;
  return (
    <form
      className="stack"
      onSubmit={async (e) => {
        e.preventDefault();
        if (pw !== again) {
          setMsg({ kind: 'error', text: 'The two new passwords don’t match.' });
          return;
        }
        try {
          await api('POST', '/v1/auth/password/change', { currentPassword: has ? cur : null, newPassword: pw });
          setMsg({ kind: 'ok', text: `Password ${has ? 'changed' : 'set'}. Signing you out of every device…` });
          setTimeout(() => logout(), 1500);
        } catch (ex) {
          setMsg({ kind: 'error', text: errorText(ex) });
        }
      }}
    >
      {has ? (
        <label className="field">
          Current password
          <input type="password" autoComplete="current-password" value={cur} onChange={(e) => setCur(e.target.value)} required />
        </label>
      ) : (
        <small className="muted">You sign in with Google or one-time codes. Set a password to also sign in with it.</small>
      )}
      <label className="field">
        New password
        <input type="password" autoComplete="new-password" minLength={10} value={pw} onChange={(e) => setPw(e.target.value)} required />
      </label>
      <label className="field">
        Re-enter new password
        <input
          type="password"
          autoComplete="new-password"
          minLength={10}
          value={again}
          onChange={(e) => setAgain(e.target.value)}
          required
          aria-invalid={mismatch}
          aria-describedby="pw-again-hint"
        />
        {mismatch && (
          <small id="pw-again-hint" style={{ color: 'var(--danger)' }}>
            Doesn’t match the new password yet.
          </small>
        )}
      </label>
      {msg && <Banner kind={msg.kind}>{msg.text}</Banner>}
      <div>
        <button type="submit" className="btn primary" disabled={mismatch}>
          {has ? 'Change password' : 'Set password'}
        </button>
      </div>
    </form>
  );
}

function IdSection() {
  const { user } = useAuth();
  if (!user) return null;
  const extra =
    user.role === 'patient'
      ? [user.patientCode && `Report code ${user.patientCode}`, user.dateOfBirth && `Born ${day(user.dateOfBirth)}`].filter(Boolean).join(' · ')
      : 'Patients, doctors and clinics use this ID to send you link requests.';
  return <IdCard uid={user.uniqueId} name={user.fullName} role={user.role} extra={extra || undefined} />;
}

type Section = { id: string; label: string; hint: string; body: ReactNode };

function sectionsFor(role: string): Section[] {
  const pro = role === 'doctor' || role === 'clinic';
  const list: (Section | false)[] = [
    { id: 'id', label: 'Your ID', hint: 'Give it to a doctor or clinic (or let them scan the QR code) so they can send you a link request.', body: <IdSection /> },
    { id: 'profile', label: 'Profile', hint: 'Your picture, name' + (role === 'patient' ? ', date of birth and emergency contact.' : '.'), body: <Profile /> },
    { id: 'contact', label: 'Mobile and email', hint: 'Where your sign-in codes and messages go.', body: <ContactDetails /> },
    role === 'patient' && {
      id: 'about',
      label: 'About you',
      hint: 'Height, weight, address with PIN code and health background. Your linked doctors see these; the PIN code also finds doctors near you.',
      body: <AboutYouForm />,
    },
    role === 'doctor' && { id: 'registration', label: 'Registration details', hint: 'Checked by CardiosensAI before you can see patients.', body: <DoctorDetails /> },
    role === 'doctor' && { id: 'listing', label: 'Doctors list', hint: 'Your card in the patients’ Doctors list. You choose whether to appear.', body: <DoctorListingSection /> },
    role === 'clinic' && { id: 'clinic', label: 'Clinic details', hint: 'Checked by CardiosensAI before the clinic can see patients.', body: <ClinicDetails /> },
    role !== 'admin' && {
      id: 'devices',
      label: 'Devices',
      hint: role === 'patient' ? 'The devices that send your readings and ECGs. A device can be registered to one account at a time.' : 'Your own equipment, such as a 12-lead monitor. It records for patients who have linked with you.',
      body: <DevicesSection />,
    },
    pro && { id: 'documents', label: 'Verification documents', hint: 'Your registration or licence certificate.', body: <LicenceSection /> },
    { id: 'security', label: 'Two-step sign-in', hint: 'A code from an authenticator app when you sign in.', body: <MfaSetup /> },
    { id: 'password', label: 'Password', hint: 'At least 10 characters. Changing it signs you out everywhere.', body: <ChangePassword /> },
  ];
  return list.filter((x): x is Section => !!x);
}

/** Account: one continuous page, a sticky menu on the left that follows the scroll and jumps to each section. */
export default function Settings() {
  const { user } = useAuth();
  const sections = sectionsFor(user?.role ?? 'patient');
  const [active, setActive] = useState(sections[0].id);
  const navRef = useRef<HTMLElement>(null);

  // phones show the menu as a sideways strip: keep the current section's tab in view
  useEffect(() => {
    const nav = navRef.current;
    const link = nav?.querySelector<HTMLElement>(`a[href="#${active}"]`);
    if (nav && link && nav.scrollWidth > nav.clientWidth) nav.scrollLeft = link.offsetLeft - (nav.clientWidth - link.offsetWidth) / 2;
  }, [active]);

  useEffect(() => {
    // a link to a section (/settings#password): jump now and once more after the sections above have loaded their data
    const hash = window.location.hash.slice(1);
    const jump = () => document.getElementById(hash)?.scrollIntoView();
    let rejump: number | undefined;
    if (hash) {
      jump();
      rejump = window.setTimeout(jump, 600);
    }
    const seen = new Map<string, boolean>();
    const io = new IntersectionObserver(
      (entries) => {
        for (const e of entries) seen.set(e.target.id, e.isIntersecting);
        const first = sections.find((x) => seen.get(x.id));
        if (first) setActive(first.id);
      },
      { rootMargin: '-90px 0px -55% 0px' }, // a section counts as current once its top passes under the header
    );
    for (const x of sections) {
      const el = document.getElementById(x.id);
      if (el) io.observe(el);
    }
    // short last sections never reach the top: at the bottom of the page the last one is current
    const atEnd = () => {
      if (window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 4) setActive(sections[sections.length - 1].id);
    };
    window.addEventListener('scroll', atEnd, { passive: true });
    return () => {
      window.clearTimeout(rejump);
      io.disconnect();
      window.removeEventListener('scroll', atEnd);
    };
  }, [user?.role]);

  if (!user) return null;
  const go = (id: string) => (e: React.MouseEvent) => {
    e.preventDefault();
    document.getElementById(id)?.scrollIntoView({ behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' });
    window.history.replaceState(null, '', `#${id}`);
    setActive(id);
  };
  return (
    <div className="account">
      <aside className="account-nav" aria-label="Account sections">
        <div className="account-who">
          <strong>{user.fullName || user.email}</strong>
          <span className="muted mono">
            {ROLE_LABEL[user.role]} · {formatId(user.uniqueId)}
          </span>
        </div>
        <nav ref={navRef}>
          {sections.map((x) => (
            <a key={x.id} href={`#${x.id}`} onClick={go(x.id)} className={active === x.id ? 'active' : undefined} aria-current={active === x.id ? 'true' : undefined}>
              {x.label}
            </a>
          ))}
        </nav>
      </aside>
      <div className="account-body">
        <h1>Account</h1>
        {sections.map((x) => (
          <section key={x.id} id={x.id} className="account-section" aria-labelledby={`${x.id}-h`}>
            <header>
              <h2 id={`${x.id}-h`}>{x.label}</h2>
              <p className="muted">{x.hint}</p>
            </header>
            <div className="account-content">{x.body}</div>
          </section>
        ))}
      </div>
    </div>
  );
}
