'use client';

import { useEffect, useState } from 'react';
import { useSearchParams } from 'next/navigation';
import Link from 'next/link';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  AlertTriangle,
  ArrowRight,
  Ban,
  Check,
  CheckCircle2,
  Compass,
  GitBranch,
  Play,
  PlayCircle,
  RefreshCw,
  Rocket,
  TestTube,
  XCircle,
} from 'lucide-react';
import { Badge, statusVariant } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { DataTable, TableCell, TableHead, TableHeaderCell, TableRow } from '@/components/ui/data-table';
import { Modal } from '@/components/ui/modal';
import { PageHeader } from '@/components/ui/page-header';
import { ProgressBar } from '@/components/ui/progress-bar';
import { StatCard } from '@/components/ui/stat-card';
import { HelpTip } from '@/components/ui/help-tip';
import { EmptyState, ErrorState, LoadingState, Notice } from '@/components/states';
import { api, getApi } from '@/lib/api';
import { useI18n } from '@/lib/i18n';
import { useApiHealth, useBrandsQuery, useParserHealth, useRunsQuery, useSettingsQuery } from '@/lib/queries';
import { formatPercent } from '@/lib/utils';
import type {
  DiscoveryResponse,
  FetchPlan,
  RunProgress,
  RunReport,
  RunStartResponse,
  RunSummary,
} from '@/lib/types';

const terminal = new Set(['completed', 'partial', 'failed', 'cancelled']);
const blockingReasons = new Set([
  'live_compliance_not_acknowledged',
  'credentials_missing',
  'schema_missing',
]);

/* ── Stepper step ── */
function WorkflowStep({
  step,
  title,
  subtitle,
  status,
  isLast,
  children,
}: {
  step: number;
  title: string;
  subtitle: string;
  status: 'done' | 'active' | 'pending';
  isLast?: boolean;
  children?: React.ReactNode;
}) {
  const colors = {
    done: 'border-[var(--success-border)] bg-[var(--success-bg)] text-[var(--success)]',
    active: 'border-[var(--accent)] bg-[var(--accent-soft)] text-[var(--accent)]',
    pending: 'border-[var(--border-default)] bg-[var(--bg-surface)] text-[var(--text-muted)]',
  };
  const lineColor = {
    done: 'bg-[var(--success)]',
    active: 'bg-[var(--accent)]',
    pending: 'bg-[var(--border-subtle)]',
  };

  return (
    <div className="flex gap-3">
      <div className="flex flex-col items-center">
        <div
          className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-full border text-xs font-bold ${colors[status]}`}
        >
          {status === 'done' ? <Check size={14} /> : step}
        </div>
        {!isLast && <div className={`mt-1 h-full w-0.5 ${lineColor[status]}`} />}
      </div>
      <div className="pb-6">
        <p className="text-sm font-semibold text-[var(--text-primary)]">{title}</p>
        <p className="text-xs text-[var(--text-muted)]">{subtitle}</p>
        {children && <div className="mt-2">{children}</div>}
      </div>
    </div>
  );
}

export default function ParserRunsPage() {
  const { t } = useI18n();
  const searchParams = useSearchParams();
  const client = useQueryClient();
  const apiHealth = useApiHealth();
  const parserHealth = useParserHealth();
  const [mode, setMode] = useState<'delta' | 'full' | 'refresh_active'>('delta');
  const [brandIds, setBrandIds] = useState<number[]>([]);
  const [plan, setPlan] = useState<FetchPlan | null>(null);
  const [selectedRun, setSelectedRun] = useState<number | null>(null);
  const [notice, setNotice] = useState('');
  const [maxRequests, setMaxRequests] = useState(5000);
  const [maxItems, setMaxItems] = useState(500);
  const [requestsPerMinute, setRequestsPerMinute] = useState(90);
  const [concurrentRequests, setConcurrentRequests] = useState(3);

  const openRun = (runId: number) => setSelectedRun(runId);
  const closeRun = () => setSelectedRun(null);

  useEffect(() => {
    const value = Number(searchParams.get('run'));
    if (value) setSelectedRun(value);
  }, [searchParams]);

  const brands = useBrandsQuery();
  const settings = useSettingsQuery();
  useEffect(() => {
    const parser = settings.data?.groups.parser;
    if (!parser) return;
    setMaxRequests(Number(parser.parser_max_requests_per_run?.value ?? 5000));
    setMaxItems(Number(parser.parser_max_items_per_brand?.value ?? 500));
    setRequestsPerMinute(Number(parser.requests_per_minute?.value ?? 90));
    setConcurrentRequests(Number(parser.max_concurrent_requests?.value ?? 3));
  }, [settings.data]);
  const runs = useRunsQuery(50, 0, (query: any) =>
    query.state.data?.data.some((run: any) => !terminal.has(run.status)) ? 2_000 : false,
  );
  const selectedSummary = runs.data?.data.find((run) => run.id === selectedRun);
  const progress = useQuery({
    queryKey: ['run-progress', selectedRun],
    enabled: selectedRun !== null,
    queryFn: ({ signal }) => getApi<RunProgress>(`/parser/runs/${selectedRun}/progress`, signal),
    refetchInterval: (query) =>
      query.state.data && !terminal.has(query.state.data.status) ? 2_000 : false,
  });
  const report = useQuery({
    queryKey: ['run-report', selectedRun],
    enabled: selectedRun !== null && !!selectedSummary && terminal.has(selectedSummary.status),
    queryFn: ({ signal }) => getApi<RunReport>(`/parser/runs/${selectedRun}/report`, signal),
  });
  const refresh = () => client.invalidateQueries({ queryKey: ['runs'] });
  const discovery = useMutation({
    mutationFn: () =>
      api<DiscoveryResponse>('/parser/discovery/refresh', 'POST', { force: true }),
    onSuccess: () => {
      setPlan(null);
      setNotice(t('success'));
      client.invalidateQueries({ queryKey: ['parser-health'] });
      client.invalidateQueries({ queryKey: ['brands'] });
    },
  });
  const start = useMutation({
    mutationFn: (payload: {
      dry_run: boolean;
      confirm_over_budget?: boolean;
      confirmation_token?: string;
    }) =>
      api<RunStartResponse>('/parser/run', 'POST', {
        mode,
        brand_ids: brandIds.length ? brandIds : null,
        max_requests: maxRequests,
        max_items_per_brand: maxItems,
        requests_per_minute: requestsPerMinute,
        concurrent_requests: concurrentRequests,
        ...payload,
      }),
    onSuccess: (result) => {
      if (result.dry_run) {
        setPlan(result.plan);
        setNotice('');
      } else {
        setPlan(null);
        openRun(result.run.id);
        setNotice(t('success'));
        refresh();
      }
    },
  });
  const control = useMutation({
    mutationFn: ({ id, action }: { id: number; action: 'cancel' | 'resume' }) =>
      api<RunSummary>(`/parser/runs/${id}/${action}`, 'POST'),
    onSuccess: (run) => {
      openRun(run.id);
      setNotice(t('success'));
      refresh();
      client.invalidateQueries({ queryKey: ['run-progress', run.id] });
    },
  });
  const overLimit = Boolean(plan?.budget.over_limit);
  const blockers = (parserHealth.data?.reasons ?? []).filter((reason) =>
    blockingReasons.has(reason),
  );
  const selectedBrands = brandIds.length
    ? (brands.data?.data ?? []).filter((brand) => brandIds.includes(brand.id))
    : (brands.data?.data ?? []);
  const mappingsReady =
    selectedBrands.length > 0 && selectedBrands.every((brand) => brand.status === 'verified');
  const canPlan = Boolean(
    apiHealth.writable && parserHealth.data && blockers.length === 0 && mappingsReady,
  );
  const progressValue = progress.data?.tasks_total
    ? Math.round((progress.data.tasks_done / progress.data.tasks_total) * 100)
    : 0;
  const error =
    brands.error ??
    runs.error ??
    settings.error ??
    discovery.error ??
    start.error ??
    control.error ??
    progress.error ??
    report.error;
  if (brands.isLoading && runs.isLoading) return <LoadingState />;

  const discoveryStatus = parserHealth.data?.discovery?.status ?? 'unavailable';
  const discoveryDone = ['ready', 'completed', 'stale'].includes(discoveryStatus);
  const step1Status = discoveryDone ? 'done' : 'active';
  const step2Status = discoveryDone ? (mappingsReady ? 'done' : 'active') : 'pending';
  const step3Status = plan ? 'done' : step2Status === 'done' ? 'active' : 'pending';
  const step4Status = plan ? 'active' : 'pending';

  return (
    <section className="space-y-6" aria-labelledby="runs-heading">
      <PageHeader title={t('runParser')} description={t('runIntro')} />
      <Notice>{notice}</Notice>
      {error && (
        <ErrorState
          error={error}
          retry={() => {
            brands.refetch();
            runs.refetch();
          }}
        />
      )}

      <Card className="p-5">
        {blockers.length > 0 && (
          <div className="mt-3 rounded-md border border-[var(--danger-border)] bg-[var(--danger-bg)] p-3" role="alert">
            {blockers.map((reason) => (
              <p key={reason} className="flex items-center gap-2 text-xs text-[var(--danger)]">
                <XCircle size={14} /> {t(reason)}
              </p>
            ))}
          </div>
        )}
        {!mappingsReady && (
          <Notice error>
            {t('brand_mapping_required')}{' '}
            <Link className="underline" href="/brands">{t('reviewMappings')}</Link>
          </Notice>
        )}
      </Card>

      {/* Config form */}
      <Card className="p-5">
        <div className="mb-5">
          <h2 className="text-lg font-semibold text-[var(--text-primary)]">{t('newParserRun')}</h2>
          <p className="mt-1 text-sm text-[var(--text-muted)]">{t('newParserRunHelp')}</p>
        </div>
        <form
          className="space-y-5"
          onSubmit={(event) => {
            event.preventDefault();
            start.mutate({ dry_run: true });
          }}
        >
          <label className="block max-w-md text-sm">
            <span className="flex items-center gap-1 font-medium text-[var(--text-primary)]">
              {t('mode')} <HelpTip label={t('mode')} text={t('modeHelp')} />
            </span>
            <select
              className="mt-1.5 w-full rounded-lg"
              value={mode}
              onChange={(event) => {
                setMode(event.target.value as typeof mode);
                setPlan(null);
              }}
            >
              <option value="delta">{t('deltaSimple')}</option>
              <option value="full">{t('fullSimple')}</option>
              <option value="refresh_active">{t('refreshActiveSimple')}</option>
            </select>
          </label>
          <fieldset>
            <legend className="font-medium text-[var(--text-primary)]">
              {t('selectedBrands')}
            </legend>
            <div className="mt-1.5 flex max-h-32 flex-wrap gap-2 overflow-auto">
              {brands.data?.data.map((brand) => (
                <label
                  className={`inline-flex cursor-pointer items-center gap-1.5 rounded-md border px-2.5 py-1.5 text-xs transition-colors ${
                    brandIds.includes(brand.id)
                      ? 'border-[var(--accent)] bg-[var(--accent-soft)] text-[var(--accent)]'
                      : 'border-[var(--border-subtle)] text-[var(--text-secondary)] hover:border-[var(--border-default)]'
                  }`}
                  key={brand.id}
                >
                  <input
                    className="sr-only"
                    type="checkbox"
                    checked={brandIds.includes(brand.id)}
                    onChange={(event) =>
                      setBrandIds((old) => {
                        setPlan(null);
                        return event.target.checked
                          ? [...old, brand.id]
                          : old.filter((id) => id !== brand.id);
                      })
                    }
                  />
                  {brand.name}
                </label>
              ))}
            </div>
            <button
              type="button"
              className="mt-2 text-xs text-[var(--accent)] transition-colors hover:text-[var(--accent-hover)]"
              onClick={() => {
                setBrandIds([]);
                setPlan(null);
              }}
            >
              {t('allBrands')}
            </button>
          </fieldset>
          <fieldset>
            <legend className="mb-3 font-medium text-[var(--text-primary)]">{t('runLimits')}</legend>
            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
              {[
                ['maxItemsThisRun', maxItems, setMaxItems, 1, undefined, 'maxItemsThisRunHelp'],
                ['maxRequestsThisRun', maxRequests, setMaxRequests, 1, undefined, 'maxRequestsThisRunHelp'],
                ['requestsPerMinuteSimple', requestsPerMinute, setRequestsPerMinute, 1, 90, 'requestsPerMinuteHelp'],
                ['simultaneousRequests', concurrentRequests, setConcurrentRequests, 1, 3, 'simultaneousRequestsHelp'],
              ].map(([labelKey, value, setter, min, max, helpKey]) => (
                <label className="text-sm" key={String(labelKey)}>
                  <span className="flex min-h-7 items-center gap-1 text-[var(--text-secondary)]">
                    {t(String(labelKey))}
                    <HelpTip label={t(String(labelKey))} text={t(String(helpKey))} />
                  </span>
                  <input
                    className="w-full tabular-nums"
                    min={Number(min)}
                    max={max === undefined ? undefined : Number(max)}
                    type="number"
                    value={Number(value)}
                    onChange={(event) => (setter as (value: number) => void)(Number(event.target.value))}
                  />
                </label>
              ))}
            </div>
          </fieldset>
          <Button
            type="submit"
            icon={<Play size={16} />}
            disabled={!canPlan || start.isPending}
          >
            {start.isPending ? t('planning') : t('calculateRun')}
          </Button>
        </form>
      </Card>

      {/* Budget preview */}
      {plan && (
        <Card className="p-5 animate-slide-up">
          <h2 className="mb-3 text-sm font-semibold uppercase tracking-wider text-[var(--text-muted)]">
            {t('budget')}
          </h2>
          <div className="grid gap-4 sm:grid-cols-3">
            <StatCard label={t('estimatedRequests')} value={String(plan.budget.estimated_requests)} />
            <StatCard label={t('estimatedHits')} value={String(plan.budget.estimated_hits)} />
            <StatCard label={t('limit')} value={String(plan.budget.limit)} />
          </div>
          {overLimit && <Notice error>{t('overBudget')}</Notice>}
          {plan.warnings.length > 0 && (
            <ul className="mt-3 space-y-1">
              {plan.warnings.map((warning) => (
                <li key={warning} className="flex items-center gap-2 text-xs text-[var(--warning)]">
                  <AlertTriangle size={14} /> {warning}
                </li>
              ))}
            </ul>
          )}
          <Button
            className="mt-4"
            icon={<Rocket size={16} />}
            disabled={!canPlan || start.isPending}
            onClick={() =>
              start.mutate({
                dry_run: false,
                confirm_over_budget: overLimit,
                confirmation_token: plan.confirmation_token,
              })
            }
          >
            {start.isPending ? t('starting') : overLimit ? t('confirmBudget') : t('startRun')}
          </Button>
        </Card>
      )}

      {/* Runs table */}
      <DataTable>
        <TableHead>
          <tr>
            <TableHeaderCell>ID</TableHeaderCell>
            <TableHeaderCell>{t('mode')}</TableHeaderCell>
            <TableHeaderCell>{t('status')}</TableHeaderCell>
            <TableHeaderCell>{t('phase')}</TableHeaderCell>
            <TableHeaderCell>{t('coverage')}</TableHeaderCell>
            <TableHeaderCell>{t('requests')}</TableHeaderCell>
            <TableHeaderCell>{t('actions')}</TableHeaderCell>
          </tr>
        </TableHead>
        <tbody>
          {runs.data?.data.map((run) => (
            <TableRow key={run.id}>
              <TableCell>
                <span className="font-medium text-[var(--text-primary)]">#{run.id}</span>
              </TableCell>
              <TableCell>{t(run.mode)}</TableCell>
              <TableCell>
                <Badge variant={statusVariant(run.status)} dot>
                  {t(run.status)}
                </Badge>
              </TableCell>
              <TableCell>
                <Badge variant={statusVariant(run.phase)}>{t(run.phase)}</Badge>
              </TableCell>
              <TableCell>{formatPercent(run.coverage)}</TableCell>
              <TableCell>{run.requests_made}</TableCell>
              <TableCell>
                <span className="flex flex-wrap gap-2">
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => openRun(run.id)}
                  >
                    {t('progress')}
                  </Button>
                  {['pending', 'running'].includes(run.status) && (
                    <Button
                      variant="danger"
                      size="sm"
                      icon={<Ban size={12} />}
                      disabled={!apiHealth.writable || control.isPending}
                      onClick={() => control.mutate({ id: run.id, action: 'cancel' })}
                    >
                      {t('cancel')}
                    </Button>
                  )}
                  {['interrupted', 'cancelled', 'failed', 'partial'].includes(run.status) && (
                    <Button
                      variant="success"
                      size="sm"
                      icon={<PlayCircle size={12} />}
                      disabled={!canPlan || control.isPending}
                      onClick={() => control.mutate({ id: run.id, action: 'resume' })}
                    >
                      {t('resume')}
                    </Button>
                  )}
                </span>
              </TableCell>
            </TableRow>
          ))}
        </tbody>
      </DataTable>
      {!runs.data?.data.length && <EmptyState message={t('noRuns')} />}

      {/* Run detail modal */}
      <Modal
        open={selectedRun !== null}
        onClose={closeRun}
        title={`${t('run')} #${selectedRun}`}
        maxWidth="max-w-5xl"
      >
        {progress.isLoading ? (
          <LoadingState />
        ) : (
          progress.data && (
            <div className="space-y-5">
              <ProgressBar value={progressValue} label={t('progress')} />

              <div className="grid gap-3 sm:grid-cols-3 lg:grid-cols-6">
                <StatCard label={t('status')} value={<Badge variant={statusVariant(progress.data.status)} dot>{t(progress.data.status)}</Badge>} />
                <StatCard label={t('phase')} value={<Badge variant={statusVariant(progress.data.phase)}>{t(progress.data.phase)}</Badge>} />
                <StatCard label={t('tasks')} value={`${progress.data.tasks_done}/${progress.data.tasks_total}`} />
                <StatCard label={t('requests')} value={progress.data.requests_made} />
                <StatCard label={t('tier')} value={progress.data.tier ?? '—'} />
                <StatCard label={t('coverage')} value={formatPercent(progress.data.coverage)} />
              </div>

              {progress.data.current_brand && (
                <p className="text-sm text-[var(--text-secondary)]">
                  {t('currentBrand')}: <strong className="text-[var(--text-primary)]">{progress.data.current_brand}</strong>
                </p>
              )}
              {(progress.data.partial || progress.data.truncated) && (
                <Notice error>
                  {progress.data.truncated ? t('truncatedResult') : t('partialResult')}
                </Notice>
              )}
              {progress.data.warnings.length > 0 && (
                <ul className="space-y-1">
                  {progress.data.warnings.map((warning) => (
                    <li key={warning} className="flex items-center gap-2 text-xs text-[var(--warning)]">
                      <AlertTriangle size={14} /> {warning}
                    </li>
                  ))}
                </ul>
              )}
              {(progress.data.errors ?? []).length > 0 && (
                <ul className="space-y-1" role="alert">
                  {(progress.data.errors ?? []).map((item) => (
                    <li key={`${item.task_id}-${item.code}`} className="flex items-center gap-2 text-xs text-[var(--danger)]">
                      <XCircle size={14} />
                      {t('task')} #{item.task_id} · {item.index_type}: {item.code}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )
        )}

        {report.data && (
          <div className="mt-6 border-t border-[var(--border-subtle)] pt-6">
            <h3 className="mb-3 text-sm font-semibold uppercase tracking-wider text-[var(--text-muted)]">
              {t('report')}
            </h3>
            <div className="grid gap-3 sm:grid-cols-3 lg:grid-cols-6">
              {[
                [t('requests'), report.data.metrics.requests_total],
                [t('retries'), report.data.metrics.retries],
                [t('p95Latency'), `${report.data.metrics.p95_latency_ms.toFixed(1)} ms`],
                [t('cacheHitRate'), `${(report.data.metrics.cache_hit_rate * 100).toFixed(1)}%`],
                [t('invalidListings'), report.data.metrics.listings_invalid],
                [t('duration'), `${report.data.metrics.duration_s.toFixed(1)} s`],
              ].map(([label, value]) => (
                <StatCard key={String(label)} label={String(label)} value={value} />
              ))}
            </div>
            <div className="mt-4">
              <DataTable>
                <TableHead>
                  <tr>
                    <TableHeaderCell>{t('brand')}</TableHeaderCell>
                    <TableHeaderCell>{t('index')}</TableHeaderCell>
                    <TableHeaderCell>{t('status')}</TableHeaderCell>
                    <TableHeaderCell>{t('hits')}</TableHeaderCell>
                    <TableHeaderCell>{t('coverage')}</TableHeaderCell>
                    <TableHeaderCell>{t('tier')}</TableHeaderCell>
                    <TableHeaderCell>{t('sourceError')}</TableHeaderCell>
                  </tr>
                </TableHead>
                <tbody>
                  {report.data.tasks.map((task) => (
                    <TableRow key={task.id}>
                      <TableCell>{task.brand_id ?? '—'}</TableCell>
                      <TableCell>{task.index_type}</TableCell>
                      <TableCell>
                        <Badge variant={statusVariant(task.status)} dot>
                          {task.status}
                        </Badge>
                      </TableCell>
                      <TableCell>
                        {task.hits_collected}/{task.expected_hits ?? '—'}
                      </TableCell>
                      <TableCell>{formatPercent(task.coverage)}</TableCell>
                      <TableCell>{task.tier ?? '—'}</TableCell>
                      <TableCell>
                        {task.error ? (
                          <span className="text-[var(--danger)]">{task.error}</span>
                        ) : '—'}
                      </TableCell>
                    </TableRow>
                  ))}
                </tbody>
              </DataTable>
            </div>
          </div>
        )}
      </Modal>
    </section>
  );
}
