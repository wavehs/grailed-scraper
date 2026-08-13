'use client';

import { useEffect } from 'react';
import { Button } from '@/components/ui/button';
import { useI18n } from '@/lib/i18n';

export default function ErrorPage({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  const { t } = useI18n();
  useEffect(() => {
    console.error(error);
  }, [error]);
  return (
    <section role="alert" className="rounded-lg border border-red-300 bg-red-50 p-5 text-red-900">
      <h1 className="text-xl font-semibold">{t('requestFailed')}</h1>
      <p className="mt-2">{error.message}</p>
      <Button className="mt-4" onClick={reset}>
        {t('retry')}
      </Button>
    </section>
  );
}
