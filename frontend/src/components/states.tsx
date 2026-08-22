'use client';

import { AlertCircle, Inbox, Loader2, RefreshCw } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { errorMessage } from '@/lib/api';
import { useI18n } from '@/lib/i18n';

export function LoadingState() {
  const { t } = useI18n();
  return (
    <div className="flex min-h-40 flex-col items-center justify-center gap-3 rounded-lg border border-dashed border-[var(--border-default)] bg-[var(--bg-surface)] p-8 animate-fade-in">
      <Loader2 size={24} className="animate-spin text-[var(--accent)]" />
      <p className="text-sm text-[var(--text-secondary)]">{t('loading')}</p>
    </div>
  );
}

export function EmptyState({ message }: { message?: string }) {
  const { t } = useI18n();
  return (
    <div className="flex min-h-40 flex-col items-center justify-center gap-3 rounded-lg border border-dashed border-[var(--border-default)] bg-[var(--bg-surface)] p-8 animate-fade-in">
      <Inbox size={28} className="text-[var(--text-muted)]" />
      <p className="text-sm text-[var(--text-secondary)]">{message ?? t('noData')}</p>
    </div>
  );
}

export function ErrorState({ error, retry }: { error: unknown; retry?: () => void }) {
  const { t } = useI18n();
  return (
    <div
      role="alert"
      className="flex min-h-40 flex-col items-center justify-center gap-3 rounded-lg border border-[var(--danger-border)] bg-[var(--danger-bg)] p-8 animate-fade-in"
    >
      <AlertCircle size={24} className="text-[var(--danger)]" />
      <p className="text-sm text-[var(--danger)]">{errorMessage(error) || t('requestFailed')}</p>
      {retry && (
        <Button variant="secondary" size="sm" icon={<RefreshCw size={14} />} onClick={retry}>
          {t('retry')}
        </Button>
      )}
    </div>
  );
}

export function Notice({
  children,
  error = false,
}: {
  children?: React.ReactNode;
  error?: boolean;
}) {
  if (!children) return null;
  return (
    <p
      role={error ? 'alert' : 'status'}
      aria-live="polite"
      className={`flex items-center gap-2 rounded-lg border px-4 py-3 text-sm animate-slide-up ${
        error
          ? 'border-[var(--danger-border)] bg-[var(--danger-bg)] text-[var(--danger)]'
          : 'border-[var(--success-border)] bg-[var(--success-bg)] text-[var(--success)]'
      }`}
    >
      {children}
    </p>
  );
}

export function SkeletonLine({ className }: { className?: string }) {
  return (
    <div className={`h-4 rounded animate-shimmer ${className ?? 'w-full'}`} />
  );
}
