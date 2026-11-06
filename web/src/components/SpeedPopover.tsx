import { useEffect, useId, useRef } from "react";
import { SpeedControl } from "./SpeedControl";

export function SpeedPopover({
  speed,
  onChange,
  open,
  onOpenChange,
}: {
  speed: number;
  onChange: (value: number) => void;
  open: boolean;
  onOpenChange: (value: boolean) => void;
}) {
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  const id = useId();
  useEffect(() => {
    if (!open) return;
    const outside = (event: PointerEvent) => {
      if (!root.current?.contains(event.target as Node)) onOpenChange(false);
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      event.stopPropagation();
      onOpenChange(false);
      trigger.current?.focus();
    };
    document.addEventListener("pointerdown", outside);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("pointerdown", outside);
      document.removeEventListener("keydown", escape);
    };
  }, [open, onOpenChange]);
  useEffect(() => {
    if (open)
      panel.current?.querySelector<HTMLElement>('[role="slider"]')?.focus();
  }, [open]);
  return (
    <div ref={root} className="speed-popover-anchor">
      <button
        ref={trigger}
        className="speed-button"
        aria-label="Change playback speed"
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls={open ? id : undefined}
        onClick={() => onOpenChange(!open)}
      >
        {(speed / 10).toFixed(1)}×
      </button>
      {open && (
        <div
          ref={panel}
          id={id}
          className="speed-popover"
          role="dialog"
          aria-label="Playback speed"
          aria-modal="false"
        >
          <SpeedControl speed={speed} onChange={onChange} />
        </div>
      )}
    </div>
  );
}
