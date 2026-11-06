import { describe, expect, it } from "vitest";
import {
  boundZoom,
  FIT,
  fittedSize,
  maxZoom,
  pinchZoom,
  type Viewport,
} from "./zoom";

const portrait: Viewport = {
  width: 393,
  height: 687,
  videoWidth: 3840,
  videoHeight: 2160,
};
describe("mobile video framing", () => {
  it("fits without distortion and allows the entire portrait video area to be filled", () => {
    const fit = fittedSize(portrait);
    expect(fit.width / fit.height).toBeCloseTo(16 / 9);
    expect(fit.width).toBe(portrait.width);
    expect(fit.height * maxZoom(portrait)).toBeGreaterThanOrEqual(
      portrait.height,
    );
  });
  it("prevents panning into empty space on every edge", () => {
    const scale = 4;
    const fit = fittedSize(portrait);
    for (const sign of [-1, 1]) {
      const value = boundZoom(
        { scale, x: sign * 10000, y: sign * 10000 },
        portrait,
      );
      expect(Math.abs(value.x)).toBeCloseTo(
        (fit.width * scale - portrait.width) / 2,
      );
      expect(Math.abs(value.y)).toBeCloseTo(
        (fit.height * scale - portrait.height) / 2,
      );
    }
  });
  it("keeps letterboxed axes centered and returns cleanly to fit", () => {
    expect(boundZoom({ scale: 1, x: 400, y: -400 }, portrait)).toEqual(FIT);
    expect(boundZoom({ scale: 2, x: 40, y: 400 }, portrait).y).toBe(0);
    expect(boundZoom({ scale: 1.01, x: 20, y: 40 }, portrait)).toEqual(FIT);
  });
  it("keeps the video point beneath the moving pinch midpoint", () => {
    const value = pinchZoom(FIT, { x: 40, y: 0 }, { x: 70, y: 0 }, 3, portrait);
    expect(40 * value.scale + value.x).toBeCloseTo(70);
    expect(value.scale).toBe(3);
  });
  it("clamps magnification and pan when the viewport rotates", () => {
    const landscape = { ...portrait, width: 851, height: 393 };
    const value = boundZoom({ scale: 100, x: 5000, y: -5000 }, landscape);
    expect(value.scale).toBe(maxZoom(landscape));
    const fit = fittedSize(landscape);
    expect(Math.abs(value.x)).toBeLessThanOrEqual(
      (fit.width * value.scale - landscape.width) / 2,
    );
    expect(Math.abs(value.y)).toBeLessThanOrEqual(
      (fit.height * value.scale - landscape.height) / 2,
    );
  });
});
