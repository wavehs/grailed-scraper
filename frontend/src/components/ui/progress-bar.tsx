import { cn } from '@/lib/utils';

export function ProgressBar({
  value,
  max = 100,
  className,
  label,
  size = 'md',
}: {
  value: number;
  max?: number;
  className?: string;
  label?: string;
  size?: 'sm' | 'md';
}) {
  const pct = max > 0 ? Math.min(Math.round((value / max) * 100), 100) : 0;
  return (
    <div className={cn('w-full', className)}>
      {label && (
        <div className="mb-1.5 flex items-center justify-between text-xs">
          <span className="text-[var(--text-secondary)]">{label}</span>
          <span className="font-medium text-[var(--text-primary)]">{pct}%</span>
        </div>
      )}
      <div
        className={cn(
          'overflow-hidden rounded-full bg-[var(--bg-surface-hover)]',
          size === 'sm' ? 'h-1.5' : 'h-2.5',
        )}
        role="progressbar"
        aria-valuenow={pct}
        aria-valuemin={0}
        aria-valuemax={100}
      >
        <div
          className="h-full rounded-full bg-[var(--accent)] transition-all duration-500 ease-out"
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}
