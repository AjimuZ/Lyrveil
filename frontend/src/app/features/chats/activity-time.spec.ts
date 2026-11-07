import { formatActivity } from './activity-time';

const NOW = new Date('2026-11-07T15:30:00.000Z');

describe('formatActivity', () => {
  it('shows the hour for today, the word for yesterday and the date before that', () => {
    expect(formatActivity('2026-11-07T09:05:00.000Z', NOW, 'it-IT', 'UTC')).toBe('09:05');
    expect(formatActivity('2026-11-06T23:59:00.000Z', NOW, 'it-IT', 'UTC')).toBe('ieri');
    expect(formatActivity('2026-11-01T10:00:00.000Z', NOW, 'it-IT', 'UTC')).toBe('1 nov');
    expect(formatActivity('2025-12-24T10:00:00.000Z', NOW, 'it-IT', 'UTC')).toBe('24 dic 2025');
  });

  it('counts days in the given time zone, not in UTC', () => {
    // 23:30 UTC on the 6th is already the 7th in Rome (UTC+1 in November).
    expect(formatActivity('2026-11-06T23:30:00.000Z', NOW, 'it-IT', 'Europe/Rome')).toBe('00:30');
  });

  it('returns an empty label for text that is not a date', () => {
    expect(formatActivity('not a date', NOW, 'it-IT', 'UTC')).toBe('');
  });
});
