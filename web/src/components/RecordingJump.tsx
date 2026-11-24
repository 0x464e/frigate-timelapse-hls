import { ExternalLink } from "lucide-react";
import { useEffect, useState, type RefObject } from "react";
import type { Day, Library } from "../types";

export interface BufferedSegment {
  name: string;
  start: number;
  end: number;
}

interface PresentedFrame {
  segment: string;
  ticks: number;
}

interface SourceTimes {
  version: 1;
  generation_id: string;
  segment: string;
  frames: [ticks: number, sourceMilliseconds: number][];
}

function presentedFrame(
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
  video,
  fragments,
  now,
  blocked,
}: {
  day: Day;
  library: Library;
  video: RefObject<HTMLVideoElement | null>;
  fragments: RefObject<Map<string, BufferedSegment>>;
  now: Date;
  blocked: boolean;
}) {
  const [sourceTimes, setSourceTimes] = useState<SourceTimes | null>(null);
  const { source_times_url: mapUrl, generation_id: generation } = day;
  const [frame, setFrame] = useState<PresentedFrame | null>(null);
  const [seeking, setSeeking] = useState(false);
  const segment = frame?.segment;
  useEffect(() => {
    const element = video.current;
    if (!element?.requestVideoFrameCallback) return;
    let callback: number;
    const track = (_: number, { mediaTime }: VideoFrameCallbackMetadata) => {
      setFrame(presentedFrame(mediaTime, fragments.current.values()));
      callback = element.requestVideoFrameCallback(track);
    };
    const seek = () => {
      setSeeking(true);
      setFrame(null);
    };
    const seeked = () => setSeeking(false);
    const reset = () => {
      setSeeking(false);
      setFrame(null);
    };
    element.addEventListener("seeking", seek);
    element.addEventListener("seeked", seeked);
    element.addEventListener("emptied", reset);
    callback = element.requestVideoFrameCallback(track);
    return () => {
      element.cancelVideoFrameCallback(callback);
      element.removeEventListener("seeking", seek);
      element.removeEventListener("seeked", seeked);
      element.removeEventListener("emptied", reset);
    };
  }, [video, fragments]);
  const camera = library.camera;
  useEffect(() => {
    if (
      !mapUrl?.startsWith("/published/") ||
      !mapUrl.endsWith("/") ||
      mapUrl.includes("..") ||
      !segment ||
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
        const map = await json<SourceTimes>(segmentUrl, signal);
        if (!signal.aborted) {
          setSourceTimes(map);
          window.clearInterval(timer);
        }
      } catch {
        // The segment can finish publishing while playback is open.
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

  // Keep the button in place during seeks, map loads, and expiry.
  const source =
    !blocked &&
    !seeking &&
    frame &&
    sourceTimes?.version === 1 &&
    sourceTimes.generation_id === generation &&
    sourceTimes.segment === segment
      ? sourceTimes.frames?.find(
          ([ticks]) => Math.abs(ticks - frame.ticks) <= 2,
        )?.[1]
      : undefined;
  const days = library.frigate_retention_days;
  const expired =
    source !== undefined &&
    typeof days === "number" &&
    Number.isFinite(days) &&
    days > 0 &&
    now.getTime() - source >= days * 86_400_000;
  if (source === undefined || !Number.isFinite(source) || expired) {
    return (
      <button
        className="icon-button recording-jump"
        type="button"
        disabled
        aria-label="Open in Frigate"
        title={
          expired
            ? "Outside the recording retention window"
            : "Original timestamp unavailable"
        }
      >
        <ExternalLink aria-hidden="true" />
        <span className="recording-jump-label">
          <span className="recording-jump-prefix">Open in </span>Frigate
        </span>
      </button>
    );
  }
  if (!library.frigate_url) return null;
  const second = Math.floor(source / 1000);
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
    <a
      className="icon-button recording-jump"
      href={url.href}
      target="_blank"
      rel="noopener noreferrer"
      aria-label="Open in Frigate"
      title={`Jump to ${time} in Frigate`}
    >
      <ExternalLink aria-hidden="true" />
      <span className="recording-jump-label">
        <span className="recording-jump-prefix">Open in </span>Frigate
      </span>
    </a>
  );
}
