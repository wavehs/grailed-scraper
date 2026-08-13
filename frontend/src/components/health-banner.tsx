'use client';

import { useParserHealth } from '@/lib/queries';
import { useI18n } from '@/lib/i18n';

export function HealthBanner() {
  const { t } = useI18n();
  const health = useParserHealth();
  if (
    !health.data ||
    health.data.status === 'ready' ||
    !['degraded', 'unavailable'].includes(health.data.status)
  )
    return null;
  const unavailable = health.data.status === 'unavailable';
  return (
    <div
      role={unavailable ? 'alert' : 'status'}
      aria-live="polite"
      className={`mb-5 rounded border p-4 ${
        unavailable
          ? 'border-red-300 bg-red-50 text-red-950'
          : 'border-amber-300 bg-amber-50 text-amber-950'
      }`}
    >
      <p className="font-semibold">
        {unavailable ? t('systemUnavailable') : t('systemDegraded')}
      </p>
      {(health.data.reasons ?? []).length > 0 && (
        <ul className="mt-2 list-disc pl-5 text-sm">
          {(health.data.reasons ?? []).map((reason) => (
            <li key={reason}>{t(reason)}</li>
          ))}
        </ul>
      )}
    </div>
  );
}
