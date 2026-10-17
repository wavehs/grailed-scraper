import { type ReactNode } from 'react';
import { cn } from '@/lib/utils';

/** Linear's list: a quiet 32px header, 40px rows divided by hairlines, no zebra or card. */
export function DataTable({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn('overflow-hidden rounded-md border border-[var(--border-subtle)] bg-[var(--bg-base)]', className)}>
      <div className="overflow-x-auto">
        <table className="w-full text-left text-[13px] tabular">{children}</table>
      </div>
    </div>
  );
}

export function TableHead({ children }: { children: ReactNode }) {
  return (
    <thead className="border-b border-[var(--border-subtle)] bg-[var(--bg-surface)]">
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
    <th
      className={cn('h-8 whitespace-nowrap px-3 text-xs font-medium text-[var(--text-muted)]', className)}
      aria-sort={sortDir === 'asc' ? 'ascending' : sortDir === 'desc' ? 'descending' : undefined}
    >
      {onClick ? (
        <button
          className="inline-flex items-center gap-1 transition-colors hover:text-[var(--text-primary)]"
          onClick={onClick}
        >
          {children}
          {sortDir === 'asc' && <span className="text-[var(--text-primary)]">↑</span>}
          {sortDir === 'desc' && <span className="text-[var(--text-primary)]">↓</span>}
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
        'h-10 border-b border-[var(--border-subtle)] transition-colors duration-75 last:border-b-0 hover:bg-[var(--bg-surface)]',
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
  return <td colSpan={colSpan} className={cn('px-3 py-2 text-[13px]', className)}>{children}</td>;
}
