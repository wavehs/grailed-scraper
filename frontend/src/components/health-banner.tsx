'use client';

import { AlertTriangle, XCircle } from 'lucide-react';
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
      className={`mb-5 flex items-start gap-3 rounded-lg border px-4 py-3 animate-slide-down ${
        unavailable
          ? 'border-[var(--danger-border)] bg-[var(--danger-bg)]'
          : 'border-[var(--warning-border)] bg-[var(--warning-bg)]'
      }`}
    >
      {unavailable ? (
        <XCircle size={18} className="mt-0.5 shrink-0 text-[var(--danger)]" />
      ) : (
        <AlertTriangle size={18} className="mt-0.5 shrink-0 text-[var(--warning)]" />
      )}
      <div>
        <p className={`text-sm font-semibold ${unavailable ? 'text-[var(--danger)]' : 'text-[var(--warning)]'}`}>
          {unavailable ? t('systemUnavailable') : t('systemDegraded')}
        </p>
        {(health.data.reasons ?? []).length > 0 && (
          <ul className="mt-1.5 space-y-0.5 text-xs text-[var(--text-secondary)]">
            {(health.data.reasons ?? []).map((reason) => (
              <li key={reason}>• {t(reason)}</li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
