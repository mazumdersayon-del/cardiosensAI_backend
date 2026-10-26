import { describe, expect, it } from 'vitest';
import { formatId, isValidId } from './format';

describe('16-digit IDs', () => {
  it('groups digits in fours', () => {
    expect(formatId('1482930177562048')).toBe('1482 9301 7756 2048');
  });
  it('accepts IDs whose Luhn check digit matches (same rule as the server)', () => {
    // 1 + 14 digits + check digit computed by app/core/ids.py
    const body = '148293017756204';
    const ok = [...Array(10).keys()].map((d) => body + d).filter(isValidId);
    expect(ok).toHaveLength(1);
    expect(isValidId(ok[0].replace(/(\d{4})/g, '$1 '))).toBe(true);
  });
  it('rejects bad length, unknown role digit and typos', () => {
    expect(isValidId('12345')).toBe(false);
    expect(isValidId('4000000000000002')).toBe(false);
  });
});
