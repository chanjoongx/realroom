import { useEffect, useState } from "react";
import { Activity, Columns2, Layers } from "lucide-react";
import { photoSrcSet, runFile } from "../api";
import { undash } from "../lib/format";
import { STAGE_NAME } from "../lib/stages";
import { recordProducts } from "../lib/verdicts";
import type { Box, FixRecord, Run, RunEvent, StageId } from "../types";
import { Compare } from "./Compare";
import { ProductMarks, ScanOverlay, SlotBoxes } from "./Overlays";
import { CANVAS_SIZES, HALF_CANVAS_SIZES, Photo } from "./Photo";

type ResultMode = "compare" | "proof" | "drift";

export function CanvasView({
  run,
  events,
  stage,
  working,
  stoppedAt,
  focus,
  setFocus,
}: {
  run: Run;
  events: RunEvent[];
  stage: StageId | null;
  working: StageId | null;
  /** set when the run stopped: the step it stopped at */
  stoppedAt: StageId | null;
  focus: string | null;
  setFocus: (id: string | null) => void;
}) {
  const { w, h } = run.room;
  const before = runFile(run.id, run.room.before);
  const room = run.room.title || "Your room";
  const beforeAlt = `${room}, the original photo${run.room.ai_generated ? " (an AI-generated demo room)" : ""}`;
  const [mode, setMode] = useState<ResultMode>("compare");
  const [cand, setCand] = useState<number | null>(null);
  useEffect(() => setCand(null), [stage]);
  const chosen = run.render ? (run.render.candidates.find((c) => c.index === run.render!.chosen) ?? run.render.candidates[0]) : undefined;

  // a stopped run shows a still photo where its last step would have been working
  if (stoppedAt && (!stage || stage === stoppedAt)) {
    const late = stoppedAt === "verify" || stoppedAt === "fix" || stoppedAt === "result";
    const still = late && chosen ? chosen : null;
    return (
      <Photo
        src={still ? runFile(run.id, still.comp) : before}
        w={w}
        h={h}
        alt={still ? `Candidate ${still.index}, aligned to the original photo` : beforeAlt}
      >
        <span className="canvas-cap mono cap-stopped">stopped at {STAGE_NAME[stoppedAt].toLowerCase()}</span>
      </Photo>
    );
  }

  // a replay has the whole scan from the start, so it follows the events: the beam while Spark
  // reads the room, Spark's boxes when it is done, and SAM's outlines once SAM has traced them
  if (!stage || (stage === "scan" && (!run.scan || working === "scan"))) {
    return (
      <Photo src={before} w={w} h={h} alt={beforeAlt} className="is-scanning">
        <div className="scan-beam" />
        <Brackets />
      </Photo>
    );
  }

  if (stage === "scan" && run.scan) {
    return (
      <>
        <Photo src={before} w={w} h={h} alt={beforeAlt}>
          <ScanOverlay scan={run.scan} w={w} h={h} focus={focus} onFocus={setFocus} traced={traced(events)} />
          <Brackets />
        </Photo>
        <Legend run={run} />
      </>
    );
  }

  if ((stage === "plan" || stage === "search" || stage === "pick") && run.scan) {
    const slots = run.plan?.slots ?? run.scan.plan.slots;
    return (
      <Photo src={before} w={w} h={h} alt={beforeAlt} className="is-dimmed">
        <ScanOverlay scan={run.scan} w={w} h={h} showFixed={false} showLabels={false} faint />
        <SlotBoxes slots={slots} w={w} h={h} products={run.products} focus={focus} onFocus={setFocus} showThumbs={stage === "pick"} />
      </Photo>
    );
  }

  if (stage === "render") {
    const r = run.render;
    if (!r || working === "render") {
      // live runs get the candidates only once they are verified: say so instead of looking stuck
      const drawn = working !== "render" && events.some((e) => e.stage === "render" && e.status === "done");
      return (
        <Photo
          src={runFile(run.id, "guide.jpg")}
          w={w}
          h={h}
          alt="Placement guide sent to Muse Image 1.0: the room photo with a numbered box where each product goes"
          className="is-rendering"
        >
          <div className="render-veil" />
          <span className="canvas-cap mono">{drawn ? "candidates drawn, now verifying each one" : "placement guide sent to Muse Image 1.0"}</span>
        </Photo>
      );
    }
    return <CandidateGrid run={run} />;
  }

  if (stage === "verify" && run.render) {
    const r = run.render;
    const show = cand ?? r.chosen;
    const c = r.candidates.find((x) => x.index === show) ?? r.candidates[0];
    // the verdicts this candidate got here; the result may already carry a later redraw
    const order = (run.plan?.slots ?? []).map((s) => s.id);
    const marks = (run.result?.products ?? []).map((rp) => {
      const k = order.indexOf(rp.slot);
      const v = c.judge.products?.find((p) => p.index === k + 1);
      return { ...rp, verdict: v ?? rp.verdict, box: c.boxes?.[k] ?? rp.box, fixed: false };
    });
    return (
      <>
        <Photo src={runFile(run.id, c.comp)} w={w} h={h} alt={`Candidate ${c.index}, aligned to the original photo`}>
          {show === r.chosen && run.result && (
            <ProductMarks items={marks} w={w} h={h} focus={focus} onFocus={setFocus} showChips />
          )}
        </Photo>
        <div className="canvas-tabs">
          {r.candidates.map((x) => (
            <button
              key={x.index}
              className={`tab${x.index === show ? " on" : ""}`}
              onClick={() => setCand(x.index)}
              aria-pressed={x.index === show}
              aria-label={`Candidate ${x.index}, score ${x.score.toFixed(2)}${x.index === r.chosen ? ", kept" : ""}`}
            >
              <span className="mono">#{x.index}</span>
              <span className="mono num faint">{x.score.toFixed(2)}</span>
              {x.index === r.chosen && <span className="tab-kept">kept</span>}
            </button>
          ))}
        </div>
      </>
    );
  }

  if (stage === "fix") {
    const f = run.fixes?.[0];
    // a live run writes its fixes at the end of the step: until then show the candidate it kept
    if (!f)
      return (
        <Photo
          src={runFile(run.id, chosen ? chosen.comp : run.room.before)}
          w={w}
          h={h}
          alt={chosen ? `Candidate ${chosen.index}, the one kept, while weak products are redrawn` : beforeAlt}
        />
      );
    // a single wide crop (a rug, a sofa against a long wall) reads better stacked than side by side
    const [fx1, fy1, fx2, fy2] = f.region;
    const stacked = run.fixes!.length === 1 && (fx2 - fx1) / Math.max(1, fy2 - fy1) > 1.4;
    return (
      <div className={`fix-canvas${stacked ? " stacked" : ""}`}>
        {run.fixes!.map((fx) => {
          const why = fx.kind === "clear" ? "" : fixReason(fx);
          const first = runFile(run.id, fx.images[0]);
          const second = runFile(run.id, fx.images[1]);
          const sizes = stacked ? CANVAS_SIZES : HALF_CANVAS_SIZES;
          return (
            <div key={fx.slot} className="fix-canvas-row">
              <figure>
                <img src={first} srcSet={photoSrcSet(first)} sizes={sizes} alt="First render" />
                <figcaption className="mono">
                  {fx.kind === "clear"
                    ? `first render · ${(fx.names ?? []).join(", ")} left`
                    : `first render · ${fx.before.verdict} ${fx.before.score}`}
                </figcaption>
                {why && <p className="fix-why">Spark: {why}</p>}
              </figure>
              <figure>
                <img src={second} srcSet={photoSrcSet(second)} sizes={sizes} alt={fx.kind === "clear" ? "Cleared" : "Redrawn"} />
                <figcaption className="mono">
                  {fx.kind === "clear" ? "cleared · checked with SAM 3.1" : `redrawn · ${fx.after.verdict} ${fx.after.score}`}
                </figcaption>
              </figure>
            </div>
          );
        })}
      </div>
    );
  }

  if (stage === "result" && run.result) {
    const res = run.result;
    const after = runFile(run.id, res.after);
    const recs = recordProducts(res);
    const edited = run.room.ai_generated ? "Edited photo" : "Edited photo, products drawn by Muse Image 1.0 (AI-edited)";
    const tabs: { id: ResultMode; label: string; icon: typeof Columns2 }[] = [
      { id: "compare", label: "Before / after", icon: Columns2 },
      { id: "proof", label: "What changed", icon: Layers },
      { id: "drift", label: "Model drift", icon: Activity },
    ];
    return (
      <>
        {mode === "compare" && (
          <Photo w={w} h={h}>
            <Compare
              before={before}
              after={after}
              alts={[beforeAlt, `${room}, ${edited.charAt(0).toLowerCase()}${edited.slice(1)}`]}
              rest={restAt(recs, w)}
              afterLayer={<ProductMarks items={recs} w={w} h={h} focus={focus} onFocus={setFocus} showOutline={!!focus} />}
            />
          </Photo>
        )}
        {mode === "proof" && (
          <Photo src={after} w={w} h={h} alt={`${room}, the result with the changed areas colored`}>
            {res.layers && (
              <img
                className="photo-img layer-img"
                src={runFile(run.id, res.layers)}
                srcSet={photoSrcSet(runFile(run.id, res.layers))}
                sizes={CANVAS_SIZES}
                alt=""
              />
            )}
            <LayerLegend run={run} />
          </Photo>
        )}
        {mode === "drift" && (
          <Photo src={runFile(run.id, res.drift)} w={w} h={h} alt="Model drift map: where Muse Image changed the photo, brighter where it changed more">
            <div className="drift-legend">
              <span className="mono">Where Muse Image changed the photo</span>
              <span className="drift-note">
                The new products were meant to change. Every other pixel was copied back from the original.
              </span>
              <span className="drift-scale" />
              <span className="mono faint drift-ends">
                <span>no change</span>
                <span>ΔE 30+</span>
              </span>
            </div>
          </Photo>
        )}
        <div className="canvas-tabs">
          {tabs.map((t) => (
            <button key={t.id} className={`tab${mode === t.id ? " on" : ""}`} onClick={() => setMode(t.id)} aria-pressed={mode === t.id}>
              <t.icon size={14} /> {t.label}
            </button>
          ))}
        </div>
      </>
    );
  }

  return <Photo src={before} w={w} h={h} alt={beforeAlt} />;
}

/** Where the result wipe settles: 3% left of the leftmost new product, so every product and its
 *  number are on the after side while a strip of the original stays in view. Kept within 10 to 50%,
 *  except that it moves further left rather than cover a number (each sits at its product's middle). */
function restAt(items: { box?: Box | null }[], w: number): number {
  const boxes = items.map((p) => p.box).filter((b): b is Box => Array.isArray(b) && b.length === 4);
  if (!boxes.length || !(w > 0)) return 50;
  const left = (Math.min(...boxes.map((b) => b[0])) / w) * 100;
  const mid = (Math.min(...boxes.map((b) => (b[0] + b[2]) / 2)) / w) * 100;
  return Math.min(50, Math.max(Math.min(10, mid - 2), left - 3, 2));
}

/** Spark's main reason a product was redrawn: its first issue, or else its note on color. */
function fixReason(f: FixRecord): string {
  const why = f.before.issues?.find((s) => s.trim()) ?? f.before.color ?? "";
  return undash(why.trim());
}

function CandidateGrid({ run }: { run: Run }) {
  const r = run.render!;
  return (
    <div className={`cand-grid n${Math.min(r.candidates.length, 4)}`}>
      {r.candidates.map((c, i) => (
        <figure key={c.index} className="cand" style={{ animationDelay: `${i * 160}ms` }}>
          <Photo src={runFile(run.id, c.raw)} w={run.room.w} h={run.room.h} alt={`Candidate ${c.index}, raw output from Muse Image 1.0`}>
            <figcaption className="canvas-cap mono">candidate {c.index} · raw output</figcaption>
          </Photo>
        </figure>
      ))}
    </div>
  );
}

function Brackets() {
  return (
    <span className="brackets" aria-hidden="true">
      <i />
      <i />
      <i />
      <i />
    </span>
  );
}

function Legend({ run }: { run: Run }) {
  const c = run.scan?.stats.classes;
  if (!c) return null;
  return (
    <div className="legend mono">
      <span className="lg c-replace"><i />replace {c.replace}</span>
      <span className="lg c-remove"><i />clear away {c.remove}</span>
      <span className="lg c-keep"><i />keep {c.keep}</span>
      <span className="lg c-fixed"><i />fixed {run.scan?.fixed.length}</span>
    </div>
  );
}

function LayerLegend({ run }: { run: Run }) {
  const lp = run.result?.proof.layers_pct;
  if (!lp) return null;
  return (
    <div className="legend mono layer-legend">
      <span className="lg"><i style={{ background: "var(--new)" }} />new products {lp.products.toFixed(1)}%</span>
      <span className="lg"><i style={{ background: "var(--remove)" }} />cleared {lp.cleared.toFixed(1)}%</span>
      <span className="lg"><i style={{ background: "var(--shadow-layer)" }} />shadows {lp.shadows.toFixed(1)}%</span>
      <span className="lg"><i style={{ background: "transparent", border: "1px solid var(--line-3)" }} />original pixels {run.result!.proof.untouched_pct.toFixed(1)}%</span>
    </div>
  );
}

/** SAM's outlines are on screen once its scan event is, or once a later step has started. */
function traced(events: RunEvent[]): boolean {
  return events.some((e) => e.stage !== "scan" || (e.model === "sam-3.1" && e.status === "done"));
}
