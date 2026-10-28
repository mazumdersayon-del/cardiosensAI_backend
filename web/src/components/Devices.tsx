import { useEffect, useState } from 'react';
import { api, type DeviceConfig, type LinkedPatient, type Pairing, type RegisteredDevice } from '../api';
import { useAuth } from '../auth';
import { Banner, errorText, useLoad } from '../components/ui';
import { ResearchConsent } from './EcgCards';
import { ago, day } from '../format';

const CAP: Record<string, string> = { hr: 'Heart rate', spo2: 'SpO₂', bp: 'Blood pressure', resp: 'Respiration', temp: 'Temperature', glucose: 'Glucose', ecg: 'ECG' };
const UID = /^[A-Za-z0-9][A-Za-z0-9_-]{5,63}$/; // = app/core/devicekeys.UID_RE

export function intervalLabel(min: number): string {
  if (min === 0) return 'Off';
  if (min < 60) return `Every ${min} minutes`;
  if (min === 60) return 'Every hour';
  if (min === 1440) return 'Once a day';
  return `Every ${min / 60} hours`;
}

function timeZones(current: string): string[] {
  const all = (Intl as unknown as { supportedValuesOf?: (k: string) => string[] }).supportedValuesOf?.('timeZone') ?? ['Asia/Kolkata', 'UTC'];
  return all.includes(current) ? all : [current, ...all];
}

/** Account → Devices: pair a CardiosensAI device, configure its snapshots, see and remove devices. */
export function DevicesSection() {
  const { user } = useAuth();
  const [devices, err, reload] = useLoad(() => api<RegisteredDevice[]>('GET', '/v1/devices'), []);
  const [msg, setMsg] = useState<{ kind: 'ok' | 'error'; text: string } | null>(null);
  if (!user) return null;
  return (
    <div className="stack" style={{ gap: 20 }}>
      <PairDevice
        onPaired={(d) => {
          setMsg({ kind: 'ok', text: `${d.displayName} is paired with your account.` });
          reload();
        }}
      />
      {msg && <Banner kind={msg.kind}>{msg.text}</Banner>}
      {user.role === 'patient' && <ResearchConsent />}
      <div className="stack">
        <h3>Your devices</h3>
        {err && <Banner kind="error">{err}</Banner>}
        {devices && !devices.length && <p className="muted">No devices yet.</p>}
        <ul className="device-list">
          {(devices ?? []).map((d) => (
            <DeviceRow key={d.id} d={d} onChange={reload} onMessage={setMsg} />
          ))}
        </ul>
      </div>
    </div>
  );
}

/** Device ID + serial → the server asks the device to pair → the user presses the device's button. */
function PairDevice({ onPaired }: { onPaired: (d: RegisteredDevice) => void }) {
  const [f, setF] = useState({ deviceUid: '', serial: '', displayName: '' });
  const [pairing, setPairing] = useState<Pairing | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [now, setNow] = useState(Date.now());
  const uidOk = UID.test(f.deviceUid.trim());

  useEffect(() => {
    if (pairing?.status !== 'pending') return;
    const t = setInterval(async () => {
      setNow(Date.now());
      try {
        const p = await api<Pairing>('GET', `/v1/devices/pairings/${pairing.id}`);
        if (p.status === 'pending') return;
        setPairing(p);
        if (p.status === 'accepted' && p.device) {
          setF({ deviceUid: '', serial: '', displayName: '' });
          setPairing(null);
          onPaired(p.device);
        }
      } catch (ex) {
        setError(errorText(ex));
        setPairing(null);
      }
    }, 2000);
    return () => clearInterval(t);
  }, [pairing, onPaired]);

  const start = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      setPairing(await api<Pairing>('POST', '/v1/devices/pair', { deviceUid: f.deviceUid.trim(), serial: f.serial.trim(), displayName: f.displayName.trim() || null }));
      setNow(Date.now());
    } catch (ex) {
      setError(errorText(ex));
    } finally {
      setBusy(false);
    }
  };
  const cancel = async () => {
    if (pairing) await api('DELETE', `/v1/devices/pairings/${pairing.id}`).catch(() => undefined);
    setPairing(null);
  };

  if (pairing?.status === 'pending') {
    const left = Math.max(0, Math.round((new Date(pairing.expiresAt).getTime() - now) / 1000));
    return (
      <div className="stack">
        <h3>Add a CardiosensAI device</h3>
        <Banner kind="info">
          <strong>Press the button on device {pairing.deviceUid} now.</strong> Its light blinks while it waits. {left > 0 ? `${left} s left.` : 'Waiting for the device…'}
        </Banner>
        <p className="muted">The device must be switched on and connected to Wi-Fi.</p>
        <div className="row">
          <button type="button" className="btn ghost" onClick={() => void cancel()}>
            Cancel
          </button>
        </div>
      </div>
    );
  }
  return (
    <div className="stack">
      <h3>Add a CardiosensAI device</h3>
      <p className="muted" style={{ marginTop: -4 }}>
        Switch the device on and connect it to Wi-Fi, then enter the details from its label. You’ll be asked to press its button to finish.
      </p>
      {pairing?.status === 'expired' && <Banner kind="warn">The device didn’t confirm in time. Check that it’s on and online, then try again.</Banner>}
      <form className="device-form" onSubmit={start}>
        <label className="field">
          Device ID
          <small className="muted">On the device label, e.g. CS12-000123</small>
          <input value={f.deviceUid} onChange={(e) => setF({ ...f, deviceUid: e.target.value })} required autoComplete="off" spellCheck={false} aria-invalid={!!f.deviceUid && !uidOk} />
        </label>
        <label className="field">
          Serial number
          <small className="muted">As printed on the device</small>
          <input value={f.serial} onChange={(e) => setF({ ...f, serial: e.target.value })} required maxLength={64} autoComplete="off" spellCheck={false} />
        </label>
        <label className="field">
          Name (optional)
          <small className="muted">How it shows in your lists</small>
          <input value={f.displayName} onChange={(e) => setF({ ...f, displayName: e.target.value })} maxLength={80} placeholder="e.g. Ward monitor" />
        </label>
        <div className="device-form-actions">
          {f.deviceUid && !uidOk && <small style={{ color: 'var(--danger)' }}>A device ID is 6–64 letters, digits, - or _</small>}
          <button type="submit" className="btn primary" disabled={busy || !uidOk || !f.serial.trim()}>
            {busy ? 'Contacting the device…' : 'Pair device'}
          </button>
        </div>
      </form>
      {error && <Banner kind="error">{error}</Banner>}
    </div>
  );
}

function DeviceRow({ d, onChange, onMessage }: { d: RegisteredDevice; onChange: () => void; onMessage: (m: { kind: 'ok' | 'error'; text: string }) => void }) {
  const [confirm, setConfirm] = useState(false);
  const [editing, setEditing] = useState(false);
  const cfg = d.config;
  const remove = async () => {
    try {
      await api('DELETE', `/v1/devices/${d.id}`);
      onMessage({ kind: 'ok', text: d.connected ? `${d.displayName} was unpaired. Its snapshots stay in the record.` : `${d.displayName} was removed. Its past readings are kept.` });
      onChange();
    } catch (ex) {
      onMessage({ kind: 'error', text: errorText(ex) });
    } finally {
      setConfirm(false);
    }
  };
  const live = async (on: boolean) => {
    try {
      await api('POST', `/v1/devices/${d.id}/live`, { on });
      onMessage({ kind: 'ok', text: on ? `${d.displayName} is recording 20 seconds now. Watch it on the Home page under Live ECG.` : 'Live recording cancelled.' });
      onChange();
    } catch (ex) {
      onMessage({ kind: 'error', text: errorText(ex) });
    }
  };
  return (
    <li className="device-row">
      <div className="device-main">
        <div className="row" style={{ gap: 8, flexWrap: 'wrap' }}>
          <strong>{d.displayName}</strong>
          {d.connected && <span className={`pill ${d.online ? 'ok' : 'none'}`}>{d.online ? 'Online' : 'Offline'}</span>}
          {cfg?.liveUntil && <span className="pill info">Recording live</span>}
        </div>
        <span className="muted">{d.modelName}</span>
        <dl>
          <dt>Device ID</dt>
          <dd className="mono">{d.deviceUid ?? '—'}</dd>
          <dt>Serial</dt>
          <dd className="mono">{d.serial}</dd>
          <dt>{d.connected ? 'Paired' : 'Registered'}</dt>
          <dd>{day(cfg?.pairedAt ?? d.registeredAt)}</dd>
          <dt>Last seen</dt>
          <dd>{d.lastSeenAt ? ago(d.lastSeenAt) : 'not yet'}</dd>
          {d.firmware && (
            <>
              <dt>Firmware</dt>
              <dd className="mono">{d.firmware}</dd>
            </>
          )}
          {cfg && (
            <>
              <dt>Snapshots</dt>
              <dd>
                {cfg.recording ? `${intervalLabel(cfg.intervalMin)} (20 s, 12-lead)` : 'Not recording'}
                {cfg.recording && cfg.quietStart && cfg.quietEnd && `, none ${cfg.quietStart}–${cfg.quietEnd}`}
                {cfg.recordingFor && cfg.canAssign && ` for ${cfg.recordingFor}`}
              </dd>
            </>
          )}
        </dl>
        {cfg && !cfg.recording && cfg.canAssign && <Banner kind="warn">Choose the patient this device records for in Device configuration.</Banner>}
        {!d.connected && (
          <div className="row" style={{ gap: 6, flexWrap: 'wrap' }}>
            {d.capabilities.map((c) => (
              <span key={c} className="pill ok">
                {CAP[c] ?? c}
              </span>
            ))}
          </div>
        )}
        {editing && cfg && (
          <ConfigForm
            d={d}
            cfg={cfg}
            onDone={(text) => {
              setEditing(false);
              if (text) onMessage({ kind: 'ok', text });
              onChange();
            }}
          />
        )}
      </div>
      <div className="device-actions">
        {cfg?.canEdit && !editing && (
          <button type="button" className="btn ghost small" onClick={() => setEditing(true)}>
            Device configuration
          </button>
        )}
        {cfg?.canEdit && cfg.recording && (
          <button type="button" className="btn ghost small" onClick={() => void live(!cfg.liveUntil)}>
            {cfg.liveUntil ? 'Cancel live' : 'Live (20 s now)'}
          </button>
        )}
        {confirm ? (
          <>
            <span className="muted">{d.connected ? 'Unpair? Snapshots stay.' : 'Remove? Past readings stay.'}</span>
            <button type="button" className="btn ghost small" onClick={() => setConfirm(false)}>
              Keep
            </button>
            <button type="button" className="btn danger small" onClick={() => void remove()}>
              {d.connected ? 'Unpair' : 'Remove'}
            </button>
          </>
        ) : (
          <button type="button" className="btn ghost small" onClick={() => setConfirm(true)}>
            {d.connected ? 'Unpair…' : 'Remove…'}
          </button>
        )}
      </div>
    </li>
  );
}

/** Snapshot interval, quiet hours, time zone and (doctor/clinic devices) the patient it records for. */
function ConfigForm({ d, cfg, onDone }: { d: RegisteredDevice; cfg: DeviceConfig; onDone: (text?: string) => void }) {
  const [f, setF] = useState({
    intervalMin: cfg.intervalMin,
    quiet: !!(cfg.quietStart && cfg.quietEnd),
    quietStart: cfg.quietStart ?? '22:00',
    quietEnd: cfg.quietEnd ?? '07:00',
    timezone: cfg.timezone,
    assignedPatientId: cfg.assignedPatientId ?? '',
  });
  const [patients] = useLoad(() => (cfg.canAssign ? api<LinkedPatient[]>('GET', '/v1/linked-patients') : Promise.resolve([] as LinkedPatient[])), [cfg.canAssign]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const body: Record<string, unknown> = {
        intervalMin: f.intervalMin,
        quietStart: f.quiet ? f.quietStart : null,
        quietEnd: f.quiet ? f.quietEnd : null,
        timezone: f.timezone,
      };
      if (cfg.canAssign) body.assignedPatientId = f.assignedPatientId || null;
      await api('PUT', `/v1/devices/${d.id}/config`, body);
      onDone(`${d.displayName}: configuration saved. The device picks it up as soon as it’s online.`);
    } catch (ex) {
      setError(errorText(ex));
    } finally {
      setBusy(false);
    }
  };
  return (
    <form className="device-form" style={{ marginTop: 12 }} onSubmit={save}>
      <label className="field">
        Snapshot interval
        <small className="muted">A 20-second 12-lead ECG is saved this often</small>
        <select value={f.intervalMin} onChange={(e) => setF({ ...f, intervalMin: Number(e.target.value) })}>
          {cfg.intervals.map((m) => (
            <option key={m} value={m}>
              {intervalLabel(m)}
            </option>
          ))}
        </select>
      </label>
      <label className="field">
        Time zone
        <small className="muted">For quiet hours</small>
        <select value={f.timezone} onChange={(e) => setF({ ...f, timezone: e.target.value })}>
          {timeZones(f.timezone).map((z) => (
            <option key={z} value={z}>
              {z}
            </option>
          ))}
        </select>
      </label>
      <div className="field" role="group" aria-label="Quiet hours">
        <label className="row" style={{ gap: 8, fontWeight: 600 }}>
          <input type="checkbox" checked={f.quiet} onChange={(e) => setF({ ...f, quiet: e.target.checked })} /> Quiet hours
        </label>
        <small className="muted">No snapshots between these times (e.g. while sleeping)</small>
        {f.quiet && (
          <div className="row" style={{ gap: 8 }}>
            <input type="time" value={f.quietStart} onChange={(e) => setF({ ...f, quietStart: e.target.value })} required aria-label="Quiet from" />
            <span>to</span>
            <input type="time" value={f.quietEnd} onChange={(e) => setF({ ...f, quietEnd: e.target.value })} required aria-label="Quiet until" />
          </div>
        )}
      </div>
      {cfg.canAssign && (
        <label className="field">
          Records for
          <small className="muted">A patient linked with you. Snapshots go to their record.</small>
          <select value={f.assignedPatientId} onChange={(e) => setF({ ...f, assignedPatientId: e.target.value })}>
            <option value="">Nobody (not recording)</option>
            {(patients ?? []).map((p) => (
              <option key={p.patientId} value={p.patientId}>
                {p.fullName ?? p.uniqueId}
              </option>
            ))}
          </select>
        </label>
      )}
      <div className="device-form-actions">
        {error && <small style={{ color: 'var(--danger)' }}>{error}</small>}
        <button type="button" className="btn ghost" onClick={() => onDone()}>
          Cancel
        </button>
        <button type="submit" className="btn primary" disabled={busy || (f.quiet && f.quietStart === f.quietEnd)}>
          {busy ? 'Saving…' : 'Save'}
        </button>
      </div>
    </form>
  );
}
