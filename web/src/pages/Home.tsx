import { useCallback, useEffect, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { ArrowRight, ImageUp, LoaderCircle } from "lucide-react";
import { getConfig, getDemos, netMessage, runFile, startRun } from "../api";
import { Compare } from "../components/Compare";
import { Logo } from "../components/Logo";
import { ModelChips } from "../components/ModelChip";
import { Photo } from "../components/Photo";
import { navigate } from "../lib/router";
import { pct, secs, usd } from "../lib/format";
import { reducedMotion } from "../lib/stages";
import type { AppConfig, Demo } from "../types";

const GITHUB = "https://github.com/chanjoongx/realroom";
/** the size of the catalog in the repo, shown until the server's config answers */
const CATALOG_SIZE = 4375;

type Load<T> = { state: "loading" } | { state: "ok"; data: T } | { state: "error" };

/** Fetches once on mount; the returned function fetches again. */
function useLoad<T>(fetcher: (signal: AbortSignal) => Promise<T>): [Load<T>, () => void] {
  const [st, setSt] = useState<Load<T>>({ state: "loading" });
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const ctrl = new AbortController();
    fetcher(ctrl.signal).then(
      (data) => {
        if (!ctrl.signal.aborted) setSt({ state: "ok", data });
      },
      () => {
        if (!ctrl.signal.aborted) setSt({ state: "error" });
      },
    );
    return () => ctrl.abort();
  }, [fetcher, attempt]);
  const retry = useCallback(() => {
    setSt({ state: "loading" });
    setAttempt((a) => a + 1);
  }, []);
  return [st, retry];
}

export function Home() {
  const [demos, retryDemos] = useLoad<Demo[]>(getDemos);
  const [cfg, retryCfg] = useLoad<AppConfig>(getConfig);
  useEffect(() => {
    document.title = "Realroom";
  }, []);
  const rooms = demos.state === "ok" ? demos.data : [];
  const hero = rooms[0];
  const catalogN = (cfg.state === "ok" ? cfg.data.catalog_size : undefined) ?? CATALOG_SIZE;

  return (
    <div className="home">
      <header className="home-nav">
        <Logo onClick={() => navigate("/")} />
        <nav className="home-links">
          <a href="#rooms">Demo rooms</a>
          <a href="#how">How it works</a>
          <a href="#try">Your photo</a>
          <a href={GITHUB} target="_blank" rel="noreferrer">
            GitHub
          </a>
        </nav>
      </header>

      <main>
        <section className="hero">
          <div className="hero-text">
            <span className="label">Room makeovers with real catalog products</span>
            <h1 className="hero-h serif">
              Try real furniture in a photo of your room. <em>Keep every other pixel.</em>
            </h1>
            <p className="hero-sub">
              Realroom measures the room in your photo, picks catalog products that fit, and draws them in. Then it checks
              each product against its catalog photo and copies everything else back from your original.
            </p>
            <div className="hero-cta">
              <a
                className="btn primary"
                href={hero ? `/r/${hero.id}` : "#rooms"}
                onClick={(e) => {
                  // a plain click opens the run in place; a modifier click keeps the browser's own behavior
                  if (!hero || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
                  e.preventDefault();
                  navigate(`/r/${hero.id}`);
                }}
              >
                Watch a demo run <ArrowRight size={15} />
              </a>
              <a className="btn" href="#try">
                <ImageUp size={15} /> Use your own photo
              </a>
            </div>
            <div className="hero-stats mono num">
              <span>
                <b>{catalogN.toLocaleString("en-US")}</b> real products with real dimensions
              </span>
              <span>
                <b>5</b> Meta models
              </span>
              {hero && (
                <span>
                  <b>{pct(hero.untouched_pct)}</b> of the demo photo untouched
                </span>
              )}
            </div>
          </div>
          <div className="hero-visual">
            {hero ? (
              <>
                <Photo w={16} h={12}>
                  <Compare
                    before={runFile(hero.id, hero.before)}
                    after={runFile(hero.id, hero.after)}
                    alts={[`${hero.title}, an AI-generated demo room, before`, `${hero.title} after Realroom added real products`]}
                    priority
                    rest={32}
                  />
                </Photo>
                <span className="hero-cap mono faint">{hero.title} · AI-generated demo room · drag to compare</span>
              </>
            ) : (
              <div className={`hero-ph${demos.state === "loading" ? "" : " is-static"}`} />
            )}
          </div>
        </section>

        <section className="rooms" id="rooms">
          <div className="sec-head">
            <span className="label">Demo rooms</span>
            <h2 className="serif">Watch the agent work, step by step.</h2>
            <p className="muted">
              Each one is a recorded run: every model call, timing and cost is real. The replay shortens the waits. The
              rooms are AI-generated with Muse Image 1.0.
            </p>
          </div>
          <div className="room-grid">
            {rooms.map((d) => (
              <button key={d.id} className="room-card" onClick={() => navigate(`/r/${d.id}`)}>
                <span className="room-img">
                  <img src={runFile(d.id, d.before)} alt="" className="ri-before" loading="lazy" decoding="async" />
                  <img src={runFile(d.id, d.after)} alt="" className="ri-after" loading="lazy" decoding="async" />
                  <span className="room-kicker mono">{d.kicker}</span>
                  <span className="room-before mono" aria-hidden="true">Before</span>
                </span>
                <span className="room-body">
                  <span className="room-title">{d.title}</span>
                  <span className="room-brief faint">“{d.brief}”</span>
                  <span className="room-stats mono num">
                    <span>{d.products} products</span>
                    <span>{pct(d.untouched_pct)} untouched</span>
                    <span>{secs(d.elapsed_s)}</span>
                    <span>{usd(d.cost_usd)}</span>
                  </span>
                </span>
              </button>
            ))}
            {demos.state === "loading" && [0, 1, 2].map((i) => <div key={i} className="room-card skeleton-card" />)}
          </div>
          {demos.state === "error" && (
            <div className="rooms-msg">
              <p className="muted">The demo rooms could not be loaded.</p>
              <button className="btn sm" onClick={retryDemos}>
                Try again
              </button>
            </div>
          )}
          {demos.state === "ok" && !rooms.length && (
            <div className="rooms-msg">
              <p className="muted">This server has no demo rooms yet.</p>
            </div>
          )}
        </section>

        <section className="how" id="how">
          <div className="sec-head">
            <span className="label">How it works</span>
            <h2 className="serif">Five Meta models, one agent loop.</h2>
          </div>
          <ol className="how-list">
            <Step n="01" title="Scan" model="muse-spark-1.3 + sam-3.1">
              Muse Spark 1.3 lists every object and the fixed structure, and estimates the room's size from doors, beds and
              ceilings. SAM 3.1 turns each loose box into an exact outline.
            </Step>
            <Step n="02" title="Plan and search" model="muse-spark-1.3 + pe-core-b16-224" offline="muse-spark-open">
              Spark decides what to add or replace and how big each piece should be. The catalog search keeps only products
              that fit. All {catalogN.toLocaleString("en-US")} products were described by Muse Spark Open, the open-weight
              model, running on Baseten and Fireworks, and Meta's Perception Encoder compares the plan with their photos.
            </Step>
            <Step n="03" title="Pick" model="muse-spark-1.3">
              Spark sees the room and a numbered sheet of candidates for every slot in a single request, so it picks a set
              that works together.
            </Step>
            <Step n="04" title="Render" model="muse-image-1.0">
              Muse Image 1.0 draws two or three candidates from the photo, a placement guide and the real product photos.
            </Step>
            <Step n="05" title="Verify and fix" model="sam-3.1 + muse-spark-1.3">
              Each candidate is aligned to the original. SAM 3.1 finds every new product again and Spark compares it with
              its catalog photo. A product that is only partly right is redrawn on its own, two tries a round, and Spark's
              notes steer the next round until it matches.
            </Step>
            <Step n="06" title="Keep the room" model="">
              Only the new products, their shadows and the cleared spots come from the render. Everything else is copied
              from your photo, and your browser can check the count.
            </Step>
          </ol>
        </section>

        <Upload cfg={cfg} retry={retryCfg} />

        <section className="stores">
          <div className="sec-head">
            <span className="label">For stores</span>
            <h2 className="serif">Bring your own catalog.</h2>
            <p className="muted">
              Realroom needs one photo and the dimensions of each product. The demo runs on Amazon Berkeley Objects, a
              public set of real Amazon listings. A furniture store could plug in its own feed and let shoppers see the
              exact item, at its real size, in their own room.
            </p>
          </div>
        </section>
      </main>

      <footer className="home-foot">
        <div>
          <Logo onClick={() => window.scrollTo({ top: 0, behavior: reducedMotion() ? "auto" : "smooth" })} />
          <p className="faint">Built for the Meta Global AI Developer Hackathon, 2026.</p>
          <p className="faint">Built with Muse Spark Open.</p>
        </div>
        <div className="credits faint">
          <p>
            Product photos and data: <a href="https://amazon-berkeley-objects.s3.amazonaws.com/index.html" target="_blank" rel="noreferrer">Amazon Berkeley Objects</a> by Amazon.com,
            licensed <a href="https://creativecommons.org/licenses/by/4.0/" target="_blank" rel="noreferrer">CC BY 4.0</a>. Photos were trimmed and
            resized, and product descriptions were written by Muse Spark Open. Realroom is not affiliated with or endorsed by Amazon.
          </p>
          <p>
            Demo rooms are AI-generated with Muse Image 1.0. Prices are not shown because the listings come from a dataset
            published in 2021.
          </p>
          <p>
            <a href={GITHUB} target="_blank" rel="noreferrer">Source on GitHub</a>
          </p>
        </div>
      </footer>
    </div>
  );
}

function Step({
  n,
  title,
  model,
  offline,
  children,
}: {
  n: string;
  title: string;
  model: string;
  /** models whose earlier work the step uses without calling them */
  offline?: string;
  children: ReactNode;
}) {
  return (
    <li className="how-step">
      <span className="how-n mono">{n}</span>
      <div>
        <h3>{title}</h3>
        {(model || offline) && <ModelChips model={model} offline={offline} />}
        <p className="muted">{children}</p>
      </div>
    </li>
  );
}

/** Downscale to at most 1792 px and re-encode as JPEG in the browser: smaller upload, and the
 *  photo's EXIF data (location included) never leaves the device. */
async function prepare(file: File): Promise<{ blob: Blob; url: string; w: number; h: number }> {
  const bmp = await createImageBitmap(file, { imageOrientation: "from-image" });
  const s = Math.min(1, 1792 / Math.max(bmp.width, bmp.height));
  const w = Math.round(bmp.width * s);
  const h = Math.round(bmp.height * s);
  const c = document.createElement("canvas");
  c.width = w;
  c.height = h;
  try {
    const ctx = c.getContext("2d");
    if (!ctx) throw new Error("no canvas");
    ctx.drawImage(bmp, 0, 0, w, h);
  } finally {
    bmp.close();
  }
  const blob: Blob = await new Promise((res, rej) => c.toBlob((b) => (b ? res(b) : rej(new Error("encode failed"))), "image/jpeg", 0.92));
  return { blob, url: URL.createObjectURL(blob), w, h };
}

const MODES = [
  { id: "refresh", label: "Refresh a lived-in room" },
  { id: "furnish", label: "Furnish an empty room" },
] as const;
type Mode = (typeof MODES)[number]["id"];

/** Arrow keys move the choice in a radio group, like native radio buttons. */
const ARROW_STEP: Record<string, number> = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 };

function Upload({ cfg, retry }: { cfg: Load<AppConfig>; retry: () => void }) {
  const [photo, setPhoto] = useState<{ blob: Blob; url: string; w: number; h: number } | null>(null);
  const [brief, setBrief] = useState("");
  const [mode, setMode] = useState<Mode>("refresh");
  const [busy, setBusy] = useState(false);
  // a problem with the photo shows at the drop zone, a problem starting the run under the button
  const [fileErr, setFileErr] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [over, setOver] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const modeBtns = useRef<(HTMLButtonElement | null)[]>([]);
  const conf = cfg.state === "ok" ? cfg.data : null;
  const live = conf?.live ?? false;
  const left = conf?.runs_left_today;
  const noneLeft = live && left === 0;

  // release the preview when the photo is replaced or the page closes
  useEffect(() => {
    if (!photo) return;
    return () => URL.revokeObjectURL(photo.url);
  }, [photo]);

  async function onFile(f?: File) {
    if (!f) return;
    setFileErr(null);
    setErr(null);
    if (!/^image\/(jpeg|png|webp)$/.test(f.type)) {
      setFileErr("Use a JPEG, PNG or WebP photo.");
      return;
    }
    try {
      setPhoto(await prepare(f));
    } catch {
      setFileErr("This photo could not be read.");
    }
  }

  function onModeKey(e: KeyboardEvent<HTMLDivElement>) {
    const step = ARROW_STEP[e.key];
    if (!step) return;
    e.preventDefault();
    const i = MODES.findIndex((m) => m.id === mode);
    const j = (i + step + MODES.length) % MODES.length;
    setMode(MODES[j].id);
    modeBtns.current[j]?.focus();
  }

  async function go() {
    if (!photo) return;
    setBusy(true);
    setErr(null);
    try {
      const { id } = await startRun(photo.blob, brief.trim() || "Make it feel finished and comfortable.", mode);
      navigate(`/r/${id}`);
    } catch (e) {
      setErr(netMessage(e));
      setBusy(false);
    }
  }

  return (
    <section className="try" id="try">
      <div className="sec-head">
        <span className="label">Your photo</span>
        <h2 className="serif">Run it on your own room.</h2>
        <p className="muted">
          A wide photo taken from a corner works best. A run takes about four to eight minutes. Your photo is sent to Meta's
          model API for this run and deleted from Realroom within 24 hours.
        </p>
      </div>
      <div className="try-box">
        <div className="drop-col">
          <div
            className={`drop${photo ? " has" : ""}${over ? " is-over" : ""}${fileErr ? " is-error" : ""}`}
            onClick={() => input.current?.click()}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                input.current?.click();
              }
            }}
            onDragEnter={(e) => {
              e.preventDefault();
              setOver(true);
            }}
            onDragOver={(e) => {
              e.preventDefault();
              if (!over) setOver(true);
            }}
            onDragLeave={(e) => {
              // leaving for a child of the drop zone is not leaving it
              if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setOver(false);
            }}
            onDrop={(e) => {
              e.preventDefault();
              setOver(false);
              onFile(e.dataTransfer.files?.[0]);
            }}
            role="button"
            tabIndex={0}
            aria-label={photo ? "Change the room photo" : undefined}
            aria-describedby={fileErr ? "drop-err" : undefined}
          >
            {photo ? (
              <img src={photo.url} alt="Your room" />
            ) : (
              <span className="drop-hint">
                <ImageUp size={22} />
                <span>{over ? "Drop the photo here" : "Drop a room photo, or click to choose one"}</span>
                <span className="faint mono">JPEG, PNG or WebP</span>
              </span>
            )}
            <input ref={input} type="file" accept="image/jpeg,image/png,image/webp" hidden onChange={(e) => onFile(e.target.files?.[0])} />
          </div>
          {fileErr && (
            <p id="drop-err" className="err" role="alert">
              {fileErr}
            </p>
          )}
        </div>
        <div className="try-form">
          <div className="seg" role="radiogroup" aria-label="What should Realroom do" onKeyDown={onModeKey}>
            {MODES.map((m, i) => (
              <button
                key={m.id}
                ref={(el) => {
                  modeBtns.current[i] = el;
                }}
                type="button"
                role="radio"
                aria-checked={mode === m.id}
                tabIndex={mode === m.id ? 0 : -1}
                className={mode === m.id ? "on" : ""}
                onClick={() => setMode(m.id)}
              >
                {m.label}
              </button>
            ))}
          </div>
          <label className="label" htmlFor="brief">
            What do you want? (optional)
          </label>
          <textarea
            id="brief"
            rows={3}
            maxLength={280}
            placeholder="Warmer and less cluttered. I rent, so the walls and floor stay."
            value={brief}
            onChange={(e) => setBrief(e.target.value)}
          />
          <button className="btn primary" disabled={!photo || busy || !live || noneLeft} onClick={go}>
            {busy ? <LoaderCircle size={15} className="spin" /> : <ArrowRight size={15} />}
            {busy ? "Starting" : "Start the run"}
          </button>
          {cfg.state === "loading" && <p className="faint try-off">Checking the live server…</p>}
          {cfg.state === "error" && (
            <div className="try-retry">
              <p className="faint try-off">Could not reach the live server.</p>
              <button className="btn sm" onClick={retry}>
                Try again
              </button>
            </div>
          )}
          {conf && !live && (
            <p className="faint try-off">{conf.reason ?? "Live runs are off on this server. The demo rooms show complete recorded runs."}</p>
          )}
          {noneLeft && <p className="faint try-off">No live runs are left today. The demo rooms show complete recorded runs.</p>}
          {live && !noneLeft && left !== undefined && (
            <p className="faint mono">
              {left} live run{left === 1 ? "" : "s"} left today, shared by all visitors
            </p>
          )}
          {err && (
            <p className="err" role="alert">
              {err}
            </p>
          )}
        </div>
      </div>
    </section>
  );
}
