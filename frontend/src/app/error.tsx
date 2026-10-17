'use client';

import { AlertCircle, RefreshCw } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { useI18n } from '@/lib/i18n';

export default function ErrorPage({ error, reset }: { error: Error; reset: () => void }) {
  const { t } = useI18n();
  return (
    <div role="alert" className="flex min-h-[60vh] flex-col items-center justify-center gap-3 text-center animate-fade-in">
      <AlertCircle size={22} className="text-[var(--danger)]" />
      <h1 className="text-[15px] font-medium">{t('errorTitle')}</h1>
      <p className="max-w-md text-[13px] text-[var(--text-secondary)]">{error.message}</p>
      <Button size="sm" icon={<RefreshCw size={13} />} onClick={reset}>
        {t('retry')}
      </Button>
    </div>
  );
}
