import { MODEL_INFO, modelsOf } from "../lib/format";

const OFFLINE_TITLE = "Wrote the catalog tags offline, before any run. Not called during this run.";

/** With `ids` each chip shows the model ID the run called (muse-spark-1.3) and keeps the name for the tooltip.
 *  `offline` lists models whose work the step uses but does not call: they get a hollow dot and "tags, offline". */
export function ModelChips({
  model,
  offline,
  compact = false,
  ids = false,
}: {
  model?: string;
  offline?: string;
  compact?: boolean;
  ids?: boolean;
}) {
  const ms = modelsOf(model);
  const off = modelsOf(offline);
  if (!ms.length && !off.length) return null;
  const chip = (m: string, isOffline: boolean) => {
    const info = MODEL_INFO[m];
    const name = info?.label ?? m;
    const text = ids && info ? (m === "index" ? "catalog index" : m) : name;
    const title = isOffline ? `${name}: ${OFFLINE_TITLE}` : ids && text !== name ? name : undefined;
    return (
      <span
        key={`${isOffline ? "off-" : ""}${m}`}
        className={`model-chip${compact ? " compact" : ""}${isOffline ? " offline" : ""}`}
        style={{ color: info?.color ?? "var(--text-2)" }}
        title={title}
      >
        <span className="dot" />
        <span className="model-name">{isOffline ? `${text} tags, offline` : text}</span>
      </span>
    );
  };
  return (
    <span className="model-chips">
      {ms.map((m) => chip(m, false))}
      {off.map((m) => chip(m, true))}
    </span>
  );
}
