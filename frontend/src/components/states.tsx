'use client';

import { AlertCircle, Inbox, Loader2, RefreshCw } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { errorMessage } from '@/lib/api';
import { useI18n } from '@/lib/i18n';

export function LoadingState() {
  const { t } = useI18n();
  return (
    <div className="flex flex-col items-center justify-center gap-3 rounded-xl glass p-12 animate-fade-in">
      <Loader2 size={28} className="text-[#818cf8] animate-spin" />
      <p className="text-sm text-[var(--text-secondary)]">{t('loading')}</p>
    </div>
  );
}

export function EmptyState({ message }: { message?: string }) {
  const { t } = useI18n();
  return (
    <div className="flex flex-col items-center justify-center gap-3 rounded-xl glass p-12 animate-fade-in">
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
      className="flex flex-col items-center justify-center gap-3 rounded-xl border border-[rgba(244,63,94,0.2)] bg-[rgba(244,63,94,0.06)] p-8 animate-fade-in"
    >
      <AlertCircle size={28} className="text-[#fb7185]" />
      <p className="text-sm text-[#fb7185]">{errorMessage(error) || t('requestFailed')}</p>
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
          ? 'border-[rgba(244,63,94,0.2)] bg-[rgba(244,63,94,0.06)] text-[#fb7185]'
          : 'border-[rgba(16,185,129,0.2)] bg-[rgba(16,185,129,0.06)] text-[#34d399]'
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
