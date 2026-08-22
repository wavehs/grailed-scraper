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
      ? 'text-[#34d399]'
      : trend === 'down'
        ? 'text-[#fb7185]'
        : 'text-[var(--text-muted)]';

  return (
    <div
      className={cn(
        'glass rounded-xl p-5 transition-all duration-200 hover:border-[rgba(255,255,255,0.1)] group animate-slide-up',
        className,
      )}
    >
      <div className="flex items-start justify-between">
        <p className="text-xs font-medium uppercase tracking-wider text-[var(--text-muted)]">
          {label}
        </p>
        {icon && (
          <span className="text-[var(--text-muted)] group-hover:text-[#818cf8] transition-colors duration-200">
            {icon}
          </span>
        )}
      </div>
      <p className={cn('mt-2 text-2xl font-bold tracking-tight', trendColor)}>
        {value}
      </p>
    </div>
  );
}
