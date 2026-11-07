// Formats the time a chat row shows: the hour today, "ieri" yesterday, and the date before that.

const DAY_MS = 24 * 60 * 60 * 1000;

function dayNumber(date: Date, timeZone: string | undefined): number {
  const parts = new Intl.DateTimeFormat('en-US', { timeZone, year: 'numeric', month: 'numeric', day: 'numeric' }).formatToParts(date);
  const value = (type: string): number => Number(parts.find((part) => part.type === type)?.value);
  return Date.UTC(value('year'), value('month') - 1, value('day')) / DAY_MS;
}

/**
 * Returns the short label of `iso` (an ISO 8601 instant) as seen from `now`, in `locale` and `timeZone` (the
 * browser's when undefined): `HH:mm` on the same day, the locale's word for yesterday, a day and month within the
 * same year, and a full date otherwise. Returns an empty string for text that is not a date, so a bad value shows
 * nothing instead of "Invalid Date".
 */
export function formatActivity(iso: string, now: Date, locale: string, timeZone?: string): string {
  const moment = new Date(iso);
  if (Number.isNaN(moment.getTime())) {
    return '';
  }
  const age = dayNumber(now, timeZone) - dayNumber(moment, timeZone);
  if (age === 0) {
    return new Intl.DateTimeFormat(locale, { timeZone, hour: '2-digit', minute: '2-digit' }).format(moment);
  }
  if (age === 1) {
    return new Intl.RelativeTimeFormat(locale, { numeric: 'auto' }).format(-1, 'day');
  }
  const sameYear = new Intl.DateTimeFormat('en-US', { timeZone, year: 'numeric' }).format(moment) ===
    new Intl.DateTimeFormat('en-US', { timeZone, year: 'numeric' }).format(now);
  return new Intl.DateTimeFormat(locale, {
    timeZone,
    day: 'numeric',
    month: 'short',
    year: sameYear ? undefined : 'numeric',
  }).format(moment);
}
