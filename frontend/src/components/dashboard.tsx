'use client';

import Link from 'next/link';
import { useQuery } from '@tanstack/react-query';
import {
  createColumnHelper,
  flexRender,
  getCoreRowModel,
  getSortedRowModel,
  type SortingState,
  useReactTable,
} from '@tanstack/react-table';
import { useMemo, useState } from 'react';
import { Card } from '@/components/ui/card';
import { EmptyState, ErrorState, LoadingState } from '@/components/states';
import { getApi } from '@/lib/api';
import { useI18n } from '@/lib/i18n';
import { useParserHealth } from '@/lib/queries';
import type { DashboardRow, RunList } from '@/lib/types';

const column = createColumnHelper<DashboardRow>();
const percent = (value?: string | number) =>
  value === undefined ? '—' : `${(Number(value) * (Number(value) <= 1 ? 100 : 1)).toFixed(1)}%`;

export function Dashboard() {
  const { locale, t } = useI18n();
  const [windowDays, setWindowDays] = useState(90);
  const [search, setSearch] = useState('');
  const [sorting, setSorting] = useState<SortingState>([
    { id: 'market_opportunity_score', desc: true },
  ]);
  const health = useParserHealth();
  const runs = useQuery({
    queryKey: ['runs', 'recent'],
    queryFn: ({ signal }) => getApi<RunList>('/parser/runs?limit=5', signal),
    refetchInterval: 5_000,
  });
  const analytics = useQuery({
    queryKey: ['dashboard', windowDays],
    queryFn: ({ signal }) =>
      getApi<{ data: DashboardRow[] }>(`/analytics/dashboard?window_days=${windowDays}`, signal),
  });
  const rows = useMemo(
    () =>
      (analytics.data?.data ?? []).filter((row) =>
        `${row.name} ${row.brand_name} ${row.category ?? ''}`
          .toLowerCase()
          .includes(search.toLowerCase()),
      ),
    [analytics.data, search],
  );
  const metrics = useMemo(() => {
    if (!rows.length) return { opportunity: 0, liquidity: 0, confidence: 0, price: 0 };
    const average = (key: keyof DashboardRow) =>
      rows.reduce((sum, row) => sum + Number(row[key] ?? 0), 0) / rows.length;
    const prices = rows
      .map((row) => row.median_sold_price)
      .filter((value): value is number => value !== undefined)
      .sort((a, b) => a - b);
    return {
      opportunity: average('market_opportunity_score'),
      liquidity: average('liquidity_score'),
      confidence: average('confidence_score'),
      price: prices.length ? prices[Math.floor(prices.length / 2)] : 0,
    };
  }, [rows]);
  const columns = useMemo(
    () => [
      column.accessor('name', {
        header: t('model'),
        cell: (info) => (
          <Link className="font-medium underline" href={`/model-groups/${info.row.original.id}`}>
            {info.getValue()}
          </Link>
        ),
      }),
      column.accessor('brand_name', { header: t('brand') }),
      column.accessor('sold_count', { header: t('sold') }),
      column.accessor('active_count', { header: t('active') }),
      column.accessor('median_sold_price', {
        header: t('medianPrice'),
        cell: (info) =>
          info.getValue() === undefined
            ? '—'
            : new Intl.NumberFormat(locale, {
                style: 'currency',
                currency: 'USD',
                maximumFractionDigits: 0,
              }).format(info.getValue()! / 100),
      }),
      column.accessor('market_opportunity_score', {
        header: t('opportunity'),
        cell: (info) => (
          <span className="rounded bg-emerald-100 px-2 py-1 font-semibold text-emerald-900">
            {Number(info.getValue()).toFixed(1)}
          </span>
        ),
      }),
    ],
    [locale, t],
  );
  const table = useReactTable({
    data: rows,
    columns,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
  });
  const error = health.error ?? runs.error ?? analytics.error;
  if (health.isLoading && runs.isLoading && analytics.isLoading) return <LoadingState />;
  if (error && !health.data && !runs.data && !analytics.data)
    return (
      <ErrorState
        error={error}
        retry={() => {
          health.refetch();
          runs.refetch();
          analytics.refetch();
        }}
      />
    );
  return (
    <section className="space-y-6" aria-labelledby="dashboard-heading">
      <div>
        <h1 id="dashboard-heading" className="text-2xl font-semibold">
          {t('marketDashboard')}
        </h1>
        <p className="text-slate-600">{t('marketIntro')}</p>
      </div>
      {health.data && (
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          <Card className="p-4">
            <p className="text-sm text-slate-600">{t('sourceStatus')}</p>
            <p className="mt-1 text-xl font-semibold">
              {t(health.data.status)} · {t(health.data.source_mode)}
            </p>
          </Card>
          <Card className="p-4">
            <p className="text-sm text-slate-600">{t('discovery')}</p>
            <p className="mt-1 text-xl font-semibold">{t(health.data.discovery.status)}</p>
          </Card>
          <Card className="p-4">
            <p className="text-sm text-slate-600">{t('schemaAlerts')}</p>
            <p className="mt-1 text-xl font-semibold">{health.data.schema.active_alerts}</p>
          </Card>
          <Card className="p-4">
            <p className="text-sm text-slate-600">{t('transports')}</p>
            <p className="mt-1 text-xl font-semibold">
              {Object.entries(health.data.transports)
                .filter(([, enabled]) => enabled)
                .map(([tier]) => tier)
                .join(', ') || '—'}
            </p>
          </Card>
        </div>
      )}
      {health.data?.schema.alerts.length ? (
        <Card className="border-amber-300 bg-amber-50 p-4">
          <h2 className="font-semibold">{t('schemaAlerts')}</h2>
          <ul className="mt-2 list-disc pl-5 text-sm">
            {health.data.schema.alerts.map((alert) => (
              <li key={alert.id}>
                <span className="font-medium">{alert.severity}</span>: {alert.message}
              </li>
            ))}
          </ul>
        </Card>
      ) : null}
      <div>
        <h2 className="mb-3 text-lg font-semibold">{t('keyMetrics')}</h2>
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          {[
            [t('opportunity'), metrics.opportunity.toFixed(1)],
            [t('liquidity'), metrics.liquidity.toFixed(1)],
            [t('confidence'), metrics.confidence.toFixed(1)],
            [t('medianPrice'), metrics.price ? `$${(metrics.price / 100).toFixed(0)}` : '—'],
          ].map(([label, value]) => (
            <Card className="p-4" key={label}>
              <p className="text-sm text-slate-600">{label}</p>
              <p className="text-2xl font-bold">{value}</p>
            </Card>
          ))}
        </div>
      </div>
      <Card className="p-4">
        <h2 className="font-semibold">{t('recentRuns')}</h2>
        {runs.data?.data.length ? (
          <div className="mt-3 overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead>
                <tr>
                  <th className="p-2">ID</th>
                  <th className="p-2">{t('mode')}</th>
                  <th className="p-2">{t('status')}</th>
                  <th className="p-2">{t('coverage')}</th>
                  <th className="p-2">{t('warnings')}</th>
                </tr>
              </thead>
              <tbody>
                {runs.data.data.map((run) => (
                  <tr className="border-t" key={run.id}>
                    <td className="p-2">
                      <Link className="underline" href={`/parser-runs?run=${run.id}`}>
                        #{run.id}
                      </Link>
                    </td>
                    <td className="p-2">{t(run.mode)}</td>
                    <td className="p-2">{t(run.status)}</td>
                    <td className="p-2">{percent(run.coverage)}</td>
                    <td className="p-2">{run.warnings.length}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="mt-2 text-sm text-slate-500">{t('noRuns')}</p>
        )}
      </Card>
      <Card className="p-4">
        <div className="flex flex-wrap gap-3">
          <label>
            {t('dataWindow')}{' '}
            <select
              className="ml-2 rounded border px-2"
              value={windowDays}
              onChange={(e) => setWindowDays(Number(e.target.value))}
            >
              <option value={30}>30</option>
              <option value={90}>90</option>
            </select>
          </label>
          <label className="flex-1">
            <span className="sr-only">{t('search')}</span>
            <input
              className="w-full rounded border px-3"
              placeholder={t('search')}
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </label>
        </div>
      </Card>
      <Card className="overflow-x-auto">
        <table className="w-full text-left text-sm">
          <thead className="bg-slate-100">
            {table.getHeaderGroups().map((group) => (
              <tr key={group.id}>
                {group.headers.map((header) => (
                  <th className="p-3" key={header.id}>
                    <button onClick={header.column.getToggleSortingHandler()}>
                      {flexRender(header.column.columnDef.header, header.getContext())}
                      {header.column.getIsSorted() === 'asc'
                        ? ' ↑'
                        : header.column.getIsSorted() === 'desc'
                          ? ' ↓'
                          : ''}
                    </button>
                  </th>
                ))}
              </tr>
            ))}
          </thead>
          <tbody>
            {table.getRowModel().rows.map((row) => (
              <tr className="border-t" key={row.id}>
                {row.getVisibleCells().map((cell) => (
                  <td className="p-3" key={cell.id}>
                    {flexRender(cell.column.columnDef.cell, cell.getContext())}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
        {!analytics.isLoading && !rows.length && <EmptyState />}
      </Card>
    </section>
  );
}
