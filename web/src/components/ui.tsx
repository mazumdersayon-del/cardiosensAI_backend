import { useEffect, useState, type ReactNode } from 'react';
import { ApiError } from '../api';

export function Banner({ kind, children }: { kind: 'warn' | 'error' | 'ok' | 'info'; children: ReactNode }) {
  return (
    <div className={`banner ${kind}`} role={kind === 'error' ? 'alert' : 'status'}>
      {children}
    </div>
  );
}

export function errorText(e: unknown): string {
  if (e instanceof ApiError) return e.message;
  return 'Something went wrong. Check your connection and try again.';
}

export function StatusPill({ status }: { status: string | null | undefined }) {
  const map: Record<string, [string, string]> = {
    verified: ['ok', 'Verified'],
    active: ['ok', 'Active'],
    ready: ['ok', 'Ready'],
    pending: ['warn', 'Pending'],
    queued: ['warn', 'Queued'],
    running: ['warn', 'Building'],
    rejected: ['error', 'Rejected'],
    declined: ['none', 'Declined'],
    ended: ['none', 'Ended'],
    failed: ['error', 'Failed'],
    missing: ['warn', 'Details needed'],
  };
  const [cls, label] = map[status ?? ''] ?? ['none', status ?? '—'];
  return <span className={`pill ${cls}`}>{label}</span>;
}

/** Load data for a component; returns [data, error, reload]. */
export function useLoad<T>(fn: () => Promise<T>, deps: unknown[]): [T | null, string | null, () => void] {
  const [data, setData] = useState<T | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    let live = true;
    setErr(null);
    fn()
      .then((d) => live && setData(d))
      .catch((e) => live && setErr(errorText(e)));
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);
  return [data, err, () => setTick((t) => t + 1)];
}
