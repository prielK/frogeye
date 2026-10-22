import { useSyncExternalStore } from "react";

/*
 * Time as readers see it: relative for recency ("3h ago"), absolute one hover away.
 * Every date and time is shown in the APP's time zone, the backend's TIMEZONE setting (the
 * day briefings are filed under, budgets reset on and the schedule runs by), delivered by
 * the API on the signed-in user (`UserOut.timezone`, set here by lib/auth). A reader abroad
 * then sees the same "today" as the briefing and the cost chart. Until the API has said
 * (the sign-in page), the browser's own zone stands in.
 *
 * Two kinds of value: an instant (an ISO timestamp, a run's start) is placed on the app's
 * calendar; a plain date ("2026-10-17", a briefing's or a cost day's) is already a calendar
 * day and is never shifted (`plainDay`). A shared ticker per interval keeps every relative
 * label on screen in step.
 */

type Clock = { subscribe: (listener: () => void) => () => void; read: () => number };
const clocks = new Map<number, Clock>();

/** One shared interval per tick rate; it runs only while something on screen listens. */
function sharedClock(intervalMs: number): Clock {
  const existing = clocks.get(intervalMs);
  if (existing) return existing;
  let now = Date.now();
  let timer: ReturnType<typeof setInterval> | null = null;
  const listeners = new Set<() => void>();
  const created: Clock = {
    subscribe(listener) {
      listeners.add(listener);
      if (timer === null) {
        // Waking from idle: catch up once (React re-reads the snapshot after subscribing).
        now = Date.now();
        timer = setInterval(() => {
          now = Date.now();
          for (const l of listeners) l();
        }, intervalMs);
      }
      return () => {
        listeners.delete(listener);
        if (listeners.size === 0 && timer !== null) {
          clearInterval(timer);
          timer = null;
        }
      };
    },
    read: () => now,
  };
  clocks.set(intervalMs, created);
  return created;
}

/** The current time, re-rendering the caller every `intervalMs` while it is mounted. */
export function useNow(intervalMs = 60_000): number {
  const { subscribe, read } = sharedClock(intervalMs);
  return useSyncExternalStore(subscribe, read, read);
}

// ---- the app's time zone ------------------------------------------------------------

let zone: string | undefined;
const formats = new Map<string, Intl.DateTimeFormat>();

function isZone(name: string): boolean {
  try {
    new Intl.DateTimeFormat("en-GB", { timeZone: name });
    return true;
  } catch {
    return false;
  }
}

/** Show every date and time in `name` (an IANA zone from the API); an unknown name is ignored. */
export function setAppTimeZone(name: string | null | undefined) {
  if (!name || name === zone || !isZone(name)) return;
  zone = name;
  formats.clear();
}

/** The zone dates are shown in: the app's once the API has said, else the browser's. */
export function appTimeZone(): string {
  return zone ?? Intl.DateTimeFormat().resolvedOptions().timeZone;
}

/** A cached en-GB formatter in the app's zone (`plain`: a calendar date, always UTC). */
function format(options: Intl.DateTimeFormatOptions, plain = false): Intl.DateTimeFormat {
  const timeZone = plain ? "UTC" : appTimeZone();
  const key = `${timeZone}|${JSON.stringify(options)}`;
  let found = formats.get(key);
  if (!found) {
    found = new Intl.DateTimeFormat("en-GB", { ...options, timeZone });
    formats.set(key, found);
  }
  return found;
}

function parts(options: Intl.DateTimeFormatOptions, date: Date, plain = false): Record<string, string> {
  return Object.fromEntries(format(options, plain).formatToParts(date).map((p) => [p.type, p.value]));
}

const MINUTE = 60_000;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

const YMD: Intl.DateTimeFormatOptions = { year: "numeric", month: "2-digit", day: "2-digit" };
const CLOCK: Intl.DateTimeFormatOptions = { hour: "2-digit", minute: "2-digit", hourCycle: "h23" };

/** The app-zone calendar day of `ms` as a UTC midnight, so days subtract cleanly. */
function calendarDay(ms: number): number {
  const p = parts(YMD, new Date(ms));
  return Date.UTC(Number(p.year), Number(p.month) - 1, Number(p.day));
}

/** Days between the app-zone calendar dates of `ms` and `now` (0 = same day, 1 = yesterday). */
function calendarDaysAgo(ms: number, now: number) {
  return Math.round((calendarDay(now) - calendarDay(ms)) / DAY);
}

/** "just now", "4m ago", "3h ago", "Yesterday", "3d ago", then a date. */
export function formatRelative(iso: string, now: number): string {
  const ms = Date.parse(iso);
  const diff = now - ms;
  if (diff < 45_000) return diff > -MINUTE ? "just now" : formatDay(ms, now);
  if (diff < HOUR) return `${Math.max(1, Math.round(diff / MINUTE))}m ago`;
  const days = calendarDaysAgo(ms, now);
  if (diff < 12 * HOUR || days === 0) return `${Math.round(diff / HOUR)}h ago`;
  if (days === 1) return "Yesterday";
  if (days < 7) return `${days}d ago`;
  return formatDay(ms, now);
}

function formatDay(ms: number, now: number) {
  const p = parts({ day: "numeric", month: "short", year: "numeric" }, new Date(ms));
  const sameYear = p.year === parts({ year: "numeric" }, new Date(now)).year;
  return sameYear ? `${p.day} ${p.month}` : `${p.day} ${p.month} ${p.year}`;
}

/** Wall-clock time of day, "14:05". */
export function formatClock(ms: number): string {
  const p = parts(CLOCK, new Date(ms));
  return `${p.hour}:${p.minute}`;
}

/** "14:05:09": a step's time in a run trace. */
export function formatClockSeconds(ms: number): string {
  const p = parts({ ...CLOCK, second: "2-digit" }, new Date(ms));
  return `${p.hour}:${p.minute}:${p.second}`;
}

/** The zone's name for readers, e.g. "Israel Time" (else its IANA city, "Jerusalem time"). */
export function zoneLabel(): string {
  try {
    const name = format({ timeZoneName: "longGeneric" }).formatToParts(new Date()).find((p) => p.type === "timeZoneName")?.value;
    if (name && !/^GMT/.test(name)) return name;
  } catch {
    // an engine without longGeneric names: fall through
  }
  const city = appTimeZone().split("/").pop()?.replace(/_/g, " ");
  return city ? `${city} time` : appTimeZone();
}

/** "Sat 17 Oct 2026, 08:42 (Israel Time)", built from parts so locale punctuation never leaks in. */
export function formatAbsolute(iso: string): string {
  const p = parts({ weekday: "short", day: "numeric", month: "short", year: "numeric", ...CLOCK }, new Date(iso));
  return `${p.weekday} ${p.day} ${p.month} ${p.year}, ${p.hour}:${p.minute} (${zoneLabel()})`;
}

/** "Today 08:00", "Yesterday 06:00", "Thu 15 Oct 06:00": a run's start, scannable down a column. */
export function formatMoment(iso: string, now: number): string {
  const ms = Date.parse(iso);
  const time = formatClock(ms);
  const days = calendarDaysAgo(ms, now);
  if (days === 0) return `Today ${time}`;
  if (days === 1) return `Yesterday ${time}`;
  const d = parts({ weekday: "short", day: "numeric", month: "short" }, new Date(ms));
  return `${d.weekday} ${d.day} ${d.month} ${time}`;
}

/** "Sunday 18 October": the app-zone day of an instant (a run's title). */
export function formatLongDay(iso: string): string {
  const p = parts({ weekday: "long", day: "numeric", month: "long" }, new Date(iso));
  return `${p.weekday} ${p.day} ${p.month}`;
}

/** "Saturday 17 October 2026": the newspaper dateline of an instant (today, on sign-in). */
export function formatDateline(ms: number): string {
  const p = parts({ weekday: "long", day: "numeric", month: "long", year: "numeric" }, new Date(ms));
  return `${p.weekday} ${p.day} ${p.month} ${p.year}`;
}

/** An app-zone calendar day key ("2026-10-17") for grouping and for "today", or "undated". */
export function dayKey(iso: string | null): string {
  if (!iso) return "undated";
  const p = parts(YMD, new Date(iso));
  return `${p.year}-${p.month}-${p.day}`;
}

/** Heading for a day of documents: a relative name when it has one, and the full date. */
export function dayHeading(iso: string, now: number): { name: string | null; date: string } {
  const ms = Date.parse(iso);
  const days = calendarDaysAgo(ms, now);
  const p = parts({ weekday: "long", day: "numeric", month: "long", year: "numeric" }, new Date(ms));
  const date = `${p.weekday} ${p.day} ${p.month}`;
  const sameYear = p.year === parts({ year: "numeric" }, new Date(now)).year;
  const dated = sameYear ? date : `${date} ${p.year}`;
  if (days === 0) return { name: "Today", date: dated };
  if (days === 1) return { name: "Yesterday", date: dated };
  return { name: null, date: dated };
}

// ---- plain calendar dates ("2026-10-17"): a day, never an instant ---------------------------

const PLAIN: Record<"dateline" | "date" | "long" | "short" | "weekdayDay" | "weekday" | "dayMonth" | "monthYear", Intl.DateTimeFormatOptions> = {
  dateline: { weekday: "long", day: "numeric", month: "long", year: "numeric" },
  date: { day: "numeric", month: "short", year: "numeric" },
  long: { weekday: "long", day: "numeric", month: "short" },
  short: { weekday: "short", day: "numeric", month: "short" },
  weekdayDay: { weekday: "short", day: "numeric" },
  weekday: { weekday: "short" },
  dayMonth: { day: "numeric", month: "short" },
  monthYear: { month: "short", year: "numeric" },
};

/**
 * A plain date as words, the same in every zone: "dateline" "Saturday 17 October 2026",
 * "date" "17 Oct 2026", "long" "Saturday 17 Oct", "short" "Sat 17 Oct", "weekdayDay" "Sat 17", "weekday" "Sat",
 * and for chart axes "dayMonth" "17 Oct" and "monthYear" "Oct 2026".
 */
export function plainDay(date: string, style: keyof typeof PLAIN): string {
  const [y, m, d] = date.split("-").map(Number);
  const p = parts(PLAIN[style], new Date(Date.UTC(y!, (m ?? 1) - 1, d ?? 1)), true);
  return [p.weekday, p.day, p.month, p.year].filter(Boolean).join(" ");
}

/** "17 Oct 2026": the app-zone calendar day of an instant (a source's publication date). */
export function formatCalendarDay(iso: string): string {
  return plainDay(dayKey(iso), "date");
}

/** "38s", "4m 12s", "1h 03m". */
export function formatDuration(ms: number): string {
  const total = Math.max(0, Math.round(ms / 1000));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  if (h > 0) return `${h}h ${String(m).padStart(2, "0")}m`;
  if (m > 0) return `${m}m ${String(s).padStart(2, "0")}s`;
  return `${s}s`;
}
