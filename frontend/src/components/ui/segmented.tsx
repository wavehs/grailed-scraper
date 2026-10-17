'use client';

import { cn } from '@/lib/utils';

export type SegmentOption<T extends string | number> = { value: T; label: string; title?: string };

/** Linear's segmented control: pressed buttons in a sunken 28px track. */
export function Segmented<T extends string | number>({
  label,
  options,
  value,
  onChange,
  disabled,
  title,
  className,
}: {
  label: string;
  options: Array<SegmentOption<T>>;
  /** The pressed option; null when none is (another control owns the choice). */
  value: T | null;
  onChange: (value: T) => void;
  disabled?: boolean;
  title?: string;
  className?: string;
}) {
  return (
    <div
      role="group"
      aria-label={label}
      title={title}
      className={cn(
        'inline-flex h-7 shrink-0 items-center gap-px rounded-[5px] border border-[var(--border-subtle)] bg-[var(--bg-surface)] p-0.5',
        className,
      )}
    >
      {options.map((option) => {
        const active = option.value === value;
        return (
          <button
            key={String(option.value)}
            type="button"
            aria-pressed={active}
            disabled={disabled}
            title={option.title}
            onClick={() => !active && onChange(option.value)}
            className={cn(
              'h-[22px] whitespace-nowrap rounded-[4px] px-2 text-xs tabular transition-colors duration-75 disabled:cursor-not-allowed disabled:opacity-50',
              active
                ? 'bg-[var(--bg-control-hover)] text-[var(--text-primary)] shadow-[0_1px_2px_rgba(0,0,0,0.32)]'
                : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]',
            )}
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
}
