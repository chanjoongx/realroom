import type { ReactNode } from "react";
import { ArrowRight, Check, CircleHelp, CornerDownRight, TriangleAlert } from "lucide-react";
import { imageUrl, runFile, thumbUrl } from "../api";
import { kindLabel, pct, secs, sizeCm, sizeIn, titleCase, undash, usd } from "../lib/format";
import { isMatch, recordProducts, verdictLabel } from "../lib/verdicts";
import type { FixRecord, Product, Run, RunEvent, SceneObject, Slot, StageId, Verdict } from "../types";
import { BoxCrop } from "./BoxCrop";
import { ModelChips } from "./ModelChip";
import { verdictTone } from "./Overlays";
import { PixelCheck } from "./PixelCheck";

export interface PanelProps {
  run: Run;
  events: RunEvent[];
  focus: string | null;
  setFocus: (id: string | null) => void;
  /** the run stopped during this step */
  stopped?: boolean;
}

const STEP_NAME: Record<StageId, string> = {
  scan: "01 Scan",
  plan: "02 Plan",
  search: "03 Search",
  pick: "04 Pick",
  render: "05 Render",
  verify: "06 Verify",
  fix: "07 Fix",
  result: "Result",
};

function stageCost(events: RunEvent[], stage: StageId) {
  const evs = events.filter((e) => e.stage === stage && e.status !== "start");
  const sec = evs.reduce((a, e) => a + (e.sec ?? 0), 0);
  const cost = evs.reduce((a, e) => a + (e.usd ?? 0), 0);
  const model = [...new Set(events.filter((e) => e.stage === stage && e.model).flatMap((e) => e.model!.split(" + ")))].join(" + ");
  return { sec, cost, model };
}

function Head({ stage, title, sub, events }: { stage: StageId; title: ReactNode; sub?: ReactNode; events: RunEvent[] }) {
  const c = stageCost(events, stage);
  return (
    <header className="ph">
      <div className="ph-top">
        <span className="label">{STEP_NAME[stage]}</span>
        <span className="ph-meta mono num">
          {c.sec > 0 && <span>{secs(c.sec)}</span>}
          {c.cost > 0 && <span>{usd(c.cost)}</span>}
        </span>
      </div>
      <h2 className="ph-title serif">{title}</h2>
      {sub && <p className="ph-sub">{sub}</p>}
      {c.model && <ModelChips model={c.model} ids />}
    </header>
  );
}

function Section({ title, children, aside }: { title: string; children: ReactNode; aside?: ReactNode }) {
  return (
    <section className="ins-sec">
      <div className="ins-sec-head">
        <h3 className="label">{title}</h3>
        {aside}
      </div>
      {children}
    </section>
  );
}

function Fact({ k, v, note }: { k: string; v: ReactNode; note?: ReactNode }) {
  return (
    <div className="fact">
      <span className="fact-k label">{k}</span>
      <span className="fact-v">{v}</span>
      {note && <span className="fact-n faint">{note}</span>}
    </div>
  );
}

/* ------------------------------------------------------------------ scan */

export function ScanPanel({ run, events, focus, setFocus, stopped }: PanelProps) {
  const s = run.scan;
  // a replay already has the scan, so wait for the event that finished it
  if (!s || !events.some((e) => e.stage === "scan" && e.status === "done"))
    return (
      <Working
        stage="scan"
        events={events}
        stopped={stopped}
        text="Muse Spark 1.3 is reading the photo. SAM 3.1 will trace every object it finds."
      />
    );
  const d = s.dimensions_cm;
  const groups: { cls: SceneObject["class"]; label: string }[] = [
    { cls: "replace", label: "Replace" },
    { cls: "remove", label: "Clear away" },
    { cls: "keep", label: "Keep" },
  ];
  return (
    <>
      <Head stage="scan" events={events} title={titleCase(s.room_type)} sub={s.summary} />
      <div className="facts">
        <Fact k="Floor" v={<span className="mono num">{d.width} × {d.depth} cm</span>} note={`${d.confidence} confidence`} />
        <Fact k="Ceiling" v={<span className="mono num">{d.ceiling} cm</span>} />
        <Fact k="Light" v={titleCase(s.light.quality)} note={`${s.light.direction}, ${s.light.color_temperature}`} />
      </div>
      <p className="ins-note faint">Scale from: {d.basis}</p>
      {groups.map((g) => {
        const objs = s.objects.filter((o) => o.class === g.cls);
        if (!objs.length) return null;
        return (
          <Section key={g.cls} title={`${g.label} · ${objs.length}`}>
            <ul className="obj-list">
              {objs.map((o) => (
                <li
                  key={o.id}
                  className={`obj-row c-${o.class}${focus === o.id ? " is-focus" : ""}`}
                  onMouseEnter={() => setFocus(o.id)}
                  onMouseLeave={() => setFocus(null)}
                >
                  <span className="dot" />
                  <span className="mono id">{o.id}</span>
                  <span className="obj-name">
                    {o.name}
                    {o.class !== "keep" && <span className="obj-reason faint">{o.reason}</span>}
                  </span>
                  <span className="mono num faint iou" title="Overlap between Spark's box and SAM's outline box">
                    {o.iou !== null ? o.iou.toFixed(2) : "box"}
                  </span>
                </li>
              ))}
            </ul>
          </Section>
        );
      })}
      <Section title={`Fixed · ${s.fixed.length}`}>
        <p className="ins-text muted">{s.fixed.map((f) => f.name).join(", ")}.</p>
        <p className="ins-note faint">Walls, floor, windows and built-ins stay exactly as they are. That rule is enforced on the pixels, not only in the prompt.</p>
      </Section>
      <p className="ins-foot faint">
        Spark drew a loose box around each object; SAM 3.1 traced its exact outline from that box ({s.stats.sam_requests}{" "}
        request{s.stats.sam_requests === 1 ? "" : "s"}). Mean overlap between the two boxes:{" "}
        <span className="mono num">{s.stats.mean_iou ?? "n/a"}</span>.
      </p>
    </>
  );
}

/* ------------------------------------------------------------------ plan */

export function PlanPanel({ run, events, focus, setFocus }: PanelProps) {
  const s = run.scan;
  if (!s) return null;
  const plan = s.plan;
  const objs = Object.fromEntries(s.objects.map((o) => [o.id, o]));
  return (
    <>
      <Head stage="plan" events={events} title={plan.title} sub={plan.idea} />
      <blockquote className="brief">
        <span className="label">Brief</span>
        <p>{run.brief}</p>
      </blockquote>
      <div className="chips-row">
        <span className="chip">{plan.style}</span>
        {plan.palette.map((c) => (
          <span key={c} className="chip">
            {c}
          </span>
        ))}
      </div>
      <Section title={`${plan.slots.length} products to find`}>
        <ol className="slot-list">
          {plan.slots.map((sl, i) => (
            <li
              key={sl.id}
              className={`slot-row${focus === sl.id ? " is-focus" : ""}`}
              onMouseEnter={() => setFocus(sl.id)}
              onMouseLeave={() => setFocus(null)}
            >
              <span className="slot-badge mono">{i + 1}</span>
              <div className="slot-body">
                <div className="slot-title">
                  {kindLabel(sl.kind)}
                  <span className="mono num faint">{sizeCm(sl.size_cm, sl.kind)}</span>
                </div>
                {sl.action === "replace" && objs[sl.replaces] && (
                  <div className="slot-replaces">
                    <CornerDownRight size={12} /> replaces {objs[sl.replaces].name}
                  </div>
                )}
                <p className="slot-look">“{sl.look}”</p>
                <p className="slot-why faint">{sl.placement}</p>
              </div>
            </li>
          ))}
        </ol>
      </Section>
    </>
  );
}

/* ------------------------------------------------------------------ search */

function FunnelBars({ sl }: { sl: Slot }) {
  const f = sl.funnel;
  if (!f) return null;
  const rows = [
    { k: `${kindLabel(sl.kind).toLowerCase()} in catalog`, v: f.kind },
    { k: f.fit_rule ? `fit ${f.fit_rule}` : `fit ${sizeCm(sl.size_cm, sl.kind)} ±${Math.round(f.size_tolerance * 100)}%`, v: f.fits },
    { k: `short list (${(f.ranked_by ?? ["text"]).map((r) => (r === "pe-core" ? "PE-Core" : r)).join(" + ")})`, v: f.shortlist ?? 0 },
  ];
  const max = Math.max(1, f.kind);
  return (
    <div className="funnel">
      {rows.map((r, i) => (
        <div key={i} className="funnel-row">
          <div className="funnel-bar" style={{ width: `${Math.max(3, (r.v / max) * 100)}%`, animationDelay: `${i * 120}ms` }} />
          <span className="funnel-v mono num">{r.v.toLocaleString("en-US")}</span>
          <span className="funnel-k faint">{r.k}</span>
        </div>
      ))}
    </div>
  );
}

export function SearchPanel({ run, events, focus, setFocus }: PanelProps) {
  const slots = run.plan?.slots ?? [];
  const total = events.find((e) => e.stage === "search" && e.status !== "start")?.title ?? "Searching the catalog";
  return (
    <>
      <Head
        stage="search"
        events={events}
        title={total}
        sub="Each product has to be the right kind, fit the space Spark measured, and look like what the plan asked for."
      />
      {slots.map((sl, i) => (
        <Section key={sl.id} title={`${i + 1} · ${kindLabel(sl.kind)}`}>
          <div className={`search-slot${focus === sl.id ? " is-focus" : ""}`} onMouseEnter={() => setFocus(sl.id)} onMouseLeave={() => setFocus(null)}>
            <FunnelBars sl={sl} />
            <div className="thumb-grid">
              {(sl.shortlist ?? []).slice(0, 12).map((pid, k) => (
                <span key={pid} className="thumb" style={{ animationDelay: `${k * 30}ms` }}>
                  <img src={thumbUrl(pid)} alt="" loading="lazy" />
                </span>
              ))}
              {(sl.shortlist?.length ?? 0) > 12 && <span className="thumb more mono">+{(sl.shortlist?.length ?? 0) - 12}</span>}
            </div>
          </div>
        </Section>
      ))}
      <p className="ins-foot faint">
        The catalog holds real products from Amazon Berkeley Objects, each with its listed dimensions. Muse Spark Open (open
        weights, served on Baseten and Fireworks) wrote a visual description of every product photo. Meta's Perception
        Encoder (PE-Core, open weights, run by the app's own server) compares the plan's wording with the photos
        themselves. The search ranks by both.
      </p>
    </>
  );
}

/* ------------------------------------------------------------------ pick */

export function ProductCard({
  p,
  verdict,
  note,
  big = false,
  focus = false,
  onHover,
}: {
  p: Product;
  verdict?: Verdict;
  note?: ReactNode;
  big?: boolean;
  focus?: boolean;
  onHover?: (on: boolean) => void;
}) {
  return (
    <div
      className={`pcard${big ? " big" : ""}${focus ? " is-focus" : ""}`}
      onMouseEnter={() => onHover?.(true)}
      onMouseLeave={() => onHover?.(false)}
    >
      <span className="pcard-img">
        <img src={thumbUrl(p.id)} alt={p.title} loading="lazy" />
      </span>
      <div className="pcard-body">
        <div className="pcard-title">{p.title}</div>
        <div className="pcard-sub faint">
          {p.brand ? `${p.brand} · ` : ""}
          {kindLabel(p.kind)}
        </div>
        <div className="pcard-size mono num">
          <span>{sizeCm(p.size_cm, p.kind)}</span>
          <span className="faint">{sizeIn(p.size_cm, p.kind)}</span>
        </div>
        {verdict?.verdict && <VerdictChip v={verdict} />}
        {note}
      </div>
    </div>
  );
}

export function VerdictChip({ v }: { v: Verdict }) {
  const unverified = v.verdict === "unverified";
  return (
    <span className={`verdict tone-${verdictTone(v)}`}>
      {v.verdict === "match" ? <Check size={12} /> : unverified ? <CircleHelp size={12} /> : <TriangleAlert size={12} />}
      <span className="mono">{verdictLabel(v.verdict)}</span>
      {v.score !== undefined && !unverified && <span className="mono num">{v.score}/10</span>}
      {v.fixed && <span className="mono">redrawn</span>}
    </span>
  );
}

export function PickPanel({ run, events, focus, setFocus }: PanelProps) {
  const plan = run.plan;
  const prods = run.products ?? {};
  if (!plan) return null;
  return (
    <>
      <Head stage="pick" events={events} title="Picked as a set" sub={plan.set_comment} />
      {plan.slots.map((sl, i) => {
        const p = sl.pick ? prods[sl.pick] : undefined;
        if (!p) return null;
        return (
          <Section key={sl.id} title={`${i + 1} · ${kindLabel(sl.kind)}`} aside={<span className="mono faint num">#{sl.pick_number} of {sl.shortlist?.length ?? 0}</span>}>
            <ProductCard p={p} big focus={focus === sl.id} onHover={(on) => setFocus(on ? sl.id : null)} note={<p className="pcard-why">{sl.reason}</p>} />
            {!!sl.alternates?.length && (
              <div className="alts">
                <span className="label">Alternates</span>
                {sl.alternates.map((a) => (
                  <span key={a} className="thumb sm" title={prods[a]?.title}>
                    <img src={thumbUrl(a)} alt={prods[a]?.title ?? ""} loading="lazy" />
                  </span>
                ))}
              </div>
            )}
          </Section>
        );
      })}
      <p className="ins-foot faint">Spark saw the room and one numbered sheet of candidates per slot in a single request, so it judged the products against each other and against the room.</p>
    </>
  );
}

/* ------------------------------------------------------------------ render */

export function RenderPanel({ run, events }: PanelProps) {
  const r = run.render;
  const slots = run.plan?.slots ?? [];
  const prods = run.products ?? {};
  const n = events.find((e) => e.stage === "render" && e.status === "start")?.title ?? "Drawing candidates";
  return (
    <>
      <Head
        stage="render"
        events={events}
        title={n}
        sub="Muse Image 1.0 gets the untouched photo, a numbered placement guide and one real photo per product, and draws several candidates in one call."
      />
      <Section title="Inputs">
        <div className="input-strip">
          <span className="in-img">
            <img src={runFile(run.id, run.room.before)} alt="Room photo" />
            <span className="in-cap mono">photo</span>
          </span>
          <span className="in-img">
            <img src={runFile(run.id, "guide.jpg")} alt="Placement guide" />
            <span className="in-cap mono">guide</span>
          </span>
          {slots.map((s, i) =>
            s.pick ? (
              <span key={s.id} className="in-img prod">
                <img src={thumbUrl(s.pick)} alt={prods[s.pick]?.title ?? ""} />
                <span className="in-cap mono">product {i + 1}</span>
              </span>
            ) : null,
          )}
        </div>
      </Section>
      {r && (
        <details className="prompt-box">
          <summary className="label">Prompt sent to Muse Image 1.0</summary>
          <pre>{undash(r.prompt)}</pre>
        </details>
      )}
    </>
  );
}

/* ------------------------------------------------------------------ verify */

export function VerifyPanel({ run, events, focus, setFocus, stopped }: PanelProps) {
  const r = run.render;
  const slots = run.plan?.slots ?? [];
  const prods = run.products ?? {};
  if (!r)
    return (
      <Working
        stage="verify"
        events={events}
        stopped={stopped}
        text="Aligning each candidate to the original photo and inspecting every product."
      />
    );
  const chosen = r.candidates.find((c) => c.index === r.chosen);
  return (
    <>
      <Head
        stage="verify"
        events={events}
        title={`Kept candidate ${r.chosen} of ${r.candidates.length}`}
        sub="Each candidate is aligned to the original photo, every new product is found again with SAM 3.1, and Spark compares each one with its catalog photo."
      />
      <Section title="Candidates">
        <div className="cand-table-wrap scroll-thin">
        <table className="cand-table">
          <thead>
            <tr>
              <th className="label">#</th>
              {slots.map((s, i) => (
                <th key={s.id} className="label">
                  P{i + 1}
                </th>
              ))}
              <th className="label">Scene</th>
              <th className="label">Score</th>
            </tr>
          </thead>
          <tbody>
            {r.candidates.map((c) => (
              <tr key={c.index} className={c.index === r.chosen ? "is-chosen" : ""}>
                <td className="mono">{c.index}</td>
                {slots.map((s, i) => {
                  const v = c.judge.products?.find((p) => p.index === i + 1);
                  // the verdict is written under the score, so it does not rest on the color alone
                  return (
                    <td key={s.id}>
                      <span className={`dotv tone-${verdictTone(v)}`}>{v?.score ?? "?"}</span>
                      <span className="dotv-word">{verdictLabel(v?.verdict) || "no verdict"}</span>
                    </td>
                  );
                })}
                <td className="mono num">{c.judge.scene?.overall ?? "?"}</td>
                <td className="mono num">{c.score.toFixed(3)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        </div>
      </Section>
      {chosen && (
        <Section title={`Candidate ${chosen.index}, product by product`}>
          <div className="pairs">
            {slots.map((s, i) => {
              // what Spark said about this candidate at this step; a later fix shows up in the next step
              const rp = run.result?.products.find((x) => x.slot === s.id);
              const v = chosen.judge.products?.find((x) => x.index === i + 1) ?? rp?.verdict;
              const box = chosen.boxes?.[i] ?? rp?.box;
              const p = s.pick ? prods[s.pick] : undefined;
              if (!p) return null;
              return (
                <div key={s.id} className={`pair${focus === s.id ? " is-focus" : ""}`} onMouseEnter={() => setFocus(s.id)} onMouseLeave={() => setFocus(null)}>
                  <div className="pair-imgs">
                    <figure>
                      <img src={imageUrl(p.id)} alt={`${p.title}, catalog photo`} loading="lazy" />
                      <figcaption className="mono">catalog</figcaption>
                    </figure>
                    <ArrowRight size={14} className="faint" />
                    <figure>
                      {box && (
                        <BoxCrop src={runFile(run.id, chosen.comp)} box={box} w={run.room.w} h={run.room.h} label={`${p.title}, in the room`} />
                      )}
                      <figcaption className="mono">in your room</figcaption>
                    </figure>
                  </div>
                  <div className="pair-body">
                    <div className="pair-title">
                      {i + 1}. {p.title}
                    </div>
                    {v && <VerdictChip v={v} />}
                    {!!v?.issues?.length && <p className="faint pair-issues">{v.issues.map(asSentence).join(" ")}</p>}
                  </div>
                </div>
              );
            })}
          </div>
        </Section>
      )}
      {chosen?.judge.scene && <SceneScores s={chosen.judge.scene} />}
    </>
  );
}

/** Spark lists issues as bare phrases; show each as a sentence so they do not run together. */
function asSentence(s: string): string {
  const t = s.trim().replace(/[.;,]+$/, "");
  return t ? t.charAt(0).toUpperCase() + t.slice(1) + "." : "";
}

function SceneScores({ s, title = "Whole photo" }: { s: NonNullable<Run["result"]>["scene"]; title?: string }) {
  return (
    <Section title={title}>
      <div className="facts">
        <Fact k="Photoreal" v={<span className="mono num">{s.photoreal}/10</span>} />
        <Fact k="Cohesion" v={<span className="mono num">{s.cohesion}/10</span>} />
        <Fact k="Renter rule" v={s.renter_rule_ok ? "kept" : "broken"} />
      </div>
      {s.comment && <p className="ins-text muted">{s.comment}</p>}
    </Section>
  );
}

/* ------------------------------------------------------------------ fix */

function leftPct(left?: Record<string, number>): string {
  const v = left ? Math.max(0, ...Object.values(left)) : 0;
  return `${Math.round(100 * v)}% left`;
}

function FixTries({ f }: { f: FixRecord }) {
  const tries = f.tries ?? [];
  if (tries.length < 2) return null;
  const rounds = [...new Set(tries.map((t) => t.round))];
  const best = Math.max(...tries.map((t) => t.score ?? -1));
  return (
    <div className="fix-tries">
      {rounds.map((r) => (
        <div key={r} className="fix-round">
          <span className="mono faint">round {r}</span>
          {tries
            .filter((t) => t.round === r)
            .map((t, i) => (
              <span
                key={i}
                className={`try-pill tone-${verdictTone({ verdict: t.verdict } as Verdict)}${
                  f.accepted && t.score === best ? " chosen" : ""
                }`}
                title={`${verdictLabel(t.verdict) || "?"} ${t.score ?? "?"}/10`}
              >
                <span className="mono num">{t.score ?? "?"}</span>
              </span>
            ))}
        </div>
      ))}
      <span className="mono faint">
        {tries.length} redraws, each judged by Spark
      </span>
    </div>
  );
}

function fixTitle(fixes: FixRecord[]): string {
  const redraws = fixes.filter((f) => f.kind !== "clear").length;
  const clears = fixes.length - redraws;
  const parts = [];
  if (redraws) parts.push(`Redrew ${redraws} product${redraws > 1 ? "s" : ""}`);
  if (clears) parts.push(redraws ? "cleared clutter" : "Cleared clutter");
  return parts.length ? parts.join(", ") : "Nothing to redraw";
}

export function FixPanel({ run, events, stopped }: PanelProps) {
  // a live run writes its fixes when the step ends: until then the step is still working
  if (!run.fixes && run.status !== "done")
    return (
      <Working
        stage="fix"
        events={events}
        stopped={stopped}
        text="Muse Image 1.0 is redrawing the products that came out only partly right. SAM 3.1 finds each try again and Spark judges it."
      />
    );
  const fixes = run.fixes ?? [];
  const prods = run.products ?? {};
  return (
    <>
      <Head
        stage="fix"
        events={events}
        title={fixTitle(fixes)}
        sub="Weak products are cut out and redrawn against their catalog photo, and Spark keeps a redraw only if it scores higher."
      />
      <details className="prompt-box how-box">
        <summary className="label">How the redraw works</summary>
        <p>
          A product that comes out only partly right is cut out with its surroundings and redrawn larger, two tries per
          round. A product the render left out is added at its planned spot the same way. SAM 3.1 finds each try again
          and Spark judges it. Its notes steer the next round, until the product matches or two rounds are spent. Clutter
          the render left behind gets its own pass, checked again with SAM 3.1.
        </p>
      </details>
      {fixes.map((f) =>
        f.kind === "clear" ? (
          <Section key={f.slot} title={`Clutter: ${(f.names ?? []).join(", ")}`}>
            <div className="fix-pair">
              <figure>
                <img src={runFile(run.id, f.images[0])} alt="Before clearing" />
                <figcaption className="mono">first render</figcaption>
              </figure>
              <figure>
                <img src={runFile(run.id, f.images[1])} alt="After clearing" />
                <figcaption className="mono">cleared</figcaption>
              </figure>
            </div>
            <div className="fix-verdicts">
              <span className="verdict tone-warn">
                <span className="mono">SAM 3.1: {leftPct(f.before.left)}</span>
              </span>
              <ArrowRight size={14} className="faint" />
              <span className={`verdict tone-${f.accepted ? "good" : "warn"}`}>
                <span className="mono">{leftPct(f.after.left)}</span>
              </span>
              <span className={`mono ${f.accepted ? "ok" : "faint"}`}>{f.accepted ? "kept" : "first version kept"}</span>
            </div>
          </Section>
        ) : (
        <Section key={f.slot} title={prods[f.product ?? ""]?.title ?? f.product ?? ""}>
          <div className="fix-pair">
            <figure>
              <img src={runFile(run.id, f.images[0])} alt="Before the redraw" />
              <figcaption className="mono">first render</figcaption>
            </figure>
            <figure>
              <img src={runFile(run.id, f.images[1])} alt="After the redraw" />
              <figcaption className="mono">redrawn</figcaption>
            </figure>
          </div>
          <div className="fix-verdicts">
            <VerdictChip v={f.before} />
            <ArrowRight size={14} className="faint" />
            <VerdictChip v={f.after} />
            <span className={`mono ${f.accepted ? "ok" : "faint"}`}>{f.accepted ? "kept" : "first version kept"}</span>
          </div>
          <FixTries f={f} />
        </Section>
        ),
      )}
    </>
  );
}

/* ------------------------------------------------------------------ result */

export function ResultPanel({ run, events, focus, setFocus }: PanelProps) {
  const res = run.result;
  const slots = run.plan?.slots ?? [];
  const prods = run.products ?? {};
  if (!res) return null;
  const pr = res.proof;
  const lp = pr.layers_pct;
  // each product is shown and counted with its verdict of record (see lib/verdicts.ts)
  const recs = recordProducts(res);
  const matches = recs.filter((p) => isMatch(p.verdict)).length;
  // runs since 11-03: every product was counted from separate checks of the finished photo
  const looks = recs.find((p) => Array.isArray(p.verdict.checks))?.verdict.of;
  const objs = Object.fromEntries((run.scan?.objects ?? []).map((o) => [o.id, o]));
  return (
    <>
      <Head
        stage="result"
        events={events}
        title={`${matches} of ${recs.length} products verified`}
        sub={run.plan?.title}
      />
      {/* the proof comes first: the products are numbered on the photo already */}
      <Section title="Proof">
        <div className="proof-big">
          <span className="proof-num serif num">{pr.untouched_pct.toFixed(1)}%</span>
          <span className="proof-cap">of the photo is pixel-identical to the original.</span>
        </div>
        {lp && (
          <>
            <div className="stack-bar" aria-hidden="true">
              <span style={{ width: `${lp.products}%`, background: "var(--new)" }} />
              <span style={{ width: `${lp.cleared}%`, background: "var(--remove)" }} />
              <span style={{ width: `${lp.shadows}%`, background: "var(--shadow-layer)" }} />
              <span style={{ width: `${pr.untouched_pct}%`, background: "var(--surface-3)" }} />
            </div>
            <div className="stack-legend mono num">
              <span><i style={{ background: "var(--new)" }} />products {pct(lp.products)}</span>
              <span><i style={{ background: "var(--remove)" }} />cleared {pct(lp.cleared)}</span>
              <span><i style={{ background: "var(--shadow-layer)" }} />shadows {pct(lp.shadows)}</span>
              <span><i style={{ background: "var(--surface-3)" }} />untouched {pct(pr.untouched_pct)}</span>
            </div>
          </>
        )}
        <p className="ins-text muted">
          Muse Image redraws the whole frame, and on its own it shifted this photo by up to{" "}
          <span className="mono num">{pr.alignment_shift_px.toFixed(1)} px</span> and changed colors everywhere (mean ΔE{" "}
          <span className="mono num">{pr.model_drift_dE}</span>). Realroom aligns the render back to the photo and keeps
          only the new products, their shadows and the cleared spots. Every other pixel is copied from the original.
        </p>
        {res.lossless && (
          <PixelCheck
            beforeUrl={runFile(run.id, res.lossless.before)}
            afterUrl={runFile(run.id, res.lossless.after)}
            claimed={pr.untouched_pct}
          />
        )}
      </Section>
      <Section title="In this photo">
        {looks ? (
          <p className="pcard-note faint">
            Muse Spark 1.3 checked each product {looks} times on its final pixels, each check on its own. A product
            counts as a match when at least {Math.floor(looks / 2) + 1} of them say match.
          </p>
        ) : null}
        <div className="pcards">
          {recs.map((rp, i) => {
            const p = prods[rp.product];
            const sl = slots.find((s) => s.id === rp.slot);
            if (!p) return null;
            return (
              <ProductCard
                key={rp.slot}
                p={p}
                verdict={{ ...rp.verdict, fixed: rp.fixed }}
                focus={focus === rp.slot}
                onHover={(on) => setFocus(on ? rp.slot : null)}
                note={
                  <>
                    {rp.note && <div className="pcard-note faint">{rp.note}</div>}
                    {sl?.action === "replace" && objs[sl.replaces] ? (
                      <div className="pcard-note faint">
                        <span className="mono">{i + 1}</span> replaces the {objs[sl.replaces].name}
                      </div>
                    ) : (
                      <div className="pcard-note faint">
                        <span className="mono">{i + 1}</span> added
                      </div>
                    )}
                  </>
                }
              />
            );
          })}
        </div>
      </Section>
      <SceneScores
        s={res.scene}
        title={
          res.scene_first_render
            ? "Whole photo, judged again after the fixes"
            : (run.fixes ?? []).some((f) => f.accepted)
              ? "Whole photo, judged before the fixes"
              : "Whole photo"
        }
      />
      <p className="ins-foot faint">
        {secs(run.elapsed_s)} and {usd(run.cost_usd)} of model calls for this run.
      </p>
    </>
  );
}

/* ------------------------------------------------------------------ misc */

/** A step still under way, or (`stopped`) the step a stopped run never finished. */
export function Working({
  stage,
  events,
  text,
  stopped = false,
}: {
  stage: StageId;
  events: RunEvent[];
  text: string;
  stopped?: boolean;
}) {
  if (stopped)
    return (
      <>
        <Head stage={stage} events={events} title="Stopped" />
        <p className="ins-text muted">This step did not finish.</p>
      </>
    );
  return (
    <>
      <Head stage={stage} events={events} title="Working" />
      <p className="ins-text muted">{text}</p>
      <div className="skeleton" />
      <div className="skeleton short" />
    </>
  );
}

export const PANELS: Record<StageId, (p: PanelProps) => ReactNode> = {
  scan: ScanPanel,
  plan: PlanPanel,
  search: SearchPanel,
  pick: PickPanel,
  render: RenderPanel,
  verify: VerifyPanel,
  fix: FixPanel,
  result: ResultPanel,
};
