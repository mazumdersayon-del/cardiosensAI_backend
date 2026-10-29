import { useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { api, ApiError, type PatientProfile, type PinInfo } from '../api';
import { day } from '../format';
import { Banner, errorText, useLoad } from './ui';

/*
 * Patient details (api/v1/patient_profile.py): height, weight, sex, blood group, address with PIN code, medical
 * background. Patients edit them under Account → About you, and once right after sign-up (/about-you, skippable).
 * Home shows a reminder until a PIN code is saved. Linked doctors see them read-only (street lines only if shared).
 */

export const SEX_LABEL: Record<string, string> = { female: 'Female', male: 'Male', other: 'Other', undisclosed: 'Prefer not to say' };
export const BLOOD_GROUPS = ['A+', 'A-', 'B+', 'B-', 'AB+', 'AB-', 'O+', 'O-'];
export const SMOKING_LABEL: Record<string, string> = { never: 'Never smoked', former: 'Used to smoke', current: 'Smokes now' };
export const CONDITION_LABEL: Record<string, string> = {
  hypertension: 'High blood pressure',
  diabetes: 'Diabetes',
  heart_disease: 'Heart disease (angina, heart attack, stents, bypass)',
  heart_failure: 'Heart failure',
  arrhythmia: 'Irregular heartbeat (e.g. atrial fibrillation)',
  stroke: 'Stroke',
  high_cholesterol: 'High cholesterol',
  kidney_disease: 'Kidney disease',
  thyroid: 'Thyroid problem',
  asthma_copd: 'Asthma / COPD',
};

type Form = {
  heightCm: string;
  weightKg: string;
  sex: string;
  bloodGroup: string;
  addressLine1: string;
  addressLine2: string;
  city: string;
  district: string;
  state: string;
  pinCode: string;
  shareAddress: boolean;
  conditions: string[];
  conditionsOther: string;
  allergies: string;
  medicines: string;
  smoking: string;
};

function toForm(p: PatientProfile): Form {
  return {
    heightCm: p.heightCm?.toString() ?? '',
    weightKg: p.weightKg?.toString() ?? '',
    sex: p.sex ?? '',
    bloodGroup: p.bloodGroup ?? '',
    addressLine1: p.addressLine1 ?? '',
    addressLine2: p.addressLine2 ?? '',
    city: p.city ?? '',
    district: p.district ?? '',
    state: p.state ?? '',
    pinCode: p.pinCode ?? '',
    shareAddress: p.shareAddress,
    conditions: p.conditions,
    conditionsOther: p.conditionsOther ?? '',
    allergies: p.allergies ?? '',
    medicines: p.medicines ?? '',
    smoking: p.smoking ?? '',
  };
}

function bmiText(h: string, w: string): string | null {
  const hh = Number(h);
  const ww = Number(w);
  if (!hh || !ww) return null;
  const b = ww / (hh / 100) ** 2;
  const band = b < 18.5 ? 'underweight' : b < 25 ? 'healthy range' : b < 30 ? 'overweight' : 'obese range';
  return `BMI ${b.toFixed(1)} (${band})`;
}

/** The full form. `onDone` after a successful save (sign-up step moves on). */
export function AboutYouForm({ onDone, submitLabel = 'Save' }: { onDone?: (p: PatientProfile) => void; submitLabel?: string }) {
  const [loaded, loadErr] = useLoad(() => api<PatientProfile>('GET', '/v1/me/patient-profile'), []);
  const [f, setF] = useState<Form | null>(null);
  const [pin, setPin] = useState<PinInfo | null>(null);
  const [pinMsg, setPinMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ kind: 'ok' | 'error'; text: string } | null>(null);
  useEffect(() => {
    if (loaded) setF(toForm(loaded));
  }, [loaded]);

  // District and state from the PIN code as soon as it has 6 digits.
  const digits = f?.pinCode.replace(/\s/g, '') ?? '';
  useEffect(() => {
    if (!/^[1-9]\d{5}$/.test(digits)) {
      setPin(null);
      setPinMsg(digits.length >= 6 ? 'A PIN code has 6 digits and doesn’t start with 0.' : null);
      return;
    }
    let live = true;
    api<PinInfo>('GET', `/v1/pincodes/${digits}`).then(
      (x) => {
        if (!live) return;
        setPin(x);
        setPinMsg(null);
        setF((old) => (old ? { ...old, district: old.district || x.district, state: old.state || x.state, city: old.city || x.place } : old));
      },
      (e) => live && (setPin(null), setPinMsg(e instanceof ApiError && e.status === 404 ? 'We don’t know this PIN code. Check it; you can still save it.' : null)),
    );
    return () => {
      live = false;
    };
  }, [digits]);

  if (loadErr) return <Banner kind="error">{loadErr}</Banner>;
  if (!f) return <p className="muted">Loading…</p>;
  const set = (k: keyof Form, v: string | boolean | string[]) => setF({ ...f, [k]: v });
  const text = (k: 'addressLine1' | 'addressLine2' | 'city' | 'district' | 'state', label: string, max: number, extra: React.InputHTMLAttributes<HTMLInputElement> = {}) => (
    <label className="field">
      {label}
      <input value={f[k]} maxLength={max} onChange={(e) => set(k, e.target.value)} {...extra} />
    </label>
  );
  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setMsg(null);
    try {
      const num = (v: string) => (v.trim() ? Number(v) : null);
      const p = await api<PatientProfile>('PUT', '/v1/me/patient-profile', {
        ...f,
        heightCm: num(f.heightCm),
        weightKg: num(f.weightKg),
        pinCode: digits || null,
      });
      setF(toForm(p));
      setMsg({ kind: 'ok', text: 'Saved.' });
      window.dispatchEvent(new Event('cs:profile-saved'));
      onDone?.(p);
    } catch (x) {
      setMsg({ kind: 'error', text: errorText(x) });
    } finally {
      setBusy(false);
    }
  };
  const bmi = bmiText(f.heightCm, f.weightKg);
  return (
    <form className="stack about-you" onSubmit={save}>
      <fieldset>
        <legend>Body</legend>
        <div className="form-row">
          <label className="field">
            Height (cm)
            <input type="number" inputMode="decimal" min={40} max={250} step="0.1" value={f.heightCm} onChange={(e) => set('heightCm', e.target.value)} />
          </label>
          <label className="field">
            Weight (kg)
            <small className="muted">{loaded?.weightAt ? `Last changed ${day(loaded.weightAt)}` : 'Update it whenever it changes'}</small>
            <input type="number" inputMode="decimal" min={2} max={350} step="0.1" value={f.weightKg} onChange={(e) => set('weightKg', e.target.value)} />
          </label>
          <label className="field">
            Sex
            <select value={f.sex} onChange={(e) => set('sex', e.target.value)}>
              <option value="">—</option>
              {Object.entries(SEX_LABEL).map(([k, v]) => (
                <option key={k} value={k}>
                  {v}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            Blood group
            <select value={f.bloodGroup} onChange={(e) => set('bloodGroup', e.target.value)}>
              <option value="">Don’t know</option>
              {BLOOD_GROUPS.map((b) => (
                <option key={b} value={b}>
                  {b}
                </option>
              ))}
            </select>
          </label>
        </div>
        {bmi && <p className="muted small" style={{ margin: 0 }}>{bmi}</p>}
      </fieldset>

      <fieldset>
        <legend>Address</legend>
        <div className="form-row">
          <label className="field">
            PIN code
            <small className="muted">Used to find doctors near you</small>
            <input inputMode="numeric" autoComplete="postal-code" maxLength={7} value={f.pinCode} placeholder="e.g. 700016" onChange={(e) => set('pinCode', e.target.value.replace(/[^\d ]/g, ''))} />
            {pin && <small style={{ color: 'var(--ok-fg)' }}>✓ {[...new Set([pin.place, pin.district, pin.state].filter(Boolean))].join(', ')}</small>}
            {pinMsg && <small style={{ color: 'var(--warn-fg)' }}>{pinMsg}</small>}
          </label>
          {text('city', 'City / town', 80, { autoComplete: 'address-level2' })}
          {text('district', 'District', 80)}
          {text('state', 'State', 80, { autoComplete: 'address-level1' })}
        </div>
        {text('addressLine1', 'House / flat, street', 160, { autoComplete: 'address-line1' })}
        {text('addressLine2', 'Area, landmark (optional)', 160, { autoComplete: 'address-line2' })}
        <label className="row" style={{ gap: 8, alignItems: 'flex-start' }}>
          <input type="checkbox" checked={f.shareAddress} onChange={(e) => set('shareAddress', e.target.checked)} style={{ marginTop: 3 }} />
          <span>
            Share my street address with my linked doctors and clinics
            <br />
            <small className="muted">They always see your city, district, state and PIN code. The street lines only if you tick this (e.g. for home visits).</small>
          </span>
        </label>
      </fieldset>

      <fieldset>
        <legend>Health background</legend>
        <div className="field">
          Conditions you have been told you have
          <div className="check-grid">
            {Object.entries(CONDITION_LABEL).map(([k, v]) => (
              <label key={k} className="row" style={{ gap: 8 }}>
                <input type="checkbox" checked={f.conditions.includes(k)} onChange={(e) => set('conditions', e.target.checked ? [...f.conditions, k] : f.conditions.filter((c) => c !== k))} />
                {v}
              </label>
            ))}
          </div>
        </div>
        <label className="field">
          Other conditions (optional)
          <input value={f.conditionsOther} maxLength={300} onChange={(e) => set('conditionsOther', e.target.value)} />
        </label>
        <label className="field">
          Allergies
          <small className="muted">Medicines, foods or anything else. Leave empty if none.</small>
          <input value={f.allergies} maxLength={300} onChange={(e) => set('allergies', e.target.value)} />
        </label>
        <label className="field">
          Medicines you take
          <small className="muted">Name and dose, e.g. “Amlodipine 5 mg once a day”</small>
          <textarea rows={3} value={f.medicines} maxLength={500} onChange={(e) => set('medicines', e.target.value)} />
        </label>
        <div className="seg" role="group" aria-label="Smoking">
          {Object.entries(SMOKING_LABEL).map(([k, v]) => (
            <button key={k} type="button" aria-pressed={f.smoking === k} onClick={() => set('smoking', f.smoking === k ? '' : k)}>
              {v}
            </button>
          ))}
        </div>
      </fieldset>
      <p className="muted small" style={{ margin: 0 }}>
        Everything here is optional. Your linked doctors and clinics can see these details; nobody else can.
      </p>
      {msg && <Banner kind={msg.kind}>{msg.text}</Banner>}
      <div>
        <button type="submit" className="btn primary" disabled={busy}>
          {busy ? 'Saving…' : submitLabel}
        </button>
      </div>
    </form>
  );
}

/** Right after a patient signs up: the same form, skippable. */
export function AboutYouPage() {
  const nav = useNavigate();
  return (
    <div className="stack" style={{ gap: 16, maxWidth: 820, margin: '0 auto' }}>
      <section className="card stack">
        <div className="row spread">
          <div>
            <h1 style={{ margin: 0 }}>About you</h1>
            <p className="muted" style={{ margin: '4px 0 0' }}>
              Helps your doctors and lets us show doctors near you. Takes about a minute; you can change it any time under Account → About you.
            </p>
          </div>
          <button type="button" className="btn ghost" onClick={() => nav('/', { replace: true })}>
            Skip for now
          </button>
        </div>
        <AboutYouForm submitLabel="Save and continue" onDone={() => nav('/', { replace: true })} />
      </section>
    </div>
  );
}

/** Patient Home (a span-12 grid item): until a PIN code is saved (hidden for a day with "Later"). */
export function AddressReminder() {
  const [p, , reload] = useLoad(() => api<PatientProfile>('GET', '/v1/me/patient-profile'), []);
  const [hidden, setHidden] = useState(false);
  useEffect(() => {
    window.addEventListener('cs:profile-saved', reload);
    return () => window.removeEventListener('cs:profile-saved', reload);
  }, [reload]);
  if (!p?.showReminder || hidden) return null;
  const later = async () => {
    setHidden(true);
    await api('POST', '/v1/me/patient-profile/remind-later').catch(() => undefined);
  };
  return (
    <div className="span-12 banner info address-reminder" role="status">
      <div>
        <strong>Add your address and PIN code</strong>
        <div className="small">So we can show doctors near you, and your doctors know where you are. Height, weight and health background help them too.</div>
      </div>
      <div className="row" style={{ gap: 8 }}>
        <Link className="btn primary small" to="/settings#about">
          Add now
        </Link>
        <button type="button" className="btn ghost small" onClick={() => void later()}>
          Later
        </button>
      </div>
    </div>
  );
}

/** Doctor / clinic: a linked patient's details, read-only. */
export function PatientDetailsCard({ patientId }: { patientId: string }) {
  const [p, err] = useLoad(() => api<PatientProfile>('GET', `/v1/patients/${patientId}/profile`), [patientId]);
  if (err) return <Banner kind="error">{err}</Banner>;
  if (!p) return <p className="muted">Loading…</p>;
  const rows: [string, string | null][] = [
    ['Height', p.heightCm ? `${p.heightCm} cm` : null],
    ['Weight', p.weightKg ? `${p.weightKg} kg${p.weightAt ? ` (${day(p.weightAt)})` : ''}` : null],
    ['BMI', p.bmi ? String(p.bmi) : null],
    ['Sex', p.sex ? SEX_LABEL[p.sex] : null],
    ['Blood group', p.bloodGroup],
    ['Smoking', p.smoking ? SMOKING_LABEL[p.smoking] : null],
    ['Conditions', [...p.conditions.map((c) => CONDITION_LABEL[c] ?? c), p.conditionsOther].filter(Boolean).join(', ') || null],
    ['Allergies', p.allergies],
    ['Medicines', p.medicines],
    ['Address', [...new Set([p.addressLine1, p.addressLine2, p.city, p.district, p.state, p.pinCode].filter(Boolean))].join(', ') || null],
  ];
  const filled = rows.filter(([, v]) => v);
  return (
    <div className="stack">
      <h2 style={{ margin: 0 }}>Patient details</h2>
      {filled.length === 0 ? (
        <p className="muted" style={{ margin: 0 }}>
          The patient hasn’t added height, weight, address or health background yet.
        </p>
      ) : (
        <dl className="kv">
          {filled.map(([k, v]) => (
            <div key={k}>
              <dt>{k}</dt>
              <dd>{v}</dd>
            </div>
          ))}
        </dl>
      )}
      {p.pinCode && !p.addressLine1 && !p.shareAddress && <small className="muted">Street address not shared.</small>}
    </div>
  );
}
