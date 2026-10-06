'use client';

import Link from 'next/link';
import { useEffect, useMemo, useState } from 'react';
import { ArrowDownWideNarrow, ArrowUpNarrowWide, Search } from 'lucide-react';
import { RegroupButton, groupLabel } from '@/components/group-editor';
import { Sparkline } from '@/components/trend-charts';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { DataTable, TableCell, TableHead, TableHeaderCell, TableRow } from '@/components/ui/data-table';
import { PageHeader } from '@/components/ui/page-header';
import { EmptyState, ErrorState, LoadingState } from '@/components/states';
import { useI18n } from '@/lib/i18n';
import { useBrandsQuery, useTrendsQuery, useTypeNames } from '@/lib/queries';
import type { TrendFilters, TrendLevel, TrendRow, TrendSort } from '@/lib/types';
import { formatCurrency, formatDate, formatPercent } from '@/lib/utils';

const STORAGE_KEY = 'gla-trend-filters';
const DEFAULT_FILTERS: TrendFilters = {
  level: 'model',
  brandIds: [],
  section: '',
  productType: '',
  window: 30,
  priceMin: '',
  priceMax: '',
  newOnly: false,
  minSales: 0,
  search: '',
  sort: 'trend',
  desc: true,
};
const SORTS: Array<[TrendSort, string]> = [
  ['trend', 'sortTrend'],
  ['growth', 'sortGrowth'],
  ['speed', 'sortSpeed'],
  ['sales', 'sortSales'],
  ['price', 'sortPrice'],
  ['supply', 'sortSupply'],
  ['new', 'sortNew'],
];
const LEVELS: Array<[TrendLevel, string]> = [
  ['model', 'levelModels'],
  ['type', 'levelTypes'],
  ['brand', 'levelBrands'],
];

function loadFilters(): TrendFilters {
  try {
    const saved = window.sessionStorage.getItem(STORAGE_KEY);
    return saved ? { ...DEFAULT_FILTERS, ...(JSON.parse(saved) as Partial<TrendFilters>) } : DEFAULT_FILTERS;
  } catch {
    return DEFAULT_FILTERS;
  }
}

export default function TrendsPage() {
  const { locale, t } = useI18n();
  const brands = useBrandsQuery();
  const { taxonomy, typeName } = useTypeNames();
  const [filters, setFilters] = useState<TrendFilters>(DEFAULT_FILTERS);
  const [search, setSearch] = useState('');
  const [page, setPage] = useState(0);
  useEffect(() => {
    const saved = loadFilters();
    setFilters(saved);
    setSearch(saved.search);
  }, []);
  useEffect(() => {
    try {
      window.sessionStorage.setItem(STORAGE_KEY, JSON.stringify(filters));
    } catch {
      // Session storage is a convenience; filters still work without it.
    }
  }, [filters]);
  useEffect(() => {
    const timer = window.setTimeout(
      () => setFilters((current) => (current.search === search ? current : { ...current, search })),
      300,
    );
    return () => window.clearTimeout(timer);
  }, [search]);
  useEffect(() => setPage(0), [filters]);
  const trends = useTrendsQuery(filters, page * 50);
  const update = (patch: Partial<TrendFilters>) => setFilters((current) => ({ ...current, ...patch }));
  const types = useMemo(
    () =>
      (taxonomy?.types ?? []).filter(
        (item) => item.section !== 'other' && (!filters.section || item.section === filters.section),
      ),
    [taxonomy, filters.section],
  );
  const brandList = brands.data?.data ?? [];
  const toggleBrand = (id: number) =>
    update({
      brandIds: filters.brandIds.includes(id)
        ? filters.brandIds.filter((item) => item !== id)
        : [...filters.brandIds, id],
    });
  const drill = (row: TrendRow) => {
    if (row.scope === 'brand') update({ level: 'model', brandIds: [row.brand_id] });
    if (row.scope === 'type')
      update({ level: 'model', brandIds: [row.brand_id], productType: row.product_type ?? '' });
  };
  const total = trends.data?.total ?? 0;

  return (
    <section className="space-y-5" aria-labelledby="trends-heading">
      <PageHeader
        title={t('trends')}
        description={t('trendsIntro')}
        actions={
          <>
            {trends.data?.computed_at && (
              <span className="text-xs text-[var(--text-muted)]">
                {t('updatedAt')}: {formatDate(trends.data.computed_at, locale)}
              </span>
            )}
            <RegroupButton />
          </>
        }
      />

      <Card className="space-y-3 p-4">
        <div className="flex flex-wrap items-end gap-3">
          <div className="inline-flex rounded-lg border border-[var(--border-default)] p-0.5" role="group">
            {LEVELS.map(([value, label]) => (
              <button
                key={value}
                type="button"
                aria-pressed={filters.level === value}
                onClick={() => update({ level: value })}
                className={`rounded-md px-3 py-1 text-xs ${
                  filters.level === value
                    ? 'bg-[var(--accent-soft)] font-semibold text-[var(--accent)]'
                    : 'text-[var(--text-secondary)]'
                }`}
              >
                {t(label)}
              </button>
            ))}
          </div>
          <label className="relative min-w-48 flex-1">
            <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-[var(--text-muted)]" />
            <span className="sr-only">{t('search')}</span>
            <input
              className="w-full pl-8 text-sm"
              placeholder={t('search')}
              value={search}
              onChange={(event) => setSearch(event.target.value)}
            />
          </label>
          <details className="relative">
            <summary className="cursor-pointer rounded-lg border border-[var(--border-default)] px-3 py-1.5 text-sm text-[var(--text-secondary)]">
              {filters.brandIds.length ? `${t('brandsSelected')}: ${filters.brandIds.length}` : t('allBrands')}
            </summary>
            <div className="absolute z-20 mt-1 max-h-72 w-64 space-y-1 overflow-y-auto rounded-lg border border-[var(--border-default)] bg-[var(--bg-surface-raised)] p-2 shadow-lg">
              <button type="button" className="text-xs text-[var(--accent)]" onClick={() => update({ brandIds: [] })}>
                {t('allBrands')}
              </button>
              {brandList.map((brand) => (
                <label key={brand.id} className="flex items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={filters.brandIds.includes(brand.id)}
                    onChange={() => toggleBrand(brand.id)}
                  />
                  {brand.name}
                </label>
              ))}
            </div>
          </details>
          <label className="text-xs text-[var(--text-muted)]">
            <span className="sr-only">{t('section')}</span>
            <select
              aria-label={t('section')}
              value={filters.section}
              onChange={(event) => update({ section: event.target.value, productType: '' })}
            >
              <option value="">{t('allSections')}</option>
              {(taxonomy?.sections ?? [])
                .filter((item) => item.id !== 'other')
                .map((item) => (
                  <option key={item.id} value={item.id}>
                    {item[locale]}
                  </option>
                ))}
            </select>
          </label>
          <select
            aria-label={t('productType')}
            value={filters.productType}
            onChange={(event) => update({ productType: event.target.value })}
          >
            <option value="">{t('allTypes')}</option>
            {types.map((item) => (
              <option key={item.id} value={item.id}>
                {item[locale]}
              </option>
            ))}
          </select>
        </div>
        <div className="flex flex-wrap items-end gap-3 text-sm">
          <div className="inline-flex rounded-lg border border-[var(--border-default)] p-0.5" role="group" aria-label={t('trendWindow')}>
            {([7, 30, 90] as const).map((value) => (
              <button
                key={value}
                type="button"
                aria-pressed={filters.window === value}
                onClick={() => update({ window: value })}
                className={`rounded-md px-2.5 py-1 text-xs tabular-nums ${
                  filters.window === value
                    ? 'bg-[var(--accent-soft)] font-semibold text-[var(--accent)]'
                    : 'text-[var(--text-secondary)]'
                }`}
              >
                {value} {t('daysShort')}
              </button>
            ))}
          </div>
          <label className="flex items-center gap-1 text-xs text-[var(--text-muted)]">
            {t('priceFrom')}
            <input
              className="w-20 text-sm"
              inputMode="numeric"
              value={filters.priceMin}
              onChange={(event) => update({ priceMin: event.target.value.replace(/\D/g, '') })}
            />
          </label>
          <label className="flex items-center gap-1 text-xs text-[var(--text-muted)]">
            {t('priceTo')}
            <input
              className="w-20 text-sm"
              inputMode="numeric"
              value={filters.priceMax}
              onChange={(event) => update({ priceMax: event.target.value.replace(/\D/g, '') })}
            />
          </label>
          <label className="flex items-center gap-1 text-xs text-[var(--text-muted)]">
            {t('minSales')}
            <input
              className="w-14 text-sm"
              type="number"
              min={0}
              value={filters.minSales}
              onChange={(event) => update({ minSales: Math.max(0, Number(event.target.value) || 0) })}
            />
          </label>
          <label className="flex items-center gap-1.5 text-xs text-[var(--text-secondary)]">
            <input type="checkbox" checked={filters.newOnly} onChange={(event) => update({ newOnly: event.target.checked })} />
            {t('newOnly')}
          </label>
          <span className="ml-auto flex items-center gap-1">
            <select aria-label={t('sortBy')} value={filters.sort} onChange={(event) => update({ sort: event.target.value as TrendSort })}>
              {SORTS.map(([value, label]) => (
                <option key={value} value={value}>
                  {t(label)}
                </option>
              ))}
            </select>
            <Button
              size="sm"
              variant="ghost"
              aria-label={filters.desc ? t('sortDescending') : t('sortAscending')}
              title={filters.desc ? t('sortDescending') : t('sortAscending')}
              icon={filters.desc ? <ArrowDownWideNarrow size={16} /> : <ArrowUpNarrowWide size={16} />}
              onClick={() => update({ desc: !filters.desc })}
            />
          </span>
        </div>
      </Card>

      <details className="text-xs text-[var(--text-muted)]">
        <summary className="cursor-pointer">{t('trendFormula')}</summary>
        <p className="mt-1 max-w-3xl">{t('trendFormulaText')}</p>
      </details>

      {trends.isLoading ? (
        <LoadingState />
      ) : trends.error ? (
        <ErrorState error={trends.error} retry={() => trends.refetch()} />
      ) : !trends.data?.computed_at ? (
        <EmptyState message={t('noMetrics')} />
      ) : !trends.data.data.length ? (
        <EmptyState message={t('noTrends')} />
      ) : (
        <>
          <DataTable>
            <TableHead>
              <tr>
                <TableHeaderCell>{t(filters.level === 'model' ? 'model' : filters.level === 'type' ? 'productType' : 'brand')}</TableHeaderCell>
                <TableHeaderCell>{t('weeklyTrend')}</TableHeaderCell>
                <TableHeaderCell>{t('trendScore')}</TableHeaderCell>
                <TableHeaderCell>{t('salesGrowth')}</TableHeaderCell>
                <TableHeaderCell>{t('timeToSell')}</TableHeaderCell>
                <TableHeaderCell>{t('sellThrough')}</TableHeaderCell>
                <TableHeaderCell>{`${t('salesInWindow')}, ${filters.window} ${t('daysShort')}`}</TableHeaderCell>
                <TableHeaderCell>{t('medianPrice')}</TableHeaderCell>
                <TableHeaderCell>{t('supplyNow')}</TableHeaderCell>
              </tr>
            </TableHead>
            <tbody>
              {trends.data.data.map((row) => (
                <TrendTableRow key={row.scope_key} row={row} onDrill={drill} typeName={typeName} />
              ))}
            </tbody>
          </DataTable>
          {(page > 0 || (page + 1) * 50 < total) && (
            <div className="flex items-center justify-end gap-2 text-sm">
              <span className="text-[var(--text-muted)] tabular-nums">
                {page * 50 + 1}–{Math.min((page + 1) * 50, total)} / {total}
              </span>
              <Button variant="secondary" size="sm" disabled={page === 0} onClick={() => setPage(page - 1)}>
                {t('previous')}
              </Button>
              <Button
                variant="secondary"
                size="sm"
                disabled={(page + 1) * 50 >= total}
                onClick={() => setPage(page + 1)}
              >
                {t('next')}
              </Button>
            </div>
          )}
        </>
      )}
    </section>
  );
}

function TrendTableRow({
  row,
  onDrill,
  typeName,
}: {
  row: TrendRow;
  onDrill: (row: TrendRow) => void;
  typeName: (id?: string | null) => string;
}) {
  const { locale, t } = useI18n();
  const parts = t('trendParts')
    .replace('{growth}', Number(row.growth).toFixed(2))
    .replace('{speed}', row.speed === null ? '—' : Number(row.speed).toFixed(2))
    .replace('{share}', formatPercent(row.sell_through_30d));
  const title =
    row.scope === 'model'
      ? `${row.brand} · ${groupLabel({ name: row.name ?? '', is_fallback: row.is_fallback }, t)} · ${typeName(row.product_type)}`
      : row.scope === 'type'
        ? `${row.brand} · ${typeName(row.product_type)}`
        : row.brand;
  const change = row.price_change === null ? null : Number(row.price_change);
  return (
    <TableRow>
      <TableCell>
        {row.scope === 'model' && row.group_id ? (
          <Link className="font-medium text-[var(--accent)] hover:underline" href={`/group?id=${row.group_id}`}>
            {title}
          </Link>
        ) : (
          <button type="button" className="text-left font-medium text-[var(--accent)] hover:underline" onClick={() => onDrill(row)}>
            {title}
          </button>
        )}
        <span className="mt-0.5 flex flex-wrap gap-1">
          {row.status === 'auto' && <Badge variant="warning">{t('autoGroup')}</Badge>}
          {row.is_new && <Badge variant="info">{t('newBadge')}</Badge>}
          {row.versions > 0 && (
            <Badge variant="muted">
              {t('versionsCount')}: {row.versions}
            </Badge>
          )}
        </span>
      </TableCell>
      <TableCell>
        <Sparkline values={row.weekly_sales} label={t('weeklyTrend')} />
      </TableCell>
      <TableCell>
        <span className="font-semibold tabular-nums text-[var(--text-primary)]" title={parts}>
          {row.trend_score === null ? '—' : Number(row.trend_score).toFixed(0)}
        </span>
      </TableCell>
      <TableCell>
        <span className="tabular-nums" title={t('vsPrevious')}>
          ×{Number(row.growth).toFixed(2)}
        </span>
        <p className="text-xs tabular-nums text-[var(--text-muted)]">
          {row.sold_30d} / {row.sold_prev_30d}
        </p>
      </TableCell>
      <TableCell>
        <span className="tabular-nums">
          {row.median_days_to_sell === null ? '—' : `${Number(row.median_days_to_sell).toFixed(0)} ${t('daysShort')}`}
        </span>
      </TableCell>
      <TableCell>
        <span className="tabular-nums">{formatPercent(row.sell_through_30d)}</span>
      </TableCell>
      <TableCell>
        <span className="tabular-nums">{row.sold}</span>
      </TableCell>
      <TableCell>
        <span className="tabular-nums">{formatCurrency(row.median_price, locale)}</span>
        {change !== null && (
          <p className="text-xs tabular-nums text-[var(--text-muted)]" title={t('priceChange')}>
            {change > 0 ? '+' : ''}
            {(change * 100).toFixed(0)}%
          </p>
        )}
      </TableCell>
      <TableCell>
        <span className="tabular-nums">{row.active_now}</span>
        <p className="text-xs tabular-nums text-[var(--text-muted)]">
          +{row.new_listings_14d} {t('newListings14d')}
        </p>
      </TableCell>
    </TableRow>
  );
}
