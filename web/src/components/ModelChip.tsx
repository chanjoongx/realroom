import { MODEL_INFO, modelsOf } from "../lib/format";

export function ModelChips({ model, compact = false }: { model?: string; compact?: boolean }) {
  const ms = modelsOf(model);
  if (!ms.length) return null;
  return (
    <span className="model-chips">
      {ms.map((m) => {
        const info = MODEL_INFO[m];
        return (
          <span key={m} className={`model-chip${compact ? " compact" : ""}`} style={{ color: info?.color ?? "var(--text-2)" }}>
            <span className="dot" />
            <span className="model-name">{info?.label ?? m}</span>
          </span>
        );
      })}
    </span>
  );
}
