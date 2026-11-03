import { useEffect, useRef, useState, type CSSProperties } from "react";
import { AnimatePresence, motion } from "motion/react";
import { LoaderCircle, TriangleAlert } from "lucide-react";
import { getRun, netMessage } from "../api";
import { CanvasView } from "../components/CanvasView";
import { PANELS, Working } from "../components/Inspector";
import { Rail } from "../components/Rail";
import { TopBar, type ClockAnchor, type Conn } from "../components/TopBar";
import { navigate } from "../lib/router";
import { useReplay } from "../lib/replay";
import { isStageId, plainError, STAGE_NAME, stoppedStage } from "../lib/stages";
import { matchLine } from "../lib/verdicts";
import type { Run, StageId } from "../types";

const POLL_MS = 1500; // between polls while a live run is going
const MAX_BACKOFF_MS = 15_000; // longest wait between tries after failed requests
// No new event for this long: stop polling and say so. One image call may wait 300 s and be retried once,
// and the server itself marks a run interrupted once no worker owns it, so this is only the last resort.
const STALL_MS = 15 * 60_000;
const REQUEST_MS = 20_000; // one request may take this long

const RECONNECTING = "Connection lost. Reconnecting…";
const STALLED = "No new progress for 15 minutes. The server may have restarted, so this run may not finish.";

export function Studio({ id }: { id: string }) {
  const [run, setRun] = useState<Run | null>(null);
  const [clock, setClock] = useState<ClockAnchor | null>(null);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [conn, setConn] = useState<Conn>("ok");
  // the run was still going when it loaded: show every event as it arrives instead of replaying
  const [followLive, setFollowLive] = useState(false);
  // bumping it loads the run again (Try again, Check again)
  const [attempt, setAttempt] = useState(0);
  const haveRun = useRef(false);

  // Polls a live run. A failed request keeps the last run on screen and tries again, waiting
  // longer each time; a hidden tab stops polling until it is shown; five minutes without a new
  // event ends the polling with a message, so a run the server lost is not polled forever.
  useEffect(() => {
    let alive = true;
    let timer: number | undefined;
    let req: AbortController | null = null;
    let failures = 0;
    let lastKey = "";
    let lastChange = Date.now();
    let waitingForTab = false;

    async function tick() {
      timer = undefined;
      if (!alive) return;
      if (document.hidden && haveRun.current) {
        waitingForTab = true;
        return;
      }
      const ctrl = new AbortController();
      req = ctrl;
      const limit = window.setTimeout(() => ctrl.abort(new DOMException("timed out", "TimeoutError")), REQUEST_MS);
      try {
        const r = await getRun(id, ctrl.signal);
        if (!alive) return;
        failures = 0;
        haveRun.current = true;
        const key = `${r.status}:${r.events?.length ?? 0}`;
        if (key !== lastKey) {
          lastKey = key;
          lastChange = Date.now();
        }
        if (r.status === "running") setFollowLive(true);
        // the top bar's clock counts on from the response that puts the run furthest along: the server
        // writes elapsed_s only with each event, so any response can only be behind the true time
        const e = typeof r.elapsed_s === "number" ? r.elapsed_s : 0;
        const at = performance.now();
        setClock((c) => (c && c.e - c.at / 1000 >= e - at / 1000 ? c : { e, at }));
        setRun(r);
        setLoadErr(null);
        if (r.status !== "running") {
          setConn("ok");
        } else if (Date.now() - lastChange > STALL_MS) {
          setConn("stalled");
        } else {
          setConn("ok");
          timer = window.setTimeout(tick, POLL_MS);
        }
      } catch (e) {
        if (!alive) return;
        if (!haveRun.current) {
          setLoadErr(netMessage(e));
        } else if (Date.now() - lastChange > STALL_MS) {
          setConn("stalled");
        } else {
          failures++;
          setConn("retrying");
          timer = window.setTimeout(tick, Math.min(MAX_BACKOFF_MS, POLL_MS * 2 ** failures));
        }
      } finally {
        window.clearTimeout(limit);
        if (req === ctrl) req = null;
      }
    }

    const onVisibility = () => {
      if (alive && !document.hidden && waitingForTab) {
        waitingForTab = false;
        tick();
      }
    };
    document.addEventListener("visibilitychange", onVisibility);
    tick();
    return () => {
      alive = false;
      req?.abort();
      window.clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [id, attempt]);

  const rp = useReplay(run, true, followLive);
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
    if (isStageId(v)) setPicked(v);
    initialView.current = null;
  }, [run?.status, rp.finished, rp]);

  const roomTitle = run ? run.room.title || "Your room" : null;
  useEffect(() => {
    document.title = roomTitle ? `${roomTitle} · Realroom` : "Realroom";
  }, [roomTitle]);

  if (loadErr) {
    const gone = loadErr === "No such run.";
    return (
      <div className="studio">
        <TopBar run={null} rp={null} />
        <main className="studio-error">
          <h1 className="serif">This run could not be loaded.</h1>
          <p className="muted">
            {loadErr}
            {gone && " Live runs are deleted within 24 hours."}
          </p>
          <div className="studio-error-actions">
            {!gone && (
              <button
                className="btn"
                onClick={() => {
                  setLoadErr(null);
                  setAttempt((a) => a + 1);
                }}
              >
                Try again
              </button>
            )}
            <button className="btn" onClick={() => navigate("/")}>
              Back to the rooms
            </button>
          </div>
        </main>
      </div>
    );
  }
  if (!run) {
    // live run ids are "u", the UTC start minute and 24 hex digits; any other id is a recorded demo
    const what = /^u\d{8}[0-9a-f]{24}$/.test(id) ? "Loading the run" : "Loading the recorded run";
    return (
      <div className="studio">
        <TopBar run={null} rp={null} />
        <div className="studio-body is-loading">
          <div className="rail sk-rail" aria-hidden="true">
            {[0, 1, 2, 3, 4, 5, 6].map((i) => (
              <span key={i} className="sk-step">
                <i />
                <b style={{ width: `${[58, 52, 66, 60, 70, 74, 48][i]}%` }} />
              </span>
            ))}
          </div>
          <main className="canvas-area">
            <div className="photo-fit" style={{ "--ar": "4 / 3", "--arn": 4 / 3 } as CSSProperties}>
              <div className="photo-box sk-photo">
                <p className="sk-msg" role="status">
                  {what}
                </p>
              </div>
            </div>
          </main>
          <aside className="inspector sk-ins" aria-hidden="true">
            <span className="sk-line short" />
            <span className="sk-line title" />
            <span className="sk-line" />
            <span className="sk-line mid" />
          </aside>
        </div>
      </div>
    );
  }

  // a stopped run shows its failure once the replay (or the live view) reaches the error event
  const errorEvent = run.events.find((e) => e.stage === "error");
  const failed =
    run.status === "error" && (errorEvent ? rp.visible.includes(errorEvent) : rp.visible.length >= run.events.length);
  const stopAt: StageId | null = failed ? stoppedStage(rp.visible, run.stopped_at) : null;
  const current: StageId | null = picked && (rp.reached.has(picked) || picked === stopAt) ? picked : (stopAt ?? rp.stage);
  const Panel = current ? PANELS[current] : null;
  const atStop = stopAt !== null && current === stopAt;
  const stopFinished = atStop && rp.visible.some((e) => e.stage === stopAt && e.status === "done");
  const stopMsg = failed ? plainError(run.error ?? errorEvent?.detail) : null;
  const interrupted = run.stopped_at === "interrupted";

  // one polite announcement at a time: the connection, the failure, or the newest step
  const last = rp.visible.findLast((e) => e.stage !== "error");
  const announce =
    conn === "retrying"
      ? RECONNECTING
      : conn === "stalled"
        ? STALLED
        : stopMsg
          ? `${interrupted ? "The run was interrupted." : "The run stopped."} ${stopMsg.text}`
          : last && last.stage !== "error"
            ? `${STAGE_NAME[last.stage]}: ${last.stage === "result" && run.result ? matchLine(run.result) : last.title}`
            : "";

  return (
    <div className="studio">
      <TopBar run={run} rp={rp} conn={conn} clock={clock} />
      <div className="sr-only" role="status" aria-live="polite" aria-atomic="true">
        {announce}
      </div>
      {conn !== "ok" && (
        <div className={`conn-note${conn === "stalled" ? " is-stalled" : ""}`}>
          {conn === "retrying" ? <LoaderCircle size={13} className="spin" /> : <TriangleAlert size={13} />}
          <span>{conn === "retrying" ? RECONNECTING : STALLED}</span>
          {conn === "stalled" && (
            <button
              className="btn sm"
              onClick={() => {
                setConn("ok");
                setAttempt((a) => a + 1);
              }}
            >
              Check again
            </button>
          )}
        </div>
      )}
      <div className="studio-body">
        <Rail
          run={run}
          events={rp.visible}
          reached={rp.reached}
          working={rp.working}
          selected={current}
          stoppedAt={stopAt}
          onSelect={setPicked}
        />
        {/* on a phone the canvas is sized to the photo (--room-hw, height over width) */}
        <main className="canvas-area" style={{ "--room-hw": run.room.h / run.room.w } as CSSProperties}>
          <h1 className="sr-only">
            {roomTitle}, {rp.mode === "live" ? "live run" : "recorded run"}
          </h1>
          <CanvasView
            run={run}
            events={rp.visible}
            stage={current}
            working={rp.working}
            stoppedAt={stopAt}
            focus={focus}
            setFocus={setFocus}
          />
        </main>
        <aside className="inspector scroll-thin">
          {stopMsg && (
            <div className="stop-banner">
              <span className="label">
                {interrupted ? "Interrupted" : "Stopped"}
                {stopAt && ` at ${STAGE_NAME[stopAt]}`}
              </span>
              <h2 className="ph-title serif">{interrupted ? "The run was interrupted" : "The run stopped"}</h2>
              <p className="ph-sub">{stopMsg.text}</p>
              {stopMsg.detail && (
                <details className="stop-detail">
                  <summary className="label">Server message</summary>
                  <pre>{stopMsg.detail}</pre>
                </details>
              )}
            </div>
          )}
          <AnimatePresence mode="wait">
            <motion.div
              key={current ?? "none"}
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -6 }}
              transition={{ duration: 0.22, ease: [0.2, 0.7, 0.2, 1] }}
            >
              {atStop && !stopFinished ? (
                <Working stage={stopAt} events={rp.visible} text="" stopped />
              ) : Panel ? (
                <Panel run={run} events={rp.visible} focus={focus} setFocus={setFocus} stopped={atStop} />
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
