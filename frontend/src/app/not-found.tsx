'use client';

import Link from 'next/link';
import { useI18n } from '@/lib/i18n';

export default function NotFound() {
  const { t } = useI18n();
  return (
    <div className="flex min-h-[60vh] flex-col items-center justify-center gap-3 text-center animate-fade-in">
      <p className="text-[32px] font-medium tabular-nums text-[var(--text-muted)]">404</p>
      <h1 className="text-[15px] font-medium">{t('notFoundTitle')}</h1>
      <Link
        href="/trends"
        className="inline-flex h-8 items-center rounded-[5px] bg-[var(--accent)] px-3 text-[13px] font-medium text-white transition-colors hover:bg-[var(--accent-hover)]"
      >
        {t('goToTrends')}
      </Link>
    </div>
  );
}
