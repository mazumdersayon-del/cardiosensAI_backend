import { useEffect, useMemo, useRef, useState } from 'react';
import { api, type Appointment, type CalendarWeek, type WeeklyHours } from '../api';
import {
  addDays,
  addMonths,
  DAY_NAMES,
  dayLabel,
  minLabel,
  MODE_LABEL,
  monthGrid,
  monthLabel,
  monthOf,
  mondayOf,
  STATUS_LABEL,
  today,
  weekday,
  zoned,
  zonedToIso,
  zoneName,
} from '../calendar';
import { Banner, useLoad } from './ui';

/*
 * The doctor's calendar, laid out like Google Calendar: Day / Week / Month, an hour grid with working hours
 * white and closed hours grey, appointments as blocks, offline periods as hatched blocks, a red now line.
 * Free slots aren't drawn: empty working time is free. Clicking empty time adds a visit at that time.
 * Everything is in the doctor's time zone.
 */

export type CalView = 'day' | 'week' | 'month';

const HOUR_PX = 64;
const PX = HOUR_PX / 60;
const HIDDEN = new Set(['declined', 'cancelled', 'expired']); // not on the grid: the time is free again
const VIEW_KEY = 'cs.calView';

function savedView(narrow: boolean): CalView {
  if (narrow) return 'day';
  try {
    const v = localStorage.getItem(VIEW_KEY);
    if (v === 'day' || v === 'week' || v === 'month') return v;
  } catch {
    /* storage blocked */
  }
  return 'week';
}

export function ModeIcon({ mode, size = 12 }: { mode: Appointment['mode']; size?: number }) {
  return mode === 'online' ? (
    <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden="true" className="mode-icon">
      <path fill="currentColor" d="M4 6h11a2 2 0 0 1 2 2v1.5l4-2.5v10l-4-2.5V16a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2z" />
    </svg>
  ) : (
    <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden="true" className="mode-icon">
      <circle cx="12" cy="7" r="4" fill="currentColor" />
      <path fill="currentColor" d="M4 21a8 8 0 0 1 16 0z" />
    </svg>
  );
}

type Span = { from: number; to: number };
type Placed = { a: Appointment; from: number; to: number; lane: number; lanes: number };
type Off = Span & { reason: string | null; kind: string };

/** Minutes of a [start, end) instant range that fall on a local day. */
function clip(startsAt: string, endsAt: string, d: string, tz: string): Span | null {
  const a = zoned(startsAt, tz);
  const b = zoned(endsAt, tz);
  const from = a.day < d ? 0 : a.day === d ? a.min : 1440;
  const to = b.day > d ? 1440 : b.day === d ? b.min : 0;
  return to > from ? { from, to } : null;
}

/** Side by side when appointments overlap (walk-ins can). */
function lanes(rows: { a: Appointment; from: number; to: number }[]): Placed[] {
  const out: Placed[] = [];
  let group: Placed[] = [];
  let groupEnd = -1;
  const close = () => {
    const n = Math.max(1, ...group.map((g) => g.lane + 1));
    for (const g of group) g.lanes = n;
    out.push(...group);
    group = [];
  };
  for (const r of [...rows].sort((x, y) => x.from - y.from || y.to - x.to)) {
    if (r.from >= groupEnd && group.length) close();
    const busy = new Set(group.filter((g) => g.to > r.from).map((g) => g.lane));
    let lane = 0;
    while (busy.has(lane)) lane++;
    group.push({ ...r, lane, lanes: 1 });
    groupEnd = Math.max(groupEnd, r.to);
  }
  if (group.length) close();
  return out;
}

function openHours(hours: WeeklyHours[], d: string): Span[] {
  const wd = weekday(d);
  const m = (v: string) => Number(v.slice(0, 2)) * 60 + Number(v.slice(3, 5));
  return hours.filter((h) => h.day === wd).map((h) => ({ from: m(h.start), to: m(h.end) }));
}

function useNow(): Date {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 60_000);
    return () => clearInterval(t);
  }, []);
  return now;
}

export function CalendarBoard({
  tz,
  hours,
  slotMin,
  version,
  narrow,
  selectedId,
  onPick,
  onEmpty,
}: {
  tz: string;
  hours: WeeklyHours[];
  slotMin: number;
  version: number;
  narrow: boolean;
  selectedId: string | null;
  onPick: (a: Appointment) => void;
  onEmpty: (iso: string) => void;
}) {
  const [view, setViewState] = useState<CalView>(() => savedView(narrow));
  const [anchor, setAnchor] = useState(() => today(tz));
  const [picker, setPicker] = useState(false);
  const now = useNow();
  const nowZ = zoned(now, tz);
  const setView = (v: CalView) => {
    setViewState(v);
    try {
      localStorage.setItem(VIEW_KEY, v);
    } catch {
      /* storage blocked */
    }
  };

  const days = useMemo(() => {
    if (view === 'day') return [anchor];
    if (view === 'week') return Array.from({ length: 7 }, (_, i) => addDays(mondayOf(anchor), i));
    return monthGrid(anchor);
  }, [view, anchor]);
  const [data, err] = useLoad(() => api<CalendarWeek>('GET', `/v1/me/calendar/week?start=${days[0]}&days=${days.length}&slots=false`), [days[0], days.length, version]);

  const byDay = useMemo(() => {
    const m = new Map<string, { appts: { a: Appointment; from: number; to: number }[]; off: Off[] }>();
    for (const d of days) m.set(d, { appts: [], off: [] });
    for (const a of data?.appointments ?? []) {
      if (HIDDEN.has(a.status)) continue;
      for (const d of days) {
        const s = clip(a.startsAt, a.endsAt, d, tz);
        if (s) m.get(d)!.appts.push({ a, ...s });
      }
    }
    for (const p of data?.offlinePeriods ?? [])
      for (const d of days) {
        const s = clip(p.startsAt, p.endsAt, d, tz);
        if (s) m.get(d)!.off.push({ ...s, reason: p.reason, kind: p.kind });
      }
    return m;
  }, [data, days, tz]);

  const step = (n: number) => setAnchor(view === 'day' ? addDays(anchor, n) : view === 'week' ? addDays(anchor, 7 * n) : addMonths(anchor, n));
  const title =
    view === 'month'
      ? monthLabel(anchor)
      : view === 'day'
        ? new Date(`${anchor}T00:00:00Z`).toLocaleDateString('en-GB', { timeZone: 'UTC', weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' })
        : weekTitle(days[0], days[6]);

  return (
    <div className="gcal">
      <div className="gcal-bar">
        <button type="button" className="btn ghost small" onClick={() => setAnchor(today(tz))}>
          Today
        </button>
        <div className="row" style={{ gap: 2 }}>
          <button type="button" className="gcal-icon" aria-label={`Previous ${view}`} onClick={() => step(-1)}>
            ‹
          </button>
          <button type="button" className="gcal-icon" aria-label={`Next ${view}`} onClick={() => step(1)}>
            ›
          </button>
        </div>
        <div className="gcal-title-wrap">
          <button type="button" className="gcal-title" aria-expanded={picker} aria-haspopup="dialog" onClick={() => setPicker(!picker)}>
            {title} <span aria-hidden="true">▾</span>
          </button>
          {picker && (
            <MiniMonth
              tz={tz}
              selected={anchor}
              onPick={(d) => {
                setAnchor(d);
                setPicker(false);
              }}
              onClose={() => setPicker(false)}
            />
          )}
        </div>
        <div className="seg gcal-views" role="group" aria-label="View">
          {(['day', 'week', 'month'] as CalView[]).map((v) => (
            <button key={v} type="button" aria-pressed={view === v} onClick={() => setView(v)}>
              {v[0].toUpperCase() + v.slice(1)}
            </button>
          ))}
        </div>
      </div>
      {err && <Banner kind="error">{err}</Banner>}
      {view === 'month' ? (
        <MonthGrid
          days={days}
          month={monthOf(anchor)}
          todayDay={nowZ.day}
          byDay={byDay}
          tz={tz}
          selectedId={selectedId}
          onPick={onPick}
          onDay={(d) => {
            setAnchor(d);
            setView('day');
          }}
        />
      ) : (
        <TimeGrid
          days={days}
          hours={hours}
          slotMin={slotMin}
          nowZ={nowZ}
          byDay={byDay}
          tz={tz}
          selectedId={selectedId}
          onPick={onPick}
          onEmpty={onEmpty}
          onDay={(d) => {
            setAnchor(d);
            setView('day');
          }}
        />
      )}
      <div className="row gcal-legend muted small">
        <span>
          <i className="lg gev-accepted" /> accepted
        </span>
        <span>
          <i className="lg gev-requested" /> request
        </span>
        <span>
          <i className="lg gev-completed" /> completed
        </span>
        <span>
          <i className="lg gev-no_show" /> no-show
        </span>
        <span>
          <i className="lg offline" /> offline (in person only)
        </span>
        <span>
          <ModeIcon mode="online" /> video · <ModeIcon mode="in_person" /> in person
        </span>
        <span>Times in {zoneName(tz)}</span>
      </div>
    </div>
  );
}

function weekTitle(a: string, b: string): string {
  const fa = new Date(`${a}T00:00:00Z`);
  const fb = new Date(`${b}T00:00:00Z`);
  const mon = (d: Date) => d.toLocaleDateString('en-GB', { timeZone: 'UTC', month: 'short' });
  if (a.slice(0, 7) === b.slice(0, 7)) return `${fa.getUTCDate()} – ${fb.getUTCDate()} ${mon(fb)} ${fb.getUTCFullYear()}`;
  if (a.slice(0, 4) === b.slice(0, 4)) return `${fa.getUTCDate()} ${mon(fa)} – ${fb.getUTCDate()} ${mon(fb)} ${fb.getUTCFullYear()}`;
  return `${fa.getUTCDate()} ${mon(fa)} ${fa.getUTCFullYear()} – ${fb.getUTCDate()} ${mon(fb)} ${fb.getUTCFullYear()}`;
}

type DayData = Map<string, { appts: { a: Appointment; from: number; to: number }[]; off: Off[] }>;

function TimeGrid({
  days,
  hours,
  slotMin,
  nowZ,
  byDay,
  tz,
  selectedId,
  onPick,
  onEmpty,
  onDay,
}: {
  days: string[];
  hours: WeeklyHours[];
  slotMin: number;
  nowZ: { day: string; min: number };
  byDay: DayData;
  tz: string;
  selectedId: string | null;
  onPick: (a: Appointment) => void;
  onEmpty: (iso: string) => void;
  onDay: (d: string) => void;
}) {
  const body = useRef<HTMLDivElement>(null);
  const first = days[0];
  // Open at the start of the working day (or 08:00), like Google opens near the current time.
  useEffect(() => {
    const starts = days.flatMap((d) => openHours(hours, d).map((h) => h.from));
    const at = starts.length ? Math.min(...starts) : 8 * 60;
    if (body.current) body.current.scrollTop = Math.max(0, (at - 30) * PX);
  }, [first, days.length]);
  const cols = `56px repeat(${days.length}, minmax(0, 1fr))`;
  return (
    <div className="gcal-grid">
      <div className="gcal-head" style={{ gridTemplateColumns: cols }}>
        <div className="gcal-tz">{zoneName(tz)}</div>
        {days.map((d) => (
          <button key={d} type="button" className={`gcal-dayhead ${d === nowZ.day ? 'today' : ''} ${d < nowZ.day ? 'past' : ''}`} onClick={() => onDay(d)} aria-label={`Open ${dayLabel(d)}`}>
            <span className="dow">{DAY_NAMES[weekday(d)]}</span>
            <span className="num">{Number(d.slice(8))}</span>
          </button>
        ))}
      </div>
      <div className="gcal-body" ref={body}>
        <div className="gcal-inner" style={{ gridTemplateColumns: cols, height: 1440 * PX }}>
          <div className="gcal-gutter">
            {Array.from({ length: 23 }, (_, i) => (i + 1) * 60).map((m) => (
              <span key={m} style={{ top: m * PX }}>
                {minLabel(m)}
              </span>
            ))}
          </div>
          {days.map((d) => {
            const col = byDay.get(d);
            return (
              <div
                key={d}
                className="gcal-col"
                role="presentation"
                onClick={(e) => {
                  if (e.target !== e.currentTarget && !(e.target as HTMLElement).classList.contains('gcal-open')) return;
                  const y = e.clientY - e.currentTarget.getBoundingClientRect().top;
                  const min = Math.max(0, Math.min(1440 - slotMin, Math.floor(y / PX / slotMin) * slotMin));
                  onEmpty(zonedToIso(d, min, tz));
                }}
              >
                {openHours(hours, d).map((h, i) => (
                  <div key={i} className="gcal-open" style={{ top: h.from * PX, height: (h.to - h.from) * PX }} />
                ))}
                {col?.off.map((o, i) => (
                  <div key={`o${i}`} className="gcal-offline" style={{ top: o.from * PX, height: (o.to - o.from) * PX }}>
                    <span>{o.kind === 'now' ? 'Offline' : `Offline${o.reason ? ` · ${o.reason}` : ''}`}</span>
                  </div>
                ))}
                {lanes(col?.appts ?? []).map(({ a, from, to, lane, lanes: n }) => {
                  const h = Math.max(22, (to - from) * PX - 2);
                  return (
                    <button
                      key={`${a.id}${d}`}
                      type="button"
                      className={`gcal-ev gev-${a.status} ${a.id === selectedId ? 'selected' : ''} ${h < 36 ? 'short' : ''}`}
                      style={{ top: from * PX + 1, height: h, left: `calc(${(lane / n) * 100}% + 2px)`, width: `calc(${100 / n}% - 6px)` }}
                      onClick={() => onPick(a)}
                      title={`${a.patient.fullName} · ${minLabel(from)}–${minLabel(to % 1440)} · ${MODE_LABEL[a.mode]} · ${STATUS_LABEL[a.status]}`}
                      aria-label={`${a.patient.fullName}, ${dayLabel(d)} ${minLabel(from)}, ${MODE_LABEL[a.mode]}, ${STATUS_LABEL[a.status]}`}
                    >
                      <span className="name">
                        <ModeIcon mode={a.mode} /> {a.status === 'requested' ? 'Request: ' : ''}
                        {a.patient.fullName}
                      </span>
                      <span className="time">
                        {minLabel(from)} – {minLabel(to % 1440)}
                      </span>
                    </button>
                  );
                })}
                {d === nowZ.day && <div className="gcal-now" style={{ top: nowZ.min * PX }} aria-hidden="true" />}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

function MonthGrid({
  days,
  month,
  todayDay,
  byDay,
  tz,
  selectedId,
  onPick,
  onDay,
}: {
  days: string[];
  month: string;
  todayDay: string;
  byDay: DayData;
  tz: string;
  selectedId: string | null;
  onPick: (a: Appointment) => void;
  onDay: (d: string) => void;
}) {
  const MAX = 3;
  return (
    <div className="gcal-month">
      {DAY_NAMES.map((n) => (
        <div key={n} className="gcal-mhead">
          {n}
        </div>
      ))}
      {days.map((d) => {
        const col = byDay.get(d);
        const rows = [...(col?.appts ?? [])].sort((x, y) => x.from - y.from);
        const off = col?.off ?? [];
        return (
          <div key={d} className={`gcal-cell ${d.slice(0, 7) !== month.slice(0, 7) ? 'other' : ''}`} onClick={(e) => e.target === e.currentTarget && onDay(d)} role="presentation">
            <button type="button" className={`gcal-mnum ${d === todayDay ? 'today' : ''}`} onClick={() => onDay(d)} aria-label={`Open ${dayLabel(d)}`}>
              {Number(d.slice(8))}
            </button>
            {off.length > 0 && (
              <div className="gcal-moff" title={off.map((o) => `${minLabel(o.from)}–${minLabel(o.to % 1440)}${o.reason ? ` ${o.reason}` : ''}`).join(', ')}>
                Offline {off[0].from === 0 && off[0].to === 1440 ? 'all day' : `${minLabel(off[0].from)}–${minLabel(off[0].to % 1440)}`}
              </div>
            )}
            {rows.slice(0, MAX).map(({ a, from }) => (
              <button key={a.id} type="button" className={`gcal-mev gev-dot-${a.status} ${a.id === selectedId ? 'selected' : ''}`} onClick={() => onPick(a)} title={`${a.patient.fullName} · ${MODE_LABEL[a.mode]} · ${STATUS_LABEL[a.status]}`}>
                <i aria-hidden="true" />
                <span className="t">{minLabel(from)}</span> {a.patient.fullName}
              </button>
            ))}
            {rows.length > MAX && (
              <button type="button" className="gcal-more" onClick={() => onDay(d)}>
                {rows.length - MAX} more
              </button>
            )}
          </div>
        );
      })}
      <span className="sr-only">Times in {tz}</span>
    </div>
  );
}

/** A small month for jumping to a date. */
function MiniMonth({ tz, selected, onPick, onClose }: { tz: string; selected: string; onPick: (d: string) => void; onClose: () => void }) {
  const [month, setMonth] = useState(() => monthOf(selected));
  const ref = useRef<HTMLDivElement>(null);
  const t = today(tz);
  useEffect(() => {
    const away = (e: MouseEvent) => ref.current && !ref.current.contains(e.target as Node) && onClose();
    const esc = (e: KeyboardEvent) => e.key === 'Escape' && onClose();
    document.addEventListener('mousedown', away);
    document.addEventListener('keydown', esc);
    return () => {
      document.removeEventListener('mousedown', away);
      document.removeEventListener('keydown', esc);
    };
  }, [onClose]);
  return (
    <div className="mini-month" role="dialog" aria-label="Pick a date" ref={ref}>
      <div className="row spread">
        <strong>{monthLabel(month)}</strong>
        <div className="row" style={{ gap: 2 }}>
          <button type="button" className="gcal-icon" aria-label="Previous month" onClick={() => setMonth(addMonths(month, -1))}>
            ‹
          </button>
          <button type="button" className="gcal-icon" aria-label="Next month" onClick={() => setMonth(addMonths(month, 1))}>
            ›
          </button>
        </div>
      </div>
      <div className="mini-grid">
        {DAY_NAMES.map((n) => (
          <span key={n} className="muted">
            {n[0]}
          </span>
        ))}
        {monthGrid(month).map((d) => (
          <button
            key={d}
            type="button"
            className={`${d.slice(0, 7) !== month.slice(0, 7) ? 'other' : ''} ${d === t ? 'today' : ''} ${d === selected ? 'sel' : ''}`}
            onClick={() => onPick(d)}
            aria-label={dayLabel(d)}
          >
            {Number(d.slice(8))}
          </button>
        ))}
      </div>
    </div>
  );
}
