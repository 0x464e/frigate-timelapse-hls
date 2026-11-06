import * as Slider from "@radix-ui/react-slider";
import { Minus, Plus, RotateCcw } from "lucide-react";
import { clamp, positionToSpeed, speedToPosition } from "../lib";

export function SpeedControl({
  speed,
  onChange,
}: {
  speed: number;
  onChange: (value: number) => void;
}) {
  const adjust = (amount: number) => onChange(clamp(speed + amount, 5, 40));
  return (
    <section className="speed-control" aria-label="Playback speed controls">
      <div className="setting-heading">
        <span>Playback speed</span>
        <output>{(speed / 10).toFixed(1)}×</output>
      </div>
      <div className="speed-stepper">
        <button
          className="icon-button"
          aria-label="Decrease speed by 0.1"
          disabled={speed === 5}
          onClick={() => adjust(-1)}
        >
          <Minus />
        </button>
        <Slider.Root
          className="speed-slider"
          value={[speedToPosition(speed)]}
          min={0}
          max={100}
          step={0.01}
          onValueChange={([value]) => onChange(positionToSpeed(value))}
          onKeyDown={(event) => {
            if (
              [
                "ArrowLeft",
                "ArrowDown",
                "ArrowRight",
                "ArrowUp",
                "Home",
                "End",
                "PageUp",
                "PageDown",
              ].includes(event.key)
            ) {
              event.preventDefault();
              event.stopPropagation();
              if (event.key === "Home") onChange(5);
              else if (event.key === "End") onChange(40);
              else
                adjust(
                  ["ArrowLeft", "ArrowDown", "PageDown"].includes(event.key)
                    ? -1
                    : 1,
                );
            }
          }}
        >
          <Slider.Track className="speed-track">
            <Slider.Range className="speed-fill" />
          </Slider.Track>
          <Slider.Thumb
            className="speed-thumb"
            aria-label="Playback speed"
            aria-valuemin={0.5}
            aria-valuemax={4}
            aria-valuenow={speed / 10}
            aria-valuetext={`${(speed / 10).toFixed(1)} times`}
          />
        </Slider.Root>
        <button
          className="icon-button"
          aria-label="Increase speed by 0.1"
          disabled={speed === 40}
          onClick={() => adjust(1)}
        >
          <Plus />
        </button>
      </div>
      <div className="speed-ticks" aria-hidden="true">
        {[5, 10, 15, 30, 40].map((value) => (
          <span key={value} style={{ left: `${speedToPosition(value)}%` }}>
            {value / 10}×
          </span>
        ))}
      </div>
      <button className="text-button reset-speed" onClick={() => onChange(10)}>
        <RotateCcw size={14} /> Reset to 1×
      </button>
    </section>
  );
}
