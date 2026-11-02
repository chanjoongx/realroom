import type { SizeCm } from "../types";

export const KIND_LABEL: Record<string, string> = {
  rug: "Rug", runner_rug: "Runner rug", floor_lamp: "Floor lamp", table_lamp: "Table lamp",
  throw_pillow: "Throw pillow", throw_blanket: "Throw blanket", bedding: "Bedding", sofa: "Sofa",
  sectional: "Sectional", accent_chair: "Accent chair", dining_chair: "Dining chair", office_chair: "Office chair",
  coffee_table: "Coffee table", side_table: "Side table", console_table: "Console table", dining_table: "Dining table",
  desk: "Desk", ottoman: "Ottoman", bench: "Bench", bed: "Bed", bookcase: "Bookcase", wall_shelf: "Wall shelf",
  wall_art: "Wall art", mirror: "Mirror", planter: "Planter", vase: "Vase",
};

export const kindLabel = (k: string) => KIND_LABEL[k] ?? k.replace(/_/g, " ");

const r = (v: number) => (Math.abs(v) >= 100 ? Math.round(v) : Math.round(v * 10) / 10);

export function sizeCm(s: SizeCm, kind?: string): string {
  if (kind === "rug" || kind === "runner_rug") return `${r(s.w)} × ${r(s.d)} cm`;
  return `${r(s.w)} × ${r(s.d)} × ${r(s.h)} cm`;
}

export function sizeIn(s: SizeCm, kind?: string): string {
  const i = (v: number) => Math.round((v / 2.54) * 10) / 10;
  if (kind === "rug" || kind === "runner_rug") {
    const ft = (v: number) => Math.round((v / 30.48) * 10) / 10;
    return `${ft(s.w)} × ${ft(s.d)} ft`;
  }
  return `${i(s.w)} × ${i(s.d)} × ${i(s.h)} in`;
}

export function usd(v: number | undefined): string {
  if (v === undefined || v === null) return "";
  if (v === 0) return "$0.00";
  if (v < 0.01) return `$${v.toFixed(3)}`;
  return `$${v.toFixed(2)}`;
}

export function secs(v: number | undefined): string {
  if (v === undefined || v === null) return "";
  if (v < 59.5) return `${v < 10 ? v.toFixed(1) : Math.round(v)} s`;
  // round once, so 119.6 s reads 2m 00s and never 1m 60s
  const t = Math.round(v);
  return `${Math.floor(t / 60)}m ${String(t % 60).padStart(2, "0")}s`;
}

export function pct(v: number | undefined, digits = 1): string {
  if (v === undefined || v === null) return "";
  return `${v.toFixed(digits)}%`;
}

export const MODEL_INFO: Record<string, { label: string; color: string }> = {
  "muse-spark-1.3": { label: "Muse Spark 1.3", color: "var(--m-spark)" },
  "sam-3.1": { label: "SAM 3.1", color: "var(--m-sam)" },
  "muse-image-1.0": { label: "Muse Image 1.0", color: "var(--m-image)" },
  "muse-spark-open": { label: "Muse Spark Open", color: "var(--m-open)" },
  "pe-core-b16-224": { label: "PE-Core B16", color: "var(--m-open)" },
  index: { label: "Catalog index", color: "var(--m-index)" },
};

/** "muse-spark-1.3 + sam-3.1" -> ["muse-spark-1.3", "sam-3.1"]; free text maps to the index entry. */
export function modelsOf(model?: string): string[] {
  if (!model) return [];
  return model.split("+").map((m) => {
    const k = m.trim().replace(/[()]/g, "");
    if (MODEL_INFO[k]) return k;
    if (k.startsWith("catalog index")) return "index";
    return k;
  });
}

export function titleCase(s: string): string {
  return s.length ? s[0].toUpperCase() + s.slice(1) : s;
}

// Model text may carry dashes and the interface shows none: the same three rules as tidy() in
// backend/realroom/pipeline.py, applied only on screen so run.json keeps the exact prompt that was sent.
const DASHES = String.fromCharCode(0x2012, 0x2013, 0x2014, 0x2015);
const DASH_RANGE = new RegExp(String.raw`(\d)(?:\s*[${DASHES}]\s*| - )(\d)`, "g");
// no lookbehind: Safari before 16.4 cannot parse one, and this runs when the module loads
const DASH_SPACED = new RegExp(String.raw`(\S) [-${DASHES}${String.fromCharCode(0x2212)}]{1,2} (?=\S)`, "g");
const DASH_ANY = new RegExp(String.raw`\s*[${DASHES}]\s*`, "g");

export function undash(s: string): string {
  return s.replace(DASH_RANGE, "$1 to $2").replace(DASH_SPACED, "$1, ").replace(DASH_ANY, ", ");
}
