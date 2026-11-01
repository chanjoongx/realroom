import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";

/** Before/after wipe. The handle sweeps in once on mount, then follows the pointer or arrow keys. */
export function Compare({
  before,
  after,
  afterLayer,
  intro = true,
  labels = ["Before", "After"],
}: {
  before: string;
  after: string;
  afterLayer?: ReactNode;
  intro?: boolean;
  labels?: [string, string];
}) {
  const [x, setX] = useState(intro ? 97 : 50);
  const ref = useRef<HTMLDivElement>(null);
  const dragging = useRef(false);

  useEffect(() => {
    if (!intro) return;
    let raf = 0;
    const t0 = performance.now();
    const from = 97;
    const to = 50;
    const dur = 1400;
    const step = (now: number) => {
      const k = Math.min(1, (now - t0) / dur);
      const e = 1 - Math.pow(1 - k, 3);
      setX(from + (to - from) * e);
      if (k < 1 && !dragging.current) raf = requestAnimationFrame(step);
    };
    const start = window.setTimeout(() => (raf = requestAnimationFrame(step)), 250);
    return () => {
      window.clearTimeout(start);
      cancelAnimationFrame(raf);
    };
  }, [intro, after]);

  const moveTo = useCallback((clientX: number) => {
    const r = ref.current?.getBoundingClientRect();
    if (!r) return;
    setX(Math.min(100, Math.max(0, ((clientX - r.left) / r.width) * 100)));
  }, []);

  return (
    <div
      ref={ref}
      className="compare"
      onPointerDown={(e) => {
        dragging.current = true;
        (e.target as HTMLElement).setPointerCapture?.(e.pointerId);
        moveTo(e.clientX);
      }}
      onPointerMove={(e) => dragging.current && moveTo(e.clientX)}
      onPointerUp={() => (dragging.current = false)}
      onPointerCancel={() => (dragging.current = false)}
    >
      <img className="photo-img" src={before} alt="Original photo" draggable={false} />
      <div className="compare-after" style={{ clipPath: `inset(0 0 0 ${x}%)` }}>
        <img className="photo-img" src={after} alt="Edited photo" draggable={false} />
        {afterLayer}
      </div>
      <div
        className="compare-handle"
        style={{ left: `${x}%` }}
        role="slider"
        tabIndex={0}
        aria-label="Compare before and after"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(x)}
        onKeyDown={(e) => {
          if (e.key === "ArrowLeft") setX((v) => Math.max(0, v - 4));
          if (e.key === "ArrowRight") setX((v) => Math.min(100, v + 4));
        }}
      >
        <span className="knob">
          <svg width="18" height="18" viewBox="0 0 18 18" aria-hidden="true">
            <path d="M7 4 2.5 9 7 14M11 4l4.5 5L11 14" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </span>
      </div>
      <span className="compare-tag left mono" style={{ opacity: x > 12 ? 1 : 0 }}>{labels[0]}</span>
      <span className="compare-tag right mono" style={{ opacity: x < 88 ? 1 : 0 }}>{labels[1]}</span>
    </div>
  );
}
