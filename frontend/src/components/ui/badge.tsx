import { type ReactNode } from 'react';
import { cn } from '@/lib/utils';

export type BadgeVariant = 'default' | 'success' | 'warning' | 'danger' | 'info' | 'muted';

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

/**
 * Linear's label pill: a coloured dot and plain text on a hairline outline. The colour lives
 * in the dot only, so a row of pills stays calm. `dot` makes the dot pulse for live states.
 */
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
        'inline-flex h-5 items-center gap-1.5 whitespace-nowrap rounded-full border border-[var(--border-default)] px-2 text-[11px] font-medium leading-none text-[var(--text-body)]',
        className,
      )}
    >
      <span
        aria-hidden="true"
        className={cn('h-1.5 w-1.5 shrink-0 rounded-full', dotColors[variant], dot && 'animate-pulse-subtle')}
      />
      {children}
    </span>
  );
}

/** The bare status dot of a badge, for dense rows where the label would repeat. */
export function StatusDot({ variant }: { variant: BadgeVariant }) {
  return <span aria-hidden="true" className={cn('h-1.5 w-1.5 shrink-0 rounded-full', dotColors[variant])} />;
}
