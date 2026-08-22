import { CircleHelp } from 'lucide-react';

export function HelpTip({ label, text }: { label: string; text: string }) {
  return (
    <span
      aria-label={`${label}: ${text}`}
      className="inline-flex h-7 w-7 cursor-help items-center justify-center rounded-full text-[var(--text-muted)] hover:bg-[var(--bg-surface-hover)] hover:text-[var(--text-primary)]"
      role="img"
      title={text}
    >
      <CircleHelp size={15} />
    </span>
  );
}
