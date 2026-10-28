import { useState } from 'react';
import { api, type DeviceModel, type RegisteredDevice } from '../api';
import { useAuth } from '../auth';
import { Banner, errorText, useLoad } from '../components/ui';
import { ago, day } from '../format';

const CAP: Record<string, string> = { hr: 'Heart rate', spo2: 'SpO₂', bp: 'Blood pressure', resp: 'Respiration', temp: 'Temperature', glucose: 'Glucose', ecg: 'ECG' };
const UID = /^[A-Za-z0-9][A-Za-z0-9_.:-]{5,63}$/;

/** Account → Devices: register a device by its device ID + serial number, see your devices, deregister one. */
export function DevicesSection() {
  const { user } = useAuth();
  const [devices, err, reload] = useLoad(() => api<RegisteredDevice[]>('GET', '/v1/devices'), []);
  const [models] = useLoad(() => api<DeviceModel[]>('GET', '/v1/device-models'), []);
  const [f, setF] = useState({ deviceUid: '', serial: '', modelCode: '', displayName: '' });
  const [msg, setMsg] = useState<{ kind: 'ok' | 'error'; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirm, setConfirm] = useState<string | null>(null);
  if (!user) return null;
  const model = f.modelCode || models?.find((m) => m.code === 'cardiosensai-12lead-dev')?.code || models?.[0]?.code || '';
  const uidOk = UID.test(f.deviceUid.trim());

  const register = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setMsg(null);
    try {
      const d = await api<RegisteredDevice>('POST', '/v1/devices', { deviceUid: f.deviceUid.trim(), serial: f.serial.trim(), modelCode: model, displayName: f.displayName.trim() || null });
      setMsg({ kind: 'ok', text: `${d.displayName} is registered to your account.` });
      setF({ deviceUid: '', serial: '', modelCode: f.modelCode, displayName: '' });
      reload();
    } catch (ex) {
      setMsg({ kind: 'error', text: errorText(ex) });
    } finally {
      setBusy(false);
    }
  };
  const deregister = async (d: RegisteredDevice) => {
    try {
      await api('DELETE', `/v1/devices/${d.id}`);
      setMsg({ kind: 'ok', text: `${d.displayName} was deregistered. Its past readings are kept.` });
      reload();
    } catch (ex) {
      setMsg({ kind: 'error', text: errorText(ex) });
    } finally {
      setConfirm(null);
    }
  };

  return (
    <div className="stack" style={{ gap: 20 }}>
      <div className="stack">
        <h3>Register a device</h3>
        <form className="device-form" onSubmit={register}>
          <label className="field">
            Device ID
            <small className="muted">On the device label or in its settings, e.g. CS12-7F3A-91B2</small>
            <input value={f.deviceUid} onChange={(e) => setF({ ...f, deviceUid: e.target.value })} required autoComplete="off" spellCheck={false} aria-invalid={!!f.deviceUid && !uidOk} />
          </label>
          <label className="field">
            Serial number
            <small className="muted">As printed on the device</small>
            <input value={f.serial} onChange={(e) => setF({ ...f, serial: e.target.value })} required maxLength={64} autoComplete="off" spellCheck={false} />
          </label>
          <label className="field">
            Model
            <small className="muted">Decides which readings it may send</small>
            <select value={model} onChange={(e) => setF({ ...f, modelCode: e.target.value })}>
              {(models ?? []).map((m) => (
                <option key={m.code} value={m.code}>
                  {m.name}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            Name (optional)
            <small className="muted">How it shows in your lists</small>
            <input value={f.displayName} onChange={(e) => setF({ ...f, displayName: e.target.value })} maxLength={80} placeholder="e.g. Ward monitor" />
          </label>
          <div className="device-form-actions">
            {f.deviceUid && !uidOk && <small style={{ color: 'var(--danger)' }}>A device ID is 6–64 letters, digits, - _ . or :</small>}
            <button type="submit" className="btn primary" disabled={busy || !uidOk || !f.serial.trim() || !model}>
              {busy ? 'Registering…' : 'Register device'}
            </button>
          </div>
        </form>
        {msg && <Banner kind={msg.kind}>{msg.text}</Banner>}
      </div>

      <div className="stack">
        <h3>Your devices</h3>
        {err && <Banner kind="error">{err}</Banner>}
        {devices && !devices.length && <p className="muted">No devices registered yet.</p>}
        <ul className="device-list">
          {(devices ?? []).map((d) => (
            <li key={d.id} className="device-row">
              <div className="device-main">
                <strong>{d.displayName}</strong>
                <span className="muted">{d.modelName}</span>
                <dl>
                  <dt>Device ID</dt>
                  <dd className="mono">{d.deviceUid ?? '—'}</dd>
                  <dt>Serial</dt>
                  <dd className="mono">{d.serial}</dd>
                  <dt>Registered</dt>
                  <dd>{day(d.registeredAt)}</dd>
                  <dt>Last data</dt>
                  <dd>{d.lastSeenAt ? ago(d.lastSeenAt) : 'none yet'}</dd>
                </dl>
                <div className="row" style={{ gap: 6, flexWrap: 'wrap' }}>
                  {d.capabilities.map((c) => (
                    <span key={c} className="pill ok">
                      {CAP[c] ?? c}
                    </span>
                  ))}
                </div>
              </div>
              <div className="device-actions">
                {confirm === d.id ? (
                  <>
                    <span className="muted">Deregister? Past readings stay.</span>
                    <button type="button" className="btn ghost small" onClick={() => setConfirm(null)}>
                      Keep
                    </button>
                    <button type="button" className="btn danger small" onClick={() => void deregister(d)}>
                      Deregister
                    </button>
                  </>
                ) : (
                  <button type="button" className="btn ghost small" onClick={() => setConfirm(d.id)}>
                    Deregister…
                  </button>
                )}
              </div>
            </li>
          ))}
        </ul>
      </div>

    </div>
  );
}
