import type { Day, Library } from "./types";

export const clamp = (value: number, min: number, max: number) =>
  Math.min(max, Math.max(min, value));
export const formatTime = (seconds: number) => {
  const value = Math.max(0, Math.floor(Number.isFinite(seconds) ? seconds : 0));
  const hours = Math.floor(value / 3600);
  return `${hours ? `${hours}:` : ""}${String(Math.floor(value / 60) % 60).padStart(2, "0")}:${String(value % 60).padStart(2, "0")}`;
};
export const speedToPosition = (tenths: number) =>
  tenths <= 15
    ? (clamp(tenths, 5, 15) - 5) * 6
    : 60 + (clamp(tenths, 15, 40) - 15) * 1.6;
export const positionToSpeed = (position: number) =>
  Math.round(
    position <= 60
      ? 5 + clamp(position, 0, 60) / 6
      : 15 + (clamp(position, 60, 100) - 60) / 1.6,
  );
export const todayIn = (timezone: string, now = new Date()) =>
  new Intl.DateTimeFormat("en-CA", {
    timeZone: timezone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(now);
export const prettyDate = (date: string, long = false) =>
  new Intl.DateTimeFormat("en-GB", {
    weekday: long ? "long" : "short",
    day: "numeric",
    month: long ? "long" : "short",
  }).format(new Date(`${date}T12:00:00`));
export const validDate = (date: string) =>
  /^\d{4}-\d{2}-\d{2}$/.test(date) &&
  !Number.isNaN(Date.parse(`${date}T12:00:00`)) &&
  new Date(`${date}T12:00:00Z`).toISOString().slice(0, 10) === date;

export function getStored(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}
export function setStored(key: string, value: string) {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* Storage can be unavailable in private mode. */
  }
}
export function getSeekInterval() {
  const value = Number(getStored("timelapse.seekSeconds"));
  return Number.isInteger(value) && value >= 1 && value <= 120 ? value : 5;
}
export const resumeKey = (camera: string, day: Day) =>
  `timelapse.position.${encodeURIComponent(camera)}.${day.date}.${day.generation_id}`;

export function emptyState(
  library: Library,
  date: string,
  day?: Day,
  now = new Date(),
) {
  if (day?.ready) return "";
  if (date !== todayIn(library.timezone, now))
    return "No timelapse available for this day";
  const clock = new Intl.DateTimeFormat("en-GB", {
    timeZone: library.timezone,
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: "h23",
  }).format(now);
  if (clock < library.schedule.start)
    return `The day begins at ${library.schedule.start.slice(0, 5)}`;
  if (day?.recordings_available === false) return "Waiting for recordings";
  if (day?.mode === "catch_up" || day?.status === "running")
    return "Waiting for the first encoded segment";
  return "Waiting for recordings";
}

export function parseLibrary(data: unknown): Library {
  if (!data || typeof data !== "object")
    throw new Error("Invalid library metadata");
  const value = data as Library;
  if (
    value.version !== 1 ||
    typeof value.camera !== "string" ||
    typeof value.timezone !== "string" ||
    !value.schedule ||
    !/^\d{2}:\d{2}:\d{2}$/.test(value.schedule.start) ||
    !/^\d{2}:\d{2}:\d{2}$/.test(value.schedule.end) ||
    !Array.isArray(value.days)
  ) {
    throw new Error(
      "Unsupported library metadata. Update the generator and reload.",
    );
  }
  // Reject foreign media origins: every resource must remain behind our proxy/authentication.
  for (const day of value.days) {
    if (
      !validDate(day.date) ||
      !Number.isFinite(day.duration) ||
      day.duration < 0 ||
      typeof day.generation_id !== "string" ||
      typeof day.ready !== "boolean" ||
      [day.playlist_url, day.live_playlist_url, day.poster_url].some(
        (url) =>
          url !== null &&
          (typeof url !== "string" ||
            !url.startsWith("/published/") ||
            url.includes("..")),
      )
    ) {
      throw new Error("Invalid day in library metadata");
    }
  }
  new Intl.DateTimeFormat("en-GB", { timeZone: value.timezone });
  return value;
}
