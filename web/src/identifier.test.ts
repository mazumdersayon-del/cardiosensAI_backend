import { describe, expect, it } from 'vitest';
import { parseIdentifier } from './api';

describe('parseIdentifier (sign-in "email or mobile number")', () => {
  it('reads email addresses', () => {
    expect(parseIdentifier('  Asha@Example.COM ')).toEqual({ kind: 'email', value: 'asha@example.com' });
    expect('error' in parseIdentifier('asha@example')).toBe(true);
  });
  it('reads mobile numbers: E.164, 00-prefixed, and Indian 10-digit or 0-prefixed', () => {
    expect(parseIdentifier('+91 98765-43210')).toEqual({ kind: 'phone', value: '+919876543210' });
    expect(parseIdentifier('9876543210')).toEqual({ kind: 'phone', value: '+919876543210' });
    expect(parseIdentifier('09876543210')).toEqual({ kind: 'phone', value: '+919876543210' });
    expect(parseIdentifier('0044 7700 900123')).toEqual({ kind: 'phone', value: '+447700900123' });
  });
  it('rejects anything else', () => {
    expect('error' in parseIdentifier('12345')).toBe(true);
    expect('error' in parseIdentifier('asha')).toBe(true);
  });
});
