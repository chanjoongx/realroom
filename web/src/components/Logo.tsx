export function Mark({ size = 22 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden="true">
      <rect x="3.5" y="3.5" width="25" height="25" rx="6" fill="none" stroke="currentColor" strokeWidth="2.2" />
      <path d="M16 3.5v25" stroke="currentColor" strokeWidth="2.2" />
      <circle cx="22.2" cy="16" r="3" fill="var(--new)" />
    </svg>
  );
}

export function Logo({ onClick }: { onClick?: () => void }) {
  return (
    <button className="logo" onClick={onClick} aria-label="Realroom home">
      <Mark />
      <span className="logo-word">Realroom</span>
    </button>
  );
}
