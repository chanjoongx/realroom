import { useEffect, useRef } from "react";
import { Check, LoaderCircle } from "lucide-react";
import { secs, usd } from "../lib/format";
import type { Run, RunEvent, StageId } from "../types";
import { ModelChips } from "./ModelChip";

const META: Record<StageId, { title: string; model: string }> = {
  scan: { title: "Scan the room", model: "muse-spark-1.3 + sam-3.1" },
  plan: { title: "Plan the change", model: "muse-spark-1.3" },
  search: { title: "Search the catalog", model: "muse-spark-open + pe-core-b16-224 + index" },
  pick: { title: "Pick real products", model: "muse-spark-1.3" },
  render: { title: "Render candidates", model: "muse-image-1.0" },
  verify: { title: "Verify every product", model: "sam-3.1 + muse-spark-1.3" },
  fix: { title: "Redraw weak products", model: "muse-image-1.0 + sam-3.1 + muse-spark-1.3" },
  result: { title: "Result", model: "" },
};

export function Rail({
  run,
  events,
  reached,
  working,
  selected,
  onSelect,
}: {
  run: Run;
  events: RunEvent[];
  reached: Set<StageId>;
  working: StageId | null;
  selected: StageId | null;
  onSelect: (s: StageId) => void;
}) {
  const hasFix = run.events.some((e) => e.stage === "fix");
  const stages: StageId[] = ["scan", "plan", "search", "pick", "render", "verify", ...(hasFix ? (["fix"] as StageId[]) : []), "result"];
  const list = useRef<HTMLOListElement>(null);
  // the result line comes from the result itself, in today's wording, so older recorded runs read the same
  const res = run.result;
  const matched = res ? res.products.filter((p) => p.verdict?.verdict === "match").length : 0;
  const resultLine = res ? `${matched} of ${res.products.length} products match their catalog photos` : null;
  // a finished search names the models it used: without the Perception Encoder it ranked by text alone
  const searched = run.events.find((e) => e.stage === "search" && e.status === "done");
  const stageModel = (s: StageId) =>
    s === "search" && searched && !searched.model?.includes("pe-core") ? "muse-spark-open + index" : META[s].model;
  // on narrow screens the rail is one scrolling row: keep the selected step in view
  useEffect(() => {
    const el = list.current;
    if (!el || el.scrollWidth <= el.clientWidth + 1) return;
    const it = el.querySelector<HTMLElement>(".rail-item.is-selected");
    if (!it) return;
    const r = it.getBoundingClientRect();
    const b = el.getBoundingClientRect();
    el.scrollBy({ left: r.left - b.left - (b.width - r.width) / 2, behavior: "smooth" });
  }, [selected]);
  return (
    <nav className="rail scroll-thin" aria-label="Agent steps">
      <ol ref={list}>
        {stages.map((s, i) => {
          const evs = events.filter((e) => e.stage === s);
          const done = evs.filter((e) => e.status !== "start");
          const sec = done.reduce((a, e) => a + (e.sec ?? 0), 0);
          const cost = done.reduce((a, e) => a + (e.usd ?? 0), 0);
          const isWorking = working === s;
          const isReached = reached.has(s);
          const state = isWorking ? "working" : isReached ? "done" : "todo";
          const last = done[done.length - 1] ?? evs[evs.length - 1];
          return (
            <li key={s} className={`rail-item is-${state}${selected === s ? " is-selected" : ""}`}>
              <button className="rail-btn" onClick={() => isReached && onSelect(s)} disabled={!isReached}>
                <span className="rail-icon">
                  {state === "working" ? <LoaderCircle size={14} className="spin" /> : state === "done" ? <Check size={13} /> : <span className="mono">{i + 1}</span>}
                </span>
                <span className="rail-main">
                  <span className="rail-title">{META[s].title}</span>
                  {stageModel(s) && <ModelChips model={stageModel(s)} compact />}
                  {last && state !== "todo" && <span className="rail-detail">{s === "result" && resultLine ? resultLine : last.title}</span>}
                </span>
                <span className="rail-meta mono num">
                  {sec > 0 && <span>{secs(sec)}</span>}
                  {cost > 0 && <span className="faint">{usd(cost)}</span>}
                </span>
              </button>
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
