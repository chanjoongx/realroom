import type { CSSProperties, ReactNode } from "react";
import { photoSrcSet } from "../api";

/** How wide the run viewer's canvas is drawn: the whole width on a phone, then what the inspector
 *  (360px) leaves, then what the step list (276px) and the inspector (400px) leave, as app.css lays it
 *  out. The browser picks a photo copy from this. */
export const CANVAS_SIZES = "(max-width: 820px) 100vw, (max-width: 1120px) calc(100vw - 360px), calc(100vw - 676px)";

/** Half of that, for two photos side by side on the canvas (the fix step). */
export const HALF_CANVAS_SIZES = "(max-width: 820px) 50vw, (max-width: 1120px) calc(50vw - 180px), calc(50vw - 338px)";

/** A photo fitted inside its container (contain), with overlay children in the same box. */
export function Photo({
  src,
  w,
  h,
  alt = "",
  className = "",
  sizes = CANVAS_SIZES,
  children,
}: {
  src?: string;
  w: number;
  h: number;
  alt?: string;
  className?: string;
  sizes?: string;
  children?: ReactNode;
}) {
  const style = { "--ar": `${w} / ${h}`, "--arn": w / h } as CSSProperties;
  return (
    <div className="photo-fit" style={style}>
      <div className={`photo-box ${className}`}>
        {src && (
          <img className="photo-img" src={src} srcSet={photoSrcSet(src)} sizes={sizes} alt={alt} draggable={false} />
        )}
        {children}
      </div>
    </div>
  );
}

/** Position helper: box in image pixels -> CSS percentages. */
export function pctBox(box: [number, number, number, number], w: number, h: number): CSSProperties {
  const [x1, y1, x2, y2] = box;
  return {
    left: `${(x1 / w) * 100}%`,
    top: `${(y1 / h) * 100}%`,
    width: `${((x2 - x1) / w) * 100}%`,
    height: `${((y2 - y1) / h) * 100}%`,
  };
}
