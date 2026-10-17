import { type ReactNode } from 'react';

/**
 * The 44px top bar of a screen: title and a muted description on the left, actions on the
 * right. It bleeds over the page gutter and sticks under the window edge like Linear's.
 */
export function PageHeader({
  title,
  description,
  actions,
}: {
  title: string;
  description?: string;
  actions?: ReactNode;
}) {
  return (
    <header className="sticky top-11 z-10 -mx-4 -mt-4 md:top-0 flex min-h-[var(--topbar-height)] flex-wrap items-center justify-between gap-x-4 gap-y-2 border-b border-[var(--border-subtle)] bg-[var(--bg-base)] px-4 py-2 md:-mx-6 md:px-6">
      <div className="flex min-w-0 items-baseline gap-3">
        <h1 className="shrink-0 text-[13px] font-medium text-[var(--text-primary)]">{title}</h1>
        {description && (
          <p className="hidden min-w-0 truncate text-xs text-[var(--text-muted)] md:block" title={description}>
            {description}
          </p>
        )}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </header>
  );
}
