'use client';

import * as RadixPopover from '@radix-ui/react-popover';
import { type ReactNode } from 'react';
import { cn } from '@/lib/utils';

/**
 * A toolbar chip that opens a floating panel, like Linear's "Filter" and "Display" menus.
 * The chip's accessible name is `label`; `count` shows how many options are active.
 */
export function Popover({
  label,
  icon,
  count,
  align = 'start',
  className,
  children,
}: {
  label: string;
  icon?: ReactNode;
  count?: number;
  align?: 'start' | 'center' | 'end';
  className?: string;
  children: ReactNode;
}) {
  return (
    <RadixPopover.Root>
      <RadixPopover.Trigger className="chip" aria-label={label}>
        {icon}
        <span aria-hidden="true">{label}</span>
        {Boolean(count) && (
          <span
            aria-hidden="true"
            className="rounded-[3px] bg-[var(--accent-soft)] px-1 text-[11px] tabular text-[var(--text-primary)]"
          >
            {count}
          </span>
        )}
      </RadixPopover.Trigger>
      <RadixPopover.Portal>
        <RadixPopover.Content
          align={align}
          sideOffset={6}
          collisionPadding={12}
          className={cn(
            'z-50 max-h-[min(70vh,var(--radix-popover-content-available-height))] w-72 overflow-y-auto rounded-lg bg-[var(--bg-surface-raised)] p-1.5 text-[13px] shadow-[var(--shadow-float)] animate-scale-in focus:outline-none',
            className,
          )}
        >
          {children}
        </RadixPopover.Content>
      </RadixPopover.Portal>
    </RadixPopover.Root>
  );
}

/** A titled group of rows inside a popover, separated from the previous one by a hairline. */
export function PopoverSection({ title, children }: { title?: string; children: ReactNode }) {
  return (
    <div className="border-t border-[var(--border-subtle)] py-1 first:border-t-0 first:pt-0 last:pb-0">
      {title && <p className="px-2 pb-1 pt-1.5 text-[11px] font-medium text-[var(--text-muted)]">{title}</p>}
      {children}
    </div>
  );
}

/** A 32px row of a popover with a label on the left and its control on the right. */
export function PopoverRow({ label, htmlFor, children }: { label: string; htmlFor?: string; children: ReactNode }) {
  return (
    <div className="flex min-h-8 items-center justify-between gap-3 px-2 py-0.5">
      <label htmlFor={htmlFor} className="text-[var(--text-body)]">
        {label}
      </label>
      {children}
    </div>
  );
}

/** A checkbox row of a popover menu. */
export function MenuCheckbox({
  label,
  checked,
  onChange,
  title,
}: {
  label: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
  title?: string;
}) {
  return (
    <label
      title={title}
      className="flex h-8 cursor-pointer items-center gap-2 rounded-[5px] px-2 text-[var(--text-body)] hover:bg-[var(--bg-surface-hover)]"
    >
      <input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} />
      {label}
    </label>
  );
}
