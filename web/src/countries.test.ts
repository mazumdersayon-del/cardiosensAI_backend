import { describe, expect, it } from 'vitest';
import { COUNTRIES, fromE164, phoneProblem, toE164 } from './countries';

describe('country list', () => {
  it('has unique ISO codes and digit-only dial codes', () => {
    expect(new Set(COUNTRIES.map((c) => c.iso)).size).toBe(COUNTRIES.length);
    expect(COUNTRIES.every((c) => /^[1-9]\d{0,3}$/.test(c.dial))).toBe(true);
  });
});

describe('toE164', () => {
  it('joins country code and national number', () => {
    expect(toE164('IN', '98123 45678')).toBe('+919812345678');
    expect(toE164('GB', '07700 900123')).toBe('+447700900123'); // trunk 0 dropped
    expect(toE164('US', '(212) 555-0100')).toBe('+12125550100');
  });
  it('handles a re-typed country code, NANP area codes, pasted numbers and empty input', () => {
    expect(toE164('IN', '91 98123 45678')).toBe('+919812345678');
    expect(toE164('JM', '876 555 0100')).toBe('+18765550100');
    expect(toE164('JM', '555 0100')).toBe('+18765550100');
    expect(toE164('IN', '+44 7700 900123')).toBe('+447700900123');
    expect(toE164('IN', '  ')).toBe('');
  });
});

describe('fromE164', () => {
  it('splits stored numbers, longest code first, shared codes to the usual country', () => {
    expect(fromE164('+913340001111')).toEqual({ iso: 'IN', national: '3340001111' });
    expect(fromE164('+18765550100')).toEqual({ iso: 'JM', national: '5550100' });
    expect(fromE164('+12125550100')).toEqual({ iso: 'US', national: '2125550100' });
    expect(fromE164('+12125550100', 'CA')).toEqual({ iso: 'CA', national: '2125550100' });
    expect(fromE164('+77012345678')).toEqual({ iso: 'RU', national: '7012345678' });
    expect(fromE164('')).toEqual({ iso: 'IN', national: '' });
  });
  it('round-trips every country', () => {
    for (const c of COUNTRIES) {
      const e164 = toE164(c.iso, '1234567');
      expect(fromE164(e164, c.iso)).toEqual({ iso: c.iso, national: '1234567' });
    }
  });
});

describe('phoneProblem', () => {
  it('enforces fixed lengths only where every number has that length', () => {
    expect(phoneProblem('IN', '98123 4567', false)).toMatch(/India numbers have 10 digits/);
    expect(phoneProblem('IN', '98123 45678', true)).toBeNull();
    expect(phoneProblem('BD', '2 5566 7788', true)).toBeNull(); // Dhaka landline: not fixed-length
  });
  it('requires a number only when asked, and keeps within E.164 limits', () => {
    expect(phoneProblem('IN', '', false)).toBeNull();
    expect(phoneProblem('IN', '', true)).toBe('Enter a phone number');
    expect(phoneProblem('DE', '12', true)).toMatch(/too few/);
    expect(phoneProblem('DE', '1234567890123456', true)).toMatch(/too many/);
  });
});
