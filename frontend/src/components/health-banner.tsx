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
      className="-mx-4 -mt-4 mb-4 flex items-start gap-2.5 border-b border-[var(--border-subtle)] bg-[var(--bg-surface)] px-4 py-2 md:-mx-6 md:px-6"
    >
      {unavailable ? (
        <XCircle size={15} className="mt-0.5 shrink-0 text-[var(--danger)]" />
      ) : (
        <AlertTriangle size={15} className="mt-0.5 shrink-0 text-[var(--warning)]" />
      )}
      <div className="min-w-0 flex-1">
        <p className="text-[13px] font-medium text-[var(--text-primary)]">
          {connectionStale
            ? t('connectionNeedsUpdate')
            : unavailable
              ? t('systemUnavailable')
              : t('systemDegraded')}
        </p>
        {(health.data.reasons ?? []).length > 0 && (
          <ul className="mt-0.5 flex flex-wrap gap-x-3 text-xs text-[var(--text-secondary)]">
            {(health.data.reasons ?? []).map((reason) => (
              <li key={reason}>{t(reason)}</li>
            ))}
          </ul>
        )}
        {refresh.isError && (
          <p className="mt-1 text-xs text-[var(--danger)]" role="alert">
            {t('connectionUpdateFailed')}
          </p>
        )}
      </div>
      {connectionStale && (
        <Button
          variant="secondary"
          size="sm"
          icon={<RefreshCw size={13} className={refresh.isPending ? 'animate-spin' : ''} />}
          disabled={refresh.isPending}
          onClick={() => refresh.mutate()}
        >
          {refresh.isPending ? t('refreshing') : t('updateNow')}
        </Button>
      )}
    </div>
  );
}
