import { type ReactNode } from 'react';
import { cn } from '@/lib/utils';

/** A titled block of a detail page, divided from the previous block by a hairline. */
export function ContentSection({
  title,
  description,
  actions,
  className,
  children,
}: {
  /** Omitted when the content carries its own caption, such as a chart. */
  title?: string;
  description?: string;
  actions?: ReactNode;
  className?: string;
  children: ReactNode;
}) {
  return (
    <section className={cn('space-y-3 border-t border-[var(--border-subtle)] pt-5 first:border-t-0 first:pt-0', className)}>
      {(title || actions) && (
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="min-w-0">
            {title && <h2 className="text-[13px] font-medium text-[var(--text-primary)]">{title}</h2>}
            {description && <p className="mt-0.5 text-xs text-[var(--text-muted)]">{description}</p>}
          </div>
          {actions}
        </div>
      )}
      {children}
    </section>
  );
}

/** Label and value rows of a property panel, like Linear's issue sidebar. */
export function PropertyList({ label, children }: { label?: string; children: ReactNode }) {
  return (
    <dl aria-label={label} className="space-y-0.5">
      {children}
    </dl>
  );
}

export function Property({ label, title, children }: { label: string; title?: string; children: ReactNode }) {
  return (
    <div className="flex min-h-8 items-center justify-between gap-3" title={title}>
      <dt className="text-xs text-[var(--text-muted)]">{label}</dt>
      <dd className="text-right text-[13px] tabular text-[var(--text-primary)]">{children}</dd>
    </div>
  );
}
