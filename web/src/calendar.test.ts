import { describe, expect, it } from 'vitest';
import { addDays, addMonths, dayLabel, monthGrid, monthLabel, mondayOf, timeLabel, weekday, zoned, zonedToIso } from './calendar';

describe('calendar dates in the doctor time zone', () => {
  it('converts instants to the local day and minute', () => {
    expect(zoned('2026-11-02T04:30:00Z', 'Asia/Kolkata')).toEqual({ day: '2026-11-02', min: 600 });
    expect(zoned('2026-11-01T20:00:00Z', 'Asia/Kolkata')).toEqual({ day: '2026-11-02', min: 90 }); // next local day
    expect(timeLabel('2026-11-02T04:30:00Z', 'Asia/Kolkata')).toBe('10:00');
  });

  it('counts weeks from Monday', () => {
    expect(weekday('2026-11-02')).toBe(0); // Monday
    expect(weekday('2026-11-08')).toBe(6); // Sunday
    expect(mondayOf('2026-11-05')).toBe('2026-11-02');
    expect(addDays('2026-12-30', 3)).toBe('2027-01-02');
    expect(dayLabel('2026-11-02')).toMatch(/Mon.*2.*Nov/);
  });
});

describe('calendar views', () => {
  it('turns a local day and minute back into an instant', () => {
    expect(zonedToIso('2026-11-02', 600, 'Asia/Kolkata')).toBe('2026-11-02T04:30:00.000Z');
    expect(zonedToIso('2026-11-02', 30, 'Asia/Kolkata')).toBe('2026-11-01T19:00:00.000Z');
    expect(zonedToIso('2026-07-01', 540, 'Europe/London')).toBe('2026-07-01T08:00:00.000Z'); // BST
  });

  it('lays out a month as 6 Monday-first weeks', () => {
    const g = monthGrid('2026-11-17');
    expect(g).toHaveLength(42);
    expect(g[0]).toBe('2026-10-26');
    expect(g).toContain('2026-11-30');
    expect(addMonths('2026-12-15', 1)).toBe('2027-01-01');
    expect(monthLabel('2026-11-17')).toBe('November 2026');
  });
});
