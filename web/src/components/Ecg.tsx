import { useEffect, useId, useRef, useState } from 'react';
import { api, apiBinary, ApiError, type EcgLiveStatus, type EcgRecording } from '../api';
import { ago, when } from '../format';
import { useLoad } from './ui';

/* ECG paper: 1 mm minor and 5 mm major squares at 25 mm/s and 10 mm/mV (standard calibration).
   Samples are int16 µV, interleaved frames when there are several leads (frame k = [lead0, lead1, ...]). */

const PX_PER_MM = 4;
const GRID_12: string[][] = [
  ['I', 'aVR', 'V1', 'V4'],
  ['II', 'aVL', 'V2', 'V5'],
  ['III', 'aVF', 'V3', 'V6'],
];
const ROW_MM = 25; // 2.5 mV of vertical room per row

export interface Trace {
  data: Int16Array;
  leads: string[];
  fs: number;
  mmPerSec: number;
  mmPerMv: number;
  /** frame index of data[0] in the whole recording (live buffers start later) */
  firstFrame?: number;
}

/** SVG path for one lead between times t0..t1 (seconds, recording time), drawn from x0 at baseline yMid. */
export function leadPath(tr: Trace, lead: string, t0: number, t1: number, x0: number, yMid: number): string {
  const n = tr.leads.length;
  const idx = tr.leads.indexOf(lead);
  if (idx < 0) return '';
  const first = tr.firstFrame ?? 0;
  const frames = tr.data.length / n;
  const k0 = Math.max(0, Math.ceil(t0 * tr.fs) - first);
  const k1 = Math.min(frames, Math.floor(t1 * tr.fs) - first);
  if (k1 <= k0) return '';
  const pxPerSec = tr.mmPerSec * PX_PER_MM;
  const step = Math.max(1, Math.floor(tr.fs / (pxPerSec * 2))); // about 2 points per pixel
  let d = '';
  for (let k = k0; k < k1; k += step) {
    const x = x0 + ((k + first) / tr.fs - t0) * pxPerSec;
    const y = yMid - (tr.data[k * n + idx] / 1000) * tr.mmPerMv * PX_PER_MM;
    d += `${d ? 'L' : 'M'}${x.toFixed(1)},${y.toFixed(1)}`;
  }
  return d;
}

function Paper({ width, height, children, label }: { width: number; height: number; children: React.ReactNode; label: string }) {
  const id = useId().replace(/:/g, '');
  return (
    <div className="ecg-scroll">
      <svg width={width} height={height} role="img" aria-label={label} style={{ display: 'block', background: 'var(--ecg-paper)' }}>
        <defs>
          <pattern id={`${id}m`} width={PX_PER_MM} height={PX_PER_MM} patternUnits="userSpaceOnUse">
            <path d={`M${PX_PER_MM} 0H0V${PX_PER_MM}`} fill="none" stroke="var(--ecg-minor)" strokeWidth="0.5" />
          </pattern>
          <pattern id={`${id}M`} width={PX_PER_MM * 5} height={PX_PER_MM * 5} patternUnits="userSpaceOnUse">
            <rect width={PX_PER_MM * 5} height={PX_PER_MM * 5} fill={`url(#${id}m)`} />
            <path d={`M${PX_PER_MM * 5} 0H0V${PX_PER_MM * 5}`} fill="none" stroke="var(--ecg-major)" strokeWidth="1" />
          </pattern>
        </defs>
        <rect width={width} height={height} fill={`url(#${id}M)`} />
        {children}
      </svg>
    </div>
  );
}

const traceStyle = { fill: 'none', stroke: 'var(--ecg-trace)', strokeWidth: 1.1, strokeLinejoin: 'round' as const };
const labelStyle = { fontSize: 12, fontWeight: 700, fill: 'var(--ecg-trace)' };

/** 1 mV calibration pulse (10 mm tall, 0.2 s wide) at the start of a row. */
function CalPulse({ y, mmPerMv }: { y: number; mmPerMv: number }) {
  const h = mmPerMv * PX_PER_MM;
  const w = 5 * PX_PER_MM;
  return <path d={`M2,${y}H${4}V${y - h}H${4 + w}V${y}H${4 + w + 4}`} {...traceStyle} />;
}

/**
 * 12-lead: standard 3x4 layout (each lead shows its own 2.5 s of a 10 s window) plus a 10 s lead II
 * rhythm strip. Other lead sets: one full-width strip per lead. `t0` = window start in seconds.
 */
export function EcgView({ tr, t0 = 0, seconds = 10, label }: { tr: Trace; t0?: number; seconds?: number; label: string }) {
  const pxPerSec = tr.mmPerSec * PX_PER_MM;
  const lead0 = 6 * PX_PER_MM; // room for the calibration pulse
  const width = Math.round(lead0 + seconds * pxPerSec);
  const rowH = ROW_MM * PX_PER_MM;
  const is12 = GRID_12.flat().every((l) => tr.leads.includes(l));
  if (is12) {
    const col = seconds / 4;
    return (
      <Paper width={width} height={rowH * 4} label={`${label}: 12-lead, ${seconds} s`}>
        {GRID_12.map((row, r) =>
          row.map((lead, c) => {
            const yMid = rowH * r + rowH * 0.6;
            const x0 = lead0 + c * col * pxPerSec;
            return (
              <g key={lead}>
                {c === 0 && <CalPulse y={yMid} mmPerMv={tr.mmPerMv} />}
                {c > 0 && <path d={`M${x0},${yMid - 20}V${yMid + 20}`} stroke="var(--ecg-trace)" strokeWidth="1" />}
                <path d={leadPath(tr, lead, t0 + c * col, t0 + (c + 1) * col, x0, yMid)} {...traceStyle} />
                <text x={x0 + 6} y={rowH * r + 14} style={labelStyle}>
                  {lead}
                </text>
              </g>
            );
          }),
        )}
        <CalPulse y={rowH * 3 + rowH * 0.6} mmPerMv={tr.mmPerMv} />
        <path d={leadPath(tr, 'II', t0, t0 + seconds, lead0, rowH * 3 + rowH * 0.6)} {...traceStyle} />
        <text x={lead0 + 6} y={rowH * 3 + 14} style={labelStyle}>
          II (rhythm)
        </text>
      </Paper>
    );
  }
  return (
    <Paper width={width} height={rowH * tr.leads.length} label={`${label}: ${tr.leads.join(', ')}`}>
      {tr.leads.map((lead, r) => {
        const yMid = rowH * r + rowH * 0.6;
        return (
          <g key={lead}>
            <CalPulse y={yMid} mmPerMv={tr.mmPerMv} />
            <path d={leadPath(tr, lead, t0, t0 + seconds, lead0, yMid)} {...traceStyle} />
            <text x={lead0 + 6} y={rowH * r + 14} style={labelStyle}>
              {lead}
            </text>
          </g>
        );
      })}
    </Paper>
  );
}

/** One stored recording on ECG paper, 10 s per page. */
export function RecordingView({ rec }: { rec: EcgRecording }) {
  const [data, err] = useLoad(async () => new Int16Array((await apiBinary(`/v1/ecg-recordings/${rec.id}/samples`)).data), [rec.id]);
  const [page, setPage] = useState(0);
  if (err) return <p className="muted">{err}</p>;
  if (!data) return <p className="muted">Loading waveform…</p>;
  const pages = Math.max(1, Math.ceil(rec.durationS / 10));
  const tr: Trace = { data, leads: rec.leads, fs: rec.sampleRateHz, mmPerSec: rec.calibration.mmPerSec, mmPerMv: rec.calibration.mmPerMv };
  return (
    <div className="stack" style={{ gap: 8 }}>
      <div className="row spread">
        <strong style={{ color: 'var(--heading)' }}>
          {rec.leads.length === 12 ? '12-lead ECG' : rec.lead} · {when(rec.startedAt)}
          {rec.averageHr ? ` · ${rec.averageHr} bpm` : ''}
        </strong>
        {pages > 1 && (
          <div className="row" style={{ gap: 6 }}>
            <button type="button" className="btn ghost small" disabled={page === 0} onClick={() => setPage(page - 1)}>
              ← Earlier
            </button>
            <span className="muted">
              {page * 10}–{Math.min(rec.durationS, (page + 1) * 10).toFixed(0)} s of {rec.durationS.toFixed(0)} s
            </span>
            <button type="button" className="btn ghost small" disabled={page >= pages - 1} onClick={() => setPage(page + 1)}>
              Later →
            </button>
          </div>
        )}
      </div>
      <EcgView tr={tr} t0={page * 10} label={`ECG recorded ${when(rec.startedAt)}`} />
    </div>
  );
}

// ---------------------------------------------------------------- live

const LIVE_WINDOW_S = 10;

function duration(s: number): string {
  const m = Math.floor(s / 60);
  return `${m}:${String(Math.floor(s % 60)).padStart(2, '0')}`;
}

/**
 * Live ECG: polls the status every 2 s; while recording, fetches new frames every second and draws
 * the last 10 s. When the recording stops it calls `onSaved` so lists can show the new recording.
 */
export function LiveEcg({ patientId, onSaved, hideWhenIdle = false, idle }: { patientId: string; onSaved?: () => void; hideWhenIdle?: boolean; idle?: React.ReactNode }) {
  const [status, setStatus] = useState<EcgLiveStatus | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [trace, setTrace] = useState<Trace | null>(null);
  const buf = useRef<{ id: string; data: Int16Array; first: number; next: number } | null>(null);
  const wasLive = useRef<string | null>(null);
  const savedCb = useRef(onSaved);
  savedCb.current = onSaved;

  useEffect(() => {
    let stop = false;
    const tick = async () => {
      try {
        const st = await api<EcgLiveStatus>('GET', `/v1/patients/${patientId}/ecg-live`);
        if (stop) return;
        setStatus(st);
        setErr(null);
        if (wasLive.current && !st.live) savedCb.current?.();
        wasLive.current = st.live?.id ?? null;
      } catch (e) {
        if (!stop) setErr(e instanceof ApiError ? e.message : 'Live status unavailable');
      }
    };
    tick();
    const t = setInterval(tick, 2000);
    return () => {
      stop = true;
      clearInterval(t);
    };
  }, [patientId]);

  const live = status?.live ?? null;
  useEffect(() => {
    if (!live) {
      buf.current = null;
      setTrace(null);
      return;
    }
    let stop = false;
    const n = live.leads.length;
    const keep = LIVE_WINDOW_S * live.sampleRateHz;
    const pull = async () => {
      const b = buf.current?.id === live.id ? buf.current : { id: live.id, data: new Int16Array(0), first: 0, next: 0 };
      try {
        const { data, headers } = await apiBinary(`/v1/ecg-live/${live.id}/samples?fromFrame=${b.next}`);
        if (stop) return;
        const from = Number(headers.get('x-from-frame') ?? b.next);
        const chunk = new Int16Array(data);
        // A jump forward (viewer fell behind) drops the old buffer; otherwise append.
        const base = from === b.next ? b.data : new Int16Array(0);
        const first = from === b.next ? b.first : from;
        const merged = new Int16Array(base.length + chunk.length);
        merged.set(base);
        merged.set(chunk, base.length);
        const frames = merged.length / n;
        const drop = Math.max(0, frames - keep);
        const nb = { id: live.id, data: merged.slice(drop * n), first: first + drop, next: from + chunk.length / n };
        buf.current = nb;
        setTrace({ data: nb.data, leads: live.leads, fs: live.sampleRateHz, mmPerSec: live.calibration.mmPerSec, mmPerMv: live.calibration.mmPerMv, firstFrame: nb.first });
      } catch {
        /* 410 = just ended; the status poll picks that up */
      }
    };
    pull();
    const t = setInterval(pull, 1000);
    return () => {
      stop = true;
      clearInterval(t);
    };
  }, [live?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  if (err) return hideWhenIdle ? null : <p className="muted">{err}</p>;
  if (!status) return hideWhenIdle ? null : <p className="muted">Checking for a live ECG…</p>;
  if (!live) {
    if (hideWhenIdle) return null;
    const last = status.lastRecording;
    return (
      <div className="stack" style={{ gap: 10 }}>
        <div className="row" style={{ gap: 10 }}>
          <span className="pill none">● Not recording</span>
          {!idle && <span className="muted">{last ? `Last ECG ${ago(last.startedAt)} (${last.leads.length === 12 ? '12-lead' : last.lead}, ${last.durationS.toFixed(0)} s)` : 'No ECG recorded yet'}</span>}
        </div>
        {idle}
      </div>
    );
  }
  const end = trace ? ((trace.firstFrame ?? 0) + trace.data.length / trace.leads.length) / trace.fs : 0;
  const t0 = Math.max(0, end - LIVE_WINDOW_S);
  return (
    <div className="stack" style={{ gap: 8 }}>
      <div className="row" style={{ gap: 10 }} aria-live="polite">
        {live.state === 'live' ? (
          <span className="pill error live-dot">● Live</span>
        ) : (
          <span className="pill warn">● Signal lost · last data {ago(live.lastChunkAt ?? live.startedAt)}</span>
        )}
        <strong style={{ color: 'var(--heading)' }}>
          {live.leads.length === 12 ? '12-lead' : `${live.leads.length}-lead`} · {duration(live.durationS)}
          {live.averageHr ? ` · ${live.averageHr} bpm` : ''}
        </strong>
        <span className="muted">Started {when(live.startedAt)}. Saved when it stops.</span>
      </div>
      {trace && trace.data.length > 0 ? <EcgView tr={trace} t0={t0} label="Live ECG, last 10 seconds" /> : <p className="muted">Waiting for data…</p>}
    </div>
  );
}
