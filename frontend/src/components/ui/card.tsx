import { type HTMLAttributes } from 'react';
import { cn } from '@/lib/utils';

/** A raised panel with a hairline border, used for grouped settings and side content. */
export function Card({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        'rounded-md border border-[var(--border-subtle)] bg-[var(--bg-surface)]',
        className,
      )}
      {...props}
    />
  );
}
