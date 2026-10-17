'use client';

import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Play } from 'lucide-react';
import { Badge, statusVariant } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { SettingRow, SettingsGroup } from '@/components/settings/settings-group';
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
      <div className="flex flex-wrap gap-1.5">
        {(brands.data?.data ?? []).map((brand) => (
          <button
            key={brand.id}
            type="button"
            className="chip"
            aria-pressed={selected.includes(brand.id)}
            onClick={() => toggle(brand.id)}
          >
            {brand.name}
          </button>
        ))}
      </div>
    </div>
  );
}

/** The daily run's state: next slot, a throttling pause and the last scheduled run. */
export function ScheduleStatus({ writable }: { writable: boolean }) {
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
    <SettingsGroup title={t('scheduleStatus')}>
      <SettingRow label={t('status')} description={reason}>
        <div className="flex justify-end">
          <Badge variant={status.enabled ? 'success' : 'muted'} dot>
            {t(`scheduleState_${status.state}`)}
          </Badge>
        </div>
      </SettingRow>
      <SettingRow label={t('scheduleNextSlot')}>
        <p className="text-right tabular-nums text-[var(--text-primary)]">{formatDate(status.next_slot_at, locale)}</p>
      </SettingRow>
      {status.paused_until && (
        <SettingRow label={t('schedulePausedUntil')} description={formatDate(status.paused_until, locale)}>
          <div className="flex justify-end">
            <Button
              variant="secondary"
              size="sm"
              icon={<Play size={13} />}
              disabled={!writable || resume.isPending}
              onClick={() => resume.mutate()}
            >
              {t('scheduleResume')}
            </Button>
          </div>
        </SettingRow>
      )}
      {lastRun && (
        <SettingRow label={t('scheduleLastRun')}>
          <p className="flex items-center justify-end gap-2 text-[var(--text-primary)]">
            #{lastRun.id}
            <Badge variant={statusVariant(lastRun.status)}>{t(lastRun.status)}</Badge>
            {lastRun.full && <span className="text-xs text-[var(--text-muted)]">{t('fullRun')}</span>}
          </p>
        </SettingRow>
      )}
      {(lastRun?.maintenance_error || resume.error || resume.isSuccess) && (
        <div className="space-y-2 px-4 py-3">
          {lastRun?.maintenance_error && (
            <Notice error>
              {t('scheduleMaintenanceError')}: {lastRun.maintenance_error}
            </Notice>
          )}
          {resume.error && <ErrorState error={resume.error} />}
          {resume.isSuccess && <Notice>{t('schedulePauseCleared')}</Notice>}
        </div>
      )}
    </SettingsGroup>
  );
}
