import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Run, RunEvent, StageId } from "../types";

export const STAGES: StageId[] = ["scan", "plan", "search", "pick", "render", "verify", "fix", "result"];

export interface Replay {
  mode: "replay" | "live";
  visible: RunEvent[];
  stage: StageId | null;
  reached: Set<StageId>;
  working: StageId | null;
  progress: number;
  playing: boolean;
  speed: number;
  finished: boolean;
  play: () => void;
  pause: () => void;
  restart: () => void;
  skip: () => void;
  setSpeed: (s: number) => void;
}

const clamp = (x: number, a: number, b: number) => Math.min(b, Math.max(a, x));

/** Virtual times: long model calls are compressed so a recorded run replays in about half a
 *  minute, while every step stays on screen long enough to read. */
function virtualTimes(events: RunEvent[]): number[] {
  const out: number[] = [];
  let v = 0.6;
  events.forEach((ev, i) => {
    if (i > 0) {
      const prev = events[i - 1];
      const gap = Math.max(0, ev.t - prev.t);
      v += prev.status === "start" ? clamp(gap * 0.06, 2.2, 3.8) : clamp(gap * 0.04, 1.2, 2.6);
    }
    out.push(v);
  });
  return out;
}

/** `followLive`: the run was still going when the page opened, so show every event as it
 *  arrives (and keep showing all of them after it finishes) instead of replaying from zero. */
export function useReplay(run: Run | null, autoplay = true, followLive = false): Replay {
  const events = useMemo(() => run?.events ?? [], [run]);
  const live = run?.status === "running" || followLive;
  const vts = useMemo(() => virtualTimes(events), [events]);
  const total = vts.length ? vts[vts.length - 1] + 0.5 : 0;

  const clockRef = useRef(0);
  const [count, setCount] = useState(0);
  const [progress, setProgress] = useState(0);
  const [playing, setPlaying] = useState(autoplay);
  const [speed, setSpeed] = useState(1);

  const sync = useCallback(
    (c: number) => {
      clockRef.current = c;
      let n = 0;
      while (n < vts.length && vts[n] <= c) n++;
      setCount(n);
      setProgress(total ? Math.min(1, c / total) : 0);
    },
    [vts, total],
  );

  useEffect(() => {
    if (live || !playing || !events.length) return;
    let raf = 0;
    let last: number | null = null;
    let lastPaint = 0;
    const tick = (now: number) => {
      const dt = last === null ? 0 : (now - last) / 1000;
      last = now;
      const c = Math.min(total, clockRef.current + dt * speed);
      clockRef.current = c;
      if (now - lastPaint > 90 || c >= total) {
        lastPaint = now;
        sync(c);
      }
      if (c >= total) {
        setPlaying(false);
        return;
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [live, playing, speed, total, events.length, sync]);

  const visible = useMemo(() => (live ? events : events.slice(0, count)), [live, events, count]);

  const derived = useMemo(() => {
    const reached = new Set<StageId>();
    let stage: StageId | null = null;
    for (const ev of visible) {
      if (ev.stage === "error") continue;
      reached.add(ev.stage);
      stage = ev.stage;
    }
    const lastEv = visible[visible.length - 1];
    let working: StageId | null = null;
    if (lastEv && lastEv.status === "start" && lastEv.stage !== "error") working = lastEv.stage;
    else if (run?.status === "running" && stage) working = nextStage(stage);
    return { reached, stage, working };
  }, [visible, run?.status]);

  const play = useCallback(() => {
    if (clockRef.current >= total) sync(0);
    setPlaying(true);
  }, [total, sync]);
  const pause = useCallback(() => setPlaying(false), []);
  const restart = useCallback(() => {
    sync(0);
    setPlaying(true);
  }, [sync]);
  const skip = useCallback(() => {
    sync(total);
    setPlaying(false);
  }, [total, sync]);

  return {
    mode: run?.status === "running" ? "live" : followLive ? "live" : "replay",
    visible,
    stage: derived.stage,
    reached: derived.reached,
    working: derived.working,
    progress: live ? 0 : progress,
    playing: live ? true : playing,
    speed,
    finished: !live && count >= events.length && events.length > 0,
    play,
    pause,
    restart,
    skip,
    setSpeed,
  };
}

function nextStage(s: StageId): StageId | null {
  const order: StageId[] = ["scan", "plan", "search", "pick", "render", "verify", "result"];
  const i = order.indexOf(s);
  return i >= 0 && i < order.length - 1 ? order[i + 1] : null;
}
