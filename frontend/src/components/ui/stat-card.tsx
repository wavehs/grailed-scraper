import { type ReactNode } from 'react';
import { cn } from '@/lib/utils';

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
        : 'text-[var(--text-muted)]';

  return (
    <div
      className={cn(
        'rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-surface)] p-4 group animate-slide-up',
        className,
      )}
    >
      <div className="flex items-start justify-between">
        <p className="text-xs font-medium uppercase tracking-wider text-[var(--text-muted)]">
          {label}
        </p>
        {icon && (
          <span className="text-[var(--text-muted)] transition-colors group-hover:text-[var(--accent)]">
            {icon}
          </span>
        )}
      </div>
      <p className={cn('mt-2 text-xl font-semibold tabular-nums tracking-tight', trendColor)}>
        {value}
      </p>
    </div>
  );
}
