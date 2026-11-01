import { Pause, Play, RotateCcw, SkipForward } from "lucide-react";
import { navigate } from "../lib/router";
import { secs, usd } from "../lib/format";
import type { Replay } from "../lib/replay";
import type { Run } from "../types";
import { Logo } from "./Logo";

export function TopBar({ run, rp }: { run: Run | null; rp: Replay | null }) {
  const last = rp?.visible[rp.visible.length - 1];
  const spent = rp?.visible.reduce((a, e) => a + (e.usd ?? 0), 0) ?? 0;
  return (
    <header className="topbar">
      <div className="tb-left">
        <Logo onClick={() => navigate("/")} />
        {run && (
          <span className="tb-crumb">
            <span className="faint">/</span>
            <span className="tb-room">{run.room.title || "Your room"}</span>
            {run.room.ai_generated && <span className="chip tb-ai" title={run.room.made_with}>AI-generated demo room</span>}
          </span>
        )}
      </div>
      {run && rp && (
        <div className="tb-right">
          <span className="tb-clock mono num" title="Real time and model cost of the recorded run, up to this step">
            <span>{secs(last?.t ?? 0)}</span>
            <span className="faint">{usd(spent) || "$0.00"}</span>
          </span>
          {rp.mode === "live" ? (
            <span className="chip live">
              <span className="dot" /> live
            </span>
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
              <button className="btn sm ghost" onClick={() => rp.setSpeed(rp.speed === 1 ? 2 : rp.speed === 2 ? 4 : 1)} aria-label="Speed">
                <span className="mono">{rp.speed}×</span>
              </button>
              {!rp.finished && (
                <button className="btn sm" onClick={rp.skip}>
                  <SkipForward size={14} /> Result
                </button>
              )}
            </span>
          )}
        </div>
      )}
      {rp && rp.mode === "replay" && <span className="tb-progress" style={{ transform: `scaleX(${rp.progress})` }} />}
    </header>
  );
}
