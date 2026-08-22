import { type ReactNode } from 'react';
import { cn } from '@/lib/utils';

type BadgeVariant = 'default' | 'success' | 'warning' | 'danger' | 'info' | 'muted';

const variantClasses: Record<BadgeVariant, string> = {
  default:
    'bg-[var(--accent-soft)] text-[var(--accent)] border-[var(--accent)]',
  success:
    'bg-[var(--success-bg)] text-[var(--success)] border-[var(--success-border)]',
  warning:
    'bg-[var(--warning-bg)] text-[var(--warning)] border-[var(--warning-border)]',
  danger:
    'bg-[var(--danger-bg)] text-[var(--danger)] border-[var(--danger-border)]',
  info:
    'bg-[var(--info-bg)] text-[var(--info)] border-[var(--info-border)]',
  muted:
    'bg-[var(--bg-surface-hover)] text-[var(--text-secondary)] border-[var(--border-default)]',
};

const dotColors: Record<BadgeVariant, string> = {
  default: 'bg-[var(--accent)]',
  success: 'bg-[var(--success)]',
  warning: 'bg-[var(--warning)]',
  danger: 'bg-[var(--danger)]',
  info: 'bg-[var(--info)]',
  muted: 'bg-[var(--text-muted)]',
};

/** Status‐aware string → variant. */
export function statusVariant(status?: string): BadgeVariant {
  if (!status) return 'muted';
  const s = status.toLowerCase();
  if (['ready', 'completed', 'verified', 'confirmed', 'auto_confirmed', 'ok', 'live'].includes(s))
    return 'success';
  if (['running', 'pending', 'discovering', 'normalizing', 'scoring', 'planning', 'mapping', 'refreshing'].includes(s))
    return 'info';
  if (['degraded', 'review', 'partial', 'stale', 'interrupted', 'cooldown', 'unresolved'].includes(s))
    return 'warning';
  if (['failed', 'unavailable', 'rejected', 'cancelled', 'error'].includes(s))
    return 'danger';
  return 'muted';
}

export function Badge({
  children,
  variant = 'default',
  dot = false,
  className,
}: {
  children: ReactNode;
  variant?: BadgeVariant;
  dot?: boolean;
  className?: string;
}) {
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1.5 rounded-md border px-1.5 py-0.5 text-[11px] font-medium leading-4',
        variantClasses[variant],
        className,
      )}
    >
      {dot && (
        <span className={cn('h-1.5 w-1.5 rounded-full animate-pulse-subtle', dotColors[variant])} />
      )}
      {children}
    </span>
  );
}
