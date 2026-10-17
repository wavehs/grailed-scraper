import { type ReactNode } from 'react';
import { cn } from '@/lib/utils';

/** A property cell: a muted label over a figure, as in Linear's project overview. */
export function StatCard({
  label,
  value,
  icon,
  className,
  trend,
}: {
  label: string;
  value: ReactNode;
  icon?: ReactNode;
  className?: string;
  trend?: 'up' | 'down' | 'neutral';
}) {
  const trendColor =
    trend === 'up'
      ? 'text-[var(--success)]'
      : trend === 'down'
        ? 'text-[var(--danger)]'
        : 'text-[var(--text-primary)]';

  return (
    <div
      className={cn(
        'rounded-md border border-[var(--border-subtle)] bg-[var(--bg-surface)] px-3 py-2.5',
        className,
      )}
    >
      <div className="flex items-center justify-between gap-2">
        <p className="truncate text-xs text-[var(--text-muted)]">{label}</p>
        {icon && <span className="text-[var(--text-muted)]">{icon}</span>}
      </div>
      <p className={cn('mt-1 text-[15px] font-medium tabular-nums', trendColor)}>{value}</p>
    </div>
  );
}
