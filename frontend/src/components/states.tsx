'use client';

import { AlertCircle, Inbox, Loader2, RefreshCw } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { errorMessage } from '@/lib/api';
import { useI18n } from '@/lib/i18n';

const BLOCK = 'flex min-h-40 flex-col items-center justify-center gap-2.5 p-8 text-center animate-fade-in';

export function LoadingState() {
  const { t } = useI18n();
  return (
    <div className={BLOCK}>
      <Loader2 size={18} className="animate-spin text-[var(--text-muted)]" />
      <p className="text-[13px] text-[var(--text-muted)]">{t('loading')}</p>
    </div>
  );
}

export function EmptyState({ message }: { message?: string }) {
  const { t } = useI18n();
  return (
    <div className={BLOCK}>
      <Inbox size={22} strokeWidth={1.5} className="text-[var(--text-muted)]" />
      <p className="text-[13px] text-[var(--text-secondary)]">{message ?? t('noData')}</p>
    </div>
  );
}

export function ErrorState({ error, retry }: { error: unknown; retry?: () => void }) {
  const { t } = useI18n();
  return (
    <div role="alert" className={BLOCK}>
      <AlertCircle size={20} className="text-[var(--danger)]" />
      <p className="max-w-md text-[13px] text-[var(--text-body)]">{errorMessage(error) || t('requestFailed')}</p>
      {retry && (
        <Button variant="secondary" size="sm" icon={<RefreshCw size={13} />} onClick={retry}>
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
      className="flex items-center gap-2 rounded-md border border-[var(--border-subtle)] bg-[var(--bg-surface)] px-3 py-2 text-[13px] text-[var(--text-body)] animate-slide-up"
    >
      <span
        aria-hidden="true"
        className={`h-1.5 w-1.5 shrink-0 rounded-full ${error ? 'bg-[var(--danger)]' : 'bg-[var(--success)]'}`}
      />
      {children}
    </p>
  );
}

export function SkeletonLine({ className }: { className?: string }) {
  return <div className={`h-3.5 rounded animate-shimmer ${className ?? 'w-full'}`} />;
}
