import { useState } from 'react';
import { api, apiBinary, type EcgRecording } from '../api';
import { when } from '../format';
import { useLoad } from './ui';

export default function EcgPanel({ patientId }: { patientId: string }) {
  const [list, err] = useLoad(() => api<EcgRecording[]>('GET', `/v1/patients/${patientId}/ecg-recordings?limit=20`), [patientId]);
  const [open, setOpen] = useState<EcgRecording | null>(null);
  if (err) return <p className="muted">{err}</p>;
  if (!list) return <p className="muted">Loading…</p>;
  if (!list.length) return <div className="empty">No ECG recordings yet.</div>;
  return (
    <div className="stack">
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Recorded</th>
              <th>Length</th>
              <th>Lead</th>
              <th>Avg HR</th>
              <th>Quality</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {list.map((r) => (
              <tr key={r.id}>
                <td>{when(r.startedAt)}</td>
                <td>{r.durationS.toFixed(0)} s</td>
                <td>{r.lead}</td>
                <td>{r.averageHr ?? '—'}</td>
                <td>{r.quality}</td>
                <td>
                  <button type="button" className="btn ghost small" onClick={() => setOpen(r)}>
                    View strip
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {open && <EcgStrip rec={open} />}
      <small>Raw signal on standard ECG paper (25 mm/s, 10 mm/mV). No automated interpretation is shown.</small>
    </div>
  );
}

function EcgStrip({ rec }: { rec: EcgRecording }) {
  const [state, err] = useLoad(async () => {
    const { data } = await apiBinary(`/v1/ecg-recordings/${rec.id}/samples`);
    return new Int16Array(data);
  }, [rec.id]);
  if (err) return <p className="muted">{err}</p>;
  if (!state) return <p className="muted">Loading waveform…</p>;
  const pxPerMm = 4;
  const seconds = Math.min(rec.durationS, 30);
  const n = Math.min(state.length, Math.round(seconds * rec.sampleRateHz));
  const W = Math.round(seconds * rec.calibration.mmPerSec * pxPerMm);
  const H = 40 * pxPerMm; // 4 mV tall
  const mid = H / 2;
  const step = Math.max(1, Math.floor(n / (W * 2)));
  let d = '';
  for (let i = 0; i < n; i += step) {
    const xx = (i / rec.sampleRateHz) * rec.calibration.mmPerSec * pxPerMm;
    const yy = mid - (state[i] / 1000) * rec.calibration.mmPerMv * pxPerMm;
    d += `${i ? 'L' : 'M'}${xx.toFixed(1)},${yy.toFixed(1)}`;
  }
  return (
    <div className="ecg-scroll">
      <svg width={W} height={H} role="img" aria-label={`ECG lead ${rec.lead}, first ${seconds.toFixed(0)} seconds`} style={{ display: 'block', background: 'var(--ecg-paper)' }}>
        <defs>
          <pattern id="ecg-minor" width={pxPerMm} height={pxPerMm} patternUnits="userSpaceOnUse">
            <path d={`M${pxPerMm} 0H0V${pxPerMm}`} fill="none" stroke="var(--ecg-minor)" strokeWidth="0.5" />
          </pattern>
          <pattern id="ecg-major" width={pxPerMm * 5} height={pxPerMm * 5} patternUnits="userSpaceOnUse">
            <rect width={pxPerMm * 5} height={pxPerMm * 5} fill="url(#ecg-minor)" />
            <path d={`M${pxPerMm * 5} 0H0V${pxPerMm * 5}`} fill="none" stroke="var(--ecg-major)" strokeWidth="1" />
          </pattern>
        </defs>
        <rect width={W} height={H} fill="url(#ecg-major)" />
        <path d={d} fill="none" stroke="var(--ecg-trace)" strokeWidth="1.2" strokeLinejoin="round" />
      </svg>
    </div>
  );
}
