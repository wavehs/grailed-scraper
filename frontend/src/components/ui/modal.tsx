'use client';

import { useEffect, useRef, type ReactNode } from 'react';
import { X } from 'lucide-react';
import { cn } from '@/lib/utils';

export function Modal({
  open,
  onClose,
  title,
  children,
  className,
  maxWidth = 'max-w-4xl',
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
  className?: string;
  maxWidth?: string;
}) {
  const closeRef = useRef<HTMLButtonElement>(null);
  const dialogRef = useRef<HTMLDivElement>(null);
  const returnRef = useRef<HTMLElement | null>(null);
  // Callers pass inline closures; keeping the latest one in a ref stops each parent render
  // from re-running the effect and pulling focus back to the close button.
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;

  useEffect(() => {
    if (!open) return;
    returnRef.current = document.activeElement as HTMLElement | null;
    closeRef.current?.focus();
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onCloseRef.current();
    };
    window.addEventListener('keydown', handler);
    return () => {
      window.removeEventListener('keydown', handler);
      window.setTimeout(() => returnRef.current?.focus(), 0);
    };
  }, [open]);

  if (!open) return null;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="modal-title"
      className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto p-4 pt-[8vh] animate-fade-in"
      onKeyDown={(event) => {
        if (event.key !== 'Tab') return;
        const items = dialogRef.current?.querySelectorAll<HTMLElement>(
          'button:not([disabled]), a[href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
        );
        if (!items?.length) return;
        const first = items[0];
        const last = items[items.length - 1];
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first.focus();
        }
      }}
    >
      {/* Backdrop */}
      <button
        aria-label="Close"
        className="fixed inset-0 cursor-default bg-black/50"
        onClick={onClose}
        tabIndex={-1}
      />

      {/* Content */}
      <div
        ref={dialogRef}
        className={cn(
          'relative z-10 w-full rounded-lg bg-[var(--bg-surface-raised)] shadow-[var(--shadow-float)] animate-scale-in',
          maxWidth,
          className,
        )}
      >
        {/* Header */}
        <div className="flex h-12 items-center justify-between border-b border-[var(--border-subtle)] px-5">
          <h2 id="modal-title" className="text-sm font-medium text-[var(--text-primary)]">
            {title}
          </h2>
          <button
            ref={closeRef}
            onClick={onClose}
            className="rounded p-1 text-[var(--text-muted)] transition-colors hover:bg-[var(--bg-control)] hover:text-[var(--text-primary)]"
            aria-label="Close"
          >
            <X size={16} />
          </button>
        </div>

        {/* Body */}
        <div className="p-5">{children}</div>
      </div>
    </div>
  );
}
