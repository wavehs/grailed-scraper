'use client';

import { useMutation, useQuery } from '@tanstack/react-query';
import { RefreshCw, Trash2 } from 'lucide-react';
import { ErrorState, LoadingState } from '@/components/states';
import { Badge, statusVariant } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { DataTable, TableCell, TableHead, TableHeaderCell, TableRow } from '@/components/ui/data-table';
import { Modal } from '@/components/ui/modal';
import { api, getApi } from '@/lib/api';
import { useI18n } from '@/lib/i18n';
import type { RunDetail, RunSummary } from '@/lib/types';
import { formatDate, formatPercent } from '@/lib/utils';

/** Run states that will not change on their own. */
export const TERMINAL_STATUSES = new Set(['completed', 'partial', 'failed', 'cancelled', 'interrupted']);
const RESUMABLE_STATUSES = new Set(['interrupted', 'partial', 'failed', 'cancelled']);

/** Past collections with resume and delete; a run's id opens its per-brand tasks. */
export function RunHistory({
  runs,
  busy,
  onOpen,
  onChanged,
}: {
  runs: RunSummary[];
  /** A run is in progress, so none can be resumed. */
  busy: boolean;
  onOpen: (id: number) => void;
  onChanged: () => void;
}) {
  const { t, locale } = useI18n();
  const resume = useMutation({
    mutationFn: (id: number) => api<RunSummary>(`/parser/runs/${id}/resume`, 'POST'),
    onSuccess: onChanged,
  });
  const remove = useMutation({
    mutationFn: (id: number) => api<void>(`/parser/runs/${id}`, 'DELETE'),
    onSuccess: onChanged,
  });
  return (
    <>
      {(resume.error ?? remove.error) && (
        <div className="py-3">
          <ErrorState error={resume.error ?? remove.error} />
        </div>
      )}
      <DataTable bleed className="[&_td]:whitespace-nowrap">
        <TableHead>
          <tr>
            <TableHeaderCell>{t('run')}</TableHeaderCell>
            <TableHeaderCell>{t('started')}</TableHeaderCell>
            <TableHeaderCell>{t('kind')}</TableHeaderCell>
            <TableHeaderCell>{t('status')}</TableHeaderCell>
            <TableHeaderCell>{t('coverage')}</TableHeaderCell>
            <TableHeaderCell>{t('requests')}</TableHeaderCell>
            <TableHeaderCell>
              <span className="sr-only">{t('actions')}</span>
            </TableHeaderCell>
          </tr>
        </TableHead>
        <tbody>
          {runs.map((run) => (
            <TableRow key={run.id}>
              <TableCell>
                <button
                  className="text-[var(--text-primary)] hover:underline"
                  type="button"
                  onClick={() => onOpen(run.id)}
                >
                  #{run.id}
                </button>
              </TableCell>
              <TableCell className="text-[var(--text-secondary)]">
                {formatDate(run.started_at ?? run.created_at, locale)}
              </TableCell>
              <TableCell>{t(run.mode === 'full' ? 'firstCollection' : 'incrementalUpdate')}</TableCell>
              <TableCell>
                <Badge variant={statusVariant(run.status)} dot>
                  {t(run.status)}
                </Badge>
              </TableCell>
              <TableCell className="tabular-nums">
                <CoverageValue value={run.coverage} estimated={run.coverage_is_estimated} />
              </TableCell>
              <TableCell className="tabular-nums">{run.requests_made}</TableCell>
              <TableCell className="text-right">
                <span className="inline-flex gap-1">
                  {RESUMABLE_STATUSES.has(run.status) && (
                    <Button
                      size="sm"
                      variant="secondary"
                      icon={<RefreshCw size={13} />}
                      disabled={busy || resume.isPending}
                      onClick={() => resume.mutate(run.id)}
                    >
                      {t('resume')}
                    </Button>
                  )}
                  {TERMINAL_STATUSES.has(run.status) && (
                    <Button
                      size="sm"
                      variant="ghost"
                      icon={<Trash2 size={13} />}
                      disabled={remove.isPending}
                      onClick={() => remove.mutate(run.id)}
                    >
                      {t('delete')}
                    </Button>
                  )}
                </span>
              </TableCell>
            </TableRow>
          ))}
        </tbody>
      </DataTable>
    </>
  );
}

/** The per-brand tasks of one run in a dialog. */
export function RunDetailModal({
  runId,
  brandName,
  onClose,
}: {
  runId: number | null;
  brandName: (id?: number) => string;
  onClose: () => void;
}) {
  const { t } = useI18n();
  const detail = useQuery({
    queryKey: ['run-detail', runId],
    queryFn: ({ signal }) => getApi<RunDetail>(`/parser/runs/${runId}`, signal),
    enabled: runId !== null,
  });
  return (
    <Modal open={runId !== null} onClose={onClose} title={`${t('run')} #${runId ?? ''}`}>
      {detail.isLoading && <LoadingState />}
      {detail.error && <ErrorState error={detail.error} retry={() => detail.refetch()} />}
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
                <TableCell className="tabular-nums">
                  {task.hits_collected}
                  {task.expected_hits ? ` / ${task.expected_hits}` : ''}
                </TableCell>
                <TableCell className="tabular-nums">
                  <CoverageValue value={task.coverage} estimated={task.expected_source === 'estimated'} />
                </TableCell>
                <TableCell className="text-[var(--text-secondary)]">{task.error ?? '—'}</TableCell>
              </TableRow>
            ))}
          </tbody>
        </DataTable>
      )}
    </Modal>
  );
}

function CoverageValue({ value, estimated }: { value?: string; estimated?: boolean }) {
  const { t } = useI18n();
  return (
    <>
      {formatPercent(value)}
      {estimated && value !== undefined && (
        <span className="ml-1 text-xs text-[var(--text-muted)]" title={t('coverageEstimateHint')}>
          {t('estimate')}
        </span>
      )}
    </>
  );
}
