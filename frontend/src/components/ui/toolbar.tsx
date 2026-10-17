import { type ReactNode } from 'react';
import { cn } from '@/lib/utils';

/** A full-bleed 44px bar under the top bar for a list's filters and view options. */
export function Toolbar({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div
      className={cn(
        '-mx-4 flex min-h-[var(--topbar-height)] flex-wrap items-center gap-2 border-b border-[var(--border-subtle)] px-4 py-2 md:-mx-6 md:px-6',
        className,
      )}
    >
      {children}
    </div>
  );
}
