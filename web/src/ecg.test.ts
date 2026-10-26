import { describe, expect, it } from 'vitest';
import { leadPath, type Trace } from './components/Ecg';

const leads = ['I', 'II'];
// 4 frames at 4 Hz: I = 0, 1000, 0, -1000 µV; II = 500 everywhere
const tr: Trace = { data: new Int16Array([0, 500, 1000, 500, 0, 500, -1000, 500]), leads, fs: 4, mmPerSec: 25, mmPerMv: 10 };

describe('leadPath', () => {
  it('reads the right channel from interleaved frames at 10 mm/mV', () => {
    const d = leadPath(tr, 'I', 0, 1, 0, 100);
    // 4 px per mm: 1 mV -> 40 px up; 25 mm/s -> 25 px per 0.25 s frame
    expect(d).toBe('M0.0,100.0L25.0,60.0L50.0,100.0L75.0,140.0');
    expect(leadPath(tr, 'II', 0, 1, 0, 100)).toBe('M0.0,80.0L25.0,80.0L50.0,80.0L75.0,80.0');
  });
  it('honours the time window, x offset and live buffers that start later', () => {
    expect(leadPath(tr, 'I', 0.5, 1, 200, 100)).toBe('M200.0,100.0L225.0,140.0');
    expect(leadPath({ ...tr, firstFrame: 8 }, 'I', 2, 3, 0, 100)).toBe('M0.0,100.0L25.0,60.0L50.0,100.0L75.0,140.0');
    expect(leadPath(tr, 'V1', 0, 1, 0, 100)).toBe('');
  });
});
