import type { RunEvent, StageId } from "../types";

export const STAGE_NAME: Record<StageId, string> = {
  scan: "Scan",
  plan: "Plan",
  search: "Search",
  pick: "Pick",
  render: "Render",
  verify: "Verify",
  fix: "Fix",
  result: "Result",
};

export const isStageId = (s: unknown): s is StageId => typeof s === "string" && Object.hasOwn(STAGE_NAME, s);

/** the step under way once a step has sent a "done" event. A fix sends one per product and one for
 *  the final judgment of the finished room, so the fix step lasts until the result arrives. */
const AFTER: Record<StageId, StageId | null> = {
  scan: "plan",
  plan: "search",
  search: "pick",
  pick: "render",
  render: "verify",
  verify: "result",
  fix: "fix",
  result: null,
};

/** Where a stopped run stopped: the stage the backend names, else the step that was under way when
 *  the events end (a step that had started, or the one after the last finished step). */
export function stoppedStage(events: RunEvent[], stoppedAt?: string): StageId {
  if (isStageId(stoppedAt)) return stoppedAt;
  let last: RunEvent | undefined;
  for (const e of events) if (e.stage !== "error") last = e;
  if (!last || last.stage === "error") return "scan";
  if (last.status === "start") return last.stage;
  return AFTER[last.stage] ?? last.stage;
}

/** Older runs keep raw exception text in `error`; show those as one plain sentence with the text as detail. */
export function plainError(msg?: string | null): { text: string; detail?: string } {
  const m = (msg ?? "").trim();
  if (!m) return { text: "The run stopped before it finished." };
  if (/^(Traceback|[A-Za-z_][\w.]*(Error|Exception|Timeout|Interrupt)\b)/.test(m)) {
    return { text: "Something went wrong on the server, so the run stopped.", detail: m };
  }
  return { text: m };
}

export const reducedMotion = (): boolean =>
  typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches === true;
