'use client';

import { useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Ban, Play, RefreshCw, Trash2 } from 'lucide-react';
import { Badge, statusVariant } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { DataTable, TableCell, TableHead, TableHeaderCell, TableRow } from '@/components/ui/data-table';
import { Modal } from '@/components/ui/modal';
import { PageHeader } from '@/components/ui/page-header';
import { ProgressBar } from '@/components/ui/progress-bar';
import { StatCard } from '@/components/ui/stat-card';
import { EmptyState, ErrorState, LoadingState, Notice } from '@/components/states';
import { api, getApi } from '@/lib/api';
import { useI18n } from '@/lib/i18n';
import { useBrandsQuery, useRunsQuery } from '@/lib/queries';
import { formatDate, formatPercent } from '@/lib/utils';
import type { RunDetail, RunProgress, RunStartResponse, RunSummary } from '@/lib/types';

const terminal = new Set(['completed', 'partial', 'failed', 'cancelled', 'interrupted']);

export default function CollectPage() {
  const { t, locale } = useI18n();
  const client = useQueryClient();
  const brands = useBrandsQuery();
  const [selected, setSelected] = useState<number[] | null>(null);
  const [openRun, setOpenRun] = useState<number | null>(null);
  const [confirmClear, setConfirmClear] = useState(false);
  const [notice, setNotice] = useState('');
  const runs = useRunsQuery(30, 0, (query: { state: { data?: { data: RunSummary[] } } }) =>
    query.state.data?.data.some((run) => !terminal.has(run.status)) ? 2_000 : false,
  );
  const activeRun = runs.data?.data.find((run) => !terminal.has(run.status));
  const progress = useQuery({
    queryKey: ['run-progress', activeRun?.id],
    queryFn: ({ signal }) => getApi<RunProgress>(`/parser/runs/${activeRun?.id}/progress`, signal),
    enabled: Boolean(activeRun),
    refetchInterval: 2_000,
  });
  const detail = useQuery({
    queryKey: ['run-detail', openRun],
    queryFn: ({ signal }) => getApi<RunDetail>(`/parser/runs/${openRun}`, signal),
    enabled: openRun !== null,
  });

  const mapped = useMemo(
    () => (brands.data?.data ?? []).filter((brand) => brand.status === 'verified'),
    [brands.data],
  );
  useEffect(() => {
    if (selected === null && brands.data) setSelected(mapped.map((brand) => brand.id));
  }, [brands.data, mapped, selected]);
  const brandName = (id?: number) =>
    brands.data?.data.find((brand) => brand.id === id)?.name ?? (id ? `#${id}` : '—');

  const refreshAll = () => {
    client.invalidateQueries({ queryKey: ['runs'] });
    client.invalidateQueries({ queryKey: ['parser-health'] });
  };
  const start = useMutation({
    mutationFn: () =>
      api<RunStartResponse>('/parser/run', 'POST', {
        brand_ids: selected && selected.length < mapped.length ? selected : null,
      }),
    onSuccess: () => {
      setNotice(t('collectionStarted'));
      refreshAll();
    },
  });
  const control = useMutation({
    mutationFn: ({ id, action }: { id: number; action: 'cancel' | 'resume' }) =>
      api<RunSummary>(`/parser/runs/${id}/${action}`, 'POST'),
    onSuccess: refreshAll,
  });
  const removeRun = useMutation({
    mutationFn: (id: number) => api<void>(`/parser/runs/${id}`, 'DELETE'),
    onSuccess: refreshAll,
  });
  const clearData = useMutation({
    mutationFn: () => api('/parser/data/clear', 'POST', { confirm: true }),
    onSuccess: () => {
      setConfirmClear(false);
      setNotice(t('dataCleared'));
      client.invalidateQueries();
    },
  });

  const error = start.error ?? control.error ?? removeRun.error ?? clearData.error;
  const toggle = (id: number) =>
    setSelected((current) =>
      (current ?? []).includes(id)
        ? (current ?? []).filter((item) => item !== id)
        : [...(current ?? []), id],
    );
  const done = progress.data?.tasks_done ?? 0;
  const total = progress.data?.tasks_total ?? 0;

  return (
    <div className="space-y-6">
      <PageHeader
        title={t('collect')}
        description={t('collectIntro')}
        actions={
          <Button
            variant="danger"
            size="sm"
            icon={<Trash2 size={14} />}
            disabled={Boolean(activeRun)}
            onClick={() => setConfirmClear(true)}
          >
            {t('clearCollectedData')}
          </Button>
        }
      />
      <Notice>{notice}</Notice>
      {error && <ErrorState error={error} />}

      {activeRun ? (
        <Card className="space-y-4 p-5">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <h2 className="font-semibold text-[var(--text-primary)]">
              {t('collectionRunning')} #{activeRun.id}
            </h2>
            <Button
              variant="secondary"
              size="sm"
              icon={<Ban size={14} />}
              disabled={control.isPending}
              onClick={() => control.mutate({ id: activeRun.id, action: 'cancel' })}
            >
              {t('cancel')}
            </Button>
          </div>
          <ProgressBar value={done} max={Math.max(total, 1)} label={t('progress')} />
          <div className="grid gap-3 sm:grid-cols-4">
            <StatCard label={t('currentBrand')} value={progress.data?.current_brand ?? '—'} />
            <StatCard label={t('tasks')} value={`${done}/${total}`} />
            <StatCard label={t('listingsFetched')} value={progress.data?.hits_fetched ?? 0} />
            <StatCard label={t('requests')} value={progress.data?.requests_made ?? 0} />
          </div>
        </Card>
      ) : (
        <Card className="space-y-4 p-5">
          <div>
            <h2 className="font-semibold text-[var(--text-primary)]">{t('updateData')}</h2>
            <p className="mt-1 text-sm text-[var(--text-muted)]">{t('updateDataHelp')}</p>
          </div>
          {brands.isLoading && <LoadingState />}
          {brands.data && !mapped.length && (
            <p className="text-sm text-[var(--text-secondary)]">
              {t('noMappedBrands')}{' '}
              <Link className="underline" href="/brands">
                {t('brands')}
              </Link>
            </p>
          )}
          <div className="flex flex-wrap gap-2">
            {mapped.map((brand) => {
              const on = (selected ?? []).includes(brand.id);
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
                  {brand.listings_count ? ` · ${brand.listings_count}` : ''}
                </button>
              );
            })}
          </div>
          <Button
            icon={<Play size={16} />}
            disabled={!(selected ?? []).length || start.isPending}
            onClick={() => start.mutate()}
          >
            {start.isPending ? t('starting') : t('updateData')}
          </Button>
        </Card>
      )}

      <section className="space-y-3">
        <h2 className="text-lg font-semibold text-[var(--text-primary)]">{t('collectionHistory')}</h2>
        {runs.isLoading && <LoadingState />}
        {runs.data && !runs.data.data.length && <EmptyState message={t('noRuns')} />}
        {Boolean(runs.data?.data.length) && (
          <DataTable>
            <TableHead>
              <tr>
                <TableHeaderCell>{t('run')}</TableHeaderCell>
                <TableHeaderCell>{t('started')}</TableHeaderCell>
                <TableHeaderCell>{t('kind')}</TableHeaderCell>
                <TableHeaderCell>{t('status')}</TableHeaderCell>
                <TableHeaderCell>{t('coverage')}</TableHeaderCell>
                <TableHeaderCell>{t('requests')}</TableHeaderCell>
                <TableHeaderCell>{t('actions')}</TableHeaderCell>
              </tr>
            </TableHead>
            <tbody>
              {runs.data?.data.map((run) => (
                <TableRow key={run.id}>
                  <TableCell>
                    <button className="underline" type="button" onClick={() => setOpenRun(run.id)}>
                      #{run.id}
                    </button>
                  </TableCell>
                  <TableCell>{formatDate(run.started_at ?? run.created_at, locale)}</TableCell>
                  <TableCell>{t(run.mode === 'full' ? 'firstCollection' : 'incrementalUpdate')}</TableCell>
                  <TableCell>
                    <Badge variant={statusVariant(run.status)} dot>
                      {t(run.status)}
                    </Badge>
                  </TableCell>
                  <TableCell>{formatPercent(run.coverage)}</TableCell>
                  <TableCell>{run.requests_made}</TableCell>
                  <TableCell className="space-x-2 whitespace-nowrap">
                    {['interrupted', 'partial', 'failed', 'cancelled'].includes(run.status) && (
                      <Button
                        size="sm"
                        variant="secondary"
                        icon={<RefreshCw size={14} />}
                        disabled={Boolean(activeRun) || control.isPending}
                        onClick={() => control.mutate({ id: run.id, action: 'resume' })}
                      >
                        {t('resume')}
                      </Button>
                    )}
                    {terminal.has(run.status) && (
                      <Button
                        size="sm"
                        variant="ghost"
                        icon={<Trash2 size={14} />}
                        disabled={removeRun.isPending}
                        onClick={() => removeRun.mutate(run.id)}
                      >
                        {t('delete')}
                      </Button>
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </tbody>
          </DataTable>
        )}
      </section>

      <Modal open={openRun !== null} onClose={() => setOpenRun(null)} title={`${t('run')} #${openRun ?? ''}`}>
        {detail.isLoading && <LoadingState />}
        {detail.data && (
          <DataTable>
            <TableHead>
              <tr>
                <TableHeaderCell>{t('brand')}</TableHeaderCell>
                <TableHeaderCell>{t('index')}</TableHeaderCell>
                <TableHeaderCell>{t('status')}</TableHeaderCell>
                <TableHeaderCell>{t('hits')}</TableHeaderCell>
                <TableHeaderCell>{t('coverage')}</TableHeaderCell>
                <TableHeaderCell>{t('sourceError')}</TableHeaderCell>
              </tr>
            </TableHead>
            <tbody>
              {detail.data.tasks.map((task) => (
                <TableRow key={task.id}>
                  <TableCell>{brandName(task.brand_id)}</TableCell>
                  <TableCell>{t(task.index_type)}</TableCell>
                  <TableCell>
                    <Badge variant={statusVariant(task.status)}>{t(task.status)}</Badge>
                  </TableCell>
                  <TableCell>
                    {task.hits_collected}
                    {task.expected_hits ? ` / ${task.expected_hits}` : ''}
                  </TableCell>
                  <TableCell>{formatPercent(task.coverage)}</TableCell>
                  <TableCell>{task.error ?? '—'}</TableCell>
                </TableRow>
              ))}
            </tbody>
          </DataTable>
        )}
      </Modal>

      <Modal
        open={confirmClear}
        onClose={() => setConfirmClear(false)}
        title={t('clearCollectedData')}
        maxWidth="max-w-md"
      >
        <p className="mb-4 text-sm text-[var(--text-secondary)]">{t('clearCollectedDataHelp')}</p>
        <Button variant="danger" disabled={clearData.isPending} onClick={() => clearData.mutate()}>
          {t('delete')}
        </Button>
      </Modal>
    </div>
  );
}
