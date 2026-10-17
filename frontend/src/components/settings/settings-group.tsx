import { type ReactNode } from 'react';
import { HelpTip } from '@/components/ui/help-tip';
import { cn } from '@/lib/utils';

/** A titled panel of setting rows, as in Linear's preferences. */
export function SettingsGroup({
  title,
  description,
  children,
}: {
  title: string;
  description?: string;
  children: ReactNode;
}) {
  return (
    <section className="space-y-2">
      <div>
        <h2 className="text-[13px] font-medium text-[var(--text-primary)]">{title}</h2>
        {description && <p className="mt-0.5 text-xs text-[var(--text-muted)]">{description}</p>}
      </div>
      <div className="divide-y divide-[var(--border-subtle)] rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-surface)]">
        {children}
      </div>
    </section>
  );
}

/**
 * One setting: its name, help and notes on the left, the control on the right. `wide` puts a
 * control that needs the full width, such as a brand picker, under the name instead.
 */
export function SettingRow({
  label,
  htmlFor,
  help,
  meta,
  description,
  error,
  wide = false,
  children,
}: {
  label: string;
  htmlFor?: string;
  help?: string;
  meta?: ReactNode;
  description?: ReactNode;
  error?: string | null;
  wide?: boolean;
  children: ReactNode;
}) {
  const name = htmlFor ? (
    <label htmlFor={htmlFor} className="text-[13px] text-[var(--text-primary)]">
      {label}
    </label>
  ) : (
    <span className="text-[13px] text-[var(--text-primary)]">{label}</span>
  );
  return (
    <div
      className={cn(
        'flex flex-col gap-2 px-4 py-3',
        !wide && 'sm:flex-row sm:items-center sm:justify-between sm:gap-6',
      )}
    >
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-1.5">
          {name}
          {help && <HelpTip label={label} text={help} />}
          {meta}
        </div>
        {description && <div className="text-xs text-[var(--text-muted)]">{description}</div>}
        {error && (
          <p role="alert" className="mt-0.5 text-xs text-[var(--danger)]">
            {error}
          </p>
        )}
      </div>
      <div className={cn('min-w-0', !wide && 'sm:w-56 sm:shrink-0')}>{children}</div>
    </div>
  );
}
