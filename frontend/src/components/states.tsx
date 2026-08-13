'use client';

import { Button } from '@/components/ui/button';
import { errorMessage } from '@/lib/api';
import { useI18n } from '@/lib/i18n';

export function LoadingState() {
  const { t } = useI18n();
  return (
    <p role="status" className="rounded-lg border bg-white p-5 text-slate-600">
      {t('loading')}
    </p>
  );
}

export function EmptyState({ message }: { message?: string }) {
  const { t } = useI18n();
  return <p className="rounded-lg border bg-white p-5 text-slate-600">{message ?? t('noData')}</p>;
}

export function ErrorState({ error, retry }: { error: unknown; retry?: () => void }) {
  const { t } = useI18n();
  return (
    <div role="alert" className="rounded-lg border border-red-300 bg-red-50 p-4 text-red-900">
      <p>{errorMessage(error) || t('requestFailed')}</p>
      {retry && (
        <Button className="mt-3" onClick={retry}>
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
      className={`rounded border p-3 text-sm ${error ? 'border-red-300 bg-red-50 text-red-900' : 'border-emerald-300 bg-emerald-50 text-emerald-900'}`}
    >
      {children}
    </p>
  );
}
