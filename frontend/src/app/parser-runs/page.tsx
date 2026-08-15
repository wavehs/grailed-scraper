'use client';

import { useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'next/navigation';
import Link from 'next/link';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { EmptyState, ErrorState, LoadingState, Notice } from '@/components/states';
import { api, getApi } from '@/lib/api';
import { useI18n } from '@/lib/i18n';
import { useApiHealth, useParserHealth } from '@/lib/queries';
import type {
  BrandList,
  DiscoveryResponse,
  FetchPlan,
  RunList,
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
const pct = (value?: string | null) => (value ? `${(Number(value) * 100).toFixed(1)}%` : '—');

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
  const closeButton = useRef<HTMLButtonElement>(null);
  const returnFocus = useRef<HTMLElement | null>(null);
  const openRun = (runId: number) => {
    returnFocus.current = document.activeElement as HTMLElement | null;
    setSelectedRun(runId);
  };
  const closeRun = () => {
    setSelectedRun(null);
    window.setTimeout(() => returnFocus.current?.focus(), 0);
  };
  useEffect(() => {
    const value = Number(searchParams.get('run'));
    if (value) setSelectedRun(value);
  }, [searchParams]);
  useEffect(() => {
    if (selectedRun === null) return;
    closeButton.current?.focus();
    const close = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setSelectedRun(null);
        window.setTimeout(() => returnFocus.current?.focus(), 0);
      }
    };
    window.addEventListener('keydown', close);
    return () => window.removeEventListener('keydown', close);
  }, [selectedRun]);
  const brands = useQuery({
    queryKey: ['brands'],
    queryFn: ({ signal }) => getApi<BrandList>('/brands', signal),
  });
  const runs = useQuery({
    queryKey: ['runs'],
    queryFn: ({ signal }) => getApi<RunList>('/parser/runs?limit=50', signal),
    refetchInterval: (query) =>
      query.state.data?.data.some((run) => !terminal.has(run.status)) ? 2_000 : false,
  });
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
    discovery.error ??
    start.error ??
    control.error ??
    progress.error ??
    report.error;
  if (brands.isLoading && runs.isLoading) return <LoadingState />;
  return (
    <section className="space-y-6" aria-labelledby="runs-heading">
      <div>
        <h1 id="runs-heading" className="text-2xl font-semibold">
          {t('runParser')}
        </h1>
        <p className="text-slate-600">{t('runIntro')}</p>
      </div>
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
      <Card className="p-4">
        <h2 className="font-semibold">{t('liveWorkflow')}</h2>
        <ol className="mt-3 grid gap-3 md:grid-cols-4">
          <li className="rounded border p-3">
            <p className="font-medium">1. {t('discovery')}</p>
            <p className="text-sm text-slate-600">
              {t(parserHealth.data?.discovery?.status ?? 'unavailable')}
            </p>
            <Button
              className="mt-2"
              disabled={
                !apiHealth.writable ||
                discovery.isPending ||
                blockers.includes('live_compliance_not_acknowledged')
              }
              onClick={() => discovery.mutate()}
            >
              {discovery.isPending ? t('refreshing') : t('refreshDiscovery')}
            </Button>
          </li>
          <li className="rounded border p-3">
            <p className="font-medium">2. {t('brandMapping')}</p>
            <p className="text-sm text-slate-600">
              {mappingsReady ? t('ready') : t('incomplete')}
            </p>
            <Link className="mt-3 inline-block underline" href="/brands">
              {t('reviewMappings')}
            </Link>
          </li>
          <li className="rounded border p-3">
            <p className="font-medium">3. {t('dryRun')}</p>
            <p className="text-sm text-slate-600">{plan ? t('completed') : t('pending')}</p>
          </li>
          <li className="rounded border p-3">
            <p className="font-medium">4. {t('confirmation')}</p>
            <p className="text-sm text-slate-600">{plan ? t('ready') : t('blocked')}</p>
          </li>
        </ol>
        {blockers.length > 0 && (
          <ul className="mt-3 list-disc pl-5 text-red-800" role="alert">
            {blockers.map((reason) => (
              <li key={reason}>{t(reason)}</li>
            ))}
          </ul>
        )}
        {!mappingsReady && <Notice error>{t('brand_mapping_required')}</Notice>}
      </Card>
      <Card className="p-4">
        <form
          className="grid gap-4 lg:grid-cols-[220px_1fr_auto]"
          onSubmit={(event) => {
            event.preventDefault();
            start.mutate({ dry_run: true });
          }}
        >
          <label className="text-sm font-medium">
            {t('mode')}
            <select
              className="mt-1 w-full rounded border px-3"
              value={mode}
              onChange={(event) => {
                setMode(event.target.value as typeof mode);
                setPlan(null);
              }}
            >
              <option value="delta">{t('delta')}</option>
              <option value="full">{t('full')}</option>
              <option value="refresh_active">{t('refreshActive')}</option>
            </select>
          </label>
          <fieldset>
            <legend className="text-sm font-medium">{t('selectedBrands')}</legend>
            <div className="mt-1 flex max-h-32 flex-wrap gap-2 overflow-auto">
              {brands.data?.data.map((brand) => (
                <label className="rounded border px-2 py-1 text-sm" key={brand.id}>
                  <input
                    className="mr-1"
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
              className="mt-2 text-sm underline"
              onClick={() => {
                setBrandIds([]);
                setPlan(null);
              }}
            >
              {t('allBrands')}
            </button>
          </fieldset>
          <Button className="self-end" type="submit" disabled={!canPlan || start.isPending}>
            {start.isPending ? t('planning') : t('dryRun')}
          </Button>
        </form>
      </Card>
      {plan && (
        <Card className="p-4">
          <h2 className="font-semibold">{t('budget')}</h2>
          <dl className="mt-3 grid gap-3 sm:grid-cols-3">
            <div>
              <dt className="text-sm text-slate-600">{t('estimatedRequests')}</dt>
              <dd className="text-xl font-bold">{String(plan.budget.estimated_requests)}</dd>
            </div>
            <div>
              <dt className="text-sm text-slate-600">{t('estimatedHits')}</dt>
              <dd className="text-xl font-bold">{String(plan.budget.estimated_hits)}</dd>
            </div>
            <div>
              <dt className="text-sm text-slate-600">{t('limit')}</dt>
              <dd className="text-xl font-bold">{String(plan.budget.limit)}</dd>
            </div>
          </dl>
          {overLimit && <Notice error>{t('overBudget')}</Notice>}
          {plan.warnings.length > 0 && (
            <ul className="mt-3 list-disc pl-5 text-amber-800">
              {plan.warnings.map((warning) => (
                <li key={warning}>{warning}</li>
              ))}
            </ul>
          )}
          <Button
            className="mt-4"
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
      <Card className="overflow-x-auto">
        <table className="w-full text-left text-sm">
          <thead className="bg-slate-100">
            <tr>
              <th className="p-3">ID</th>
              <th className="p-3">{t('mode')}</th>
              <th className="p-3">{t('status')}</th>
              <th className="p-3">{t('phase')}</th>
              <th className="p-3">{t('coverage')}</th>
              <th className="p-3">{t('requests')}</th>
              <th className="p-3">{t('actions')}</th>
            </tr>
          </thead>
          <tbody>
            {runs.data?.data.map((run) => (
              <tr className="border-t" key={run.id}>
                <td className="p-3">#{run.id}</td>
                <td className="p-3">{t(run.mode)}</td>
                <td className="p-3">{t(run.status)}</td>
                <td className="p-3">{t(run.phase)}</td>
                <td className="p-3">{pct(run.coverage)}</td>
                <td className="p-3">{run.requests_made}</td>
                <td className="p-3">
                  <span className="flex flex-wrap gap-2">
                    <button className="underline" onClick={() => openRun(run.id)}>
                      {t('progress')}
                    </button>
                    {['pending', 'running'].includes(run.status) && (
                      <button
                        className="text-red-700 underline"
                        disabled={!apiHealth.writable || control.isPending}
                        onClick={() => control.mutate({ id: run.id, action: 'cancel' })}
                      >
                        {t('cancel')}
                      </button>
                    )}
                    {['interrupted', 'cancelled', 'failed', 'partial'].includes(run.status) && (
                      <button
                        className="underline"
                        disabled={!canPlan || control.isPending}
                        onClick={() => control.mutate({ id: run.id, action: 'resume' })}
                      >
                        {t('resume')}
                      </button>
                    )}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {!runs.data?.data.length && <EmptyState message={t('noRuns')} />}
      </Card>
      {selectedRun !== null && (
        <div
          role="dialog"
          aria-modal="true"
          aria-labelledby="run-detail-title"
          className="fixed inset-0 z-40 overflow-y-auto bg-black/40 p-4"
          onKeyDown={(event) => {
            if (event.key === 'Tab') {
              event.preventDefault();
              closeButton.current?.focus();
            }
          }}
        >
          <Card className="mx-auto mt-8 max-w-5xl p-5">
            <div className="flex justify-between gap-3">
              <h2 id="run-detail-title" className="text-xl font-semibold">
                {t('run')} #{selectedRun}
              </h2>
              <Button ref={closeButton} onClick={closeRun}>
                {t('close')}
              </Button>
            </div>
            {progress.isLoading ? (
              <LoadingState />
            ) : (
              progress.data && (
                <div className="mt-5 space-y-4">
                  <div
                    className="h-3 overflow-hidden rounded bg-slate-200"
                    role="progressbar"
                    aria-valuenow={progressValue}
                    aria-valuemin={0}
                    aria-valuemax={100}
                  >
                    <div className="h-full bg-emerald-600" style={{ width: `${progressValue}%` }} />
                  </div>
                  <dl className="grid gap-3 sm:grid-cols-3 lg:grid-cols-6">
                    <div>
                      <dt>{t('status')}</dt>
                      <dd className="font-semibold">{t(progress.data.status)}</dd>
                    </div>
                    <div>
                      <dt>{t('phase')}</dt>
                      <dd className="font-semibold">{t(progress.data.phase)}</dd>
                    </div>
                    <div>
                      <dt>{t('tasks')}</dt>
                      <dd className="font-semibold">
                        {progress.data.tasks_done}/{progress.data.tasks_total}
                      </dd>
                    </div>
                    <div>
                      <dt>{t('requests')}</dt>
                      <dd className="font-semibold">{progress.data.requests_made}</dd>
                    </div>
                    <div>
                      <dt>{t('tier')}</dt>
                      <dd className="font-semibold">{progress.data.tier ?? '—'}</dd>
                    </div>
                    <div>
                      <dt>{t('coverage')}</dt>
                      <dd className="font-semibold">{pct(progress.data.coverage)}</dd>
                    </div>
                  </dl>
                  {progress.data.current_brand && (
                    <p>
                      {t('currentBrand')}: <strong>{progress.data.current_brand}</strong>
                    </p>
                  )}
                  {(progress.data.partial || progress.data.truncated) && (
                    <Notice error>
                      {progress.data.truncated ? t('truncatedResult') : t('partialResult')}
                    </Notice>
                  )}
                  {progress.data.warnings.length > 0 && (
                    <ul className="list-disc pl-5 text-amber-800">
                      {progress.data.warnings.map((warning) => (
                        <li key={warning}>{warning}</li>
                      ))}
                    </ul>
                  )}
                  {(progress.data.errors ?? []).length > 0 && (
                    <ul className="list-disc pl-5 text-red-800" role="alert">
                      {(progress.data.errors ?? []).map((item) => (
                        <li key={`${item.task_id}-${item.code}`}>
                          {t('task')} #{item.task_id} · {item.index_type}: {item.code}
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              )
            )}
            {report.data && (
              <div className="mt-6">
                <h3 className="font-semibold">{t('report')}</h3>
                <dl className="mt-3 grid gap-3 sm:grid-cols-3 lg:grid-cols-6">
                  {[
                    [t('requests'), report.data.metrics.requests_total],
                    [t('retries'), report.data.metrics.retries],
                    [t('p95Latency'), `${report.data.metrics.p95_latency_ms.toFixed(1)} ms`],
                    [t('cacheHitRate'), `${(report.data.metrics.cache_hit_rate * 100).toFixed(1)}%`],
                    [t('invalidListings'), report.data.metrics.listings_invalid],
                    [t('duration'), `${report.data.metrics.duration_s.toFixed(1)} s`],
                  ].map(([label, value]) => (
                    <div className="rounded border p-2" key={String(label)}>
                      <dt className="text-xs text-slate-600">{label}</dt>
                      <dd className="font-semibold">{value}</dd>
                    </div>
                  ))}
                </dl>
                <div className="mt-3 overflow-x-auto">
                  <table className="w-full text-left text-sm">
                    <thead>
                      <tr>
                        <th className="p-2">{t('brand')}</th>
                        <th className="p-2">{t('index')}</th>
                        <th className="p-2">{t('status')}</th>
                        <th className="p-2">{t('hits')}</th>
                        <th className="p-2">{t('coverage')}</th>
                        <th className="p-2">{t('tier')}</th>
                        <th className="p-2">{t('sourceError')}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {report.data.tasks.map((task) => (
                        <tr className="border-t" key={task.id}>
                          <td className="p-2">{task.brand_id ?? '—'}</td>
                          <td className="p-2">{task.index_type}</td>
                          <td className="p-2">{task.status}</td>
                          <td className="p-2">
                            {task.hits_collected}/{task.expected_hits ?? '—'}
                          </td>
                          <td className="p-2">{pct(task.coverage)}</td>
                          <td className="p-2">{task.tier ?? '—'}</td>
                          <td className="p-2 text-red-800">{task.error ?? '—'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
          </Card>
        </div>
      )}
    </section>
  );
}
