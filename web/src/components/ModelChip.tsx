import { MODEL_INFO, modelsOf } from "../lib/format";

/** With `ids` each chip shows the model ID the run called (muse-spark-1.3) and keeps the name for the tooltip. */
export function ModelChips({ model, compact = false, ids = false }: { model?: string; compact?: boolean; ids?: boolean }) {
  const ms = modelsOf(model);
  if (!ms.length) return null;
  return (
    <span className="model-chips">
      {ms.map((m) => {
        const info = MODEL_INFO[m];
        const name = info?.label ?? m;
        const text = ids && info ? (m === "index" ? "catalog index" : m) : name;
        return (
          <span
            key={m}
            className={`model-chip${compact ? " compact" : ""}`}
            style={{ color: info?.color ?? "var(--text-2)" }}
            title={ids && text !== name ? name : undefined}
          >
            <span className="dot" />
            <span className="model-name">{text}</span>
          </span>
        );
      })}
    </span>
  );
}
