import { beforeEach, describe, expect, it } from "vitest";
import {
  clamp,
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
import type { Day } from "./types";

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
