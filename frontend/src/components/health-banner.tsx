'use client';

import { useMutation, useQueryClient } from '@tanstack/react-query';
import { AlertTriangle, RefreshCw, XCircle } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { api } from '@/lib/api';
import { useParserHealth } from '@/lib/queries';
import { useI18n } from '@/lib/i18n';

export function HealthBanner() {
  const { t } = useI18n();
  const health = useParserHealth();
  const client = useQueryClient();
  const refresh = useMutation({
    mutationFn: () => api('/parser/discovery/refresh', 'POST', { force: true }),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ['parser-health'] });
      client.invalidateQueries({ queryKey: ['brands'] });
    },
  });
  if (
    !health.data ||
    health.data.status === 'ready' ||
    !['degraded', 'unavailable'].includes(health.data.status)
  )
    return null;
  const unavailable = health.data.status === 'unavailable';
  const connectionStale = (health.data.reasons ?? []).includes('credentials_stale');
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
      <div className="min-w-0 flex-1">
        <p className={`text-sm font-semibold ${unavailable ? 'text-[var(--danger)]' : 'text-[var(--warning)]'}`}>
          {connectionStale
            ? t('connectionNeedsUpdate')
            : unavailable
              ? t('systemUnavailable')
              : t('systemDegraded')}
        </p>
        {(health.data.reasons ?? []).length > 0 && (
          <ul className="mt-1.5 space-y-0.5 text-xs text-[var(--text-secondary)]">
            {(health.data.reasons ?? []).map((reason) => (
              <li key={reason}>• {t(reason)}</li>
            ))}
          </ul>
        )}
        {refresh.isError && (
          <p className="mt-2 text-xs text-[var(--danger)]" role="alert">
            {t('connectionUpdateFailed')}
          </p>
        )}
      </div>
      {connectionStale && (
        <Button
          className="shrink-0"
          variant="secondary"
          size="sm"
          icon={<RefreshCw size={14} className={refresh.isPending ? 'animate-spin' : ''} />}
          disabled={refresh.isPending}
          onClick={() => refresh.mutate()}
        >
          {refresh.isPending ? t('refreshing') : t('updateNow')}
        </Button>
      )}
    </div>
  );
}
