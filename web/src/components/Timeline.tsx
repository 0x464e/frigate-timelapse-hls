import { useRef, useState, type CSSProperties, type PointerEvent } from "react";
import { clamp, formatTime } from "../lib";

interface Props {
  time: number;
  duration: number;
  buffered: [number, number][];
  step: number;
  onSeek: (value: number) => void;
  onScrubbing: (value: boolean) => void;
}

export function Timeline({
  time,
  duration,
  buffered,
  step,
  onSeek,
  onScrubbing,
}: Props) {
  const track = useRef<HTMLDivElement>(null);
  const frozenDuration = useRef(duration);
  const target = useRef(time);
  const dragging = useRef(false);
  const [preview, setPreview] = useState<number | null>(null);
  const [hover, setHover] = useState<number | null>(null);
  const currentDuration = preview === null ? duration : frozenDuration.current;
  const value = preview ?? time;
  const percent =
    currentDuration > 0 ? clamp((value / currentDuration) * 100, 0, 100) : 0;
  const point = (event: PointerEvent<HTMLDivElement>) => {
    const rect = track.current!.getBoundingClientRect();
    const scale = dragging.current ? frozenDuration.current : duration;
    return clamp((event.clientX - rect.left) / rect.width, 0, 1) * scale;
  };
  const end = (event: PointerEvent<HTMLDivElement>, commit: boolean) => {
    if (!dragging.current) return;
    dragging.current = false;
    if (commit) onSeek(clamp(target.current, 0, duration));
    setPreview(null);
    onScrubbing(false);
    if (event.currentTarget.hasPointerCapture(event.pointerId))
      event.currentTarget.releasePointerCapture(event.pointerId);
  };
  const tooltip = preview ?? hover;
  return (
    <div className="timeline-wrap">
      <div
        ref={track}
        className="timeline"
        role="slider"
        tabIndex={duration > 0 ? 0 : -1}
        aria-label="Seek timeline"
        aria-valuemin={0}
        aria-valuemax={currentDuration}
        aria-valuenow={clamp(value, 0, currentDuration)}
        aria-valuetext={`${formatTime(value)} of ${formatTime(currentDuration)}`}
        aria-disabled={duration <= 0}
        style={{ "--progress": `${percent}%` } as CSSProperties}
        onPointerDown={(event) => {
          if (duration <= 0 || event.button !== 0) return;
          event.preventDefault();
          event.stopPropagation();
          event.currentTarget.focus();
          frozenDuration.current = duration;
          dragging.current = true;
          target.current = point(event);
          setPreview(target.current);
          onScrubbing(true);
          event.currentTarget.setPointerCapture(event.pointerId);
        }}
        onPointerMove={(event) => {
          if (dragging.current) {
            target.current = point(event);
            setPreview(target.current);
          } else if (event.pointerType === "mouse") setHover(point(event));
        }}
        onPointerUp={(event) => end(event, true)}
        onPointerCancel={(event) => end(event, false)}
        onLostPointerCapture={(event) => end(event, false)}
        onPointerLeave={() => setHover(null)}
        onKeyDown={(event) => {
          const keys: Record<string, number> = {
            ArrowLeft: time - step,
            ArrowRight: time + step,
            Home: 0,
            End: duration,
          };
          if (event.key in keys) {
            event.preventDefault();
            event.stopPropagation();
            onSeek(clamp(keys[event.key], 0, duration));
          }
        }}
      >
        <div className="timeline-track">
          {buffered.map(([start, end], index) => (
            <span
              key={index}
              className="timeline-buffer"
              style={{
                left: `${clamp((start / (currentDuration || 1)) * 100, 0, 100)}%`,
                width: `${clamp(((end - start) / (currentDuration || 1)) * 100, 0, 100)}%`,
              }}
            />
          ))}
          <span className="timeline-progress" />
        </div>
        <span
          className={`timeline-thumb ${preview !== null ? "dragging" : ""}`}
        />
        {tooltip !== null && (
          <output
            className="timeline-tooltip"
            style={{
              left: `${clamp((tooltip / (currentDuration || 1)) * 100, 3, 97)}%`,
            }}
          >
            {formatTime(tooltip)}
          </output>
        )}
        <div className="timeline-times" aria-hidden="true">
          <span>{formatTime(value)}</span>
          <span>{formatTime(currentDuration)}</span>
        </div>
      </div>
    </div>
  );
}
