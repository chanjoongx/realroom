import { useCallback, useEffect, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { photoSrcSet } from "../api";
import { reducedMotion } from "../lib/stages";
import { CANVAS_SIZES } from "./Photo";

/** Before/after wipe. The handle sweeps in once on mount (unless the viewer asks for reduced
 *  motion) and settles at `rest`, then follows the pointer or the keys. */
export function Compare({
  before,
  after,
  afterLayer,
  intro = true,
  labels = ["Before", "After"],
  alts = ["Original photo", "Edited photo"],
  priority = false,
  rest = 50,
  sizes = CANVAS_SIZES,
}: {
  before: string;
  after: string;
  afterLayer?: ReactNode;
  intro?: boolean;
  labels?: [string, string];
  alts?: [string, string];
  /** above the fold: fetch both photos first */
  priority?: boolean;
  /** where the handle settles, in percent from the left edge */
  rest?: number;
  /** how wide the photos are drawn, for picking a copy from srcset */
  sizes?: string;
}) {
  const sweep = intro && !reducedMotion();
  const [x, setX] = useState(sweep ? 97 : rest);
  const ref = useRef<HTMLDivElement>(null);
  const tagL = useRef<HTMLSpanElement>(null);
  const tagR = useRef<HTMLSpanElement>(null);
  const dragging = useRef(false);
  const moved = useRef(false);

  useEffect(() => {
    if (!sweep) {
      if (!moved.current) setX(rest);
      return;
    }
    let raf = 0;
    let t0 = -1; // the first frame, so the sweep starts at 97 instead of part way
    const from = 97;
    const dur = 1400;
    const step = (now: number) => {
      if (dragging.current || moved.current) return;
      if (t0 < 0) t0 = now;
      const k = Math.min(1, (now - t0) / dur);
      const e = 1 - Math.pow(1 - k, 3);
      setX(from + (rest - from) * e);
      if (k < 1) raf = requestAnimationFrame(step);
    };
    const start = window.setTimeout(() => (raf = requestAnimationFrame(step)), 250);
    return () => {
      window.clearTimeout(start);
      cancelAnimationFrame(raf);
    };
  }, [sweep, after, rest]);

  // the width of the photo and how far each corner tag reaches into it, in px: a tag hides
  // only when the handle would run into it
  const [fit, setFit] = useState<{ w: number; l: number; r: number } | null>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const measure = () => {
      const l = tagL.current;
      const r = tagR.current;
      setFit({ w: el.clientWidth, l: l ? l.offsetLeft + l.offsetWidth : 0, r: r ? r.offsetLeft : el.clientWidth });
    };
    const ro = new ResizeObserver(measure);
    for (const n of [el, tagL.current, tagR.current]) if (n) ro.observe(n);
    return () => ro.disconnect();
  }, []);

  const moveTo = useCallback((clientX: number) => {
    const r = ref.current?.getBoundingClientRect();
    if (!r) return;
    setX(Math.min(100, Math.max(0, ((clientX - r.left) / r.width) * 100)));
  }, []);

  const onKey = (e: KeyboardEvent) => {
    const to: Record<string, (v: number) => number> = {
      ArrowLeft: (v) => v - 4,
      ArrowDown: (v) => v - 4,
      ArrowRight: (v) => v + 4,
      ArrowUp: (v) => v + 4,
      PageDown: (v) => v - 20,
      PageUp: (v) => v + 20,
      Home: () => 0,
      End: () => 100,
    };
    const f = to[e.key];
    if (!f) return;
    e.preventDefault();
    moved.current = true;
    setX((v) => Math.min(100, Math.max(0, f(v))));
  };

  const shown = Math.round(x);
  const px = fit ? (x / 100) * fit.w : 0;
  const showBefore = fit ? px > fit.l + 8 : x > 12;
  const showAfter = fit ? px < fit.r - 8 : x < 88;
  return (
    <div
      ref={ref}
      className="compare"
      onPointerDown={(e) => {
        dragging.current = true;
        moved.current = true;
        (e.target as HTMLElement).setPointerCapture?.(e.pointerId);
        // a finger may be starting a vertical scroll (touch-action: pan-y), which cancels the
        // pointer: on touch the wipe follows only a sideways drag or a tap
        if (e.pointerType !== "touch") moveTo(e.clientX);
      }}
      onPointerMove={(e) => dragging.current && moveTo(e.clientX)}
      onPointerUp={(e) => {
        if (dragging.current) moveTo(e.clientX);
        dragging.current = false;
      }}
      onPointerCancel={() => (dragging.current = false)}
    >
      <img className="photo-img" src={before} srcSet={photoSrcSet(before)} sizes={sizes} alt={alts[0]} draggable={false} fetchPriority={priority ? "high" : undefined} />
      <div className="compare-after" style={{ clipPath: `inset(0 0 0 ${x}%)` }}>
        <img className="photo-img" src={after} srcSet={photoSrcSet(after)} sizes={sizes} alt={alts[1]} draggable={false} fetchPriority={priority ? "high" : undefined} />
        {afterLayer}
      </div>
      {/* a full-width track moved with a transform, so the sweep never shifts layout */}
      <div className="compare-track" style={{ transform: `translateX(${x}%)` }}>
        <div
          className="compare-handle"
          role="slider"
          tabIndex={0}
          aria-label="Compare before and after"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={shown}
          aria-valuetext={`${labels[0]} ${shown}%, ${labels[1]} ${100 - shown}%`}
          onKeyDown={onKey}
        >
          <span className="knob">
            <svg width="18" height="18" viewBox="0 0 18 18" aria-hidden="true">
              <path d="M7 4 2.5 9 7 14M11 4l4.5 5L11 14" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </span>
        </div>
      </div>
      <span ref={tagL} className="compare-tag left mono" style={{ opacity: showBefore ? 1 : 0 }}>{labels[0]}</span>
      <span ref={tagR} className="compare-tag right mono" style={{ opacity: showAfter ? 1 : 0 }}>{labels[1]}</span>
    </div>
  );
}
