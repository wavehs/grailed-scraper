'use client';

import Link from 'next/link';
import { Activity, Gauge, Search, Shield, X, Zap } from 'lucide-react';
import {
  createColumnHelper,
  flexRender,
  getCoreRowModel,
  getSortedRowModel,
  type SortingState,
  useReactTable,
} from '@tanstack/react-table';
import { useEffect, useMemo, useState } from 'react';
import { Badge, statusVariant } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  DataTable,
  TableCell,
  TableHead,
  TableHeaderCell,
  TableRow,
} from '@/components/ui/data-table';
import { PageHeader } from '@/components/ui/page-header';
import { StatCard } from '@/components/ui/stat-card';
import { Card } from '@/components/ui/card';
import { EmptyState, ErrorState, LoadingState } from '@/components/states';
import { useI18n } from '@/lib/i18n';
import type { BrandAnalyticsRow, DashboardProductType, DashboardRow } from '@/lib/types';
import {
  useBrandDashboardQuery,
  useBrandsQuery,
  useDashboardQuery,
  useParserHealth,
  useRunsQuery,
} from '@/lib/queries';
import { formatCurrency, formatPercent } from '@/lib/utils';

const column = createColumnHelper<DashboardRow>();
const brandColumn = createColumnHelper<BrandAnalyticsRow>();
const EMPTY_ROWS: DashboardRow[] = [];
const EMPTY_BRAND_ROWS: BrandAnalyticsRow[] = [];

function scoreColor(score: number): string {
  if (score >= 70) return 'text-[var(--success)]';
  if (score >= 40) return 'text-[var(--warning)]';
  return 'text-[var(--danger)]';
}

export function Dashboard({
  initialWindowDays = 90,
  initialSearch = '',
  initialLowData = false,
  initialBrandId,
  initialProductType,
  initialViewMode = 'models',
}: {
  initialWindowDays?: number;
  initialSearch?: string;
  initialLowData?: boolean;
  initialBrandId?: number;
  initialProductType?: DashboardProductType;
  initialViewMode?: 'models' | 'brands';
}) {
  const { locale, t } = useI18n();
  const [viewMode, setViewMode] = useState<'models' | 'brands'>(initialViewMode);
  const [windowDays, setWindowDays] = useState(initialWindowDays);
  const [search, setSearch] = useState(initialSearch);
  const [debouncedSearch, setDebouncedSearch] = useState(initialSearch.trim());
  const [showLowData, setShowLowData] = useState(initialLowData);
  const [cursors, setCursors] = useState<Array<string | null>>([null]);
  const [page, setPage] = useState(0);
  const cursor = cursors[page] ?? null;
  const resetPage = () => {
    setCursors([null]);
    setPage(0);
  };
  useEffect(() => {
    const timer = window.setTimeout(() => {
      setDebouncedSearch(search.trim());
      setCursors([null]);
      setPage(0);
    }, 300);
    return () => window.clearTimeout(timer);
  }, [search]);
  const [brandId, setBrandId] = useState(initialBrandId);
  const [productType, setProductType] = useState(initialProductType);
  const dashboardReturn = `/dashboard?${new URLSearchParams({
    window_days: String(windowDays),
    search,
    low_data: String(showLowData),
    view_mode: viewMode,
    ...(brandId ? { brand_id: String(brandId) } : {}),
    ...(productType ? { product_type: productType } : {}),
  })}`;
  const [sorting, setSorting] = useState<SortingState>([{ id: 'demand_score', desc: true }]);
  const health = useParserHealth();
  const runs = useRunsQuery(5);
  const brands = useBrandsQuery();
  const activeSort = sorting[0] ?? { id: 'demand_score', desc: true };
  const analytics = useDashboardQuery(
    windowDays,
    debouncedSearch,
    !showLowData,
    cursor,
    activeSort.id,
    activeSort.desc,
    brandId,
    productType,
    viewMode === 'models',
  );
  const brandAnalytics = useBrandDashboardQuery(
    windowDays,
    debouncedSearch,
    !showLowData,
    activeSort.id,
    activeSort.desc,
    productType,
    viewMode === 'brands',
  );
  const rows = analytics.data?.data ?? EMPTY_ROWS;
  const brandRows = brandAnalytics.data?.data ?? EMPTY_BRAND_ROWS;

  const columns = useMemo(
    () => [
      column.accessor('name', {
        header: t('model'),
        cell: (info) => (
          <div className="min-w-48">
            <Link
              className="font-medium text-[var(--accent)] transition-colors hover:text-[var(--accent-hover)]"
              href={`/model-groups?id=${info.row.original.id}&window_days=${windowDays}&run_id=${info.row.original.run_id}&back=${encodeURIComponent(dashboardReturn)}`}
            >
              {info.getValue()}
            </Link>
            <p className="mt-0.5 text-xs text-[var(--text-muted)]">
              {info.row.original.brand_name}
              {info.row.original.category ? ` · ${info.row.original.category}` : ''}
            </p>
          </div>
        ),
      }),
      column.accessor('sold_count', { header: t('sold') }),
      column.accessor('active_count', { header: t('activeNow') }),
      column.display({
        id: 'evidence',
        header: t('saleEvidence'),
        cell: (info) => (
          <div className="min-w-32 space-y-0.5 text-xs tabular-nums text-[var(--text-secondary)]">
            <p>
              {t('daysToSell')}:{' '}
              {info.row.original.median_days_to_sell === null
                ? '—'
                : Number(info.row.original.median_days_to_sell).toFixed(0)}
            </p>
            <p>
              {t('likes')}:{' '}
              {info.row.original.median_sold_likes === null
                ? '—'
                : Number(info.row.original.median_sold_likes).toFixed(0)}
            </p>
          </div>
        ),
      }),
      column.accessor('median_sold_price', {
        header: t('medianPrice'),
        cell: (info) => (
          <span className="text-[var(--text-primary)]">
            {formatCurrency(info.getValue(), locale)}
          </span>
        ),
      }),
      column.accessor((row) => (row.demand_score === null ? null : Number(row.demand_score)), {
        id: 'demand_score',
        header: t('demand'),
        cell: (info) => {
          const val = info.getValue();
          if (val === null)
            return (
              <span className="text-[var(--text-muted)]">
                {t(info.row.original.scoring_status)}
              </span>
            );
          return <span className={`font-semibold ${scoreColor(val)}`}>{val.toFixed(1)}</span>;
        },
      }),
      column.accessor(
        (row) => (row.liquidity_score === null ? null : Number(row.liquidity_score)),
        {
          id: 'liquidity_score',
          header: t('liquidity'),
          cell: (info) => {
            const value = info.getValue();
            return value === null ? (
              <span className="text-[var(--text-muted)]">—</span>
            ) : (
              <span className={`font-semibold ${scoreColor(value)}`}>{value.toFixed(1)}</span>
            );
          },
        },
      ),
    ],
    [dashboardReturn, locale, t, windowDays],
  );

  const brandColumns = useMemo(
    () => [
      brandColumn.accessor('name', {
        header: t('brand'),
        cell: (info) => (
          <div className="min-w-40">
            <button
              type="button"
              className="text-left font-medium text-[var(--accent)] transition-colors hover:text-[var(--accent-hover)] hover:underline focus-visible:outline-none"
              onClick={() => {
                setBrandId(info.row.original.id);
                setViewMode('models');
                resetPage();
              }}
            >
              {info.getValue()}
            </button>
          </div>
        ),
      }),
      brandColumn.accessor('sold_count', { header: t('sold') }),
      brandColumn.accessor('active_count', { header: t('activeNow') }),
      brandColumn.display({
        id: 'evidence',
        header: t('saleEvidence'),
        cell: (info) => (
          <div className="min-w-32 space-y-0.5 text-xs tabular-nums text-[var(--text-secondary)]">
            <p>
              {t('daysToSell')}:{' '}
              {info.row.original.median_days_to_sell === null
                ? '—'
                : Number(info.row.original.median_days_to_sell).toFixed(0)}
            </p>
            <p>
              {t('likes')}:{' '}
              {info.row.original.median_sold_likes === null
                ? '—'
                : Number(info.row.original.median_sold_likes).toFixed(0)}
            </p>
          </div>
        ),
      }),
      brandColumn.accessor('median_sold_price', {
        header: t('medianPrice'),
        cell: (info) => (
          <span className="text-[var(--text-primary)]">
            {formatCurrency(info.getValue(), locale)}
          </span>
        ),
      }),
      brandColumn.accessor((row) => (row.demand_score === null ? null : Number(row.demand_score)), {
        id: 'demand_score',
        header: t('demand'),
        cell: (info) => {
          const val = info.getValue();
          if (val === null)
            return (
              <span className="text-[var(--text-muted)]">
                {t(info.row.original.scoring_status)}
              </span>
            );
          return <span className={`font-semibold ${scoreColor(val)}`}>{val.toFixed(1)}</span>;
        },
      }),
      brandColumn.accessor(
        (row) => (row.liquidity_score === null ? null : Number(row.liquidity_score)),
        {
          id: 'liquidity_score',
          header: t('liquidity'),
          cell: (info) => {
            const value = info.getValue();
            return value === null ? (
              <span className="text-[var(--text-muted)]">—</span>
            ) : (
              <span className={`font-semibold ${scoreColor(value)}`}>{value.toFixed(1)}</span>
            );
          },
        },
      ),
      brandColumn.accessor('groups_count', {
        header: t('brandModels'),
        cell: (info) => (
          <span className="text-xs tabular-nums text-[var(--text-secondary)]">
            {info.getValue()}
          </span>
        ),
      }),
    ],
    [locale, t],
  );

  const modelTable = useReactTable({
    data: rows,
    columns,
    state: { sorting },
    onSortingChange: (updater) => {
      setSorting(updater);
      resetPage();
    },
    manualSorting: true,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
  });

  const brandTable = useReactTable({
    data: brandRows,
    columns: brandColumns,
    state: { sorting },
    onSortingChange: (updater) => {
      setSorting(updater);
      resetPage();
    },
    manualSorting: true,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
  });

  const currentQuery = viewMode === 'models' ? analytics : brandAnalytics;
  const currentRows = viewMode === 'models' ? rows : brandRows;
  const error = health.error ?? runs.error ?? currentQuery.error;
  if (health.isLoading && runs.isLoading && currentQuery.isLoading) return <LoadingState />;
  if (error && !health.data && !runs.data && !currentQuery.data)
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
    <section className="flex flex-col gap-5" aria-labelledby="dashboard-heading">
      <PageHeader title={t('marketDashboard')} description={t('marketIntro')} />

      {/* System status cards */}
      {health.data && (
        <div className="order-6 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
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
        <Card className="order-1 border-[var(--warning-border)] p-4">
          <h2 className="text-sm font-semibold text-[var(--warning)]">{t('schemaAlerts')}</h2>
          <ul className="mt-2 space-y-1 text-sm">
            {health.data.schema.alerts.map((alert) => (
              <li key={alert.id} className="flex items-center gap-2">
                <Badge variant={statusVariant(alert.severity)}>{alert.severity}</Badge>
                <span className="text-[var(--text-secondary)]">{alert.message}</span>
              </li>
            ))}
          </ul>
        </Card>
      ) : null}

      {/* Recent Runs */}
      <Card className="order-7 p-5">
        <details>
          <summary className="flex min-h-10 cursor-pointer items-center justify-between text-sm font-semibold uppercase tracking-wider text-[var(--text-muted)]">
            {t('recentRuns')}
            <Badge variant="muted">{runs.data?.data.length ?? 0}</Badge>
          </summary>
          <div className="mt-3">
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
                          className="text-[var(--accent)] transition-colors hover:text-[var(--accent-hover)]"
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
          </div>
        </details>
      </Card>

      {/* Filters */}
      <Card className="order-2 p-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="space-y-2">
            <div>
              <h2 className="font-semibold text-[var(--text-primary)]">{t('rankedResults')}</h2>
              <p className="mt-0.5 text-sm text-[var(--text-muted)]">{t('rankedResultsHint')}</p>
            </div>
            {/* View Mode Toggle */}
            <div className="inline-flex rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-surface-raised)] p-0.5">
              <button
                type="button"
                className={`rounded-md px-3 py-1 text-xs font-medium transition-colors ${
                  viewMode === 'models'
                    ? 'bg-[var(--accent)] text-white shadow-sm'
                    : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
                }`}
                onClick={() => {
                  setViewMode('models');
                  resetPage();
                }}
              >
                {t('byModels')}
              </button>
              <button
                type="button"
                className={`rounded-md px-3 py-1 text-xs font-medium transition-colors ${
                  viewMode === 'brands'
                    ? 'bg-[var(--accent)] text-white shadow-sm'
                    : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
                }`}
                onClick={() => {
                  setViewMode('brands');
                  resetPage();
                }}
              >
                {t('byBrands')}
              </button>
            </div>
          </div>
          <span aria-live="polite" className="text-sm tabular-nums text-[var(--text-muted)]">
            {currentQuery.isLoading ? t('loading') : `${t('shown')}: ${currentRows.length}`}
          </span>
        </div>
        <div className="mt-4 flex flex-wrap items-center gap-3">
          {viewMode === 'models' && (
            <label className="flex items-center gap-2 text-sm text-[var(--text-secondary)]">
              {t('brand')}
              <select
                id="dashboard-brand"
                name="brand_id"
                className="min-h-10 max-w-56 rounded-lg"
                value={brandId ?? ''}
                onChange={(event) => {
                  setBrandId(event.target.value ? Number(event.target.value) : undefined);
                  resetPage();
                }}
              >
                <option value="">{t('allBrands')}</option>
                {brands.data?.data.map((brand) => (
                  <option key={brand.id} value={brand.id}>
                    {brand.name}
                  </option>
                ))}
              </select>
            </label>
          )}
          <label className="flex items-center gap-2 text-sm text-[var(--text-secondary)]">
            {t('productType')}
            <select
              id="dashboard-product-type"
              name="product_type"
              className="min-h-10 rounded-lg"
              value={productType ?? ''}
              onChange={(event) => {
                setProductType(
                  (event.target.value || undefined) as DashboardProductType | undefined,
                );
                resetPage();
              }}
            >
              <option value="">{t('allProductTypes')}</option>
              <option value="footwear">{t('footwear')}</option>
              <option value="clothing">{t('clothing')}</option>
              <option value="accessories">{t('accessories')}</option>
            </select>
          </label>
          <label className="flex items-center gap-2 text-sm text-[var(--text-secondary)]">
            {t('dataWindow')}
            <select
              id="dashboard-window"
              name="window_days"
              className="min-h-10 rounded-lg"
              value={windowDays}
              onChange={(e) => {
                setWindowDays(Number(e.target.value));
                resetPage();
              }}
            >
              <option value={30}>30</option>
              <option value={90}>90</option>
            </select>
          </label>
          <label className="relative min-w-64 flex-1">
            <span className="sr-only">{t('search')}</span>
            <Search
              size={16}
              className="absolute left-3 top-1/2 -translate-y-1/2 text-[var(--text-muted)]"
            />
            <input
              id="dashboard-search"
              name="search"
              className="min-h-10 w-full rounded-lg pl-9 pr-10"
              placeholder={t('search')}
              value={search}
              onChange={(e) => {
                setSearch(e.target.value);
                resetPage();
              }}
            />
            {search && (
              <button
                aria-label={t('clearSearch')}
                className="absolute right-0 top-1/2 flex h-10 w-10 -translate-y-1/2 items-center justify-center rounded-lg text-[var(--text-muted)] transition-colors hover:text-[var(--text-primary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
                onClick={() => {
                  setSearch('');
                  resetPage();
                }}
                type="button"
              >
                <X size={16} />
              </button>
            )}
          </label>
          <label className="flex min-h-10 cursor-pointer items-center gap-2 text-sm text-[var(--text-secondary)]">
            <input
              id="dashboard-low-data"
              name="low_data"
              checked={showLowData}
              onChange={(event) => {
                setShowLowData(event.target.checked);
                resetPage();
              }}
              type="checkbox"
            />
            {t('includeLowData')}
          </label>
        </div>
      </Card>

      {/* Table */}
      {viewMode === 'models' ? (
        <DataTable
          className={`order-4 min-h-[420px] transition-opacity ${analytics.isFetching ? 'opacity-60' : ''}`}
        >
          <TableHead>
            {modelTable.getHeaderGroups().map((group) => (
              <tr key={group.id}>
                {group.headers.map((header) => (
                  <TableHeaderCell
                    key={header.id}
                    onClick={
                      header.column.getCanSort()
                        ? header.column.getToggleSortingHandler()
                        : undefined
                    }
                    sortDir={header.column.getIsSorted()}
                  >
                    {flexRender(header.column.columnDef.header, header.getContext())}
                  </TableHeaderCell>
                ))}
              </tr>
            ))}
          </TableHead>
          <tbody>
            {modelTable.getRowModel().rows.map((row) => (
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
      ) : (
        <DataTable
          className={`order-4 min-h-[420px] transition-opacity ${brandAnalytics.isFetching ? 'opacity-60' : ''}`}
        >
          <TableHead>
            {brandTable.getHeaderGroups().map((group) => (
              <tr key={group.id}>
                {group.headers.map((header) => (
                  <TableHeaderCell
                    key={header.id}
                    onClick={
                      header.column.getCanSort()
                        ? header.column.getToggleSortingHandler()
                        : undefined
                    }
                    sortDir={header.column.getIsSorted()}
                  >
                    {flexRender(header.column.columnDef.header, header.getContext())}
                  </TableHeaderCell>
                ))}
              </tr>
            ))}
          </TableHead>
          <tbody>
            {brandTable.getRowModel().rows.map((row) => (
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
      )}
      {viewMode === 'models' && (page > 0 || analytics.data?.next_cursor) && (
        <div className="order-5 flex items-center justify-end gap-2">
          <Button
            variant="secondary"
            disabled={page === 0 || analytics.isFetching}
            onClick={() => setPage((current) => Math.max(0, current - 1))}
          >
            {t('previous')}
          </Button>
          <Button
            variant="secondary"
            disabled={!analytics.data?.next_cursor || analytics.isFetching}
            onClick={() => {
              const next = analytics.data?.next_cursor;
              if (!next) return;
              setCursors((current) => [...current.slice(0, page + 1), next]);
              setPage((current) => current + 1);
            }}
          >
            {t('next')}
          </Button>
        </div>
      )}
      {!currentQuery.isLoading && !currentRows.length && (
        <div className="order-5">
          <EmptyState />
        </div>
      )}
    </section>
  );
}
