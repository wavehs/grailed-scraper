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
      className={`mb-6 flex items-start gap-3 rounded-xl border p-4 animate-slide-down ${
        unavailable
          ? 'border-[rgba(244,63,94,0.2)] bg-[rgba(244,63,94,0.06)]'
          : 'border-[rgba(245,158,11,0.2)] bg-[rgba(245,158,11,0.06)]'
      }`}
    >
      {unavailable ? (
        <XCircle size={18} className="mt-0.5 shrink-0 text-[#fb7185]" />
      ) : (
        <AlertTriangle size={18} className="mt-0.5 shrink-0 text-[#fbbf24]" />
      )}
      <div>
        <p className={`text-sm font-semibold ${unavailable ? 'text-[#fb7185]' : 'text-[#fbbf24]'}`}>
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
