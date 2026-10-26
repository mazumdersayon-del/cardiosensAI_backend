import { useMemo, useState } from 'react';
import { api, type Measurement, type Vital } from '../api';
import { ago, VITALS, vitalValue, when } from '../format';
import { useLoad } from './ui';

interface Latest {
  latest: Record<string, Measurement | null>;
  sharedVitals: string[];
}

export function LatestVitals({ patientId }: { patientId: string }) {
  const [data, err] = useLoad(() => api<Latest>('GET', `/v1/patients/${patientId}/measurements/latest`), [patientId]);
  if (err) return <p className="muted">{err}</p>;
  if (!data) return <p className="muted">Loading…</p>;
  return (
    <div className="vitals">
      {VITALS.map((v) => {
        const m = data.latest[v.key];
        return (
          <div className="vital" key={v.key}>
            <div className="muted" style={{ fontSize: '0.85rem', fontWeight: 600 }}>
              {v.name}
            </div>
            <div>
              <span className="v">{m ? vitalValue(m) : '—'}</span>
              <span className="u">{v.unit}</span>
            </div>
            <small title={m ? when(m.measuredAt) : undefined}>{m ? ago(m.measuredAt) : 'No reading'}</small>
          </div>
        );
      })}
    </div>
  );
}

const RANGES = [
  { key: '7', label: '7 days', days: 7 },
  { key: '30', label: '30 days', days: 30 },
  { key: '90', label: '90 days', days: 90 },
];

/** History for one vital. Gaps longer than 6 h are drawn as breaks, never interpolated. */
export function History({ patientId }: { patientId: string }) {
  const [vital, setVital] = useState<Vital>('hr');
  const [range, setRange] = useState('7');
  const days = RANGES.find((r) => r.key === range)!.days;
  const [page, err] = useLoad(() => {
    const from = new Date(Date.now() - days * 86400_000).toISOString();
    return api<{ items: Measurement[] }>('GET', `/v1/patients/${patientId}/measurements?vital=${vital}&limit=1000&from=${encodeURIComponent(from)}`);
  }, [patientId, vital, days]);
  const meta = VITALS.find((v) => v.key === vital)!;
  return (
    <div className="stack">
      <div className="row spread">
        <div className="seg" role="group" aria-label="Vital sign">
          {VITALS.map((v) => (
            <button key={v.key} type="button" aria-pressed={v.key === vital} onClick={() => setVital(v.key)}>
              {v.name}
            </button>
          ))}
        </div>
        <div className="seg" role="group" aria-label="Period">
          {RANGES.map((r) => (
            <button key={r.key} type="button" aria-pressed={r.key === range} onClick={() => setRange(r.key)}>
              {r.label}
            </button>
          ))}
        </div>
      </div>
      {err && <p className="muted">{err}</p>}
      {page && (page.items.length ? <Chart items={page.items} days={days} unit={meta.unit} bp={vital === 'bp'} /> : <div className="empty">No {meta.name.toLowerCase()} readings in this period.</div>)}
      {page && page.items.length > 0 && (
        <details>
          <summary className="muted" style={{ cursor: 'pointer' }}>
            Show {page.items.length} readings as a table
          </summary>
          <div className="table-wrap" style={{ maxHeight: 320, overflowY: 'auto' }}>
            <table>
              <thead>
                <tr>
                  <th>Time</th>
                  <th>Value</th>
                  <th>Source</th>
                  <th>Quality</th>
                </tr>
              </thead>
              <tbody>
                {page.items.map((m) => (
                  <tr key={m.id}>
                    <td>{when(m.measuredAt)}</td>
                    <td className="mono">
                      {vitalValue(m)} {meta.unit}
                    </td>
                    <td>{m.provenance.acquisition}</td>
                    <td>{m.provenance.quality}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      )}
    </div>
  );
}

function Chart({ items, days, unit, bp }: { items: Measurement[]; days: number; unit: string; bp: boolean }) {
  const W = 760;
  const H = 240;
  const pad = { l: 44, r: 12, t: 12, b: 28 };
  const t1 = Date.now();
  const t0 = t1 - days * 86400_000;
  const series = useMemo(() => {
    const pts = [...items].sort((a, b) => a.measuredAt.localeCompare(b.measuredAt));
    const lines: { key: string; pts: { t: number; y: number; poor: boolean }[] }[] = bp
      ? [
          { key: 'sys', pts: pts.map((m) => ({ t: Date.parse(m.measuredAt), y: m.systolic ?? NaN, poor: m.provenance.quality !== 'good' })) },
          { key: 'dia', pts: pts.map((m) => ({ t: Date.parse(m.measuredAt), y: m.diastolic ?? NaN, poor: m.provenance.quality !== 'good' })) },
        ]
      : [{ key: 'v', pts: pts.map((m) => ({ t: Date.parse(m.measuredAt), y: m.value ?? NaN, poor: m.provenance.quality !== 'good' })) }];
    return lines.map((l) => ({ ...l, pts: l.pts.filter((p) => Number.isFinite(p.y)) }));
  }, [items, bp]);
  const ys = series.flatMap((s) => s.pts.map((p) => p.y));
  let lo = Math.min(...ys);
  let hi = Math.max(...ys);
  const span = Math.max(hi - lo, 1);
  lo -= span * 0.1;
  hi += span * 0.1;
  const x = (t: number) => pad.l + ((t - t0) / (t1 - t0)) * (W - pad.l - pad.r);
  const y = (v: number) => pad.t + (1 - (v - lo) / (hi - lo)) * (H - pad.t - pad.b);
  const ticks = Array.from({ length: 5 }, (_, i) => lo + ((hi - lo) * i) / 4);
  const days5 = Array.from({ length: 5 }, (_, i) => t0 + ((t1 - t0) * i) / 4);
  const path = (pts: { t: number; y: number }[]) => {
    let d = '';
    pts.forEach((p, i) => {
      const gap = i === 0 || p.t - pts[i - 1].t > 6 * 3600_000;
      d += `${gap ? 'M' : 'L'}${x(p.t).toFixed(1)},${y(p.y).toFixed(1)}`;
    });
    return d;
  };
  return (
    <svg className="chart" viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`Trend chart in ${unit}`}>
      {ticks.map((v) => (
        <g key={v}>
          <line x1={pad.l} x2={W - pad.r} y1={y(v)} y2={y(v)} stroke="var(--line)" />
          <text x={pad.l - 6} y={y(v) + 4} textAnchor="end" fontSize="11" fill="var(--muted)">
            {v.toFixed(0)}
          </text>
        </g>
      ))}
      {days5.map((t, i) => (
        <text key={t} x={x(t)} y={H - 8} textAnchor={i === 0 ? 'start' : i === days5.length - 1 ? 'end' : 'middle'} fontSize="11" fill="var(--muted)">
          {new Date(t).toLocaleDateString(undefined, { day: 'numeric', month: 'short' })}
        </text>
      ))}
      {series.map((s, i) => (
        <g key={s.key}>
          <path d={path(s.pts)} fill="none" stroke={i ? 'var(--accent)' : 'var(--primary)'} strokeWidth="1.75" />
          {s.pts.length < 200 &&
            s.pts.map((p) => (
              <circle key={p.t} cx={x(p.t)} cy={y(p.y)} r="2.6" fill={p.poor ? 'var(--card)' : i ? 'var(--accent)' : 'var(--primary)'} stroke={i ? 'var(--accent)' : 'var(--primary)'} />
            ))}
        </g>
      ))}
      <text x={pad.l} y={pad.t + 2} fontSize="11" fill="var(--muted)" dominantBaseline="hanging" dx="4">
        {unit}
        {bp ? ' · systolic / diastolic' : ''}
      </text>
    </svg>
  );
}
