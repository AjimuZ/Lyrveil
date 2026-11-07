import { dayKey, formatClock, formatDayLabel } from './day-label';

describe('day labels', () => {
  const now = new Date('2026-11-07T12:00:00Z');

  it('names the weekday, day and month in Italian', () => {
    expect(formatDayLabel('2026-11-01T10:00:00Z', now, 'it-IT', 'Europe/Rome')).toBe('domenica 1 novembre');
  });

  it('adds the year only for another year', () => {
    expect(formatDayLabel('2025-11-01T10:00:00Z', now, 'it-IT', 'Europe/Rome')).toBe('sabato 1 novembre 2025');
  });

  it('puts two instants of one local day under one key and the next local day under another', () => {
    // 23:30 and 00:30 in Rome (UTC+1 in November) are on different days even though both are on 2026-11-01 in UTC.
    expect(dayKey('2026-11-01T22:30:00Z', 'Europe/Rome')).toBe('2026-11-01');
    expect(dayKey('2026-11-01T23:30:00Z', 'Europe/Rome')).toBe('2026-11-02');
    expect(dayKey('2026-11-01T09:00:00Z', 'Europe/Rome')).toBe(dayKey('2026-11-01T21:00:00Z', 'Europe/Rome'));
  });

  it('formats the clock in the zone and shows nothing for a bad date', () => {
    expect(formatClock('2026-11-01T09:05:00Z', 'it-IT', 'Europe/Rome')).toBe('10:05');
    expect(formatClock('nonsense', 'it-IT')).toBe('');
    expect(formatDayLabel('nonsense', now, 'it-IT')).toBe('');
    expect(dayKey('nonsense')).toBe('');
  });
});
