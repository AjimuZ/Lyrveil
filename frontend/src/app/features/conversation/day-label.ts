// Day separators and clock times of the conversation, in the interface locale and the browser's time zone.

const formatters = new Map<string, Intl.DateTimeFormat>();

/**
 * Returns the formatter for `options`, built once per locale, zone and options. Building an `Intl.DateTimeFormat` costs
 * far more than using one, and a chat of thousands of messages formats each of them on every change of the list.
 */
function formatter(locale: string, timeZone: string | undefined, options: Intl.DateTimeFormatOptions): Intl.DateTimeFormat {
  const key = `${locale}|${timeZone ?? ''}|${JSON.stringify(options)}`;
  let found = formatters.get(key);
  if (found === undefined) {
    found = new Intl.DateTimeFormat(locale, { timeZone, ...options });
    formatters.set(key, found);
  }
  return found;
}

function parts(date: Date, timeZone: string | undefined): { year: string; month: string; day: string } {
  const found = formatter('en-US', timeZone, { year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(date);
  const value = (type: string): string => found.find((part) => part.type === type)?.value ?? '';
  return { year: value('year'), month: value('month'), day: value('day') };
}

/**
 * Returns `YYYY-MM-DD` of the day `iso` falls on in `timeZone` (the browser's when undefined), so two messages share a
 * separator exactly when they share this key. Returns an empty string for text that is not a date.
 */
export function dayKey(iso: string, timeZone?: string): string {
  const moment = new Date(iso);
  if (Number.isNaN(moment.getTime())) {
    return '';
  }
  const { year, month, day } = parts(moment, timeZone);
  return `${year}-${month}-${day}`;
}

/**
 * Returns the separator text of the day `iso` falls on: weekday, day and month such as `sabato 1 novembre`, with the
 * year added when it is not the year of `now`. Returns an empty string for text that is not a date.
 */
export function formatDayLabel(iso: string, now: Date, locale: string, timeZone?: string): string {
  const moment = new Date(iso);
  if (Number.isNaN(moment.getTime())) {
    return '';
  }
  const sameYear = parts(moment, timeZone).year === parts(now, timeZone).year;
  return formatter(locale, timeZone, {
    weekday: 'long',
    day: 'numeric',
    month: 'long',
    year: sameYear ? undefined : 'numeric',
  }).format(moment);
}

/** Returns `HH:mm` of `iso` in `locale` and `timeZone`, or an empty string for text that is not a date. */
export function formatClock(iso: string, locale: string, timeZone?: string): string {
  const moment = new Date(iso);
  if (Number.isNaN(moment.getTime())) {
    return '';
  }
  return formatter(locale, timeZone, { hour: '2-digit', minute: '2-digit' }).format(moment);
}
