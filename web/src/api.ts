/** Same-origin API client. The access token lives only in memory; the refresh token is an
 *  httpOnly cookie scoped to /v1/auth, sent with the X-CS-Web header (CSRF guard). */

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
  ) {
    super(message);
  }
}

let accessToken: string | null = null;
let onSessionLost: (() => void) | null = null;
let refreshing: Promise<TokenOut | null> | null = null;

export function setAccessToken(token: string | null) {
  accessToken = token;
}
export function setSessionLostHandler(fn: () => void) {
  onSessionLost = fn;
}

async function parse(res: Response): Promise<unknown> {
  if (res.status === 204) return null;
  const ct = res.headers.get('content-type') || '';
  if (ct.includes('application/json')) return res.json();
  return res.arrayBuffer();
}

async function raw(method: string, path: string, body?: unknown, auth = true): Promise<Response> {
  const headers: Record<string, string> = { 'X-CS-Web': '1' };
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  if (auth && accessToken) headers.Authorization = `Bearer ${accessToken}`;
  return fetch(path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body), credentials: 'same-origin' });
}

export async function refreshSession(): Promise<TokenOut | null> {
  if (!refreshing) {
    refreshing = (async () => {
      const res = await raw('POST', '/v1/auth/refresh', undefined, false);
      if (!res.ok) {
        accessToken = null;
        return null;
      }
      const out = (await res.json()) as TokenOut;
      accessToken = out.accessToken;
      return out;
    })().finally(() => {
      refreshing = null;
    });
  }
  return refreshing;
}

export async function api<T>(method: string, path: string, body?: unknown): Promise<T> {
  let res = await raw(method, path, body);
  if (res.status === 401 && accessToken && !path.startsWith('/v1/auth/')) {
    const again = await refreshSession();
    if (again) res = await raw(method, path, body);
    else onSessionLost?.();
  }
  const data = await parse(res);
  if (!res.ok) {
    const err = (data as { error?: { code?: string; message?: string } } | null)?.error;
    throw new ApiError(res.status, err?.code ?? 'error', err?.message ?? `Request failed (${res.status})`);
  }
  return data as T;
}

export async function apiBinary(path: string): Promise<{ data: ArrayBuffer; headers: Headers }> {
  let res = await raw('GET', path);
  if (res.status === 401 && (await refreshSession())) res = await raw('GET', path);
  if (!res.ok) throw new ApiError(res.status, res.status === 410 ? 'session_ended' : 'error', 'Could not load the waveform');
  return { data: await res.arrayBuffer(), headers: res.headers };
}

// ---------------------------------------------------------------- types

export type Role = 'patient' | 'doctor' | 'clinic' | 'admin';

export interface User {
  id: string;
  uniqueId: string | null;
  role: Role;
  phone: string | null;
  email: string | null;
  emailVerified: boolean;
  phoneVerified: boolean;
  googleLinked: boolean;
  hasPassword: boolean;
  organizationId: string | null;
  fullName: string | null;
  dateOfBirth: string | null;
  emergencyContact: string | null;
  patientCode: string | null;
  mfaEnabled: boolean;
  createdAt: string;
}

export interface TokenOut {
  accessToken: string;
  refreshToken: string | null;
  expiresIn: number;
  user: User;
}

export interface MfaRequired {
  mfaRequired: true;
  mfaToken: string;
}

export interface AccountStatus {
  role: Role;
  uniqueId: string | null;
  emailVerified: boolean;
  mfaEnabled: boolean;
  mfaRequired: boolean;
  verificationStatus: VerificationStatus | 'missing' | null;
  verificationNote: string | null;
  canLink: boolean;
  canViewPatients: boolean;
  missing: string[];
}

export interface Party {
  uniqueId: string;
  role: 'patient' | 'doctor' | 'clinic';
  name: string | null;
  verified: boolean | null;
  detail: string | null;
}

export interface Link {
  id: string;
  kind: 'patient_doctor' | 'patient_clinic' | 'doctor_clinic';
  status: 'pending' | 'active' | 'declined' | 'ended';
  other: Party;
  viaClinic: Party | null;
  direction: 'incoming' | 'outgoing';
  awaitingMe: boolean;
  createdAt: string;
  decidedAt: string | null;
  endedAt: string | null;
}

export interface LatestValue {
  value: number | null;
  systolic: number | null;
  diastolic: number | null;
  unit: string;
  measuredAt: string;
}

export interface EcgBrief {
  id: string;
  startedAt: string;
  lead: string;
  leadCount: number;
  durationS: number;
  averageHr: number | null;
}

export interface LinkedPatient {
  patientId: string;
  latest: Partial<Record<'hr' | 'spo2' | 'bp', LatestValue>>;
  latestEcg: EcgBrief | null;
  ecgLive: 'live' | 'stalled' | null;
  uniqueId: string;
  fullName: string | null;
  dateOfBirth: string | null;
  patientCode: string | null;
  linkedAt: string | null;
  lastMeasurementAt: string | null;
  doctors: Party[];
}

export interface ClinicDoctor {
  linkId: string;
  doctor: Party;
  linkedAt: string | null;
  patientCount: number;
}

export type Vital = 'hr' | 'spo2' | 'bp' | 'resp' | 'temp' | 'glucose';

export interface Measurement {
  id: string;
  vital: Vital;
  unit: string;
  value: number | null;
  systolic: number | null;
  diastolic: number | null;
  measuredAt: string;
  provenance: { acquisition: string; quality: string };
}

export interface EcgRecording {
  id: string;
  startedAt: string;
  durationS: number;
  lead: string;
  leads: string[];
  sampleCount: number;
  acquisition: string;
  sampleRateHz: number;
  averageHr: number | null;
  quality: string;
  calibration: { mmPerMv: number; mmPerSec: number };
}

export interface Report {
  id: string;
  patientId: string;
  status: 'queued' | 'running' | 'ready' | 'failed' | 'deleted';
  params: { fromDate: string; toDate: string; vitals: string[] };
  pages: number | null;
  sizeBytes: number | null;
  createdAt: string;
}

export interface Organization {
  id: string;
  uniqueId: string;
  name: string;
  address: string | null;
  city: string | null;
  registrationNumber: string | null;
  phone: string | null;
  verificationStatus: VerificationStatus;
  reviewNote: string | null;
}

export interface ProfessionalProfile {
  registrationNumber: string;
  council: string;
  specialty: string | null;
  verificationStatus: VerificationStatus;
  reviewNote: string | null;
}

export interface EcgLive {
  id: string;
  patientId: string;
  state: 'live' | 'stalled' | 'stopped';
  leads: string[];
  sampleRateHz: number;
  calibration: { mmPerMv: number; mmPerSec: number };
  startedAt: string;
  lastChunkAt: string | null;
  frames: number;
  durationS: number;
  averageHr: number | null;
  recordingId: string | null;
}

export interface EcgLiveStatus {
  live: EcgLive | null;
  lastRecording: EcgRecording | null;
}

export interface DocumentFile {
  id: string;
  filename: string;
  contentType: 'application/pdf' | 'image/jpeg';
  sizeBytes: number;
}

export interface DocComment {
  id: string;
  authorName: string | null;
  authorRole: string | null;
  body: string;
  createdAt: string;
  mine: boolean;
}

export interface MedDocument {
  id: string;
  patientId: string;
  title: string;
  category: string;
  documentDate: string | null;
  note: string | null;
  uploaderName: string | null;
  uploaderRole: string;
  createdAt: string;
  files: DocumentFile[];
  comments: DocComment[];
  canDelete: boolean;
  canComment: boolean;
}

/** multipart/form-data upload (the browser sets the boundary). */
export async function apiUpload<T>(path: string, form: FormData): Promise<T> {
  const send = () => {
    const headers: Record<string, string> = { 'X-CS-Web': '1' };
    if (accessToken) headers.Authorization = `Bearer ${accessToken}`;
    return fetch(path, { method: 'POST', headers, body: form, credentials: 'same-origin' });
  };
  let res = await send();
  if (res.status === 401 && (await refreshSession())) res = await send();
  const data = await parse(res);
  if (!res.ok) {
    const err = (data as { error?: { code?: string; message?: string } } | null)?.error;
    throw new ApiError(res.status, err?.code ?? 'error', err?.message ?? `Upload failed (${res.status})`);
  }
  return data as T;
}

// ---------------------------------------------------------------- verification and admin portal

export type VerificationStatus = 'pending' | 'verified' | 'rejected' | 'revoked';

export interface LicenceFile {
  id: string;
  filename: string;
  contentType: string;
  sizeBytes: number;
  uploadedAt: string;
}

export interface MyVerification {
  status: VerificationStatus | 'missing';
  reviewNote: string | null;
  files: LicenceFile[];
  maxFiles: number;
  canResubmit: boolean;
}

export interface AdminUser {
  id: string;
  uniqueId: string | null;
  role: Role;
  status: 'active' | 'suspended' | 'deleting' | 'deleted';
  phoneMasked: string | null;
  emailMasked: string | null;
  emailVerified: boolean;
  hasPassword: boolean;
  mfaEnabled: boolean;
  fullName: string | null;
  organizationName: string | null;
  createdAt: string;
  lastActiveAt: string | null;
  eraseAfter: string | null;
  deviceCount: number;
  verificationStatus: VerificationStatus | 'missing' | null;
}

export interface AdminUserPage {
  items: AdminUser[];
  total: number;
  offset: number;
  limit: number;
}

export interface DoctorReview {
  userId: string;
  uniqueId: string | null;
  fullName: string | null;
  emailMasked: string | null;
  accountStatus: string;
  registrationNumber: string;
  council: string;
  specialty: string | null;
  verificationStatus: VerificationStatus;
  reviewNote: string | null;
  submittedAt: string;
  decidedAt: string | null;
  files: LicenceFile[];
}

export interface ClinicReview {
  id: string;
  uniqueId: string;
  name: string;
  address: string | null;
  city: string | null;
  registrationNumber: string | null;
  phoneMasked: string | null;
  ownerUserId: string | null;
  ownerName: string | null;
  ownerEmailMasked: string | null;
  accountStatus: string | null;
  verificationStatus: VerificationStatus;
  reviewNote: string | null;
  createdAt: string;
  decidedAt: string | null;
  files: LicenceFile[];
}

type ByRole = Record<'patient' | 'doctor' | 'clinic' | 'admin', number>;
interface Volume {
  total: number;
  period: number;
}

export interface PlatformStats {
  generatedAt: string;
  days: number;
  users: { total: number; byRole: Record<string, Record<string, number>>; emailVerified: ByRole; mfaEnabled: ByRole };
  verification: { doctors: Record<string, number>; clinics: Record<string, number> };
  growth: { bucket: 'day' | 'week'; points: { start: string; patient: number; doctor: number; clinic: number }[]; newInPeriod: Record<string, number> };
  activity: {
    activeDay: ByRole;
    activeWeek: ByRole;
    activeMonth: ByRole;
    activeInPeriod: ByRole;
    patientsWithReadings: number;
    patientsWithEcg: number;
    patientsWithDevices: number;
  };
  volume: Record<'measurements' | 'ecgRecordings' | 'liveEcgSessions' | 'documents' | 'reports' | 'devices' | 'careLinks' | 'familyShares', Volume>;
}

/** Download an authenticated file (CSV exports) and save it under its server-given name. */
export async function apiDownload(path: string, fallbackName: string): Promise<void> {
  const send = () => raw('GET', path);
  let res = await send();
  if (res.status === 401 && (await refreshSession())) res = await send();
  if (!res.ok) {
    const data = (await parse(res)) as { error?: { code?: string; message?: string } } | null;
    throw new ApiError(res.status, data?.error?.code ?? 'error', data?.error?.message ?? `Download failed (${res.status})`);
  }
  const name = /filename="([^"]+)"/.exec(res.headers.get('content-disposition') ?? '')?.[1] ?? fallbackName;
  const url = URL.createObjectURL(await res.blob());
  const a = document.createElement('a');
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

/** Open a short-lived signed link in a new tab. The tab opens first so the browser doesn't block it as a pop-up. */
export async function openSignedLink(getLink: () => Promise<{ url: string }>): Promise<void> {
  const w = window.open('', '_blank');
  try {
    const { url } = await getLink();
    if (w) {
      w.opener = null;
      w.location.href = url;
    }
  } catch (e) {
    w?.close();
    throw e;
  }
}

// ---------------------------------------------------------------- sign-up and sign-in with codes

export interface AuthOptions {
  googleClientId: string | null;
  smsAvailable: boolean;
}

export interface SignupStarted {
  signupId: string;
  phoneMasked: string;
  emailMasked: string | null;
  expiresInS: number;
  devPhoneCode?: string | null;
  devEmailCode?: string | null;
}

export interface LoginStarted {
  challengeId: string;
  channel: 'sms' | 'email';
  destinationMasked: string;
  expiresInS: number;
  devCode?: string | null;
}

let optionsPromise: Promise<AuthOptions> | null = null;
export function authOptions(): Promise<AuthOptions> {
  optionsPromise ??= api<AuthOptions>('GET', '/v1/auth/options').catch(() => {
    optionsPromise = null;
    return { googleClientId: null, smsAvailable: true };
  });
  return optionsPromise;
}

/** What someone typed in "Email or mobile number": an email, or an E.164 number (10 digits = India). */
export function parseIdentifier(raw: string): { kind: 'email' | 'phone'; value: string } | { error: string } {
  const v = raw.trim();
  if (v.includes('@')) return /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(v) ? { kind: 'email', value: v.toLowerCase() } : { error: 'That email address doesn’t look right.' };
  let d = v.replace(/[\s\-().]/g, '');
  if (/^0\d{10}$/.test(d)) d = d.slice(1);
  if (/^\d{10}$/.test(d)) d = `+91${d}`;
  else if (/^00[1-9]\d{7,14}$/.test(d)) d = `+${d.slice(2)}`;
  return /^\+[1-9]\d{7,14}$/.test(d) ? { kind: 'phone', value: d } : { error: 'Enter your email address, or your mobile number with the country code (e.g. +91 98765 43210).' };
}
