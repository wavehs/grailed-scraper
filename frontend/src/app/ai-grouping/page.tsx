'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Ban, Bot, Play, PlayCircle, RotateCcw, ShieldCheck } from 'lucide-react';
import { EmptyState, ErrorState, Notice } from '@/components/states';
import { Badge, statusVariant } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { PageHeader } from '@/components/ui/page-header';
import { ProgressBar } from '@/components/ui/progress-bar';
import { StatCard } from '@/components/ui/stat-card';
import { api, errorMessage, getApi } from '@/lib/api';
import { useI18n } from '@/lib/i18n';
import type {
  AiGroupingPreflight,
  AiGroupingRun,
  AiGroupingRunList,
  GroupingRunMode,
} from '@/lib/types';

const activeStatuses = new Set([
  'preparing',
  'submitted',
  'running',
  'validating',
  'waiting_for_market',
  'applying',
]);
const resumableStatuses = new Set(['failed', 'cancelled', 'interrupted', 'needs_attention']);

const count = (value: number) => value.toLocaleString('en-US');
const usd = (value: string) => `$${Number(value).toFixed(2)}`;

export default function AiGroupingPage() {
  const { t } = useI18n();
  const client = useQueryClient();
  const preflight = useQuery({
    queryKey: ['ai-grouping-preflight', 'canary'],
    queryFn: ({ signal }) =>
      getApi<AiGroupingPreflight>('/ai-grouping/preflight?mode=canary', signal),
  });
  const runs = useQuery({
    queryKey: ['ai-grouping-runs'],
    queryFn: ({ signal }) =>
      getApi<AiGroupingRunList>('/ai-grouping/runs?limit=50&offset=0', signal),
    refetchInterval: (query) =>
      query.state.data?.data.some((run) => activeStatuses.has(run.status)) ? 5_000 : false,
  });
  const listedRun =
    runs.data?.data.find((run) => activeStatuses.has(run.status)) ?? runs.data?.data[0];
  const detail = useQuery({
    queryKey: ['ai-grouping-run', listedRun?.id],
    enabled: listedRun !== undefined,
    initialData: listedRun,
    queryFn: ({ signal }) => getApi<AiGroupingRun>(`/ai-grouping/runs/${listedRun?.id}`, signal),
    refetchInterval: (query) =>
      query.state.data && activeStatuses.has(query.state.data.status) ? 5_000 : false,
  });
  const run = detail.data ?? listedRun;
  const hasActiveRun = runs.data?.data.some((item) => activeStatuses.has(item.status)) ?? false;

  const refresh = () => {
    client.invalidateQueries({ queryKey: ['ai-grouping-preflight'] });
    client.invalidateQueries({ queryKey: ['ai-grouping-runs'] });
    client.invalidateQueries({ queryKey: ['ai-grouping-run'] });
  };
  const start = useMutation({
    mutationFn: async (mode: GroupingRunMode) => {
      const check =
        mode === 'canary' && preflight.data
          ? preflight.data
          : await getApi<AiGroupingPreflight>(`/ai-grouping/preflight?mode=${mode}`);
      if (!check.gemini_configured) throw new Error(t('aiGeminiMissing'));
      if (!check.can_start) throw new Error(check.blocked_reason ?? t('aiGroupingBlocked'));
      return api<AiGroupingRun>('/ai-grouping/runs', 'POST', {
        mode,
        budget_cap_usd: check.budget_cap_usd,
      });
    },
    onSuccess: refresh,
  });
  const control = useMutation({
    mutationFn: ({ id, action }: { id: number; action: 'cancel' | 'resume' | 'rollback' }) =>
      api<AiGroupingRun>(
        `/ai-grouping/runs/${id}/${action}`,
        'POST',
        action === 'resume' ? { additional_budget_cap_usd: '0.00' } : undefined,
      ),
    onSuccess: refresh,
  });

  const configured = preflight.data?.gemini_configured ?? false;
  const busy = start.isPending || control.isPending || hasActiveRun;
  const actionError = start.error ?? control.error;

  return (
    <section className="space-y-6">
      <PageHeader title={t('aiGrouping')} description={t('aiGroupingIntro')} />

      {preflight.isError ? (
        <ErrorState error={preflight.error} retry={() => preflight.refetch()} />
      ) : (
        <>
          <Card className="p-5">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <h2 className="flex items-center gap-2 text-sm font-semibold text-[var(--text-primary)]">
                  <ShieldCheck size={17} className="text-[var(--accent)]" />
                  {t('aiDataPrivacy')}
                </h2>
                <p className="mt-2 text-sm text-[var(--text-secondary)]">
                  {t('aiGoogleDisclosure')}
                </p>
              </div>
              <Badge
                variant={preflight.isLoading ? 'muted' : configured ? 'success' : 'danger'}
                dot={!preflight.isLoading}
              >
                {preflight.isLoading
                  ? t('loading')
                  : configured
                    ? t('aiGeminiConfigured')
                    : t('aiGeminiNotConfigured')}
              </Badge>
            </div>
          </Card>

          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4" aria-busy={preflight.isLoading}>
            <StatCard
              label={t('aiListingsPlanned')}
              value={preflight.data ? count(preflight.data.listing_count) : '—'}
            />
            <StatCard
              label={t('aiUniqueInputs')}
              value={preflight.data ? count(preflight.data.unique_input_count) : '—'}
            />
            <StatCard
              label={t('aiEstimatedTokens')}
              value={
                preflight.data
                  ? count(
                      preflight.data.estimated_input_tokens +
                        preflight.data.estimated_output_tokens,
                    )
                  : '—'
              }
            />
            <StatCard
              label={t('aiEstimatedCost')}
              value={preflight.data ? usd(preflight.data.estimated_cost_usd) : '—'}
            />
          </div>

          {preflight.data?.blocked_reason && <Notice error>{preflight.data.blocked_reason}</Notice>}
          {actionError && <Notice error>{errorMessage(actionError)}</Notice>}

          <Card className="p-5">
            <h2 className="text-sm font-semibold text-[var(--text-primary)]">{t('aiStartRun')}</h2>
            <p className="mt-1 text-xs text-[var(--text-muted)]">{t('aiStartHelp')}</p>
            <div className="mt-4 flex flex-wrap gap-2">
              <Button
                icon={<Play size={15} />}
                disabled={busy || !configured || !preflight.data?.can_start}
                onClick={() => start.mutate('canary')}
              >
                {t('aiStartCanary')} · {usd(preflight.data?.budget_cap_usd ?? '0.50')}{' '}
                {t('aiMaximum')}
              </Button>
              <Button
                variant="secondary"
                icon={<Bot size={15} />}
                disabled={busy || !configured}
                onClick={() => start.mutate('remaining')}
              >
                {t('aiProcessRemaining')}
              </Button>
              <Button
                variant="secondary"
                icon={<Bot size={15} />}
                disabled={busy || !configured}
                onClick={() => start.mutate('pending')}
              >
                {t('aiProcessPending')}
              </Button>
            </div>
          </Card>
        </>
      )}

      <div>
        <h2 className="mb-3 text-sm font-semibold uppercase tracking-wider text-[var(--text-muted)]">
          {t('aiLatestRun')}
        </h2>
        {runs.isError ? (
          <ErrorState error={runs.error} retry={() => runs.refetch()} />
        ) : !run && !runs.isLoading ? (
          <EmptyState message={t('aiNoRuns')} />
        ) : run ? (
          <Card className="space-y-5 p-5">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="flex items-center gap-2">
                <span className="font-semibold text-[var(--text-primary)]">#{run.id}</span>
                <Badge variant={statusVariant(run.status)} dot>
                  {t(run.status)}
                </Badge>
                <Badge variant="muted">{t(`aiMode_${run.mode}`)}</Badge>
              </div>
              <div className="flex flex-wrap gap-2">
                {activeStatuses.has(run.status) && (
                  <Button
                    size="sm"
                    variant="danger"
                    icon={<Ban size={13} />}
                    disabled={control.isPending}
                    onClick={() => control.mutate({ id: run.id, action: 'cancel' })}
                  >
                    {t('cancel')}
                  </Button>
                )}
                {resumableStatuses.has(run.status) && (
                  <Button
                    size="sm"
                    variant="success"
                    icon={<PlayCircle size={13} />}
                    disabled={control.isPending}
                    onClick={() => control.mutate({ id: run.id, action: 'resume' })}
                  >
                    {t('resume')}
                  </Button>
                )}
                {run.rollback_allowed && (
                  <Button
                    size="sm"
                    variant="danger"
                    icon={<RotateCcw size={13} />}
                    disabled={control.isPending}
                    onClick={() => control.mutate({ id: run.id, action: 'rollback' })}
                  >
                    {t('aiRollback')}
                  </Button>
                )}
              </div>
            </div>

            <ProgressBar
              value={run.progress_percent}
              label={t('aiGroupingProgress')}
              indeterminate={activeStatuses.has(run.status) && run.progress_percent === 0}
            />

            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-6">
              <StatCard
                label={t('aiResolved')}
                value={`${count(run.resolved_items)} / ${count(run.total_items)}`}
              />
              <StatCard label={t('aiAmbiguous')} value={count(run.ambiguous_items)} />
              <StatCard label={t('aiSafeUnique')} value={count(run.unique_fallback_items)} />
              <StatCard label={t('aiFailed')} value={count(run.failed_items)} />
              <StatCard label={t('aiActualCost')} value={usd(run.actual_cost_usd)} />
              <StatCard label={t('aiBudgetCap')} value={usd(run.budget_cap_usd)} />
            </div>

            {run.error_code && <Notice error>{run.error_code}</Notice>}
            {run.warnings.length > 0 && (
              <ul className="space-y-1 text-xs text-[var(--warning)]">
                {run.warnings.map((warning) => (
                  <li key={warning}>{warning}</li>
                ))}
              </ul>
            )}

            {run.examples.length > 0 && (
              <div>
                <h3 className="text-sm font-semibold text-[var(--text-primary)]">
                  {t('aiChangeExamples')}
                </h3>
                <ul className="mt-3 space-y-2">
                  {run.examples.map((example) => (
                    <li
                      className="rounded-md border border-[var(--border-subtle)] p-3 text-sm"
                      key={example.listing_id}
                    >
                      <p className="text-xs text-[var(--text-muted)]">“{example.title}”</p>
                      <p className="mt-1 text-[var(--text-secondary)]">
                        {example.old_group ?? t('aiNoPreviousGroup')} →{' '}
                        <strong className="text-[var(--text-primary)]">{example.new_group}</strong>
                      </p>
                      <p className="mt-1 text-xs text-[var(--text-muted)]">
                        {example.product_type} · {t('confidence')} {example.confidence}
                      </p>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </Card>
        ) : (
          <Card className="p-5 text-sm text-[var(--text-muted)]" aria-busy="true">
            {t('loading')}
          </Card>
        )}
      </div>
    </section>
  );
}
