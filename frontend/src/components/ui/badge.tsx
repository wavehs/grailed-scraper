import { type ReactNode } from 'react';
import { cn } from '@/lib/utils';

type BadgeVariant = 'default' | 'success' | 'warning' | 'danger' | 'info' | 'muted';

const variantClasses: Record<BadgeVariant, string> = {
  default:
    'bg-[rgba(99,102,241,0.12)] text-[#818cf8] border-[rgba(99,102,241,0.2)]',
  success:
    'bg-[rgba(16,185,129,0.12)] text-[#34d399] border-[rgba(16,185,129,0.2)]',
  warning:
    'bg-[rgba(245,158,11,0.12)] text-[#fbbf24] border-[rgba(245,158,11,0.2)]',
  danger:
    'bg-[rgba(244,63,94,0.12)] text-[#fb7185] border-[rgba(244,63,94,0.2)]',
  info:
    'bg-[rgba(59,130,246,0.12)] text-[#60a5fa] border-[rgba(59,130,246,0.2)]',
  muted:
    'bg-[rgba(255,255,255,0.05)] text-[#94a3b8] border-[rgba(255,255,255,0.08)]',
};

const dotColors: Record<BadgeVariant, string> = {
  default: 'bg-[#818cf8]',
  success: 'bg-[#34d399]',
  warning: 'bg-[#fbbf24]',
  danger: 'bg-[#fb7185]',
  info: 'bg-[#60a5fa]',
  muted: 'bg-[#94a3b8]',
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
        'inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-medium',
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
