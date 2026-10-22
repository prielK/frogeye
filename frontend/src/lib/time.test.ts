import { afterEach, describe, expect, it } from "vitest";
import { formatCount, formatPercent } from "./format";
import {
  appTimeZone,
  dayHeading,
  dayKey,
  formatAbsolute,
  formatClock,
  formatClockSeconds,
  formatDateline,
  formatLongDay,
  formatMoment,
  formatRelative,
  plainDay,
  setAppTimeZone,
} from "./time";

/*
 * every date and time renders in the app's time zone, delivered by the API (the
 * signed-in user's `timezone`, the backend's TIMEZONE setting), never the browser's. The
 * suite runs under TZ=UTC (vite.config.ts), so a formatter that slips back to the machine's
 * zone shows a different hour or day here.
 */

// 22:25 UTC on Sunday 18 October is 01:25 on Monday 19 October in Asia/Jerusalem (UTC+3).
const LATE = "2026-10-18T22:25:00Z";

afterEach(() => setAppTimeZone("Asia/Jerusalem"));

it("runs under UTC, so the app zone is what makes the difference", () => {
  expect(new Date(LATE).getTimezoneOffset()).toBe(0);
  expect(Intl.DateTimeFormat().resolvedOptions().timeZone).toBe("UTC");
});

describe("in the app's time zone", () => {
  it("reads the zone the API delivered", () => {
    setAppTimeZone("Asia/Jerusalem");
    expect(appTimeZone()).toBe("Asia/Jerusalem");
    setAppTimeZone("Not/AZone"); // an unknown name is ignored, never thrown
    expect(appTimeZone()).toBe("Asia/Jerusalem");
  });

  it("puts clock times and days on the app's calendar", () => {
    setAppTimeZone("Asia/Jerusalem");
    expect(formatClock(Date.parse(LATE))).toBe("01:25");
    expect(formatClockSeconds(Date.parse("2026-10-18T22:25:07Z"))).toBe("01:25:07");
    expect(dayKey(LATE)).toBe("2026-10-19");
    expect(formatLongDay(LATE)).toBe("Monday 19 October");
    expect(formatDateline(Date.parse(LATE))).toBe("Monday 19 October 2026");
    expect(formatAbsolute(LATE)).toMatch(/^Mon 19 Oct 2026, 01:25 \(.+\)$/);
  });

  it("names relative days by the app's midnight", () => {
    setAppTimeZone("Asia/Jerusalem");
    const now = Date.parse("2026-10-19T05:00:00Z"); // 08:00 Monday in Jerusalem
    expect(formatMoment(LATE, now)).toBe("Today 01:25"); // Sunday in UTC, Monday in Israel
    expect(formatMoment("2026-10-18T20:00:00Z", now)).toBe("Yesterday 23:00");
    expect(dayHeading(LATE, now)).toEqual({ name: "Today", date: "Monday 19 October" });
    expect(formatRelative("2026-10-17T12:00:00Z", now)).toBe("2d ago");
  });

  it("follows another zone when the backend is set to one", () => {
    setAppTimeZone("America/New_York");
    expect(formatClock(Date.parse(LATE))).toBe("18:25");
    expect(dayKey(LATE)).toBe("2026-10-18");
  });
});

describe("plain calendar dates", () => {
  it("never shift: a briefing date is a day, not an instant", () => {
    for (const zone of ["Asia/Jerusalem", "America/Los_Angeles", "Pacific/Kiritimati"]) {
      setAppTimeZone(zone);
      expect(plainDay("2026-10-19", "dateline")).toBe("Monday 19 October 2026");
      expect(plainDay("2026-10-19", "short")).toBe("Mon 19 Oct");
      expect(plainDay("2026-10-19", "weekdayDay")).toBe("Mon 19");
      expect(plainDay("2026-10-19", "weekday")).toBe("Mon");
      expect(plainDay("2026-10-19", "long")).toBe("Monday 19 Oct");
    }
  });
});

describe("numbers", () => {
  it("formats shares as percents, never rounding a sliver to nothing", () => {
    expect(formatPercent(0.934)).toBe("93%");
    expect(formatPercent(0.004)).toBe("<1%");
    expect(formatPercent(0)).toBe("0%");
    expect(formatPercent(1.04)).toBe("104%");
    expect(formatCount(5139)).toBe("5,139");
  });
});
