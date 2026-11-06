import { clamp } from "./lib";

export interface Point {
  x: number;
  y: number;
}
export interface Zoom extends Point {
  scale: number;
}
export interface Viewport {
  width: number;
  height: number;
  videoWidth: number;
  videoHeight: number;
}
export const FIT: Zoom = { scale: 1, x: 0, y: 0 };
export const GESTURE_SLOP = 10;

export function fittedSize(view: Viewport) {
  const factor = Math.min(
    view.width / view.videoWidth,
    view.height / view.videoHeight,
  );
  return { width: view.videoWidth * factor, height: view.videoHeight * factor };
}
export function maxZoom(view: Viewport) {
  const fit = fittedSize(view);
  // Even a tall portrait viewport can be filled, with room to inspect further.
  return Math.max(
    4,
    2 * Math.max(view.width / fit.width, view.height / fit.height),
  );
}
export function boundZoom(value: Zoom, view: Viewport): Zoom {
  const scale = value.scale < 1.02 ? 1 : clamp(value.scale, 1, maxZoom(view));
  const fit = fittedSize(view);
  const x = Math.max(0, (fit.width * scale - view.width) / 2);
  const y = Math.max(0, (fit.height * scale - view.height) / 2);
  return {
    scale,
    x: x ? clamp(value.x, -x, x) : 0,
    y: y ? clamp(value.y, -y, y) : 0,
  };
}
export function pinchZoom(
  start: Zoom,
  anchor: Point,
  midpoint: Point,
  ratio: number,
  view: Viewport,
): Zoom {
  const scale = clamp(start.scale * ratio, 1, maxZoom(view));
  return boundZoom(
    {
      scale,
      x: midpoint.x - ((anchor.x - start.x) * scale) / start.scale,
      y: midpoint.y - ((anchor.y - start.y) * scale) / start.scale,
    },
    view,
  );
}
