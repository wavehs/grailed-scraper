import { cn } from '@/lib/utils';

export function ProgressBar({
  value,
  max = 100,
  className,
  label,
  size = 'md',
  indeterminate = false,
}: {
  value: number;
  max?: number;
  className?: string;
  label?: string;
  size?: 'sm' | 'md';
  indeterminate?: boolean;
}) {
  const pct = max > 0 ? Math.min(Math.round((value / max) * 100), 100) : 0;
  return (
    <div className={cn('w-full', className)}>
      {label && (
        <div className="mb-1.5 flex items-center justify-between text-xs">
          <span className="text-[var(--text-secondary)]">{label}</span>
          <span className="font-medium text-[var(--text-primary)]">
            {indeterminate ? '…' : `${pct}%`}
          </span>
        </div>
      )}
      <div
        className={cn(
          'overflow-hidden rounded-full bg-[var(--bg-surface-hover)]',
          size === 'sm' ? 'h-1.5' : 'h-2.5',
        )}
        role="progressbar"
        aria-label={label}
        aria-valuenow={indeterminate ? undefined : pct}
        aria-valuemin={0}
        aria-valuemax={100}
      >
        <div
          className={cn(
            'h-full rounded-full',
            indeterminate
              ? 'w-full animate-shimmer'
              : 'bg-[var(--accent)] transition-[width] duration-500 ease-out',
          )}
          style={indeterminate ? undefined : { width: `${pct}%` }}
        />
      </div>
    </div>
  );
}
