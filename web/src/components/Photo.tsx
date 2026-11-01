import type { CSSProperties, ReactNode } from "react";

/** A photo fitted inside its container (contain), with overlay children in the same box. */
export function Photo({
  src,
  w,
  h,
  alt = "",
  className = "",
  children,
}: {
  src?: string;
  w: number;
  h: number;
  alt?: string;
  className?: string;
  children?: ReactNode;
}) {
  const style = { "--ar": `${w} / ${h}`, "--arn": w / h } as CSSProperties;
  return (
    <div className="photo-fit" style={style}>
      <div className={`photo-box ${className}`}>
        {src && <img className="photo-img" src={src} alt={alt} draggable={false} />}
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
