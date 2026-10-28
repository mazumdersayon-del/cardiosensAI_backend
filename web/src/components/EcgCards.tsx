import { useCallback, useEffect, useMemo, useState } from 'react';
import { api, apiBinary, type Consent, type EcgRecording, type EcgSnapshot, type RegisteredDevice, type ScpCode } from '../api';
import { useAuth } from '../auth';
import { when } from '../format';
import { EcgView, LiveEcg, RecordingView, type Trace } from './Ecg';
import { Banner, errorText, useLoad } from './ui';

/* The Live ECG and ECG recordings cards (patient home, doctor's patient page). The recordings list holds both stored
   ECG recordings and the 20-second 12-lead snapshots from CardiosensAI devices (docs/device-protocol.md), by date and
   time; the selected one is drawn in the Live ECG card. "Live" asks the patient's device to record 20 s now: it
   streams into the Live ECG card while it records and is then stored as a snapshot. Doctors with access label
   snapshots with PTB-XL SCP-ECG statements. */

const RESEARCH_VERSION = '2026-10';
const QUALITY: Record<string, string> = { good: 'ok', acceptable: 'warn', poor: 'error' };

function qualityNote(s: EcgSnapshot): string {
  const d = s.qualityDetail;
  const parts = [d.leadOff?.length && `lead off: ${d.leadOff.join(', ')}`, d.flat?.length && `flat: ${d.flat.join(', ')}`, d.saturated?.length && `saturated: ${d.saturated.join(', ')}`, d.noisy?.length && `noisy: ${d.noisy.join(', ')}`];
  return parts.filter(Boolean).join(' · ');
}

/** One snapshot on ECG paper (0–10 s / 10–20 s), its quality, the doctors' labels and, for doctors, the label form. */
export function SnapshotView({ s, onLabelled }: { s: EcgSnapshot; onLabelled: (s: EcgSnapshot) => void }) {
  const { user } = useAuth();
  const [data, err] = useLoad(async () => new Int16Array((await apiBinary(`/v1/ecg-snapshots/${s.id}/samples`)).data), [s.id]);
  const [page, setPage] = useState(0);
  const pages = Math.ceil(s.durationS / 10);
  const tr: Trace | null = data ? { data, leads: s.leads, fs: s.sampleRateHz, mmPerSec: 25, mmPerMv: 10 } : null;
  const note = qualityNote(s);
  return (
    <div className="stack" style={{ gap: 8 }}>
      <div className="row spread" style={{ flexWrap: 'wrap', gap: 8 }}>
        <strong style={{ color: 'var(--heading)' }}>
          {s.trigger === 'live' ? 'Live 12-lead ECG' : '12-lead snapshot'} · {when(s.recordedAt)}
          {s.averageHr ? ` · ${s.averageHr} bpm` : ''}
        </strong>
        <div className="row" style={{ gap: 6 }}>
          <button type="button" className="btn ghost small" disabled={page === 0} onClick={() => setPage(page - 1)}>
            ← 0–10 s
          </button>
          <button type="button" className="btn ghost small" disabled={page >= pages - 1} onClick={() => setPage(page + 1)}>
            10–20 s →
          </button>
        </div>
      </div>
      <small className="muted">
        Quality: {s.quality}
        {note && ` (${note})`} · {s.deviceName ?? 'device'}
        {s.acquisition === 'simulated' && ' · test unit (synthetic data)'}
      </small>
      {err && <p className="muted">{err}</p>}
      {!tr && !err && <p className="muted">Loading waveform…</p>}
      {tr && <EcgView tr={tr} t0={page * 10} label={`ECG snapshot ${when(s.recordedAt)}`} />}
      {s.labels.length > 0 && (
        <div className="stack" style={{ gap: 4 }}>
          <h3>Labels</h3>
          {s.labels.map((l) => (
            <p key={l.doctorId} style={{ margin: 0 }}>
              <strong>{l.doctorName}:</strong> {Object.entries(l.scpCodes).map(([c, v]) => `${c}${v < 100 ? ` (${v}%)` : ''}`).join(', ')}
              {l.signalQuality && ` · signal ${l.signalQuality}`}
              {l.note && ` · ${l.note}`}
            </p>
          ))}
        </div>
      )}
      {user?.role === 'doctor' && <LabelForm s={s} mine={s.labels.find((l) => l.doctorId === user.id) ?? null} onSaved={onLabelled} />}
    </div>
  );
}

function LabelForm({ s, mine, onSaved }: { s: EcgSnapshot; mine: EcgSnapshot['labels'][number] | null; onSaved: (s: EcgSnapshot) => void }) {
  const [codes] = useLoad(() => api<ScpCode[]>('GET', '/v1/ecg-snapshots/scp-codes'), []);
  const [chosen, setChosen] = useState<Record<string, number>>(mine?.scpCodes ?? {});
  const [quality, setQuality] = useState<string>(mine?.signalQuality ?? '');
  const [note, setNote] = useState(mine?.note ?? '');
  const [search, setSearch] = useState('');
  const [msg, setMsg] = useState<{ kind: 'ok' | 'error'; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const q = search.trim().toLowerCase();
  const matches = (codes ?? []).filter((c) => !(c.code in chosen) && (!q || c.code.toLowerCase().includes(q) || c.description.toLowerCase().includes(q))).slice(0, 8);
  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setMsg(null);
    try {
      onSaved(await api<EcgSnapshot>('PUT', `/v1/ecg-snapshots/${s.id}/label`, { scpCodes: chosen, signalQuality: quality || null, note: note.trim() || null }));
      setMsg({ kind: 'ok', text: 'Labels saved.' });
    } catch (ex) {
      setMsg({ kind: 'error', text: errorText(ex) });
    } finally {
      setBusy(false);
    }
  };
  const describe = (code: string) => codes?.find((c) => c.code === code)?.description ?? code;
  return (
    <form className="stack" style={{ gap: 8 }} onSubmit={save}>
      <h3>Your labels (SCP-ECG, as in PTB-XL)</h3>
      {Object.keys(chosen).length > 0 && (
        <ul className="stack" style={{ gap: 4, listStyle: 'none', margin: 0, padding: 0 }}>
          {Object.entries(chosen).map(([code, v]) => (
            <li key={code} className="row" style={{ gap: 8, flexWrap: 'wrap' }}>
              <span className="pill info">{code}</span>
              <span>{describe(code)}</span>
              <label className="row" style={{ gap: 4 }}>
                likelihood
                <select value={v} onChange={(e) => setChosen({ ...chosen, [code]: Number(e.target.value) })}>
                  {[100, 80, 50, 35, 15, 0].map((n) => (
                    <option key={n} value={n}>
                      {n}%
                    </option>
                  ))}
                </select>
              </label>
              <button
                type="button"
                className="btn ghost small"
                onClick={() => {
                  const next = { ...chosen };
                  delete next[code];
                  setChosen(next);
                }}
              >
                Remove
              </button>
            </li>
          ))}
        </ul>
      )}
      <label className="field">
        Add a statement
        <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search, e.g. NORM, AFIB, infarction" />
      </label>
      {q && (
        <div className="row" style={{ gap: 6, flexWrap: 'wrap' }}>
          {matches.map((c) => (
            <button
              key={c.code}
              type="button"
              className="btn ghost small"
              title={`${c.description} (${c.group})`}
              disabled={Object.keys(chosen).length >= 12}
              onClick={() => {
                setChosen({ ...chosen, [c.code]: 100 });
                setSearch('');
              }}
            >
              {c.code}: {c.description}
            </button>
          ))}
          {!matches.length && <small className="muted">No matching statement.</small>}
        </div>
      )}
      <div className="row" style={{ gap: 12, flexWrap: 'wrap', alignItems: 'flex-end' }}>
        <label className="field">
          Signal quality
          <select value={quality} onChange={(e) => setQuality(e.target.value)}>
            <option value="">—</option>
            <option value="good">Good</option>
            <option value="acceptable">Acceptable</option>
            <option value="poor">Poor</option>
          </select>
        </label>
        <label className="field" style={{ flex: 1, minWidth: 200 }}>
          Note (optional)
          <input value={note} onChange={(e) => setNote(e.target.value)} maxLength={500} />
        </label>
        <button type="submit" className="btn primary" disabled={busy || !Object.keys(chosen).length}>
          {busy ? 'Saving…' : 'Save labels'}
        </button>
      </div>
      {msg && <Banner kind={msg.kind}>{msg.text}</Banner>}
    </form>
  );
}

/** The patient's research consent: only snapshots taken while it is on may later join the research dataset. */
export function ResearchConsent() {
  const [consents, , reload] = useLoad(() => api<Consent[]>('GET', '/v1/me/consents'), []);
  const [error, setError] = useState<string | null>(null);
  const current = (consents ?? []).find((c) => c.kind === 'research');
  const on = !!current && !current.withdrawnAt;
  const toggle = async () => {
    setError(null);
    try {
      if (on && current) await api('POST', `/v1/me/consents/${current.id}/withdraw`);
      else await api('POST', '/v1/me/consents', { kind: 'research', version: RESEARCH_VERSION });
      reload();
    } catch (ex) {
      setError(errorText(ex));
    }
  };
  if (!consents) return null;
  return (
    <div className="banner info" role="status">
      <label style={{ display: 'flex', gap: 10, alignItems: 'flex-start', cursor: 'pointer' }}>
        <input type="checkbox" checked={on} onChange={() => void toggle()} style={{ marginTop: 4 }} />
        <span>
          <strong>Use my ECG snapshots for research.</strong> Snapshots taken while this is on may later be included, without your name, contact details or IDs, in an ECG research dataset to improve heart-rhythm analysis. You can switch it off at any time; snapshots taken while it is off are never included.
        </span>
      </label>
      {error && <small style={{ color: 'var(--danger)' }}>{error}</small>}
    </div>
  );
}

type EcgItem = { kind: 'recording'; id: string; at: string; rec: EcgRecording } | { kind: 'snapshot'; id: string; at: string; snap: EcgSnapshot };

function itemText(i: EcgItem): string {
  const leads = i.kind === 'snapshot' ? '12-lead' : i.rec.leads.length === 12 ? '12-lead' : i.rec.lead;
  const secs = i.kind === 'snapshot' ? i.snap.durationS : i.rec.durationS;
  const hr = i.kind === 'snapshot' ? i.snap.averageHr : i.rec.averageHr;
  const labels = i.kind === 'snapshot' ? [...new Set(i.snap.labels.flatMap((l) => Object.keys(l.scpCodes)))] : [];
  return [leads, `${secs.toFixed(0)} s`, hr ? `${hr} bpm` : null, labels.length ? labels.join(', ') : null].filter(Boolean).join(' · ');
}

/** "Live" on a patient's page: one button per CardiosensAI device recording for this patient that the viewer may use. */
function LiveButtons({ patientId, reloadKey }: { patientId: string; reloadKey: number }) {
  const [devices, , reload] = useLoad(() => api<RegisteredDevice[]>('GET', `/v1/patients/${patientId}/devices`), [patientId, reloadKey]);
  const [error, setError] = useState<string | null>(null);
  const usable = (devices ?? []).filter((d) => d.config?.recording && d.config.canEdit);
  if (!usable.length) return null;
  const press = async (d: RegisteredDevice) => {
    setError(null);
    try {
      await api('POST', `/v1/devices/${d.id}/live`, { on: !d.config?.liveUntil });
      reload();
    } catch (ex) {
      setError(errorText(ex));
    }
  };
  return (
    <div className="row" style={{ gap: 8, flexWrap: 'wrap' }}>
      {error && <small style={{ color: 'var(--danger)' }}>{error}</small>}
      {usable.map((d) => {
        const busy = !!d.config?.liveUntil;
        const name = usable.length > 1 ? ` · ${d.displayName}` : '';
        return (
          <button
            key={d.id}
            type="button"
            className={busy ? 'btn ghost small' : 'btn primary small'}
            disabled={!d.online && !busy}
            title={d.online ? 'Record 20 seconds now and watch it live' : `${d.displayName} is offline`}
            onClick={() => void press(d)}
          >
            {busy ? `Cancel live${name}` : `● Live${name}`}
          </button>
        );
      })}
    </div>
  );
}

/**
 * The Live ECG card (span-8: the live stream while a device records, otherwise the ECG selected in the list, newest
 * first by default) beside the ECG recordings card (span-4: recordings and snapshots by date and time).
 */
export function EcgCards({ patientId }: { patientId: string }) {
  const [key, setKey] = useState(0);
  const [recs] = useLoad(() => api<EcgRecording[]>('GET', `/v1/patients/${patientId}/ecg-recordings?limit=30`), [patientId, key]);
  const [snaps, , reloadSnaps] = useLoad(() => api<EcgSnapshot[]>('GET', `/v1/patients/${patientId}/ecg-snapshots?limit=30`), [patientId, key]);
  const [selected, setSelected] = useState<string | null>(null);
  const items = useMemo<EcgItem[] | null>(() => {
    if (!recs && !snaps) return null;
    const all: EcgItem[] = [
      ...(recs ?? []).map((rec) => ({ kind: 'recording' as const, id: `r:${rec.id}`, at: rec.startedAt, rec })),
      ...(snaps ?? []).map((snap) => ({ kind: 'snapshot' as const, id: `s:${snap.id}`, at: snap.recordedAt, snap })),
    ];
    return all.sort((a, b) => Date.parse(b.at) - Date.parse(a.at));
  }, [recs, snaps]);
  const current = items?.find((i) => i.id === selected) ?? items?.[0] ?? null;
  // A live recording just ended: show the newest ECG (the device's snapshot arrives with the end of the stream).
  const onSaved = useCallback(() => {
    setSelected(null);
    setKey((k) => k + 1);
  }, []);
  useEffect(() => {
    const t = setInterval(() => setKey((k) => k + 1), 60_000); // scheduled snapshots appear without a reload
    return () => clearInterval(t);
  }, []);
  const view = current ? (
    current.kind === 'recording' ? (
      <RecordingView key={current.id} rec={current.rec} />
    ) : (
      <SnapshotView key={current.id} s={current.snap} onLabelled={() => reloadSnaps()} />
    )
  ) : null;
  return (
    <>
      <section className="card span-8">
        <div className="row spread" style={{ flexWrap: 'wrap', gap: 8 }}>
          <h2 style={{ margin: 0 }}>Live ECG</h2>
          <LiveButtons patientId={patientId} reloadKey={key} />
        </div>
        <LiveEcg patientId={patientId} onSaved={onSaved} idle={view} />
      </section>
      <section className="card span-4">
        <h2>ECG recordings</h2>
        {!items && <p className="muted">Loading…</p>}
        {items && !items.length && <div className="empty">No ECG recordings yet.</div>}
        {items && items.length > 0 && (
          <ul className="ecg-mini" aria-label="ECG recordings, newest first">
            {items.map((i) => (
              <li key={i.id}>
                <button type="button" aria-pressed={i.id === current?.id} onClick={() => setSelected(i.id)}>
                  <span className="row" style={{ gap: 6, flexWrap: 'wrap' }}>
                    <span>{when(i.at)}</span>
                    {i.kind === 'snapshot' && i.snap.trigger === 'live' && <span className="pill info">Live</span>}
                    {i.kind === 'snapshot' && <span className={`pill ${QUALITY[i.snap.quality] ?? 'none'}`}>{i.snap.quality}</span>}
                    {(i.kind === 'snapshot' ? i.snap.acquisition : i.rec.acquisition) === 'simulated' && <span className="pill none">test</span>}
                  </span>
                  <small className="muted">{itemText(i)}</small>
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>
    </>
  );
}
