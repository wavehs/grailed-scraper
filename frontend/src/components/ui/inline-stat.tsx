import { type ReactNode } from 'react';
import { HelpTip } from '@/components/ui/help-tip';

/** One figure of a status strip: a muted label over its value, with optional help. */
export function InlineStat({ label, help, children }: { label: string; help?: string; children: ReactNode }) {
  return (
    <div className="min-w-0">
      <p className="flex items-center gap-1 text-xs text-[var(--text-muted)]">
        {label}
        {help && <HelpTip label={label} text={help} />}
      </p>
      <div className="text-[13px] tabular-nums text-[var(--text-primary)]">{children}</div>
    </div>
  );
}
