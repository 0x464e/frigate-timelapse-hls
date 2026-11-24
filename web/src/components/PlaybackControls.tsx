import { useLayoutEffect, useRef, type ReactNode } from "react";

export function PlaybackControls({ children }: { children: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const bar = ref.current!;
    const groups = Array.from(bar.children) as HTMLElement[];
    const fit = () => {
      const gap = Number.parseFloat(getComputedStyle(bar).columnGap) || 0;
      for (const label of ["full", "short", "icon"]) {
        bar.dataset.frigateLabel = label;
        const width = groups.reduce(
          (total, group) => total + group.getBoundingClientRect().width,
          gap * Math.max(0, groups.length - 1),
        );
        if (width <= bar.clientWidth) break;
      }
    };
    fit();
    let pending = 0;
    const observer = new ResizeObserver(() => {
      cancelAnimationFrame(pending);
      pending = requestAnimationFrame(fit);
    });
    observer.observe(bar);
    groups.forEach((group) => observer.observe(group));
    return () => {
      observer.disconnect();
      cancelAnimationFrame(pending);
    };
  }, []);
  return (
    <div className="control-bar" ref={ref}>
      {children}
    </div>
  );
}
