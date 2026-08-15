'use client';

import Link from 'next/link';
import { useI18n } from '@/lib/i18n';

export default function NotFound() {
  const { t } = useI18n();
  return (
    <section className="space-y-4 rounded-lg border border-slate-200 bg-white p-6">
      <h1 className="text-xl font-semibold">404 — Not Found</h1>
      <p className="text-slate-600">The requested page could not be found.</p>
      <Link href="/dashboard" className="inline-block rounded bg-slate-900 px-4 py-2 text-white">
        {t('dashboard')}
      </Link>
    </section>
  );
}
