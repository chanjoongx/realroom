import { useState } from "react";
import { ShieldCheck } from "lucide-react";

type State =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "done"; same: number; total: number; ms: number }
  | { status: "error"; message: string };

async function bitmap(url: string): Promise<ImageBitmap> {
  const res = await fetch(url, { cache: "force-cache" });
  if (!res.ok) throw new Error(`could not load ${url.split("/").pop()} (${res.status})`);
  return createImageBitmap(await res.blob(), { colorSpaceConversion: "none", premultiplyAlpha: "none" });
}

function pixels(b: ImageBitmap): Uint8ClampedArray {
  const c = document.createElement("canvas");
  c.width = b.width;
  c.height = b.height;
  const ctx = c.getContext("2d", { willReadFrequently: true });
  if (!ctx) throw new Error("no 2D canvas");
  ctx.drawImage(b, 0, 0);
  return ctx.getImageData(0, 0, b.width, b.height).data;
}

/** Re-checks the "untouched pixels" number in the viewer's own browser: it downloads the
 *  lossless original and result and compares them pixel by pixel. */
export function PixelCheck({ beforeUrl, afterUrl, claimed }: { beforeUrl: string; afterUrl: string; claimed: number }) {
  const [st, setSt] = useState<State>({ status: "idle" });

  async function run() {
    setSt({ status: "loading" });
    const t0 = performance.now();
    try {
      const [a, b] = await Promise.all([bitmap(beforeUrl), bitmap(afterUrl)]);
      if (a.width !== b.width || a.height !== b.height) throw new Error("the two images have different sizes");
      const pa = pixels(a);
      const pb = pixels(b);
      const total = a.width * a.height;
      let same = 0;
      for (let i = 0, j = 0; i < total; i++, j += 4) {
        if (pa[j] === pb[j] && pa[j + 1] === pb[j + 1] && pa[j + 2] === pb[j + 2]) same++;
      }
      setSt({ status: "done", same, total, ms: performance.now() - t0 });
    } catch (e) {
      setSt({ status: "error", message: e instanceof Error ? e.message : String(e) });
    }
  }

  if (st.status === "done") {
    const p = (100 * st.same) / st.total;
    const agrees = Math.abs(p - claimed) < 0.02;
    return (
      <div className={`pixel-check done${agrees ? " agrees" : ""}`}>
        <ShieldCheck size={16} />
        <div>
          <div>
            Your browser compared <span className="mono num">{st.total.toLocaleString("en-US")}</span> pixels:{" "}
            <span className="mono num">{st.same.toLocaleString("en-US")}</span> are identical to the original (
            <span className="mono num">{p.toFixed(2)}%</span>).
          </div>
          <div className="faint">
            {agrees ? "Same number the server reported." : `The server reported ${claimed.toFixed(2)}%.`} Checked
            in {Math.round(st.ms)} ms on lossless PNG copies.
          </div>
        </div>
      </div>
    );
  }
  return (
    <div className="pixel-check">
      <button className="btn sm" onClick={run} disabled={st.status === "loading"}>
        <ShieldCheck size={14} />
        {st.status === "loading" ? "Comparing pixels…" : "Check it in your browser"}
      </button>
      {st.status === "error" && <span className="faint">{st.message}</span>}
    </div>
  );
}
