import { ExternalLink } from "lucide-react";
import { useEffect, useState } from "react";
import type { Day, Library } from "../types";

export interface BufferedSegment {
  name: string;
  start: number;
  end: number;
}

export interface PresentedFrame {
  segment: string;
  ticks: number;
}

interface SourceTimes {
  version: 1;
  generation_id: string;
  segment: string;
  frames: [ticks: number, sourceMilliseconds: number][];
}

interface Coverage {
  version: 1;
  camera: string;
  checked_at: number;
  ranges: [number, number][];
}

export function presentedFrame(
  mediaTime: number,
  segments: Iterable<BufferedSegment>,
): PresentedFrame | null {
  const tick = 1 / 90000;
  let match: BufferedSegment | undefined;
  for (const segment of segments) {
    if (
      mediaTime >= segment.start - tick &&
      mediaTime < segment.end - tick &&
      (!match || segment.start > match.start)
    ) {
      match = segment;
    }
  }
  return match
    ? {
        segment: match.name,
        ticks: Math.round((mediaTime - match.start) * 90000),
      }
    : null;
}

async function json<T>(path: string, signal: AbortSignal): Promise<T> {
  const response = await fetch(path, { cache: "no-cache", signal });
  if (!response.ok) throw new Error("Recording metadata unavailable");
  return response.json();
}

export function RecordingJump({
  day,
  library,
  frame,
  now,
}: {
  day: Day;
  library: Library;
  frame: PresentedFrame;
  now: Date;
}) {
  const [sourceTimes, setSourceTimes] = useState<SourceTimes | null>(null);
  const [coverage, setCoverage] = useState<Coverage | null>(null);
  const { source_times_url: mapUrl, generation_id: generation } = day;
  const { segment } = frame;
  const camera = library.camera;
  useEffect(() => {
    if (
      !mapUrl?.startsWith("/published/") ||
      !mapUrl.endsWith("/") ||
      mapUrl.includes("..") ||
      !/^segment-\d+\.ts$/.test(segment)
    )
      return;
    const segmentUrl = `${mapUrl}${segment}.json?generation=${encodeURIComponent(generation)}`;
    let request: AbortController | undefined;
    async function refresh() {
      request?.abort();
      request = new AbortController();
      const signal = request.signal;
      const timeout = window.setTimeout(() => request?.abort(), 8000);
      try {
        const [map, retained] = await Promise.allSettled([
          json<SourceTimes>(segmentUrl, signal),
          json<Coverage>("/published/recording-coverage.json", signal),
        ]);
        if (signal.aborted) return;
        if (map.status === "fulfilled") setSourceTimes(map.value);
        if (retained.status === "fulfilled") setCoverage(retained.value);
      } finally {
        window.clearTimeout(timeout);
      }
    }
    void refresh();
    const timer = window.setInterval(refresh, 15000);
    return () => {
      window.clearInterval(timer);
      request?.abort();
    };
  }, [mapUrl, generation, segment]);

  if (
    sourceTimes?.version !== 1 ||
    sourceTimes.generation_id !== generation ||
    sourceTimes.segment !== segment
  )
    return null;
  // Allow the browser's microsecond rounding and MPEG-TS tick rounding.
  const source = sourceTimes.frames?.find(
    ([ticks]) => Math.abs(ticks - frame.ticks) <= 2,
  )?.[1];
  if (source === undefined || !Number.isFinite(source) || !library.frigate_url)
    return null;
  const second = Math.floor(source / 1000);
  const fresh =
    coverage?.version === 1 &&
    coverage.camera === camera &&
    Array.isArray(coverage.ranges) &&
    Math.abs(now.getTime() / 1000 - coverage.checked_at) <= 150;
  const available =
    fresh &&
    coverage.ranges.some(([start, end]) => start <= second && second < end);
  let url: URL;
  try {
    url = new URL(library.frigate_url);
  } catch {
    return null;
  }
  if (!["https:", "http:"].includes(url.protocol)) return null;
  url.pathname = url.pathname.replace(/\/$/, "") + "/review";
  url.search = new URLSearchParams({
    timestamp: `${camera}_${second}`,
  }).toString();
  url.hash = "";
  const time = new Intl.DateTimeFormat(undefined, {
    timeZone: library.timezone,
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(source);
  return (
    <div className="recording-jump">
      <time dateTime={new Date(source).toISOString()}>{time}</time>
      {available ? (
        <a href={url.href} target="_blank" rel="noopener noreferrer">
          Open in Frigate <ExternalLink size={14} />
        </a>
      ) : (
        <button type="button" disabled>
          {fresh
            ? "Original recording unavailable"
            : "Recording availability unknown"}
        </button>
      )}
    </div>
  );
}
