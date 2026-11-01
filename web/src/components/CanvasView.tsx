import { useEffect, useState } from "react";
import { Activity, Columns2, Layers } from "lucide-react";
import { runFile } from "../api";
import type { Run, StageId } from "../types";
import { Compare } from "./Compare";
import { ProductMarks, ScanOverlay, SlotBoxes } from "./Overlays";
import { Photo } from "./Photo";

type ResultMode = "compare" | "proof" | "drift";

export function CanvasView({
  run,
  stage,
  working,
  focus,
  setFocus,
}: {
  run: Run;
  stage: StageId | null;
  working: StageId | null;
  focus: string | null;
  setFocus: (id: string | null) => void;
}) {
  const { w, h } = run.room;
  const before = runFile(run.id, run.room.before);
  const [mode, setMode] = useState<ResultMode>("compare");
  const [cand, setCand] = useState<number | null>(null);
  useEffect(() => setCand(null), [stage]);

  if (!stage || (stage === "scan" && !run.scan)) {
    return (
      <Photo src={before} w={w} h={h} className="is-scanning">
        <div className="scan-beam" />
        <Brackets />
      </Photo>
    );
  }

  if (stage === "scan" && run.scan) {
    return (
      <>
        <Photo src={before} w={w} h={h}>
          <ScanOverlay scan={run.scan} w={w} h={h} focus={focus} onFocus={setFocus} />
          <Brackets />
        </Photo>
        <Legend run={run} />
      </>
    );
  }

  if ((stage === "plan" || stage === "search" || stage === "pick") && run.scan) {
    const slots = run.plan?.slots ?? run.scan.plan.slots;
    return (
      <Photo src={before} w={w} h={h} className="is-dimmed">
        <ScanOverlay scan={run.scan} w={w} h={h} showFixed={false} showLabels={false} faint />
        <SlotBoxes slots={slots} w={w} h={h} products={run.products} focus={focus} onFocus={setFocus} showThumbs={stage === "pick"} />
      </Photo>
    );
  }

  if (stage === "render") {
    const r = run.render;
    if (!r || working === "render") {
      return (
        <Photo src={runFile(run.id, "guide.jpg")} w={w} h={h} className="is-rendering">
          <div className="render-veil" />
          <span className="canvas-cap mono">placement guide sent to Muse Image 1.0</span>
        </Photo>
      );
    }
    return <CandidateGrid run={run} which="raw" />;
  }

  if (stage === "verify" && run.render) {
    const r = run.render;
    const show = cand ?? r.chosen;
    const c = r.candidates.find((x) => x.index === show) ?? r.candidates[0];
    return (
      <>
        <Photo src={runFile(run.id, c.comp)} w={w} h={h}>
          {show === r.chosen && run.result && (
            <ProductMarks items={run.result.products} w={w} h={h} focus={focus} onFocus={setFocus} showChips />
          )}
        </Photo>
        <div className="canvas-tabs">
          {r.candidates.map((x) => (
            <button key={x.index} className={`tab${x.index === show ? " on" : ""}`} onClick={() => setCand(x.index)}>
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
    if (!f) return <Photo src={runFile(run.id, run.render ? run.render.candidates[0].comp : run.room.before)} w={w} h={h} />;
    // a single wide crop (a rug, a sofa against a long wall) reads better stacked than side by side
    const [fx1, fy1, fx2, fy2] = f.region;
    const stacked = run.fixes!.length === 1 && (fx2 - fx1) / Math.max(1, fy2 - fy1) > 1.4;
    return (
      <div className={`fix-canvas${stacked ? " stacked" : ""}`}>
        {run.fixes!.map((fx) => (
          <div key={fx.slot} className="fix-canvas-row">
            <figure>
              <img src={runFile(run.id, fx.images[0])} alt="First render" />
              <figcaption className="mono">
                {fx.kind === "clear"
                  ? `first render · ${(fx.names ?? []).join(", ")} left`
                  : `first render · ${fx.before.verdict} ${fx.before.score}`}
              </figcaption>
            </figure>
            <figure>
              <img src={runFile(run.id, fx.images[1])} alt={fx.kind === "clear" ? "Cleared" : "Redrawn"} />
              <figcaption className="mono">
                {fx.kind === "clear" ? "cleared · checked with SAM 3.1" : `redrawn · ${fx.after.verdict} ${fx.after.score}`}
              </figcaption>
            </figure>
          </div>
        ))}
      </div>
    );
  }

  if (stage === "result" && run.result) {
    const res = run.result;
    const after = runFile(run.id, res.after);
    return (
      <>
        {mode === "compare" && (
          <Photo w={w} h={h}>
            <Compare
              before={before}
              after={after}
              afterLayer={<ProductMarks items={res.products} w={w} h={h} focus={focus} onFocus={setFocus} showOutline={!!focus} />}
            />
          </Photo>
        )}
        {mode === "proof" && (
          <Photo src={after} w={w} h={h}>
            {res.layers && <img className="photo-img layer-img" src={runFile(run.id, res.layers)} alt="" />}
            <LayerLegend run={run} />
          </Photo>
        )}
        {mode === "drift" && (
          <Photo src={runFile(run.id, res.drift)} w={w} h={h}>
            <div className="drift-legend">
              <span className="mono">What Muse Image changed on its own, before Realroom restored it</span>
              <span className="drift-scale" />
              <span className="mono faint drift-ends">
                <span>no change</span>
                <span>ΔE 30+</span>
              </span>
            </div>
          </Photo>
        )}
        <div className="canvas-tabs">
          <button className={`tab${mode === "compare" ? " on" : ""}`} onClick={() => setMode("compare")}>
            <Columns2 size={14} /> Before / after
          </button>
          <button className={`tab${mode === "proof" ? " on" : ""}`} onClick={() => setMode("proof")}>
            <Layers size={14} /> What changed
          </button>
          <button className={`tab${mode === "drift" ? " on" : ""}`} onClick={() => setMode("drift")}>
            <Activity size={14} /> Model drift
          </button>
        </div>
      </>
    );
  }

  return <Photo src={before} w={w} h={h} />;
}

function CandidateGrid({ run, which }: { run: Run; which: "raw" | "comp" }) {
  const r = run.render!;
  return (
    <div className={`cand-grid n${Math.min(r.candidates.length, 4)}`}>
      {r.candidates.map((c, i) => (
        <figure key={c.index} className="cand" style={{ animationDelay: `${i * 160}ms` }}>
          <Photo src={runFile(run.id, which === "raw" ? c.raw : c.comp)} w={run.room.w} h={run.room.h}>
            <figcaption className="canvas-cap mono">candidate {c.index} · {which === "raw" ? "raw output" : "aligned"}</figcaption>
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
