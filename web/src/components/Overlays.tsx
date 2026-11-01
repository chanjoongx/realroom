import type { CSSProperties } from "react";
import { thumbUrl } from "../api";
import { kindLabel } from "../lib/format";
import type { Box, Product, ResultProduct, Scan, SceneObject, Slot, Verdict } from "../types";
import { pctBox } from "./Photo";

const CLASS_LABEL = { keep: "keep", replace: "replace", remove: "remove" } as const;

function short(s: string, n = 26): string {
  return s.length > n ? `${s.slice(0, n - 1).trimEnd()}…` : s;
}

/** Greedy label placement: each label sits at its box's top-left corner and moves down until it
 *  no longer overlaps a label placed before it. Sizes are rough estimates in percent of the photo. */
function placeLabels<T extends { box: Box; text: string }>(items: T[], w: number, h: number) {
  const lh = 3.1; // label height, % of photo height
  const placed: { x: number; y: number; wd: number; it: T }[] = [];
  const order = [...items].sort((a, b) => area(b.box) - area(a.box));
  for (const it of order) {
    const wd = Math.min(34, 2.4 + it.text.length * 0.6);
    const x = (it.box[0] / w) * 100;
    let y = (it.box[1] / h) * 100;
    if (y < 1) y = 1;
    for (let k = 0; k < 6; k++) {
      const hit = placed.some((p) => x < p.x + p.wd && p.x < x + wd && y < p.y + lh && p.y < y + lh);
      if (!hit) break;
      y += lh + 0.3;
    }
    placed.push({ x: Math.min(x, 100 - wd), y: Math.min(y, 100 - lh), wd, it });
  }
  return placed;
}

const area = (b: Box) => (b[2] - b[0]) * (b[3] - b[1]);

export function ScanOverlay({
  scan,
  w,
  h,
  focus,
  onFocus,
  showFixed = true,
  showLabels = true,
  faint = false,
}: {
  scan: Scan;
  w: number;
  h: number;
  focus?: string | null;
  onFocus?: (id: string | null) => void;
  showFixed?: boolean;
  showLabels?: boolean;
  faint?: boolean;
}) {
  const sw = w / 560;
  const labels = placeLabels(
    scan.objects.map((o) => ({ box: o.box, text: `${o.id} ${short(o.name)}`, o })),
    w,
    h,
  );
  return (
    <>
      <svg className={`overlay-svg${faint ? " faint" : ""}`} viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none">
        {showFixed &&
          scan.fixed.map((f, i) => (
            <rect
              key={`f${i}`}
              className="fixed-box"
              x={f.box[0]}
              y={f.box[1]}
              width={Math.max(1, f.box[2] - f.box[0])}
              height={Math.max(1, f.box[3] - f.box[1])}
              strokeWidth={sw}
              strokeDasharray={`${sw * 5} ${sw * 4}`}
              style={{ animationDelay: `${i * 60}ms` }}
            />
          ))}
        {scan.objects.map((o, i) => (
          <ObjectShape key={o.id} o={o} sw={sw} delay={180 + i * 90} focus={focus} onFocus={onFocus} />
        ))}
      </svg>
      {showLabels &&
        labels.map(({ x, y, it }, i) => (
          <button
            type="button"
            key={it.o.id}
            className={`obj-tag c-${it.o.class}${focus === it.o.id ? " is-focus" : ""}${focus && focus !== it.o.id ? " is-dim" : ""}`}
            style={{ left: `${x}%`, top: `${y}%`, animationDelay: `${420 + i * 90}ms` }}
            onMouseEnter={() => onFocus?.(it.o.id)}
            onMouseLeave={() => onFocus?.(null)}
            onFocus={() => onFocus?.(it.o.id)}
            onBlur={() => onFocus?.(null)}
          >
            <span className="dot" />
            <span className="mono id">{it.o.id}</span>
            <span className="txt">{short(it.o.name)}</span>
          </button>
        ))}
    </>
  );
}

function ObjectShape({
  o,
  sw,
  delay,
  focus,
  onFocus,
}: {
  o: SceneObject;
  sw: number;
  delay: number;
  focus?: string | null;
  onFocus?: (id: string | null) => void;
}) {
  const cls = `obj c-${o.class}${focus === o.id ? " is-focus" : ""}${focus && focus !== o.id ? " is-dim" : ""}`;
  const style = { animationDelay: `${delay}ms` } as CSSProperties;
  const ev = { onMouseEnter: () => onFocus?.(o.id), onMouseLeave: () => onFocus?.(null) };
  if (o.outline) {
    return <path className={cls} d={o.outline} pathLength={1} strokeWidth={sw * 1.5} style={style} {...ev} />;
  }
  const [x1, y1, x2, y2] = o.box;
  return (
    <rect
      className={cls}
      x={x1}
      y={y1}
      width={x2 - x1}
      height={y2 - y1}
      rx={sw * 4}
      pathLength={1}
      strokeWidth={sw * 1.5}
      style={style}
      {...ev}
    />
  );
}

export function SlotBoxes({
  slots,
  w,
  h,
  products,
  focus,
  onFocus,
  showThumbs = false,
}: {
  slots: Slot[];
  w: number;
  h: number;
  products?: Record<string, Product>;
  focus?: string | null;
  onFocus?: (id: string | null) => void;
  showThumbs?: boolean;
}) {
  return (
    <>
      {slots.map((s, i) => {
        const p = s.pick && products ? products[s.pick] : undefined;
        return (
          <div
            key={s.id}
            className={`slot-box${focus === s.id ? " is-focus" : ""}${focus && focus !== s.id ? " is-dim" : ""}`}
            style={{ ...pctBox(s.target_px, w, h), animationDelay: `${i * 140}ms` }}
            onMouseEnter={() => onFocus?.(s.id)}
            onMouseLeave={() => onFocus?.(null)}
          >
            <span className="slot-num mono">{i + 1}</span>
            <span className="slot-kind">{kindLabel(s.kind)}</span>
            {showThumbs && p && (
              <span className="slot-thumb" style={{ animationDelay: `${300 + i * 160}ms` }}>
                <img src={thumbUrl(p.id)} alt={p.title} />
              </span>
            )}
          </div>
        );
      })}
    </>
  );
}

export function verdictTone(v?: Verdict): string {
  switch (v?.verdict) {
    case "match":
      return "good";
    case "partial":
      return "warn";
    case "mismatch":
    case "missing":
      return "bad";
    default:
      return "none";
  }
}

export function ProductMarks({
  items,
  w,
  h,
  focus,
  onFocus,
  onPick,
  showOutline = true,
  showChips = false,
}: {
  items: ResultProduct[];
  w: number;
  h: number;
  focus?: string | null;
  onFocus?: (id: string | null) => void;
  onPick?: (id: string) => void;
  showOutline?: boolean;
  showChips?: boolean;
}) {
  const sw = w / 560;
  return (
    <>
      {showOutline && (
        <svg className="overlay-svg" viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none">
          {items.map((it, i) =>
            it.outline ? (
              <path
                key={it.slot}
                className={`prod-outline${focus === it.slot ? " is-focus" : ""}${focus && focus !== it.slot ? " is-dim" : ""}`}
                d={it.outline}
                pathLength={1}
                strokeWidth={sw * 1.6}
                style={{ animationDelay: `${i * 120}ms` }}
              />
            ) : null,
          )}
        </svg>
      )}
      {items.map((it, i) => {
        const [x1, y1, x2] = it.box;
        const cx = ((x1 + x2) / 2 / w) * 100;
        const cy = (y1 / h) * 100;
        return (
          <button
            type="button"
            key={it.slot}
            className={`hotspot tone-${verdictTone(it.verdict)}${focus === it.slot ? " is-focus" : ""}${
              cx > 55 ? " flip" : ""
            }`}
            style={{ left: `${cx}%`, top: `${Math.max(3, cy)}%`, animationDelay: `${300 + i * 140}ms` }}
            onMouseEnter={() => onFocus?.(it.slot)}
            onMouseLeave={() => onFocus?.(null)}
            onClick={() => onPick?.(it.slot)}
            aria-label={`Product ${i + 1}`}
          >
            <span className="hs-num mono">{i + 1}</span>
            {showChips && it.verdict?.verdict && (
              <span className="hs-chip mono">
                {it.verdict.verdict} {it.verdict.score ?? ""}
              </span>
            )}
          </button>
        );
      })}
    </>
  );
}

export { CLASS_LABEL };
