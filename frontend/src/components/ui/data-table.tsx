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
    <div className={cn('glass rounded-xl overflow-hidden animate-fade-in', className)}>
      <div className="overflow-x-auto">
        <table className="w-full text-left text-sm">{children}</table>
      </div>
    </div>
  );
}

export function TableHead({ children }: { children: ReactNode }) {
  return (
    <thead className="border-b border-[var(--border-subtle)]">
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
    <th className={cn('px-4 py-3 text-xs font-medium uppercase tracking-wider text-[var(--text-muted)]', className)}>
      {onClick ? (
        <button
          className="inline-flex items-center gap-1 hover:text-[var(--text-primary)] transition-colors cursor-pointer"
          onClick={onClick}
        >
          {children}
          {sortDir === 'asc' && <span className="text-[#818cf8]">↑</span>}
          {sortDir === 'desc' && <span className="text-[#818cf8]">↓</span>}
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
        'border-b border-[var(--border-subtle)] transition-colors hover:bg-[rgba(255,255,255,0.03)]',
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
  return <td colSpan={colSpan} className={cn('px-4 py-3 text-sm', className)}>{children}</td>;
}
