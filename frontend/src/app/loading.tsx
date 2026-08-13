'use client';

import { useI18n } from '@/lib/i18n';

export default function Loading() {
  const { t } = useI18n();
  return (
    <div role="status" className="rounded-lg border bg-white p-5 text-slate-600">
      {t('loading')}
    </div>
  );
}
