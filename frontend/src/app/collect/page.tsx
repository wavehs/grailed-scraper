'use client';

import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Trash2 } from 'lucide-react';
import { RunComposer } from '@/components/collect/run-composer';
import { RunDetailModal, RunHistory, TERMINAL_STATUSES } from '@/components/collect/run-history';
import { RunProgressPanel } from '@/components/collect/run-progress';
import { Button } from '@/components/ui/button';
import { Modal } from '@/components/ui/modal';
import { PageHeader } from '@/components/ui/page-header';
import { EmptyState, ErrorState, LoadingState, Notice } from '@/components/states';
import { api } from '@/lib/api';
import { useI18n } from '@/lib/i18n';
import { useBrandsQuery, useRunsQuery } from '@/lib/queries';
import type { RunSummary } from '@/lib/types';

const RUNS_PAGE = 30;
const RUNS_POLL_MS = 2_000;

export default function CollectPage() {
  const { t } = useI18n();
  const client = useQueryClient();
  const brands = useBrandsQuery();
  const [openRun, setOpenRun] = useState<number | null>(null);
  const [confirmClear, setConfirmClear] = useState(false);
  const [notice, setNotice] = useState('');
  // Poll while a run is going so its progress and the history stay live.
  const runs = useRunsQuery(RUNS_PAGE, 0, (query: { state: { data?: { data: RunSummary[] } } }) =>
    query.state.data?.data.some((run) => !TERMINAL_STATUSES.has(run.status)) ? RUNS_POLL_MS : false,
  );
  const activeRun = runs.data?.data.find((run) => !TERMINAL_STATUSES.has(run.status));
  const brandName = (id?: number) =>
    brands.data?.data.find((brand) => brand.id === id)?.name ?? (id ? `#${id}` : '—');
  const refreshRuns = () => {
    client.invalidateQueries({ queryKey: ['runs'] });
    client.invalidateQueries({ queryKey: ['parser-health'] });
  };
  const clearData = useMutation({
    mutationFn: () => api('/parser/data/clear', 'POST', { confirm: true }),
    onSuccess: () => {
      setConfirmClear(false);
      setNotice(t('dataCleared'));
      client.invalidateQueries();
    },
  });

  return (
    <section aria-labelledby="collect-heading">
      <PageHeader
        title={t('collect')}
        headingId="collect-heading"
        description={t('collectIntro')}
        actions={
          <Button
            variant="ghost"
            size="sm"
            icon={<Trash2 size={13} />}
            disabled={Boolean(activeRun)}
            onClick={() => setConfirmClear(true)}
          >
            {t('clearCollectedData')}
          </Button>
        }
      />
      {(notice || clearData.error) && (
        <div className="space-y-2 py-3">
          <Notice>{notice}</Notice>
          {clearData.error && <ErrorState error={clearData.error} />}
        </div>
      )}

      {activeRun ? (
        <RunProgressPanel run={activeRun} onChanged={refreshRuns} />
      ) : (
        <RunComposer
          brands={brands.data?.data}
          loading={brands.isLoading}
          onStarted={() => {
            setNotice(t('collectionStarted'));
            refreshRuns();
          }}
        />
      )}

      <h2 className="-mx-4 flex h-11 items-center border-b border-[var(--border-subtle)] px-4 text-[13px] font-medium text-[var(--text-primary)] md:-mx-6 md:px-6">
        {t('collectionHistory')}
      </h2>
      {runs.isLoading && <LoadingState />}
      {runs.error && <ErrorState error={runs.error} retry={() => runs.refetch()} />}
      {runs.data && !runs.data.data.length && <EmptyState message={t('noRuns')} />}
      {Boolean(runs.data?.data.length) && (
        <RunHistory
          runs={runs.data?.data ?? []}
          busy={Boolean(activeRun)}
          onOpen={setOpenRun}
          onChanged={refreshRuns}
        />
      )}

      <RunDetailModal runId={openRun} brandName={brandName} onClose={() => setOpenRun(null)} />
      <Modal
        open={confirmClear}
        onClose={() => setConfirmClear(false)}
        title={t('clearCollectedData')}
        maxWidth="max-w-md"
      >
        <p className="mb-4 text-[13px] text-[var(--text-secondary)]">{t('clearCollectedDataHelp')}</p>
        <div className="flex justify-end gap-2">
          <Button variant="secondary" onClick={() => setConfirmClear(false)}>
            {t('cancel')}
          </Button>
          <Button variant="danger" disabled={clearData.isPending} onClick={() => clearData.mutate()}>
            {t('delete')}
          </Button>
        </div>
      </Modal>
    </section>
  );
}
