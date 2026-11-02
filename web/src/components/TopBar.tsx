import { Pause, Play, RotateCcw, SkipForward } from "lucide-react";
import { navigate } from "../lib/router";
import { secs, usd } from "../lib/format";
import type { Replay } from "../lib/replay";
import type { Run } from "../types";
import { Logo } from "./Logo";

export type Conn = "ok" | "retrying" | "stalled";

/** Every room says what is AI-made: demo rooms are AI-generated, and a live run's result is AI-edited. */
function aiLabel(run: Run): { text: string; title?: string } | null {
  if (run.room.ai_generated) return { text: "AI-generated demo room", title: run.room.made_with };
  if (run.result)
    return {
      text: "AI-edited: products drawn by Muse Image 1.0",
      title: "The new products were drawn by Muse Image 1.0. Every other pixel is copied from the original photo.",
    };
  return null;
}

/** A run followed live: live while it runs, then finished or stopped. */
function LiveChip({ status, conn }: { status: Run["status"]; conn: Conn }) {
  if (status === "error") return <span className="chip stopped">stopped</span>;
  if (status === "done") return <span className="chip">finished</span>;
  if (conn === "stalled") return <span className="chip stopped">no progress</span>;
  return (
    <span className="chip live">
      <span className="dot" /> live
    </span>
  );
}

export function TopBar({ run, rp, conn = "ok" }: { run: Run | null; rp: Replay | null; conn?: Conn }) {
  const last = rp?.visible[rp.visible.length - 1];
  const spent = rp?.visible.reduce((a, e) => a + (e.usd ?? 0), 0) ?? 0;
  const ai = run ? aiLabel(run) : null;
  return (
    <header className="topbar">
      <div className="tb-left">
        <Logo onClick={() => navigate("/")} />
        {run && (
          <span className="tb-crumb">
            <span className="faint">/</span>
            <span className="tb-room">{run.room.title || "Your room"}</span>
            {ai && (
              <span className="chip tb-ai" title={ai.title}>
                {ai.text}
              </span>
            )}
          </span>
        )}
      </div>
      {run && rp && (
        <div className="tb-right">
          <span
            className="tb-clock mono num"
            title={rp.mode === "live" ? "Time and model cost of this run so far" : "Real time and model cost of the recorded run, up to this step"}
          >
            <span>{secs(last?.t ?? 0)}</span>
            <span className="faint">{usd(spent)}</span>
          </span>
          {rp.mode === "live" ? (
            <LiveChip status={run.status} conn={conn} />
          ) : (
            <span className="tb-controls">
              <span className="chip">replay</span>
              {rp.playing ? (
                <button className="btn sm ghost" onClick={rp.pause} aria-label="Pause">
                  <Pause size={14} />
                </button>
              ) : (
                <button className="btn sm ghost" onClick={rp.play} aria-label="Play">
                  <Play size={14} />
                </button>
              )}
              <button className="btn sm ghost" onClick={rp.restart} aria-label="Restart">
                <RotateCcw size={14} />
              </button>
              <button className="btn sm ghost tb-speed" onClick={() => rp.setSpeed(rp.speed === 1 ? 2 : rp.speed === 2 ? 4 : 1)} aria-label="Speed">
                <span className="mono">{rp.speed}×</span>
              </button>
              {!rp.finished && (
                <button className="btn sm" onClick={rp.skip} aria-label="Skip to the result">
                  <SkipForward size={14} />
                  <span className="tb-skip-text">Result</span>
                </button>
              )}
            </span>
          )}
        </div>
      )}
      {/* on a phone the label gets its own row next to the controls */}
      {ai && (
        <span className="chip tb-ai-row" title={ai.title}>
          {ai.text}
        </span>
      )}
      {rp && rp.mode === "replay" && <span className="tb-progress" style={{ transform: `scaleX(${rp.progress})` }} />}
    </header>
  );
}
