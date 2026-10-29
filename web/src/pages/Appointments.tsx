import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  api,
  apiUpload,
  type Appointment,
  type AppointmentStats,
  type ApptMode,
  type CalendarSettings,
  type DoctorCalendar,
  type OfflineStatus,
  type Person,
  type RegisteredDevice,
  type WeeklyHours,
} from '../api';
import { useAuth } from '../auth';
import {
  DAY_FULL,
  dayLabel,
  localInputIn,
  localInputToIso,
  MODE_LABEL,
  STATUS_LABEL,
  STATUS_TONE,
  timeLabel,
  today,
  whenLabel,
  zoned,
  zoneName,
} from '../calendar';
import { CalendarBoard, ModeIcon } from '../components/DoctorCalendar';
import { CATEGORIES } from '../components/Documents';
import { ListingNotice } from '../components/ListingNotice';
import { PhoneReminder, SetupChecklist } from '../components/Setup';
import { Banner, errorText, useLoad } from '../components/ui';
import { formatId } from '../format';

/*
 * Appointments (services/appointments.py).
 * Doctor: Home = offline mode + week calendar + requests waiting + visits to mark; settings (weekly hours, slot length,
 * video link); Statistics tab. Patient: Appointments page (upcoming, past, book with a linked or listed doctor).
 * Calendar times are shown in the doctor's time zone.
 */

function StatusTag({ s }: { s: string }) {
  return <span className={`pill ${STATUS_TONE[s] ?? 'none'}`}>{STATUS_LABEL[s] ?? s}</span>;
}

function ModeTag({ m }: { m: ApptMode }) {
  return (
    <span className={`pill ${m === 'online' ? 'info' : 'none'}`}>
      <ModeIcon mode={m} /> {MODE_LABEL[m]}
    </span>
  );
}

function useNarrow(px = 760): boolean {
  const q = `(max-width: ${px}px)`;
  const [narrow, setNarrow] = useState(() => typeof window !== 'undefined' && window.matchMedia(q).matches);
  useEffect(() => {
    const m = window.matchMedia(q);
    const on = () => setNarrow(m.matches);
    m.addEventListener('change', on);
    return () => m.removeEventListener('change', on);
  }, [q]);
  return narrow;
}

async function decide(id: string, action: string, extra: { note?: string; meetingLink?: string } = {}): Promise<Appointment> {
  return api<Appointment>('POST', `/v1/appointments/${id}/decision`, { action, ...extra });
}

// ---------------------------------------------------------------- doctor: Home

/** Doctor home: availability (offline now / scheduled), the calendar, requests to answer, visits to mark, settings. */
export function DoctorHome() {
  const [version, setVersion] = useState(0);
  const bump = () => setVersion((v) => v + 1);
  const [settings, sErr, reloadSettings] = useLoad(() => api<CalendarSettings>('GET', '/v1/me/calendar'), [version]);
  const [selected, setSelected] = useState<Appointment | null>(null);
  const [prefill, setPrefill] = useState<string | null>(null);
  useEffect(() => {
    const t = setInterval(bump, 60000); // new requests
    return () => clearInterval(t);
  }, []);
  const tz = settings?.timezone ?? 'Asia/Kolkata';
  const narrow = useNarrow();
  return (
    <div className="grid">
      <div className="span-12 stack">
        <PhoneReminder />
        <SetupChecklist />
        <ListingNotice />
        {sErr && <Banner kind="error">{sErr}</Banner>}
      </div>
      {settings && (
        <>
          <AvailabilityCard settings={settings} onChange={bump} />
          <section className="card span-8">
            <CalendarBoard
              tz={tz}
              hours={settings.weeklyHours}
              slotMin={settings.slotMin}
              version={version}
              narrow={narrow}
              selectedId={selected?.id ?? null}
              onPick={(a) => setSelected(a)}
              onEmpty={(iso) => {
                setSelected(null);
                setPrefill(iso);
              }}
            />
          </section>
          <div className="span-4 stack" style={{ gap: 16 }}>
            {selected && (
              <section className="card">
                <AppointmentDetail
                  a={selected}
                  tz={tz}
                  onChanged={(a) => {
                    setSelected(a);
                    bump();
                  }}
                  onClose={() => setSelected(null)}
                />
              </section>
            )}
            <RequestsCard tz={tz} version={version} onChanged={bump} onPick={setSelected} />
            <ToMarkCard tz={tz} version={version} onChanged={bump} onPick={setSelected} />
            <section className="card">
              <AddVisit tz={tz} prefill={prefill} onAdded={bump} />
            </section>
          </div>
          <section className="card span-12">
            <CalendarSettingsForm
              settings={settings}
              onSaved={() => {
                reloadSettings();
                bump();
              }}
            />
          </section>
        </>
      )}
    </div>
  );
}

const OFFLINE_CHOICES: [string, number][] = [
  ['1 hour', 60],
  ['2 hours', 120],
  ['4 hours', 240],
  ['8 hours', 480],
];

/** One card: online / offline now (Go offline for 2 h by default, Go online) and the scheduled offline periods. */
function AvailabilityCard({ settings, onChange }: { settings: CalendarSettings; onChange: () => void }) {
  const [minutes, setMinutes] = useState(120);
  const [custom, setCustom] = useState('');
  const [adding, setAdding] = useState(false);
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const off = settings.offline;
  const tz = settings.timezone;
  const scheduled = off.periods.filter((p) => p.kind === 'scheduled' && new Date(p.endsAt).getTime() > Date.now());
  const run = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setErr(null);
    try {
      await fn();
      onChange();
      return true;
    } catch (e) {
      setErr(errorText(e));
      return false;
    } finally {
      setBusy(false);
    }
  };
  const until = () => (minutes === -1 ? localInputToIso(custom) : new Date(Date.now() + minutes * 60_000).toISOString());
  const addPeriod = async (e: React.FormEvent) => {
    e.preventDefault();
    const ok = await run(() => api('POST', '/v1/me/calendar/offline-periods', { startsAt: localInputToIso(from), endsAt: localInputToIso(to), reason: reason.trim() || null }));
    if (ok) {
      setFrom('');
      setTo('');
      setReason('');
      setAdding(false);
    }
  };
  return (
    <section className={`card span-12 offline-bar stack ${off.offline ? 'is-offline' : 'is-online'}`} aria-live="polite">
      <div className="row spread">
        <div className="row" style={{ gap: 10 }}>
          <span className={`status-dot ${off.offline ? 'off' : 'on'}`} aria-hidden="true" />
          <div>
            <strong>{off.offline ? `Offline until ${off.until ? whenLabel(off.until, tz) : '—'}` : settings.bookable ? 'Online: patients can book' : 'Bookings are off'}</strong>
            <div className="muted small">
              {off.offline
                ? 'Patients can’t book online appointments now. In-person visits stay bookable.'
                : settings.bookable
                  ? `Online and in-person slots are open for the next ${settings.windowDays} days.`
                  : settings.hint}
            </div>
          </div>
        </div>
        {off.offline ? (
          <button type="button" className="btn primary" disabled={busy} onClick={() => run(() => api<OfflineStatus>('DELETE', '/v1/me/calendar/offline'))}>
            Go online
          </button>
        ) : (
          <div className="row" style={{ gap: 8 }}>
            <label className="sr-only" htmlFor="offline-for">
              Offline for
            </label>
            <select id="offline-for" value={minutes} onChange={(e) => setMinutes(Number(e.target.value))} style={{ width: 'auto' }}>
              {OFFLINE_CHOICES.map(([l, m]) => (
                <option key={m} value={m}>
                  for {l}
                </option>
              ))}
              <option value={-1}>until…</option>
            </select>
            {minutes === -1 && (
              <input type="datetime-local" aria-label="Offline until" value={custom} min={localInputIn(10)} onChange={(e) => setCustom(e.target.value)} style={{ width: 'auto' }} />
            )}
            <button type="button" className="btn ghost" disabled={busy || (minutes === -1 && !custom)} onClick={() => run(() => api('POST', '/v1/me/calendar/offline', { until: until() }))}>
              Go offline
            </button>
          </div>
        )}
      </div>
      <div className="row" style={{ gap: 8, alignItems: 'center' }}>
        <span className="muted small" style={{ fontWeight: 600 }}>
          Scheduled offline:
        </span>
        {scheduled.length === 0 && !adding && <span className="muted small">nothing planned</span>}
        <div className="avail-periods">
          {scheduled.map((p) => (
            <span key={p.id} className="avail-chip">
              {whenLabel(p.startsAt, tz)} – {sameDay(p.startsAt, p.endsAt, tz) ? timeLabel(p.endsAt, tz) : whenLabel(p.endsAt, tz)}
              {p.reason ? ` · ${p.reason}` : ''}
              <button type="button" aria-label={`Remove offline period ${whenLabel(p.startsAt, tz)}`} title="Remove" disabled={busy} onClick={() => run(() => api('DELETE', `/v1/me/calendar/offline-periods/${p.id}`))}>
                ✕
              </button>
            </span>
          ))}
        </div>
        {!adding && (
          <button type="button" className="btn ghost small" onClick={() => setAdding(true)}>
            + Schedule offline
          </button>
        )}
      </div>
      {adding && (
        <form className="avail-form" onSubmit={addPeriod}>
          <label className="field">
            From
            <input type="datetime-local" required value={from} min={localInputIn(0)} onChange={(e) => setFrom(e.target.value)} />
          </label>
          <label className="field">
            To
            <input type="datetime-local" required value={to} min={from} onChange={(e) => setTo(e.target.value)} />
          </label>
          <label className="field" style={{ flex: 1 }}>
            Reason (optional)
            <input value={reason} onChange={(e) => setReason(e.target.value)} maxLength={120} placeholder="e.g. Conference" />
          </label>
          <button type="submit" className="btn primary" disabled={busy}>
            Add
          </button>
          <button type="button" className="btn ghost" onClick={() => setAdding(false)}>
            Cancel
          </button>
          <p className="muted small" style={{ margin: 0, flexBasis: '100%' }}>
            No online bookings during this time; in-person visits stay bookable. Appointments already accepted stay, so cancel them if needed.
          </p>
        </form>
      )}
      {err && <Banner kind="error">{err}</Banner>}
    </section>
  );
}

function sameDay(a: string, b: string, tz: string): boolean {
  return zoned(a, tz).day === zoned(b, tz).day;
}

function AppointmentDetail({ a, tz, onChanged, onClose }: { a: Appointment; tz: string; onChanged: (a: Appointment) => void; onClose?: () => void }) {
  const [note, setNote] = useState('');
  const [link, setLink] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    setNote('');
    setLink('');
    setErr(null);
  }, [a.id]);
  const started = new Date(a.startsAt).getTime() <= Date.now();
  const run = async (action: string, extra: { note?: string; meetingLink?: string } = {}) => {
    setBusy(true);
    setErr(null);
    try {
      onChanged(await decide(a.id, action, extra));
    } catch (e) {
      setErr(errorText(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="stack appt-detail">
      <div className="row spread">
        <h3 style={{ margin: 0 }}>{a.patient.fullName}</h3>
        {onClose && (
          <button type="button" className="btn ghost small" onClick={onClose}>
            Close
          </button>
        )}
      </div>
      <div className="row" style={{ gap: 6 }}>
        <StatusTag s={a.status} />
        <ModeTag m={a.mode} />
        {a.source === 'walk_in' && <span className="pill none">Added by you</span>}
      </div>
      <div>
        {whenLabel(a.startsAt, tz)}–{timeLabel(a.endsAt, tz)}
      </div>
      {a.patient.uniqueId && (
        <div className="muted small">
          ID <span className="mono">{formatId(a.patient.uniqueId)}</span>
          {a.patientRecordId && (
            <>
              {' · '}
              <Link to={`/patients/${a.patientRecordId}`}>Open record</Link>
            </>
          )}
        </div>
      )}
      {a.reason && (
        <p className="appt-reason">
          <span className="muted small">Patient’s note</span>
          <br />
          {a.reason}
        </p>
      )}
      {a.doctorNote && <p className="muted small">Your note: {a.doctorNote}</p>}
      {a.meetingLink && (
        <a href={a.meetingLink} target="_blank" rel="noreferrer noopener" className="btn ghost small">
          Open video link
        </a>
      )}
      {err && <Banner kind="error">{err}</Banner>}
      {a.status === 'requested' && (
        <>
          {a.mode === 'online' && (
            <label className="field">
              Video link (optional)
              <small className="muted">Leave empty to use your default link</small>
              <input type="url" value={link} onChange={(e) => setLink(e.target.value)} placeholder="https://meet.google.com/…" maxLength={300} />
            </label>
          )}
          <label className="field">
            Note to the patient (optional)
            <input value={note} onChange={(e) => setNote(e.target.value)} maxLength={300} />
          </label>
          <div className="row" style={{ gap: 8 }}>
            <button type="button" className="btn primary" disabled={busy} onClick={() => run('accept', { note: note || undefined, meetingLink: link || undefined })}>
              Accept
            </button>
            <button type="button" className="btn danger" disabled={busy} onClick={() => run('decline', { note: note || undefined })}>
              Decline
            </button>
          </div>
        </>
      )}
      {a.status === 'accepted' && !started && (
        <button type="button" className="btn danger small" disabled={busy} onClick={() => window.confirm('Cancel this appointment? The patient is told.') && run('cancel')}>
          Cancel appointment
        </button>
      )}
      {['accepted', 'completed', 'no_show'].includes(a.status) && started && (
        <div className="row" style={{ gap: 8 }}>
          <button type="button" className="btn primary small" disabled={busy || a.status === 'completed'} onClick={() => run('complete')}>
            Completed
          </button>
          <button type="button" className="btn ghost small" disabled={busy || a.status === 'no_show'} onClick={() => run('no_show')}>
            No-show
          </button>
        </div>
      )}
      {a.patient.uniqueId && ['requested', 'accepted', 'completed'].includes(a.status) && <Consultation key={a.id} a={a} tz={tz} />}
    </div>
  );
}

/**
 * During the visit: record with the doctor's own CardiosensAI device for this patient (the device's
 * "Records for" setting, as in Account → Devices) and add reports to the patient's documents.
 * Needs an active link: booking can carry the patient's link request, which accepting approves.
 */
function Consultation({ a, tz }: { a: Appointment; tz: string }) {
  const pid = a.patientLinked ? a.patientRecordId : null;
  if (!pid) {
    return (
      <div className="consult stack">
        <h4>Consultation</h4>
        <p className="muted small" style={{ margin: 0 }}>
          {a.linkPending
            ? a.status === 'requested'
              ? 'The patient asked to link with you when booking. Accepting the appointment approves it; then you can record with your device and add reports here.'
              : 'The patient asked to link with you. Approve it under Links to record with your device and add reports.'
            : 'To record with your device or add reports, the patient needs to link with you. Ask them to, or send a request under Links.'}
        </p>
        <Link to="/links" className="small">
          Open Links
        </Link>
      </div>
    );
  }
  if (a.status === 'requested') return null;
  return (
    <div className="consult stack">
      <h4>Consultation</h4>
      <ConsultDevice a={a} pid={pid} />
      <ConsultReport a={a} pid={pid} tz={tz} />
    </div>
  );
}

function ConsultDevice({ a, pid }: { a: Appointment; pid: string }) {
  const [version, setVersion] = useState(0);
  const [devices, err] = useLoad(() => api<RegisteredDevice[]>('GET', '/v1/devices'), [version]);
  const mine = (devices ?? []).filter((d) => d.connected && d.config?.canAssign);
  const [pick, setPick] = useState<string | null>(null);
  const d = mine.find((x) => x.id === pick) ?? mine[0];
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ kind: 'ok' | 'error'; text: string } | null>(null);
  if (err) return <Banner kind="error">{err}</Banner>;
  if (!devices) return null;
  if (!d)
    return (
      <p className="muted small" style={{ margin: 0 }}>
        No device yet. <Link to="/settings#devices">Pair your CardiosensAI device</Link> to record ECGs for patients during visits.
      </p>
    );
  const cfg = d.config!;
  const here = cfg.assignedPatientId === pid;
  const live = !!cfg.liveUntil && new Date(cfg.liveUntil).getTime() > Date.now();
  const act = async (fn: () => Promise<unknown>, ok: string) => {
    setBusy(true);
    setMsg(null);
    try {
      await fn();
      setMsg({ kind: 'ok', text: ok });
      setVersion((v) => v + 1);
    } catch (e) {
      setMsg({ kind: 'error', text: errorText(e) });
    } finally {
      setBusy(false);
    }
  };
  const assign = (id: string | null) => api('PUT', `/v1/devices/${d.id}/config`, { assignedPatientId: id });
  return (
    <div className="consult-device stack" style={{ gap: 8 }}>
      <div className="row spread">
        <strong>{d.displayName}</strong>
        <span className={`pill ${d.online ? 'ok' : 'none'}`}>{d.online ? 'Online' : 'Offline'}</span>
      </div>
      {mine.length > 1 && (
        <label className="field">
          Device
          <select value={d.id} onChange={(e) => setPick(e.target.value)}>
            {mine.map((x) => (
              <option key={x.id} value={x.id}>
                {x.displayName}
              </option>
            ))}
          </select>
        </label>
      )}
      <span className="small">
        {here
          ? `Recording for ${a.patient.fullName}: snapshots and live ECGs go to their record.`
          : cfg.assignedPatientName
            ? `Recording for ${cfg.assignedPatientName}. Switch it to ${a.patient.fullName} for this visit.`
            : 'Not recording for anyone.'}
      </span>
      <div className="row" style={{ gap: 8 }}>
        {here ? (
          <>
            <button type="button" className="btn primary small" disabled={busy || live} onClick={() => act(() => api('POST', `/v1/devices/${d.id}/live`, { on: true }), 'Recording now: the ECG appears in the patient’s record in a moment.')}>
              {live ? 'Recording…' : 'Record ECG now (20 s)'}
            </button>
            <button type="button" className="btn ghost small" disabled={busy} onClick={() => act(() => assign(null), 'The device stopped recording for this patient.')}>
              Stop recording
            </button>
          </>
        ) : (
          <button type="button" className="btn primary small" disabled={busy} onClick={() => act(() => assign(pid), `${d.displayName} now records for ${a.patient.fullName}.`)}>
            Use for {a.patient.fullName}
          </button>
        )}
      </div>
      {!d.online && here && <span className="muted small">The device is offline: it records when it reconnects.</span>}
      {msg && <Banner kind={msg.kind}>{msg.text}</Banner>}
      <Link to={`/patients/${pid}`} className="small">
        See readings in the patient’s record
      </Link>
    </div>
  );
}

function ConsultReport({ a, pid, tz }: { a: Appointment; pid: string; tz: string }) {
  const [open, setOpen] = useState(false);
  const [title, setTitle] = useState(() => `Consultation ${dayLabel(zoned(a.startsAt, tz).day)}`);
  const [category, setCategory] = useState('ecg');
  const [note, setNote] = useState('');
  const [files, setFiles] = useState<File[]>([]);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ kind: 'ok' | 'error'; text: string } | null>(null);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!files.length) {
      setMsg({ kind: 'error', text: 'Choose at least one PDF or JPEG file' });
      return;
    }
    const form = new FormData();
    files.forEach((f) => form.append('files', f, f.name));
    form.append('title', title.trim());
    form.append('category', category);
    form.append('documentDate', zoned(a.startsAt, tz).day);
    if (note.trim()) form.append('note', note.trim());
    setBusy(true);
    setMsg(null);
    try {
      await apiUpload(`/v1/patients/${pid}/documents`, form);
      setMsg({ kind: 'ok', text: `Added to ${a.patient.fullName}’s documents. They’re told in the app.` });
      setFiles([]);
      setNote('');
      setOpen(false);
    } catch (x) {
      setMsg({ kind: 'error', text: errorText(x) });
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="stack" style={{ gap: 8 }}>
      {!open && (
        <button type="button" className="btn ghost small" onClick={() => (setOpen(true), setMsg(null))}>
          + Add report or result
        </button>
      )}
      {open && (
        <form className="stack" style={{ gap: 8 }} onSubmit={submit}>
          <label className="field">
            Title
            <input required value={title} onChange={(e) => setTitle(e.target.value)} maxLength={160} />
          </label>
          <label className="field">
            Type
            <select value={category} onChange={(e) => setCategory(e.target.value)}>
              {Object.entries(CATEGORIES).map(([k, v]) => (
                <option key={k} value={k}>
                  {v}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            Files
            <small className="muted">PDF or JPEG: a printout, the device report, a prescription</small>
            <input type="file" multiple accept=".pdf,.jpg,.jpeg,application/pdf,image/jpeg" onChange={(e) => setFiles([...(e.target.files ?? [])])} />
          </label>
          <label className="field">
            Note (optional)
            <textarea rows={2} value={note} onChange={(e) => setNote(e.target.value)} maxLength={1000} />
          </label>
          <div className="row" style={{ gap: 8 }}>
            <button type="submit" className="btn primary small" disabled={busy}>
              {busy ? 'Uploading…' : 'Add to patient’s records'}
            </button>
            <button type="button" className="btn ghost small" onClick={() => setOpen(false)}>
              Cancel
            </button>
          </div>
        </form>
      )}
      {msg && <Banner kind={msg.kind}>{msg.text}</Banner>}
    </div>
  );
}

function ApptRow({ a, tz, onPick, children }: { a: Appointment; tz: string; onPick: (a: Appointment) => void; children?: React.ReactNode }) {
  return (
    <li className="appt-row">
      <button type="button" className="linklike appt-main" onClick={() => onPick(a)}>
        <strong>{a.patient.fullName}</strong>
        <span className="muted small">
          {whenLabel(a.startsAt, tz)} · {MODE_LABEL[a.mode]}
        </span>
      </button>
      {children}
    </li>
  );
}

function RequestsCard({ tz, version, onChanged, onPick }: { tz: string; version: number; onChanged: () => void; onPick: (a: Appointment) => void }) {
  const [rows, err] = useLoad(() => api<Appointment[]>('GET', '/v1/appointments?scope=upcoming&status=requested'), [version]);
  const [busy, setBusy] = useState<string | null>(null);
  const [actionErr, setActionErr] = useState<string | null>(null);
  const act = async (a: Appointment, action: string) => {
    setBusy(a.id);
    setActionErr(null);
    try {
      await decide(a.id, action);
      onChanged();
    } catch (e) {
      setActionErr(errorText(e));
    } finally {
      setBusy(null);
    }
  };
  return (
    <section className="card">
      <h2>
        Requests waiting {rows && rows.length > 0 && <span className="pill warn">{rows.length}</span>}
      </h2>
      {err && <Banner kind="error">{err}</Banner>}
      {actionErr && <Banner kind="error">{actionErr}</Banner>}
      {rows && rows.length === 0 && <p className="muted">No requests waiting.</p>}
      <ul className="appt-list">
        {(rows ?? []).map((a) => (
          <ApptRow key={a.id} a={a} tz={tz} onPick={onPick}>
            <div className="row" style={{ gap: 6 }}>
              <button type="button" className="btn primary small" disabled={busy === a.id} onClick={() => act(a, 'accept')}>
                Accept
              </button>
              <button type="button" className="btn ghost small" disabled={busy === a.id} onClick={() => act(a, 'decline')}>
                Decline
              </button>
            </div>
          </ApptRow>
        ))}
      </ul>
      {rows && rows.length > 0 && <p className="muted small">Requests you don’t answer expire when the slot starts.</p>}
    </section>
  );
}

function ToMarkCard({ tz, version, onChanged, onPick }: { tz: string; version: number; onChanged: () => void; onPick: (a: Appointment) => void }) {
  const [up] = useLoad(() => api<Appointment[]>('GET', '/v1/appointments?scope=upcoming&status=accepted'), [version]);
  const [past] = useLoad(() => api<Appointment[]>('GET', '/v1/appointments?scope=past&status=accepted'), [version]);
  const [err, setErr] = useState<string | null>(null);
  const todayStr = today(tz);
  const todays = (up ?? []).filter((a) => zoned(a.startsAt, tz).day === todayStr);
  const mark = async (a: Appointment, action: string) => {
    setErr(null);
    try {
      await decide(a.id, action);
      onChanged();
    } catch (e) {
      setErr(errorText(e));
    }
  };
  return (
    <section className="card">
      <h2>Today</h2>
      {err && <Banner kind="error">{err}</Banner>}
      {todays.length === 0 && <p className="muted">No more appointments today.</p>}
      <ul className="appt-list">
        {todays.map((a) => (
          <ApptRow key={a.id} a={a} tz={tz} onPick={onPick}>
            {a.meetingLink && (
              <a className="btn ghost small" href={a.meetingLink} target="_blank" rel="noreferrer noopener">
                Video
              </a>
            )}
          </ApptRow>
        ))}
      </ul>
      {past && past.length > 0 && (
        <>
          <h3 style={{ marginTop: 12 }}>Mark these visits</h3>
          <ul className="appt-list">
            {past.slice(0, 8).map((a) => (
              <ApptRow key={a.id} a={a} tz={tz} onPick={onPick}>
                <div className="row" style={{ gap: 6 }}>
                  <button type="button" className="btn primary small" onClick={() => mark(a, 'complete')}>
                    Completed
                  </button>
                  <button type="button" className="btn ghost small" onClick={() => mark(a, 'no_show')}>
                    No-show
                  </button>
                </div>
              </ApptRow>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}

function AddVisit({ tz, prefill, onAdded }: { tz: string; prefill: string | null; onAdded: () => void }) {
  const [when, setWhen] = useState('');
  const [who, setWho] = useState('');
  const [mode, setMode] = useState<ApptMode>('in_person');
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ kind: 'ok' | 'error'; text: string } | null>(null);
  useEffect(() => {
    if (!prefill) return;
    const d = new Date(prefill);
    setWhen(new Date(d.getTime() - d.getTimezoneOffset() * 60_000).toISOString().slice(0, 16));
  }, [prefill]);
  const digits = who.replace(/\s/g, '');
  const isId = /^\d{16}$/.test(digits);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setMsg(null);
    try {
      const a = await api<Appointment>('POST', '/v1/me/appointments', {
        startsAt: localInputToIso(when),
        mode,
        ...(isId ? { patientId: digits } : { name: who.trim() }),
      });
      setMsg({ kind: 'ok', text: `Added: ${a.patient.fullName}, ${whenLabel(a.startsAt, tz)} (${STATUS_LABEL[a.status].toLowerCase()}).` });
      setWho('');
      onAdded();
    } catch (err) {
      setMsg({ kind: 'error', text: errorText(err) });
    } finally {
      setBusy(false);
    }
  };
  return (
    <form className="stack" onSubmit={submit}>
      <h2 style={{ margin: 0 }}>Add a visit</h2>
      <p className="muted small" style={{ margin: 0 }}>
        A walk-in, or a visit arranged by phone. Past visits are saved as completed.
      </p>
      <label className="field">
        When
        <input type="datetime-local" required value={when} onChange={(e) => setWhen(e.target.value)} />
      </label>
      <label className="field">
        Patient
        <small className="muted">Linked patient’s 16-digit ID, or a name</small>
        <input required value={who} onChange={(e) => setWho(e.target.value)} maxLength={120} />
      </label>
      <div className="seg" role="group" aria-label="Type">
        {(['in_person', 'online'] as ApptMode[]).map((m) => (
          <button key={m} type="button" aria-pressed={mode === m} onClick={() => setMode(m)}>
            {MODE_LABEL[m]}
          </button>
        ))}
      </div>
      {msg && <Banner kind={msg.kind}>{msg.text}</Banner>}
      <button type="submit" className="btn primary" disabled={busy}>
        {busy ? 'Adding…' : 'Add visit'}
      </button>
    </form>
  );
}

const SLOT_MINUTES = [10, 15, 20, 30, 45, 60];
const WINDOWS = [7, 14, 30];
const TIMEZONES: string[] = (() => {
  try {
    return (Intl as unknown as { supportedValuesOf(k: string): string[] }).supportedValuesOf('timeZone');
  } catch {
    return ['Asia/Kolkata', 'UTC'];
  }
})();

function CalendarSettingsForm({ settings, onSaved }: { settings: CalendarSettings; onSaved: () => void }) {
  const [f, setF] = useState(settings);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ kind: 'ok' | 'error'; text: string } | null>(null);
  useEffect(() => setF(settings), [settings]);
  const setHours = (day: number, rows: WeeklyHours[]) => setF({ ...f, weeklyHours: [...f.weeklyHours.filter((h) => h.day !== day), ...rows].sort((a, b) => a.day - b.day || a.start.localeCompare(b.start)) });
  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setMsg(null);
    try {
      await api('PUT', '/v1/me/calendar', {
        enabled: f.enabled,
        timezone: f.timezone,
        slotMin: f.slotMin,
        windowDays: f.windowDays,
        weeklyHours: f.weeklyHours,
        online: f.online,
        inPerson: f.inPerson,
        meetingLink: f.meetingLink ?? '',
      });
      setMsg({ kind: 'ok', text: 'Saved.' });
      onSaved();
    } catch (err) {
      setMsg({ kind: 'error', text: errorText(err) });
    } finally {
      setBusy(false);
    }
  };
  return (
    <details open={!settings.weeklyHours.length}>
      <summary>
        <h2 style={{ display: 'inline' }}>Booking settings</h2> <span className="muted small">weekly hours, slot length, video link</span>
      </summary>
      <form className="stack" style={{ marginTop: 12 }} onSubmit={save}>
        <label className="row" style={{ gap: 8 }}>
          <input type="checkbox" checked={f.enabled} onChange={(e) => setF({ ...f, enabled: e.target.checked })} />
          <span>
            <strong>Patients can book appointments</strong>
            <span className="muted small"> · linked patients, and everyone who finds you in the Doctors list</span>
          </span>
        </label>
        <div className="settings-grid">
          <label className="field">
            Time zone
            <select value={f.timezone} onChange={(e) => setF({ ...f, timezone: e.target.value })}>
              {TIMEZONES.map((z) => (
                <option key={z}>{z}</option>
              ))}
            </select>
          </label>
          <label className="field">
            Slot length
            <select value={f.slotMin} onChange={(e) => setF({ ...f, slotMin: Number(e.target.value) })}>
              {SLOT_MINUTES.map((m) => (
                <option key={m} value={m}>
                  {m} minutes
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            Patients can book up to
            <select value={f.windowDays} onChange={(e) => setF({ ...f, windowDays: Number(e.target.value) })}>
              {WINDOWS.map((d) => (
                <option key={d} value={d}>
                  {d} days ahead
                </option>
              ))}
            </select>
          </label>
          <div className="field" role="group" aria-label="Appointment types">
            Appointment types
            <label className="row" style={{ gap: 6, fontWeight: 400 }}>
              <input type="checkbox" checked={f.online} onChange={(e) => setF({ ...f, online: e.target.checked })} /> Online (video)
            </label>
            <label className="row" style={{ gap: 6, fontWeight: 400 }}>
              <input type="checkbox" checked={f.inPerson} onChange={(e) => setF({ ...f, inPerson: e.target.checked })} /> In person
            </label>
          </div>
        </div>
        <label className="field">
          Default video link
          <small className="muted">Google Meet, Zoom or WhatsApp link; patients see it once you accept</small>
          <input type="url" value={f.meetingLink ?? ''} onChange={(e) => setF({ ...f, meetingLink: e.target.value })} placeholder="https://meet.google.com/…" maxLength={300} />
        </label>
        <div className="field" role="group" aria-label="Weekly hours">
          Weekly hours
          <small className="muted">Slots are made from these hours every week ({zoneName(f.timezone)}). Bookings need at least 1 hour’s notice.</small>
          <div className="hours">
            {DAY_FULL.map((name, day) => (
              <DayHours key={day} name={name} rows={f.weeklyHours.filter((h) => h.day === day)} onChange={(rows) => setHours(day, rows.map((r) => ({ ...r, day })))} />
            ))}
          </div>
        </div>
        {msg && <Banner kind={msg.kind}>{msg.text}</Banner>}
        <div>
          <button type="submit" className="btn primary" disabled={busy}>
            {busy ? 'Saving…' : 'Save settings'}
          </button>
        </div>
      </form>
    </details>
  );
}

function DayHours({ name, rows, onChange }: { name: string; rows: WeeklyHours[]; onChange: (rows: WeeklyHours[]) => void }) {
  return (
    <div className="hours-day">
      <span className="hours-name">{name}</span>
      <div className="row" style={{ gap: 8 }}>
        {rows.length === 0 && <span className="muted small">Closed</span>}
        {rows.map((r, i) => (
          <span key={i} className="hours-range">
            <input type="time" aria-label={`${name} from`} value={r.start} onChange={(e) => onChange(rows.map((x, j) => (j === i ? { ...x, start: e.target.value } : x)))} />
            –
            <input type="time" aria-label={`${name} to`} value={r.end} onChange={(e) => onChange(rows.map((x, j) => (j === i ? { ...x, end: e.target.value } : x)))} />
            <button type="button" className="btn ghost small" aria-label={`Remove ${name} ${r.start}–${r.end}`} onClick={() => onChange(rows.filter((_, j) => j !== i))}>
              ✕
            </button>
          </span>
        ))}
        <button
          type="button"
          className="btn ghost small"
          onClick={() => onChange([...rows, rows.length ? { day: 0, start: '17:00', end: '20:00' } : { day: 0, start: '10:00', end: '13:00' }])}
        >
          + Add hours
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- doctor: Statistics

const RANGES = [
  ['today', 'Today'],
  ['7d', '7 days'],
  ['30d', '30 days'],
  ['all', 'All time'],
] as const;

export function DoctorStats() {
  const [s, err] = useLoad(() => api<AppointmentStats>('GET', '/v1/me/appointment-stats'), []);
  const [settings] = useLoad(() => api<CalendarSettings>('GET', '/v1/me/calendar'), []);
  const [range, setRange] = useState<(typeof RANGES)[number][0]>('30d');
  const tz = settings?.timezone ?? 'Asia/Kolkata';
  const r = s?.ranges[range];
  const total = r ? r.servedOnline + r.servedInPerson : 0;
  return (
    <div className="grid">
      {err && (
        <div className="span-12">
          <Banner kind="error">{err}</Banner>
        </div>
      )}
      <section className="card span-12">
        <h2>Coming up</h2>
        <div className="vitals">
          <Tile label="Requests waiting" value={s?.pending} to="/" tone={s?.pending ? 'warn' : undefined} />
          <Tile label="Upcoming appointments" value={s?.upcoming} sub={s ? `${s.upcomingOnline} online · ${s.upcomingInPerson} in person` : undefined} />
          <Tile label="Still today" value={s?.upcomingToday} />
          <Tile label="Next appointment" text={s ? (s.nextAt ? whenLabel(s.nextAt, tz) : 'None booked') : undefined} />
        </div>
      </section>
      <section className="card span-12">
        <div className="row spread">
          <h2 style={{ margin: 0 }}>Patients served</h2>
          <div className="seg" role="group" aria-label="Period">
            {RANGES.map(([k, l]) => (
              <button key={k} type="button" aria-pressed={range === k} onClick={() => setRange(k)}>
                {l}
              </button>
            ))}
          </div>
        </div>
        {r && (
          <>
            <div className="vitals" style={{ marginTop: 12 }}>
              <Tile label="Visits completed" value={r.served} />
              <Tile label="Online" value={r.servedOnline} />
              <Tile label="In person" value={r.servedInPerson} />
              <Tile label="Different patients" value={r.patients} />
            </div>
            {total > 0 && (
              <div className="split-bar" role="img" aria-label={`${r.servedOnline} online, ${r.servedInPerson} in person`}>
                <span className="on" style={{ flex: r.servedOnline }} />
                <span className="ip" style={{ flex: r.servedInPerson }} />
              </div>
            )}
            <div className="vitals" style={{ marginTop: 12 }}>
              <Tile label="No-shows" value={r.noShow} small />
              <Tile label="Cancelled" value={r.cancelled} small />
              <Tile label="Declined" value={r.declined} small />
              <Tile label="Expired requests" value={r.expired} small />
              <Tile label="Not marked yet" value={r.toMark} small to={r.toMark ? '/' : undefined} tone={r.toMark ? 'warn' : undefined} />
            </div>
            <p className="muted small">A visit counts as served when you mark it Completed. Times in {zoneName(tz)}.</p>
          </>
        )}
      </section>
    </div>
  );
}

function Tile({ label, value, text, sub, to, tone, small }: { label: string; value?: number; text?: string; sub?: string; to?: string; tone?: 'warn'; small?: boolean }) {
  const body = (
    <>
      <div className="muted small">{label}</div>
      <div className="v" style={small ? { fontSize: '1.25rem' } : text ? { fontSize: '1.1rem' } : undefined}>
        {value ?? text ?? '…'}
      </div>
      {sub && <div className="muted small">{sub}</div>}
    </>
  );
  return to ? (
    <Link to={to} className={`vital tile-link ${tone ?? ''}`}>
      {body}
    </Link>
  ) : (
    <div className={`vital ${tone ?? ''}`}>{body}</div>
  );
}

// ---------------------------------------------------------------- patients

/** Patient: upcoming and past appointments, and booking with linked doctors (others via the Doctors list). */
/** Patients: the Appointments tile beside the Doctors list: upcoming (and past) appointments, and booking with a linked doctor. */
export function AppointmentsTile() {
  const [version, setVersion] = useState(0);
  const [up, upErr] = useLoad(() => api<Appointment[]>('GET', '/v1/appointments?scope=upcoming'), [version]);
  const [past] = useLoad(() => api<Appointment[]>('GET', '/v1/appointments?scope=past'), [version]);
  const [doctors] = useLoad(() => api<Person[]>('GET', '/v1/appointments/doctors'), []);
  const [booking, setBooking] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const dialog = useRef<HTMLDialogElement>(null);
  const bump = () => setVersion((v) => v + 1);
  const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
  useEffect(() => {
    if (booking) dialog.current?.showModal();
  }, [booking]);
  // open ones on top; cancelled / declined / expired upcoming ones go with the past, newest first
  const live = (a: Appointment) => a.status === 'requested' || a.status === 'accepted';
  const open = (up ?? []).filter(live);
  const done = [...(up ?? []).filter((a) => !live(a)).reverse(), ...(past ?? [])];
  return (
    <section className="card appt-tile" id="appointments" aria-label="My appointments">
      <h2>My appointments</h2>
      {notice && <Banner kind="ok">{notice}</Banner>}
      {upErr && <Banner kind="error">{upErr}</Banner>}
      {up && open.length === 0 && <p className="muted small">No upcoming appointments.</p>}
      <ul className="appt-list">
        {open.map((a) => (
          <PatientApptRow key={a.id} a={a} onChanged={bump} />
        ))}
      </ul>
      {done.length > 0 && (
        <details>
          <summary className="small">Past and cancelled ({done.length})</summary>
          <ul className="appt-list">
            {done.slice(0, 50).map((a) => (
              <PatientApptRow key={a.id} a={a} onChanged={bump} />
            ))}
          </ul>
        </details>
      )}
      <h3>Book</h3>
      {doctors && doctors.length > 0 ? (
        <ul className="appt-list">
          {doctors.map((d) => (
            <li key={d.uniqueId} className="appt-row">
              <div className="appt-main">
                <strong>{d.fullName}</strong>
                <span className="muted small">Linked doctor</span>
              </div>
              <button type="button" className="btn primary small" onClick={() => (setNotice(null), setBooking(d.uniqueId))}>
                Book
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <p className="muted small">None of your linked doctors takes bookings in CardiosensAI yet.</p>
      )}
      <p className="muted small" style={{ margin: 0 }}>
        Or open a doctor’s card in the list and book from their profile.
      </p>
      {booking && (
        <dialog ref={dialog} className="ecg-dialog" onClose={() => setBooking(null)} onClick={(e) => e.target === e.currentTarget && dialog.current?.close()} aria-label="Book an appointment">
          <div className="ecg-dialog-box">
            <BookPanel
              doctorId={booking}
              onBooked={(a) => {
                dialog.current?.close();
                setNotice(`Request sent to ${a.doctor.fullName} for ${whenLabel(a.startsAt, tz)}. You’ll be told when they accept.`);
                bump();
              }}
              onClose={() => dialog.current?.close()}
            />
          </div>
        </dialog>
      )}
    </section>
  );
}

function PatientApptRow({ a, onChanged }: { a: Appointment; onChanged: () => void }) {
  const [err, setErr] = useState<string | null>(null);
  const browserTz = Intl.DateTimeFormat().resolvedOptions().timeZone;
  const future = new Date(a.startsAt).getTime() > Date.now();
  const cancel = async () => {
    if (!window.confirm('Cancel this appointment? The doctor is told.')) return;
    try {
      await decide(a.id, 'cancel');
      onChanged();
    } catch (e) {
      setErr(errorText(e));
    }
  };
  return (
    <li className="appt-row">
      <div className="appt-main">
        <strong>{a.doctor.fullName}</strong>
        <span className="muted small">
          {whenLabel(a.startsAt, browserTz)} · {MODE_LABEL[a.mode]}
          {a.mode === 'in_person' && a.practice ? ` · ${a.practice}` : ''}
        </span>
        {a.doctorNote && <span className="small">Doctor’s note: {a.doctorNote}</span>}
        {err && <Banner kind="error">{err}</Banner>}
      </div>
      <div className="row" style={{ gap: 6 }}>
        <StatusTag s={a.status} />
        {a.meetingLink && future && (
          <a className="btn primary small" href={a.meetingLink} target="_blank" rel="noreferrer noopener">
            Join video
          </a>
        )}
        {a.doctor.uniqueId && (a.status === 'requested' || a.status === 'accepted') && (
          <Link className="btn ghost small" to={`/links?with=${a.doctor.uniqueId}`}>
            Message
          </Link>
        )}
        {future && (a.status === 'requested' || a.status === 'accepted') && (
          <button type="button" className="btn ghost small" onClick={cancel}>
            Cancel
          </button>
        )}
      </div>
    </li>
  );
}

/** Pick a type, a day and a free slot of one doctor, add a note, and send the request. */
export function BookPanel({ doctorId, onBooked, onClose }: { doctorId: string; onBooked?: (a: Appointment) => void; onClose?: () => void }) {
  const { user } = useAuth();
  const [version, setVersion] = useState(0);
  const [cal, err] = useLoad(() => api<DoctorCalendar>('GET', `/v1/doctors/${doctorId}/calendar`), [doctorId, version]);
  const [mode, setMode] = useState<ApptMode>('online');
  const [day, setDay] = useState<string | null>(null);
  const [slot, setSlot] = useState<string | null>(null);
  const [reason, setReason] = useState('');
  const [allowLink, setAllowLink] = useState(true);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ kind: 'ok' | 'error'; text: string } | null>(null);
  const tz = Intl.DateTimeFormat().resolvedOptions().timeZone; // the patient's own clock
  useEffect(() => {
    if (cal && !cal.online && cal.inPerson) setMode('in_person');
  }, [cal]);
  const usable = (cal?.slots ?? []).filter((s) => (mode === 'online' ? s.online : s.inPerson));
  const days = [...new Set(usable.map((s) => zoned(s.startsAt, tz).day))];
  const current = day && days.includes(day) ? day : (days[0] ?? null);
  const daySlots = usable.filter((s) => zoned(s.startsAt, tz).day === current);
  if (user?.role !== 'patient') return null;
  const book = async () => {
    if (!slot) return;
    setBusy(true);
    setMsg(null);
    try {
      const askLink = allowLink && cal?.linkStatus == null;
      const a = await api<Appointment>('POST', '/v1/appointments', { doctorId, startsAt: slot, mode, reason: reason.trim() || null, allowLink: askLink });
      setMsg({
        kind: 'ok',
        text: `Request sent for ${whenLabel(a.startsAt, tz)}. You’ll be told when the doctor accepts.${askLink ? ' Accepting also links you, so the doctor can see your records and add the visit’s readings and reports.' : ''}`,
      });
      setSlot(null);
      setReason('');
      onBooked?.(a);
    } catch (e) {
      setMsg({ kind: 'error', text: errorText(e) });
      setVersion((v) => v + 1); // someone else may have taken it
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="stack book-panel">
      <div className="row spread">
        <h2 style={{ margin: 0 }}>Book with {cal?.doctor.fullName ?? '…'}</h2>
        {onClose && (
          <button type="button" className="btn ghost small" onClick={onClose}>
            Close
          </button>
        )}
      </div>
      {err && <Banner kind="error">{err}</Banner>}
      {cal && !cal.bookable && <Banner kind="info">{cal.reason}</Banner>}
      {cal?.bookable && (
        <>
          {cal.offlineUntil && mode === 'online' && <Banner kind="info">The doctor is offline until {whenLabel(cal.offlineUntil, tz)}: no online appointments before then.</Banner>}
          <div className="seg" role="group" aria-label="Appointment type">
            {cal.online && (
              <button type="button" aria-pressed={mode === 'online'} onClick={() => (setMode('online'), setSlot(null))}>
                Online (video)
              </button>
            )}
            {cal.inPerson && (
              <button type="button" aria-pressed={mode === 'in_person'} onClick={() => (setMode('in_person'), setSlot(null))}>
                In person{cal.practice ? ` · ${cal.practice}` : ''}
              </button>
            )}
          </div>
          {cal.limitReached && <Banner kind="info">You already have 2 upcoming appointments with this doctor. Cancel one to book another.</Banner>}
          {days.length === 0 ? (
            <p className="muted">No free slots in the next {cal.windowDays} days.</p>
          ) : (
            <>
              <div className="day-tabs" role="group" aria-label="Day">
                {days.map((d) => (
                  <button key={d} type="button" aria-pressed={d === current} onClick={() => (setDay(d), setSlot(null))}>
                    {dayLabel(d)}
                  </button>
                ))}
              </div>
              <div className="slot-grid" role="radiogroup" aria-label="Time">
                {daySlots.map((s) => (
                  <button key={s.startsAt} type="button" role="radio" aria-checked={slot === s.startsAt} onClick={() => setSlot(s.startsAt)}>
                    {timeLabel(s.startsAt, tz)}
                  </button>
                ))}
              </div>
              <label className="field">
                Reason for the visit (optional)
                <small className="muted">Only this doctor sees it</small>
                <textarea rows={2} value={reason} onChange={(e) => setReason(e.target.value)} maxLength={300} />
              </label>
              {cal.linkStatus == null && (
                <label className="row" style={{ gap: 8, alignItems: 'flex-start' }}>
                  <input type="checkbox" checked={allowLink} onChange={(e) => setAllowLink(e.target.checked)} style={{ marginTop: 3 }} />
                  <span>
                    Also link with {cal.doctor.fullName}
                    <br />
                    <small className="muted">
                      When they accept, they can see your records and add your ECG readings and reports from the visit. You can end the link any time under Care team.
                    </small>
                  </span>
                </label>
              )}
              <div className="row">
                <button type="button" className="btn primary" disabled={!slot || busy || cal.limitReached} onClick={book}>
                  {busy ? 'Sending…' : slot ? `Request ${whenLabel(slot, tz)}` : 'Pick a time'}
                </button>
                <span className="muted small">Times in your time zone ({zoneName(tz)}). The doctor accepts or declines.</span>
              </div>
            </>
          )}
        </>
      )}
      {msg && <Banner kind={msg.kind}>{msg.text}</Banner>}
    </div>
  );
}

/** Patient home: the next appointments, with a link to book. */
export function UpcomingAppointmentsCard() {
  const [up] = useLoad(() => api<Appointment[]>('GET', '/v1/appointments?scope=upcoming'), []);
  const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
  const open = (up ?? []).filter((a) => a.status === 'requested' || a.status === 'accepted').slice(0, 3);
  return (
    <section className="card span-12">
      <div className="row spread">
        <h2 style={{ margin: 0 }}>Appointments</h2>
        <Link to="/doctors#appointments" className="btn ghost small">
          {open.length ? 'All appointments' : 'Book an appointment'}
        </Link>
      </div>
      {open.length > 0 && (
        <ul className="appt-list" style={{ marginTop: 8 }}>
          {open.map((a) => (
            <li key={a.id} className="appt-row">
              <div className="appt-main">
                <strong>{a.doctor.fullName}</strong>
                <span className="muted small">
                  {whenLabel(a.startsAt, tz)} · {MODE_LABEL[a.mode]}
                </span>
              </div>
              <div className="row" style={{ gap: 6 }}>
                <StatusTag s={a.status} />
                {a.meetingLink && (
                  <a className="btn primary small" href={a.meetingLink} target="_blank" rel="noreferrer noopener">
                    Join video
                  </a>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
