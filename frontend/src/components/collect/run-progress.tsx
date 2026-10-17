'use client';

import { useMutation, useQuery } from '@tanstack/react-query';
import { Ban } from 'lucide-react';
import { ErrorState } from '@/components/states';
import { Button } from '@/components/ui/button';
import { InlineStat } from '@/components/ui/inline-stat';
import { ProgressBar } from '@/components/ui/progress-bar';
import { api, getApi } from '@/lib/api';
import { useI18n } from '@/lib/i18n';
import type { RunProgress, RunSummary } from '@/lib/types';

const PROGRESS_POLL_MS = 2_000;

/** The running collection: progress, the brand in work and a cancel button. */
export function RunProgressPanel({ run, onChanged }: { run: RunSummary; onChanged: () => void }) {
  const { t } = useI18n();
  const progress = useQuery({
    queryKey: ['run-progress', run.id],
    queryFn: ({ signal }) => getApi<RunProgress>(`/parser/runs/${run.id}/progress`, signal),
    refetchInterval: PROGRESS_POLL_MS,
  });
  const cancel = useMutation({
    mutationFn: () => api<RunSummary>(`/parser/runs/${run.id}/cancel`, 'POST'),
    onSuccess: onChanged,
  });
  const done = progress.data?.tasks_done ?? 0;
  const total = progress.data?.tasks_total ?? 0;
  return (
    <div className="-mx-4 space-y-4 border-b border-[var(--border-subtle)] px-4 py-5 md:-mx-6 md:px-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-[13px] font-medium text-[var(--text-primary)]">
          {t('collectionRunning')} #{run.id}
        </h2>
        <Button
          variant="secondary"
          size="sm"
          icon={<Ban size={13} />}
          disabled={cancel.isPending}
          onClick={() => cancel.mutate()}
        >
          {t('cancel')}
        </Button>
      </div>
      <ProgressBar value={done} max={Math.max(total, 1)} label={t('progress')} />
      <div className="flex flex-wrap gap-x-10 gap-y-3">
        <InlineStat label={t('currentBrand')}>{progress.data?.current_brand ?? '—'}</InlineStat>
        <InlineStat label={t('tasks')}>
          {done}/{total}
        </InlineStat>
        <InlineStat label={t('listingsFetched')}>{progress.data?.hits_fetched ?? 0}</InlineStat>
        <InlineStat label={t('requests')}>{progress.data?.requests_made ?? 0}</InlineStat>
      </div>
      {cancel.error && <ErrorState error={cancel.error} />}
    </div>
  );
}
