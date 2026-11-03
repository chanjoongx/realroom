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

/** "2 of 3 checks say match: match 9, match 9, partial 7", with how many looks did not come back. */
export function checksNote(v: Verdict): string {
  const looks = v.checks ?? [];
  const m = looks.filter((c) => c.verdict === "match").length;
  const missing = (v.of ?? looks.length) - looks.length;
  const each = looks.map((c) => `${verdictLabel(c.verdict)} ${c.score}`).join(", ");
  return `${m} of ${looks.length} checks say match` + (each ? `: ${each}` : "") +
    (missing > 0 ? ` (${missing} did not come back)` : "");
}

/**
 * The verdict of record for the product at 0-based position `pos` in `result.products`.
 *
 * Runs since 11-03 carry it already: Spark looked at the finished image several times, each look on its
 * own, and the majority decided (`verdict.checks`). Older recorded runs have one or two looks: there it is
 * the worse of the product's own verdict (the verify step's, or the accepted redraw's) and the final check
 * of the finished image (`finalCheck` entry with `index === pos + 1`), because Spark's looks at the same
 * pixels can disagree either way. With no final check entry, the product's own verdict stands.
 */
export function recordVerdict(product: ResultProduct, pos: number, finalCheck?: Verdict[]): RecordVerdict {
  const own: Verdict = product.verdict ?? {};
  if (Array.isArray(own.checks)) return { verdict: own.verdict ? own : { ...own, verdict: "unverified" }, note: checksNote(own) };
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
