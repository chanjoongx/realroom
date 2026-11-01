import type { Box } from "../types";

/** A product's close crop, cut from a full render by its box with the same 12% margin the
 *  backend uses for the crops it sends to Spark. Lets a step show the render it judged, even
 *  when a later step replaced part of it. */
export function BoxCrop({ src, box, w, h, label }: { src: string; box: Box; w: number; h: number; label: string }) {
  const [x1, y1, x2, y2] = box;
  const mx = (x2 - x1) * 0.12;
  const my = (y2 - y1) * 0.12;
  const cx = Math.max(0, x1 - mx);
  const cy = Math.max(0, y1 - my);
  const cw = Math.min(w, x2 + mx) - cx;
  const ch = Math.min(h, y2 + my) - cy;
  const vb = `${cx} ${cy} ${cw} ${ch}`;
  // the inner svg clips to the crop, so the bands that letterboxing leaves stay empty
  // instead of showing the rest of the photo
  return (
    <svg className="box-crop" viewBox={vb} preserveAspectRatio="xMidYMid meet" role="img" aria-label={label}>
      <svg x={cx} y={cy} width={cw} height={ch} viewBox={vb} overflow="hidden">
        <image href={src} x={0} y={0} width={w} height={h} />
      </svg>
    </svg>
  );
}
