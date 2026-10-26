import type { Vital } from './api';

export function formatId(uid: string | null | undefined): string {
  if (!uid) return '—';
  return uid.replace(/\D/g, '').replace(/(\d{4})(?=\d)/g, '$1 ');
}

/** Same rule as app/core/ids.py, so typos are caught before the request. */
export function isValidId(raw: string): boolean {
  const uid = raw.replace(/[\s-]/g, '');
  if (!/^\d{16}$/.test(uid) || !'1239'.includes(uid[0])) return false;
  let total = 0;
  for (let i = 0; i < 16; i++) {
    let d = Number(uid[15 - i]);
    if (i % 2 === 1) {
      d *= 2;
      if (d > 9) d -= 9;
    }
    total += d;
  }
  return total % 10 === 0;
}

export const ROLE_OF_PREFIX: Record<string, string> = { '1': 'patient', '2': 'doctor', '3': 'clinic', '9': 'staff' };

export const VITALS: { key: Vital; name: string; unit: string; digits: number }[] = [
  { key: 'hr', name: 'Heart rate', unit: 'bpm', digits: 0 },
  { key: 'spo2', name: 'SpO₂', unit: '%', digits: 0 },
  { key: 'bp', name: 'Blood pressure', unit: 'mmHg', digits: 0 },
  { key: 'resp', name: 'Respiration', unit: 'breaths/min', digits: 0 },
  { key: 'temp', name: 'Temperature', unit: '°C', digits: 1 },
  { key: 'glucose', name: 'Glucose', unit: 'mg/dL', digits: 0 },
];

export function vitalValue(m: { vital: Vital; value: number | null; systolic: number | null; diastolic: number | null }): string {
  if (m.vital === 'bp') return m.systolic != null && m.diastolic != null ? `${m.systolic}/${m.diastolic}` : '—';
  const meta = VITALS.find((v) => v.key === m.vital);
  return m.value == null ? '—' : m.value.toFixed(meta?.digits ?? 0);
}

const dtf = new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' });
const df = new Intl.DateTimeFormat(undefined, { dateStyle: 'medium' });

export function when(iso: string | null | undefined): string {
  return iso ? dtf.format(new Date(iso)) : '—';
}
export function day(iso: string | null | undefined): string {
  return iso ? df.format(new Date(iso)) : '—';
}
export function ago(iso: string | null | undefined): string {
  if (!iso) return 'never';
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 90) return 'just now';
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} d ago`;
}

export const ROLE_LABEL: Record<string, string> = { patient: 'Patient', doctor: 'Doctor', clinic: 'Clinic', admin: 'CardiosensAI staff' };
