'use client';

import { useMutation, useQueryClient } from '@tanstack/react-query';
import { CalendarClock, Play } from 'lucide-react';
import { Badge, statusVariant } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { ErrorState, Notice } from '@/components/states';
import { api } from '@/lib/api';
import { useI18n } from '@/lib/i18n';
import { useBrandsQuery, useScheduleQuery } from '@/lib/queries';
import { formatDate } from '@/lib/utils';

/** Brands of the daily run; none selected means every brand with collected listings. */
export function ScheduleBrandPicker({
  value,
  onChange,
}: {
  value: number[] | null;
  onChange: (value: number[] | null) => void;
}) {
  const { t } = useI18n();
  const brands = useBrandsQuery();
  const selected = value ?? [];
  const toggle = (id: number) => {
    const next = selected.includes(id) ? selected.filter((item) => item !== id) : [...selected, id];
    onChange(next.length ? next.sort((a, b) => a - b) : null);
  };
  return (
    <div className="space-y-2">
      {!selected.length && (
        <p className="text-xs text-[var(--text-muted)]">{t('scheduleAllBrands')}</p>
      )}
      <div className="flex flex-wrap gap-2">
        {(brands.data?.data ?? []).map((brand) => {
          const on = selected.includes(brand.id);
          return (
            <button
              key={brand.id}
              type="button"
              aria-pressed={on}
              onClick={() => toggle(brand.id)}
              className={`rounded-md border px-2.5 py-1 text-xs ${
                on
                  ? 'border-[var(--accent)] bg-[var(--accent-soft)] text-[var(--accent)]'
                  : 'border-[var(--border-default)] text-[var(--text-secondary)]'
              }`}
            >
              {brand.name}
            </button>
          );
        })}
      </div>
    </div>
  );
}

export function ScheduleCard({ writable }: { writable: boolean }) {
  const { t, locale } = useI18n();
  const client = useQueryClient();
  const schedule = useScheduleQuery();
  const resume = useMutation({
    mutationFn: () => api<void>('/parser/schedule/pause', 'DELETE'),
    onSuccess: () => client.invalidateQueries({ queryKey: ['schedule'] }),
  });
  const status = schedule.data;
  if (schedule.error) return <ErrorState error={schedule.error} retry={() => schedule.refetch()} />;
  if (!status?.state) return null;
  const reasonKey = `scheduleReason_${status.reason}`;
  const reason = t(reasonKey) === reasonKey ? t(status.reason) : t(reasonKey);
  const lastRun = status.last_run;
  return (
    <Card className="space-y-3 p-5">
      <h2 className="flex items-center gap-2 text-sm font-semibold uppercase tracking-wider text-[var(--text-muted)]">
        <CalendarClock size={16} /> {t('scheduleStatus')}
      </h2>
      <p className="flex flex-wrap items-center gap-2 text-sm text-[var(--text-secondary)]">
        <Badge variant={status.enabled ? 'success' : 'muted'} dot>
          {t(`scheduleState_${status.state}`)}
        </Badge>
        {reason}
      </p>
      <dl className="grid gap-2 text-sm sm:grid-cols-3">
        <div>
          <dt className="text-[var(--text-muted)]">{t('scheduleNextSlot')}</dt>
          <dd className="text-[var(--text-primary)]">{formatDate(status.next_slot_at, locale)}</dd>
        </div>
        {status.paused_until && (
          <div>
            <dt className="text-[var(--text-muted)]">{t('schedulePausedUntil')}</dt>
            <dd className="text-[var(--text-primary)]">
              {formatDate(status.paused_until, locale)}
            </dd>
          </div>
        )}
        {lastRun && (
          <div>
            <dt className="text-[var(--text-muted)]">{t('scheduleLastRun')}</dt>
            <dd className="flex items-center gap-2 text-[var(--text-primary)]">
              #{lastRun.id}
              <Badge variant={statusVariant(lastRun.status)}>{t(lastRun.status)}</Badge>
              {lastRun.full && <span className="text-[var(--text-muted)]">{t('fullRun')}</span>}
            </dd>
          </div>
        )}
      </dl>
      {lastRun?.maintenance_error && (
        <Notice error>
          {t('scheduleMaintenanceError')}: {lastRun.maintenance_error}
        </Notice>
      )}
      {resume.error && <ErrorState error={resume.error} />}
      {resume.isSuccess && <Notice>{t('schedulePauseCleared')}</Notice>}
      {status.paused_until && (
        <Button
          variant="secondary"
          size="sm"
          icon={<Play size={14} />}
          disabled={!writable || resume.isPending}
          onClick={() => resume.mutate()}
        >
          {t('scheduleResume')}
        </Button>
      )}
    </Card>
  );
}
