import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { useState } from "react";
import { SpeedControl } from "./SpeedControl";
import { Timeline } from "./Timeline";

beforeEach(() => {
  window.PointerEvent = MouseEvent as typeof PointerEvent;
  Element.prototype.setPointerCapture = vi.fn();
  Element.prototype.hasPointerCapture = vi.fn(() => false);
  Element.prototype.releasePointerCapture = vi.fn();
});
it("adjusts exact speed tenths with buttons, keys, and reset", () => {
  function Controls() {
    const [speed, setSpeed] = useState(10);
    return <SpeedControl speed={speed} onChange={setSpeed} />;
  }
  render(<Controls />);
  fireEvent.click(screen.getByLabelText("Increase speed by 0.1"));
  expect(screen.getByLabelText("Playback speed")).toHaveAttribute(
    "aria-valuenow",
    "1.1",
  );
  fireEvent.keyDown(screen.getByLabelText("Playback speed"), {
    key: "ArrowRight",
  });
  expect(screen.getByLabelText("Playback speed")).toHaveAttribute(
    "aria-valuenow",
    "1.2",
  );
  fireEvent.click(screen.getByText("Reset to 1×"));
  expect(screen.getByLabelText("Playback speed")).toHaveAttribute(
    "aria-valuenow",
    "1",
  );
});
it("previews without seeking, freezes the drag scale, and commits once", () => {
  const onSeek = vi.fn();
  const onScrubbing = vi.fn();
  const { rerender } = render(
    <Timeline
      time={0}
      duration={20}
      buffered={[]}
      step={5}
      onSeek={onSeek}
      onScrubbing={onScrubbing}
    />,
  );
  const slider = screen.getByLabelText("Seek timeline");
  slider.getBoundingClientRect = () => ({ left: 0, width: 200 }) as DOMRect;
  fireEvent.pointerDown(slider, { clientX: 100, button: 0 });
  expect(onSeek).not.toHaveBeenCalled();
  rerender(
    <Timeline
      time={0}
      duration={40}
      buffered={[]}
      step={5}
      onSeek={onSeek}
      onScrubbing={onScrubbing}
    />,
  );
  fireEvent.pointerMove(slider, { clientX: 200 });
  expect(slider).toHaveAttribute("aria-valuenow", "20");
  fireEvent.pointerUp(slider, { clientX: 200 });
  expect(onSeek).toHaveBeenCalledExactlyOnceWith(20);
});
it("cancels dragging and clamps keyboard seeks", () => {
  const onSeek = vi.fn();
  render(
    <Timeline
      time={1}
      duration={3}
      buffered={[]}
      step={5}
      onSeek={onSeek}
      onScrubbing={vi.fn()}
    />,
  );
  const slider = screen.getByLabelText("Seek timeline");
  slider.getBoundingClientRect = () => ({ left: 0, width: 200 }) as DOMRect;
  fireEvent.pointerDown(slider, { clientX: 100, button: 0 });
  fireEvent.pointerCancel(slider);
  expect(onSeek).not.toHaveBeenCalled();
  fireEvent.keyDown(slider, { key: "ArrowLeft" });
  expect(onSeek).toHaveBeenLastCalledWith(0);
  fireEvent.keyDown(slider, { key: "ArrowRight" });
  expect(onSeek).toHaveBeenLastCalledWith(3);
});
