import { useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { getRun } from "../api";
import { CanvasView } from "../components/CanvasView";
import { PANELS } from "../components/Inspector";
import { Rail } from "../components/Rail";
import { TopBar } from "../components/TopBar";
import { navigate } from "../lib/router";
import { useReplay } from "../lib/replay";
import type { Run, StageId } from "../types";

export function Studio({ id }: { id: string }) {
  const [run, setRun] = useState<Run | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const wasLive = useRef(false);

  useEffect(() => {
    let alive = true;
    let timer: number | undefined;
    const ctrl = new AbortController();
    const load = async () => {
      try {
        const r = await getRun(id, ctrl.signal);
        if (!alive) return;
        if (r.status === "running") wasLive.current = true;
        setRun(r);
        if (r.status === "running") timer = window.setTimeout(load, 1500);
      } catch (e) {
        if (alive && !ctrl.signal.aborted) setErr(e instanceof Error ? e.message : String(e));
      }
    };
    load();
    return () => {
      alive = false;
      ctrl.abort();
      if (timer) window.clearTimeout(timer);
    };
  }, [id]);

  const rp = useReplay(run, true, wasLive.current);
  const [picked, setPicked] = useState<StageId | null>(null);
  const [focus, setFocus] = useState<string | null>(null);

  useEffect(() => {
    setPicked(null);
    setFocus(null);
  }, [rp.stage]);

  // ?view=result (or any stage) opens a finished run at that step without the replay
  const initialView = useRef<string | null>(new URLSearchParams(window.location.search).get("view"));
  useEffect(() => {
    const v = initialView.current;
    if (!v || run?.status !== "done") return;
    if (!rp.finished) {
      rp.skip();
      return;
    }
    if (["scan", "plan", "search", "pick", "render", "verify", "fix", "result"].includes(v)) setPicked(v as StageId);
    initialView.current = null;
  }, [run?.status, rp.finished, rp]);

  if (err) {
    return (
      <div className="studio">
        <TopBar run={null} rp={null} />
        <div className="studio-error">
          <h2 className="serif">This run could not be loaded.</h2>
          <p className="muted">{err}</p>
          <button className="btn" onClick={() => navigate("/")}>
            Back to the rooms
          </button>
        </div>
      </div>
    );
  }
  if (!run) {
    return (
      <div className="studio">
        <TopBar run={null} rp={null} />
        <div className="studio-loading mono faint">loading run {id}</div>
      </div>
    );
  }

  const current: StageId | null = picked && rp.reached.has(picked) ? picked : rp.stage;
  const Panel = current ? PANELS[current] : null;
  const failed = run.status === "error" && rp.visible.some((e) => e.stage === "error");

  return (
    <div className="studio">
      <TopBar run={run} rp={rp} />
      <div className="studio-body">
        <Rail run={run} events={rp.visible} reached={rp.reached} working={rp.working} selected={current} onSelect={setPicked} />
        <main className="canvas-area">
          <CanvasView run={run} events={rp.visible} stage={current} working={rp.working} focus={focus} setFocus={setFocus} />
        </main>
        <aside className="inspector scroll-thin">
          <AnimatePresence mode="wait">
            <motion.div
              key={current ?? "none"}
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -6 }}
              transition={{ duration: 0.22, ease: [0.2, 0.7, 0.2, 1] }}
            >
              {failed ? (
                <div className="ph">
                  <span className="label">Stopped</span>
                  <h2 className="ph-title serif">The run stopped</h2>
                  <p className="ph-sub">{run.error}</p>
                </div>
              ) : Panel ? (
                <Panel run={run} events={rp.visible} focus={focus} setFocus={setFocus} />
              ) : (
                <div className="ph">
                  <span className="label">Starting</span>
                  <h2 className="ph-title serif">Reading the room</h2>
                  <p className="ph-sub">{run.brief}</p>
                </div>
              )}
            </motion.div>
          </AnimatePresence>
        </aside>
      </div>
    </div>
  );
}
