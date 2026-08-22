import { type ReactNode } from 'react';
import { cn } from '@/lib/utils';

export function DataTable({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn('overflow-hidden rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-surface)] animate-fade-in', className)}>
      <div className="overflow-x-auto">
        <table className="w-full text-left text-sm">{children}</table>
      </div>
    </div>
  );
}

export function TableHead({ children }: { children: ReactNode }) {
  return (
    <thead className="border-b border-[var(--border-default)] bg-[var(--bg-surface-hover)]">
      {children}
    </thead>
  );
}

export function TableHeaderCell({
  children,
  className,
  onClick,
  sortDir,
}: {
  children: ReactNode;
  className?: string;
  onClick?: (event?: unknown) => void;
  sortDir?: 'asc' | 'desc' | false;
}) {
  return (
    <th className={cn('px-3 py-2.5 text-xs font-medium uppercase tracking-wider text-[var(--text-muted)]', className)}>
      {onClick ? (
        <button
          className="inline-flex items-center gap-1 hover:text-[var(--text-primary)] transition-colors cursor-pointer"
          onClick={onClick}
        >
          {children}
          {sortDir === 'asc' && <span className="text-[var(--accent)]">↑</span>}
          {sortDir === 'desc' && <span className="text-[var(--accent)]">↓</span>}
        </button>
      ) : (
        children
      )}
    </th>
  );
}

export function TableRow({
  children,
  className,
  onClick,
}: {
  children: ReactNode;
  className?: string;
  onClick?: () => void;
}) {
  return (
    <tr
      className={cn(
        'border-b border-[var(--border-subtle)] transition-colors last:border-b-0 hover:bg-[var(--bg-surface-hover)]',
        onClick && 'cursor-pointer',
        className,
      )}
      onClick={onClick}
    >
      {children}
    </tr>
  );
}

export function TableCell({
  children,
  className,
  colSpan,
}: {
  children: ReactNode;
  className?: string;
  colSpan?: number;
}) {
  return <td colSpan={colSpan} className={cn('px-3 py-2.5 text-[13px]', className)}>{children}</td>;
}
