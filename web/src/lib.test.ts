import { beforeEach, describe, expect, it } from "vitest";
import {
  clamp,
  defaultWatchDate,
  formatTime,
  getSeekInterval,
  parseLibrary,
  positionToSpeed,
  resumeKey,
  setStored,
  speedToPosition,
  todayIn,
  validDate,
} from "./lib";
import type { Day, Library } from "./types";

describe("playback controls", () => {
  beforeEach(() => localStorage.clear());
  it("round-trips every speed without floating point drift", () => {
    for (let tenths = 5; tenths <= 40; tenths++)
      expect(positionToSpeed(speedToPosition(tenths))).toBe(tenths);
    expect(speedToPosition(5)).toBe(0);
    expect(speedToPosition(15)).toBe(60);
    expect(speedToPosition(40)).toBe(100);
  });
  it("stores one browser-wide skip interval and rejects invalid saved values", () => {
    expect(getSeekInterval()).toBe(5);
    setStored("timelapse.seekSeconds", "9");
    expect(getSeekInterval()).toBe(9);
    for (const value of ["0", "121", "nan", "1.5"]) {
      setStored("timelapse.seekSeconds", value);
      expect(getSeekInterval()).toBe(5);
    }
  });
  it("bounds seeking and formats long durations", () => {
    expect(clamp(-5, 0, 12)).toBe(0);
    expect(clamp(40, 0, 12)).toBe(12);
    expect(formatTime(3661)).toBe("1:01:01");
    expect(formatTime(Infinity)).toBe("00:00");
  });
  it("keeps positions isolated between regenerated sessions", () => {
    const day = { date: "2026-11-06", generation_id: "one" } as Day;
    expect(resumeKey("cam", day)).not.toBe(
      resumeKey("cam", { ...day, generation_id: "two" }),
    );
  });
  it("uses the application timezone across midnight and validates actual dates", () => {
    expect(todayIn("Europe/Helsinki", new Date("2026-11-06T22:01:00Z"))).toBe(
      "2026-11-07",
    );
    expect(validDate("2026-02-30")).toBe(false);
    expect(validDate("2024-02-29")).toBe(true);
  });
  it("rejects unsupported metadata without falling through to media requests", () => {
    expect(() => parseLibrary({ version: 2, days: [] })).toThrow();
  });
});

describe("default watch day", () => {
  const day = (date: string, overrides: Partial<Day> = {}): Day => ({
    date,
    generation_id: date,
    poster_url: null,
    playlist_url: `/published/cam/${date}/index.m3u8`,
    live_playlist_url: `/published/cam/${date}/live.m3u8`,
    duration: 12,
    ready: true,
    status: "completed",
    mode: "completed",
    complete: true,
    stream_ended: true,
    published_at: null,
    schedule_start: null,
    schedule_end: null,
    render_speed: 288,
    ...overrides,
  });
  const library = (days: Day[]): Library => ({
    version: 1,
    camera: "cam",
    timezone: "Europe/Helsinki",
    schedule: { start: "08:00:00", end: "20:00:00" },
    thumbnail_offset_seconds: 0,
    published_at: "2026-11-06T20:00:00+02:00",
    days,
  });
  const midnight = new Date("2026-11-06T22:01:00Z");

  it("keeps yesterday after local midnight and ignores future or unready days", () => {
    const value = library([
      day("2026-11-05"),
      day("2026-11-07", { ready: false, status: "unknown" }),
      day("2026-11-08"),
      day("2026-11-06"),
    ]);
    expect(defaultWatchDate(value, midnight)).toBe("2026-11-06");
    // The clock reaching schedule start alone does not mean the generator started.
    expect(defaultWatchDate(value, new Date("2026-11-07T06:01:00Z"))).toBe(
      "2026-11-06",
    );
  });

  it.each(["starting", "running", "stopped", "failed", "completed"])(
    "shows today's %s session before the first segment is ready",
    (status) => {
      const value = library([
        day("2026-11-07", { ready: false, status }),
        day("2026-11-06"),
      ]);
      expect(defaultWatchDate(value, midnight)).toBe("2026-11-07");
    },
  );

  it("opens today's playable archive even without session status", () => {
    expect(
      defaultWatchDate(
        library([day("2026-11-07", { status: "unknown" }), day("2026-11-06")]),
        midnight,
      ),
    ).toBe("2026-11-07");
  });

  it("uses the latest playable past day when yesterday is missing", () => {
    expect(
      defaultWatchDate(
        library([day("2026-11-06", { ready: false }), day("2026-11-04")]),
        midnight,
      ),
    ).toBe("2026-11-04");
  });

  it("keeps today's waiting view when no previous playable day exists", () => {
    expect(defaultWatchDate(library([]), midnight)).toBe("2026-11-07");
  });

  it.each([
    ["2026-03-28T22:01:00Z", "2026-03-28"],
    ["2026-12-31T22:01:00Z", "2026-12-31"],
  ])(
    "uses calendar dates across timezone and year boundaries at %s",
    (time, date) => {
      expect(defaultWatchDate(library([day(date)]), new Date(time))).toBe(date);
    },
  );
});
