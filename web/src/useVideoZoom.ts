import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type PointerEvent,
  type RefObject,
} from "react";
import {
  boundZoom,
  FIT,
  GESTURE_SLOP,
  pinchZoom,
  type Point,
  type Viewport,
  type Zoom,
} from "./zoom";

interface Pinch {
  value: Zoom;
  midpoint: Point;
  distance: number;
}
interface Pan {
  value: Zoom;
  point: Point;
}
const distance = (a: Point, b: Point) => Math.hypot(a.x - b.x, a.y - b.y);
const midpoint = (a: Point, b: Point) => ({
  x: (a.x + b.x) / 2,
  y: (a.y + b.y) / 2,
});

export function useVideoZoom(
  enabled: boolean,
  stage: RefObject<HTMLDivElement | null>,
  video: RefObject<HTMLVideoElement | null>,
) {
  const [value, setValue] = useState<Zoom>(FIT);
  const current = useRef<Zoom>(FIT);
  const view = useRef<Viewport>({
    width: 1,
    height: 1,
    videoWidth: 16,
    videoHeight: 9,
  });
  const pointers = useRef(new Map<number, Point>());
  const pinch = useRef<Pinch | null>(null);
  const pan = useRef<Pan | null>(null);
  const suppressTap = useRef(false);
  const update = useCallback((next: Zoom) => {
    const bounded = boundZoom(next, view.current);
    current.current = bounded;
    setValue(bounded);
  }, []);
  const measure = useCallback(() => {
    const rect = stage.current?.getBoundingClientRect();
    if (!rect?.width || !rect.height) return;
    view.current = {
      width: rect.width,
      height: rect.height,
      videoWidth: video.current?.videoWidth || 16,
      videoHeight: video.current?.videoHeight || 9,
    };
    update(current.current);
  }, [stage, video, update]);
  const cancel = useCallback(() => {
    pointers.current.clear();
    pinch.current = null;
    pan.current = null;
    suppressTap.current = false;
  }, []);
  const reset = useCallback(() => {
    cancel();
    update(FIT);
  }, [cancel, update]);
  useEffect(() => {
    if (!enabled) {
      reset();
      return;
    }
    measure();
    const observer = new ResizeObserver(() => {
      cancel();
      measure();
    });
    if (stage.current) observer.observe(stage.current);
    window.addEventListener("blur", cancel);
    document.addEventListener("visibilitychange", cancel);
    return () => {
      observer.disconnect();
      window.removeEventListener("blur", cancel);
      document.removeEventListener("visibilitychange", cancel);
      cancel();
    };
  }, [enabled, stage, measure, cancel, reset]);
  const local = (event: PointerEvent<HTMLDivElement>): Point => {
    const rect = stage.current!.getBoundingClientRect();
    return {
      x: event.clientX - rect.left - rect.width / 2,
      y: event.clientY - rect.top - rect.height / 2,
    };
  };
  const down = (event: PointerEvent<HTMLDivElement>) => {
    if (!enabled || event.pointerType !== "touch") return false;
    pointers.current.set(event.pointerId, local(event));
    event.currentTarget.setPointerCapture(event.pointerId);
    if (pointers.current.size >= 2) {
      const [a, b] = [...pointers.current.values()];
      pinch.current = {
        value: current.current,
        midpoint: midpoint(a, b),
        distance: Math.max(1, distance(a, b)),
      };
      suppressTap.current = true;
      return true;
    }
    pan.current = { value: current.current, point: local(event) };
    return false;
  };
  const move = (event: PointerEvent<HTMLDivElement>) => {
    if (!pointers.current.has(event.pointerId)) return false;
    pointers.current.set(event.pointerId, local(event));
    if (pointers.current.size >= 2 && pinch.current) {
      const [a, b] = [...pointers.current.values()];
      update(
        pinchZoom(
          pinch.current.value,
          pinch.current.midpoint,
          midpoint(a, b),
          distance(a, b) / pinch.current.distance,
          view.current,
        ),
      );
    } else if (
      pan.current &&
      current.current.scale > 1 &&
      (suppressTap.current ||
        distance(local(event), pan.current.point) > GESTURE_SLOP)
    ) {
      suppressTap.current = true;
      const point = local(event);
      update({
        ...pan.current.value,
        x: pan.current.value.x + point.x - pan.current.point.x,
        y: pan.current.value.y + point.y - pan.current.point.y,
      });
    }
    return suppressTap.current;
  };
  const up = (event: PointerEvent<HTMLDivElement>) => {
    if (!pointers.current.delete(event.pointerId)) return false;
    const suppressed = suppressTap.current;
    pinch.current = null;
    const [remaining, second] = [...pointers.current.values()];
    if (remaining && second) {
      pinch.current = {
        value: current.current,
        midpoint: midpoint(remaining, second),
        distance: Math.max(1, distance(remaining, second)),
      };
    }
    pan.current = remaining
      ? { value: current.current, point: remaining }
      : null;
    if (!remaining) suppressTap.current = false;
    return suppressed;
  };
  return {
    value,
    zoomed: value.scale > 1,
    style: {
      transform: `translate(${value.x}px, ${value.y}px) scale(${value.scale})`,
    },
    down,
    move,
    up,
    cancel,
    measure,
    reset,
    isZoomed: () => current.current.scale > 1,
  };
}
