/* Dates for the appointment calendar, in the doctor's time zone (CalendarSettings.timezone), not the browser's.
   Days are 'YYYY-MM-DD' strings; minutes count from local midnight. Mirrors services/appointments.py. */

export const DAY_NAMES = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
export const DAY_FULL = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];

const fmtCache = new Map<string, Intl.DateTimeFormat>();
function fmt(tz: string): Intl.DateTimeFormat {
  let f = fmtCache.get(tz);
  if (!f) {
    f = new Intl.DateTimeFormat('en-CA', { timeZone: tz, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' });
    fmtCache.set(tz, f);
  }
  return f;
}

/** The local day and minute of an instant in a time zone. */
export function zoned(at: string | Date, tz: string): { day: string; min: number } {
  const p: Record<string, string> = {};
  for (const x of fmt(tz).formatToParts(new Date(at))) p[x.type] = x.value;
  return { day: `${p.year}-${p.month}-${p.day}`, min: Number(p.hour) * 60 + Number(p.minute) };
}

export function addDays(day: string, n: number): string {
  const [y, m, d] = day.split('-').map(Number);
  return new Date(Date.UTC(y, m - 1, d + n)).toISOString().slice(0, 10);
}

/** 0 = Monday … 6 = Sunday (as the server's weekly hours). */
export function weekday(day: string): number {
  const [y, m, d] = day.split('-').map(Number);
  return (new Date(Date.UTC(y, m - 1, d)).getUTCDay() + 6) % 7;
}

export function mondayOf(day: string): string {
  return addDays(day, -weekday(day));
}

/** The instant of a local day + minute in a time zone (inverse of zoned; DST gaps land on the later side). */
export function zonedToIso(day: string, min: number, tz: string): string {
  const [y, m, d] = day.split('-').map(Number);
  const want = Date.UTC(y, m - 1, d) + min * 60_000;
  let t = want;
  for (let i = 0; i < 3; i++) {
    const z = zoned(new Date(t), tz);
    const [zy, zm, zd] = z.day.split('-').map(Number);
    const diff = want - (Date.UTC(zy, zm - 1, zd) + z.min * 60_000);
    if (diff === 0) break;
    t += diff;
  }
  return new Date(t).toISOString();
}

/** The first of the month of a day. */
export function monthOf(day: string): string {
  return `${day.slice(0, 7)}-01`;
}

/** The first of the month n months away. */
export function addMonths(day: string, n: number): string {
  const [y, m] = day.split('-').map(Number);
  return new Date(Date.UTC(y, m - 1 + n, 1)).toISOString().slice(0, 10);
}

/** 6 weeks of days (Monday first) covering the month of a day. */
export function monthGrid(day: string): string[] {
  const first = mondayOf(monthOf(day));
  return Array.from({ length: 42 }, (_, i) => addDays(first, i));
}

/** 'November 2026' */
export function monthLabel(day: string): string {
  const [y, m] = day.split('-').map(Number);
  return new Date(Date.UTC(y, m - 1, 1)).toLocaleDateString('en-GB', { timeZone: 'UTC', month: 'long', year: 'numeric' });
}

export function today(tz: string): string {
  return zoned(new Date(), tz).day;
}

/** 'Thu 30 Oct' */
export function dayLabel(day: string): string {
  const [y, m, d] = day.split('-').map(Number);
  return new Date(Date.UTC(y, m - 1, d)).toLocaleDateString('en-GB', { timeZone: 'UTC', weekday: 'short', day: 'numeric', month: 'short' });
}

/** '10:30' in the time zone */
export function timeLabel(at: string, tz: string): string {
  const { min } = zoned(at, tz);
  return minLabel(min);
}

export function minLabel(min: number): string {
  return `${String(Math.floor(min / 60)).padStart(2, '0')}:${String(min % 60).padStart(2, '0')}`;
}

/** 'Thu 30 Oct, 10:30' in the time zone */
export function whenLabel(at: string, tz: string): string {
  const z = zoned(at, tz);
  return `${dayLabel(z.day)}, ${minLabel(z.min)}`;
}

/** Short zone name for labels, e.g. 'GMT+5:30' or 'IST'. */
export function zoneName(tz: string, at: Date = new Date()): string {
  const p = new Intl.DateTimeFormat('en-GB', { timeZone: tz, timeZoneName: 'short' }).formatToParts(at);
  return p.find((x) => x.type === 'timeZoneName')?.value ?? tz;
}

/** A 'datetime-local' input value (browser time) → ISO with offset; '' → null. */
export function localInputToIso(v: string): string | null {
  if (!v) return null;
  const d = new Date(v);
  return Number.isNaN(d.getTime()) ? null : d.toISOString();
}

/** Now + minutes as a 'datetime-local' value (browser time). */
export function localInputIn(minutes: number): string {
  const d = new Date(Date.now() + minutes * 60_000);
  d.setSeconds(0, 0);
  return new Date(d.getTime() - d.getTimezoneOffset() * 60_000).toISOString().slice(0, 16);
}

export const STATUS_LABEL: Record<string, string> = {
  requested: 'Requested',
  accepted: 'Accepted',
  declined: 'Declined',
  cancelled: 'Cancelled',
  expired: 'Expired',
  completed: 'Completed',
  no_show: 'No-show',
};

export const STATUS_TONE: Record<string, string> = {
  requested: 'warn',
  accepted: 'ok',
  completed: 'info',
  declined: 'none',
  cancelled: 'none',
  expired: 'none',
  no_show: 'error',
};

export const MODE_LABEL = { online: 'Online', in_person: 'In person' } as const;
