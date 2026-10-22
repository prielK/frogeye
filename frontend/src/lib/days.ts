import { dayKey } from "./time";

/*
 * Calendar-day arithmetic on plain days ("2026-10-20", as the API sends them): one module and one
 * DAY for every page. Days are never shifted into a zone: a plain day
 * is its UTC midnight, so adding whole days never crosses a daylight-saving step.
 */

/** One day in milliseconds. */
export const DAY = 86_400_000;

const midnight = (day: string) => Date.parse(`${day}T00:00:00Z`);

/** `day` moved by `days` calendar days. */
export function shiftDay(day: string, days: number): string {
  return new Date(midnight(day) + days * DAY).toISOString().slice(0, 10);
}

/** Whole calendar days from `from` to `to` (negative when `to` is earlier). */
export function daysBetween(from: string, to: string): number {
  return Math.round((midnight(to) - midnight(from)) / DAY);
}

/** The app-zone calendar day of `now` ("2026-10-20"), the day the API's weeks end on. */
export function localToday(now: number): string {
  return dayKey(new Date(now).toISOString());
}
