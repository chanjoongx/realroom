import type { Result, ResultProduct, Verdict, VerdictKind } from "../types";

/** match > partial > mismatch > missing > unverified or no verdict */
const RANK: Record<VerdictKind, number> = { match: 4, partial: 3, mismatch: 2, missing: 1, unverified: 0 };
const rank = (v: Verdict) => (v.verdict ? (RANK[v.verdict] ?? 0) : 0);

/** a is worse than b: a lower verdict, or the same verdict with a lower score (no score sorts lowest) */
function worse(a: Verdict, b: Verdict): boolean {
  if (rank(a) !== rank(b)) return rank(a) < rank(b);
  return (a.score ?? -Infinity) < (b.score ?? -Infinity);
}

export function verdictLabel(v?: Verdict["verdict"]): string {
  return v === "unverified" ? "not verified" : (v ?? "");
}

export const isMatch = (v?: Verdict) => v?.verdict === "match";

export interface RecordVerdict {
  /** the verdict the product is shown and counted with */
  verdict: Verdict;
  /** the other judgment, when the two disagree on the verdict */
  note?: string;
}

function noteFor(v: Verdict, what: string): string {
  return v.score !== undefined ? `${what} ${v.score}` : `${what}: ${verdictLabel(v.verdict) || "no verdict"}`;
}

/**
 * The verdict of record for the product at 0-based position `pos` in `result.products`: the worse of
 * its own verdict (the verify step's, or the accepted redraw's) and the final check of the finished
 * image (`finalCheck` entry with `index === pos + 1`). Spark's two looks at the same pixels can
 * disagree either way, so the page shows the cautious one. With no final check entry, the product's
 * own verdict stands.
 */
export function recordVerdict(product: ResultProduct, pos: number, finalCheck?: Verdict[]): RecordVerdict {
  const own: Verdict = product.verdict ?? {};
  const fin = finalCheck?.find((f) => f.index === pos + 1);
  if (!fin) return { verdict: own };
  const finalWorse = worse(fin, own);
  const shown = finalWorse ? fin : own;
  const other = finalWorse ? own : fin;
  const verdict: Verdict = shown.verdict ? shown : { ...shown, verdict: "unverified" };
  if ((shown.verdict ?? "") === (other.verdict ?? "")) return { verdict };
  const what = finalWorse ? (product.fixed ? "redraw scored" : "first check") : "final check";
  return { verdict, note: noteFor(other, what) };
}

/** Every product of a result with its verdict of record. */
export function recordProducts(res: Result): (ResultProduct & { note?: string })[] {
  return res.products.map((p, i) => {
    const r = recordVerdict(p, i, res.final_check_products);
    return { ...p, verdict: r.verdict, note: r.note };
  });
}

/** "2 of 3 products match their catalog photos", counted from the verdicts of record. */
export function matchLine(res: Result): string {
  const ps = recordProducts(res);
  return `${ps.filter((p) => isMatch(p.verdict)).length} of ${ps.length} products match their catalog photos`;
}
