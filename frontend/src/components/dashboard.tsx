'use client';

import Link from 'next/link';
import {
  Activity,
  DollarSign,
  Gauge,
  Search,
  Shield,
  TrendingUp,
  Zap,
} from 'lucide-react';
import {
  createColumnHelper,
  flexRender,
  getCoreRowModel,
  getSortedRowModel,
  type SortingState,
  useReactTable,
} from '@tanstack/react-table';
import { useMemo, useState } from 'react';
import { Badge, statusVariant } from '@/components/ui/badge';
import { DataTable, TableCell, TableHead, TableHeaderCell, TableRow } from '@/components/ui/data-table';
import { PageHeader } from '@/components/ui/page-header';
import { StatCard } from '@/components/ui/stat-card';
import { Card } from '@/components/ui/card';
import { EmptyState, ErrorState, LoadingState } from '@/components/states';
import { useI18n } from '@/lib/i18n';
import { useDashboardQuery, useParserHealth, useRunsQuery } from '@/lib/queries';
import { formatCurrency, formatPercent } from '@/lib/utils';
import type { DashboardRow } from '@/lib/types';

const column = createColumnHelper<DashboardRow>();

function opportunityColor(score: number): string {
  if (score >= 7) return 'text-[#34d399]';
  if (score >= 4) return 'text-[#fbbf24]';
  return 'text-[#fb7185]';
}

export function Dashboard() {
  const { locale, t } = useI18n();
  const [windowDays, setWindowDays] = useState(90);
  const [search, setSearch] = useState('');
  const [sorting, setSorting] = useState<SortingState>([
    { id: 'market_opportunity_score', desc: true },
  ]);
  const health = useParserHealth();
  const runs = useRunsQuery(5);
  const analytics = useDashboardQuery(windowDays);
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
          <Link
            className="font-medium text-[#818cf8] hover:text-[#a5b4fc] transition-colors"
            href={`/model-groups/${info.row.original.id}`}
          >
            {info.getValue()}
          </Link>
        ),
      }),
      column.accessor('brand_name', { header: t('brand') }),
      column.accessor('sold_count', { header: t('sold') }),
      column.accessor('active_count', { header: t('active') }),
      column.accessor('median_sold_price', {
        header: t('medianPrice'),
        cell: (info) => (
          <span className="text-[var(--text-primary)]">
            {formatCurrency(info.getValue(), locale)}
          </span>
        ),
      }),
      column.accessor('market_opportunity_score', {
        header: t('opportunity'),
        cell: (info) => {
          const val = Number(info.getValue());
          return (
            <span className={`font-semibold ${opportunityColor(val)}`}>
              {val.toFixed(1)}
            </span>
          );
        },
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
      <PageHeader title={t('marketDashboard')} description={t('marketIntro')} />

      {/* System status cards */}
      {health.data && (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <StatCard
            label={t('sourceStatus')}
            value={
              <span className="flex items-center gap-2">
                <Badge variant={statusVariant(health.data.status)} dot>
                  {t(health.data.status)}
                </Badge>
              </span>
            }
            icon={<Activity size={18} />}
          />
          <StatCard
            label={t('discovery')}
            value={
              <Badge variant={statusVariant(health.data.discovery.status)} dot>
                {t(health.data.discovery.status)}
              </Badge>
            }
            icon={<Zap size={18} />}
          />
          <StatCard
            label={t('schemaAlerts')}
            value={health.data.schema.active_alerts}
            icon={<Shield size={18} />}
          />
          <StatCard
            label={t('transports')}
            value={
              Object.entries(health.data.transports)
                .filter(([, enabled]) => enabled)
                .map(([tier]) => tier)
                .join(', ') || '—'
            }
            icon={<Gauge size={18} />}
          />
        </div>
      )}

      {/* Schema alerts */}
      {health.data?.schema.alerts.length ? (
        <Card className="border-[rgba(245,158,11,0.2)] p-5">
          <h2 className="text-sm font-semibold text-[#fbbf24]">{t('schemaAlerts')}</h2>
          <ul className="mt-2 space-y-1 text-sm">
            {health.data.schema.alerts.map((alert) => (
              <li key={alert.id} className="flex items-center gap-2">
                <Badge variant={statusVariant(alert.severity)}>
                  {alert.severity}
                </Badge>
                <span className="text-[var(--text-secondary)]">{alert.message}</span>
              </li>
            ))}
          </ul>
        </Card>
      ) : null}

      {/* Key metrics */}
      <div>
        <h2 className="mb-3 text-sm font-semibold uppercase tracking-wider text-[var(--text-muted)]">
          {t('keyMetrics')}
        </h2>
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <StatCard
            label={t('opportunity')}
            value={metrics.opportunity.toFixed(1)}
            icon={<TrendingUp size={18} />}
          />
          <StatCard
            label={t('liquidity')}
            value={metrics.liquidity.toFixed(1)}
            icon={<Activity size={18} />}
          />
          <StatCard
            label={t('confidence')}
            value={metrics.confidence.toFixed(1)}
            icon={<Shield size={18} />}
          />
          <StatCard
            label={t('medianPrice')}
            value={metrics.price ? `$${(metrics.price / 100).toFixed(0)}` : '—'}
            icon={<DollarSign size={18} />}
          />
        </div>
      </div>

      {/* Recent Runs */}
      <Card className="p-5">
        <h2 className="text-sm font-semibold uppercase tracking-wider text-[var(--text-muted)] mb-3">
          {t('recentRuns')}
        </h2>
        {runs.data?.data.length ? (
          <DataTable>
            <TableHead>
              <tr>
                <TableHeaderCell>ID</TableHeaderCell>
                <TableHeaderCell>{t('mode')}</TableHeaderCell>
                <TableHeaderCell>{t('status')}</TableHeaderCell>
                <TableHeaderCell>{t('coverage')}</TableHeaderCell>
                <TableHeaderCell>{t('warnings')}</TableHeaderCell>
              </tr>
            </TableHead>
            <tbody>
              {runs.data.data.map((run) => (
                <TableRow key={run.id}>
                  <TableCell>
                    <Link
                      className="text-[#818cf8] hover:text-[#a5b4fc] transition-colors"
                      href={`/parser-runs?run=${run.id}`}
                    >
                      #{run.id}
                    </Link>
                  </TableCell>
                  <TableCell>{t(run.mode)}</TableCell>
                  <TableCell>
                    <Badge variant={statusVariant(run.status)} dot>
                      {t(run.status)}
                    </Badge>
                  </TableCell>
                  <TableCell>{formatPercent(run.coverage)}</TableCell>
                  <TableCell>
                    {run.warnings.length > 0 ? (
                      <Badge variant="warning">{run.warnings.length}</Badge>
                    ) : (
                      <span className="text-[var(--text-muted)]">0</span>
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </tbody>
          </DataTable>
        ) : (
          <p className="text-sm text-[var(--text-muted)]">{t('noRuns')}</p>
        )}
      </Card>

      {/* Filters */}
      <Card className="p-4">
        <div className="flex flex-wrap items-center gap-3">
          <label className="flex items-center gap-2 text-sm text-[var(--text-secondary)]">
            {t('dataWindow')}
            <select
              className="rounded-lg"
              value={windowDays}
              onChange={(e) => setWindowDays(Number(e.target.value))}
            >
              <option value={30}>30</option>
              <option value={90}>90</option>
            </select>
          </label>
          <label className="relative flex-1">
            <Search
              size={16}
              className="absolute left-3 top-1/2 -translate-y-1/2 text-[var(--text-muted)]"
            />
            <input
              className="w-full rounded-lg pl-9"
              placeholder={t('search')}
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </label>
        </div>
      </Card>

      {/* Model table */}
      <DataTable>
        <TableHead>
          {table.getHeaderGroups().map((group) => (
            <tr key={group.id}>
              {group.headers.map((header) => (
                <TableHeaderCell
                  key={header.id}
                  onClick={header.column.getToggleSortingHandler()}
                  sortDir={header.column.getIsSorted()}
                >
                  {flexRender(header.column.columnDef.header, header.getContext())}
                </TableHeaderCell>
              ))}
            </tr>
          ))}
        </TableHead>
        <tbody>
          {table.getRowModel().rows.map((row) => (
            <TableRow key={row.id}>
              {row.getVisibleCells().map((cell) => (
                <TableCell key={cell.id}>
                  {flexRender(cell.column.columnDef.cell, cell.getContext())}
                </TableCell>
              ))}
            </TableRow>
          ))}
        </tbody>
      </DataTable>
      {!analytics.isLoading && !rows.length && <EmptyState />}
    </section>
  );
}
