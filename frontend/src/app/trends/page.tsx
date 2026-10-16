'use client';

import Link from 'next/link';
import { useEffect, useMemo, useState } from 'react';
import { ArrowDownWideNarrow, ArrowUpNarrowWide, Search } from 'lucide-react';
import { GroupKindBadge, RegroupButton, groupLabel, isServiceGroup } from '@/components/group-editor';
import {
  AskVsSoldCell,
  LiquidityFilters,
  LiquidityScoreCell,
  P30Cell,
  SupplyCell,
} from '@/components/liquidity-cells';
import { ExportCsvLink } from '@/components/export-csv-link';
import { MetricBadges } from '@/components/metric-badges';
import { WatchStar } from '@/components/watch-star';
import { PeriodCoverageLine, PeriodPicker } from '@/components/period-picker';
import { PeriodControl, RankingSettings, RankingStatus, useRankingControls } from '@/components/ranking-settings';
import { Sparkline } from '@/components/trend-charts';
import { CLEAR_WARDROBE, WardrobePresets } from '@/components/wardrobe-presets';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { DataTable, TableCell, TableHead, TableHeaderCell, TableRow } from '@/components/ui/data-table';
import { PageHeader } from '@/components/ui/page-header';
import { EmptyState, ErrorState, LoadingState } from '@/components/states';
import { useI18n } from '@/lib/i18n';
import { periodLabel } from '@/lib/liquidity';
import { useBrandsQuery, useTrendsQuery, useTypeNames } from '@/lib/queries';
import type { NoModelShare, TrendFilters, TrendLevel, TrendRanking, TrendRow, TrendSort } from '@/lib/types';
import { formatCurrency, formatDate, formatNumber, formatPercent } from '@/lib/utils';

const STORAGE_KEY = 'gla-trend-filters';
const DEFAULT_FILTERS: TrendFilters = {
  level: 'model',
  brandIds: [],
  section: '',
  productType: '',
  wardrobe: null,
  productTypes: [],
  window: 30,
  period: 'window',
  monthFrom: '',
  monthTo: '',
  priceMin: '',
  priceMax: '',
  minSales: 0,
  includeUnranked: false,
  search: '',
  descriptors: false,
  condition: 'all',
  department: '',
  hideLowData: false,
  watchedOnly: false,
  sort: 'liquidity',
  desc: true,
};
const SORTS: Array<[TrendSort, string]> = [
  ['liquidity', 'sortLiquidity'],
  ['p30', 'sortP30'],
  ['growth', 'sortGrowth'],
  ['sales', 'sortSales'],
  ['speed', 'sortSpeed'],
  ['likes', 'sortLikes'],
  ['price', 'sortPrice'],
];
const LEVELS: Array<[TrendLevel, string]> = [
  ['model', 'levelModels'],
  ['type', 'levelTypes'],
  ['brand', 'levelBrands'],
];

function loadFilters(): TrendFilters {
  try {
    const saved = window.sessionStorage.getItem(STORAGE_KEY);
    if (!saved) return DEFAULT_FILTERS;
    const parsed = JSON.parse(saved) as Record<string, unknown>;
    // Keep only known filters: an older session may hold keys such as the removed `window`.
    const known = Object.fromEntries(
      Object.entries(parsed).filter(([key]) => key in DEFAULT_FILTERS),
    ) as Partial<TrendFilters>;
    const merged = { ...DEFAULT_FILTERS, ...known };
    // A session saved before the liquidity ranking may hold a sort the API no longer accepts.
    return SORTS.some(([value]) => value === merged.sort) ? merged : { ...merged, sort: DEFAULT_FILTERS.sort };
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
  const ranking = useRankingControls();
  const periodDays = trends.data?.ranking?.period_days ?? ranking.settings?.period_days;
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
      update({
        ...CLEAR_WARDROBE,
        level: 'model',
        brandIds: [row.brand_id],
        productType: row.product_type ?? '',
      });
  };
  const total = trends.data?.total ?? 0;
  const periodMode = filters.period !== 'window' && Boolean(trends.data?.period);

  return (
    <section className="space-y-5" aria-labelledby="trends-heading">
      <PageHeader
        title={t('trends')}
        description={t('trendsIntro')}
        actions={
          <>
            <NoModelLine share={trends.data?.no_model} window={filters.window} periodMode={periodMode} />
            {trends.data?.computed_at && (
              <span className="text-xs text-[var(--text-muted)]">
                {t('updatedAt')}: {formatDate(trends.data.computed_at, locale)}
              </span>
            )}
            <ExportCsvLink filters={filters} />
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
              onChange={(event) => update({ ...CLEAR_WARDROBE, section: event.target.value, productType: '' })}
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
            onChange={(event) => update({ ...CLEAR_WARDROBE, productType: event.target.value })}
          >
            <option value="">{t('allTypes')}</option>
            {types.map((item) => (
              <option key={item.id} value={item.id}>
                {item[locale]}
              </option>
            ))}
          </select>
        </div>
        <WardrobePresets presets={taxonomy?.seasons} filters={filters} update={update} />
        <div className="flex flex-wrap items-end gap-3 text-sm">
          <PeriodPicker filters={filters} update={update} />
          <PeriodControl controls={ranking} />
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
          <LiquidityFilters filters={filters} update={update} />
          <label className="flex items-center gap-1.5 text-xs text-[var(--text-secondary)]">
            <input
              type="checkbox"
              checked={filters.includeUnranked}
              onChange={(event) => update({ includeUnranked: event.target.checked })}
            />
            {t('includeUnranked')}
          </label>
          {filters.level === 'model' && (
            <label className="flex items-center gap-1.5 text-xs text-[var(--text-secondary)]" title={t('showDescriptorsHelp')}>
              <input
                type="checkbox"
                checked={filters.descriptors}
                onChange={(event) => update({ descriptors: event.target.checked })}
              />
              {t('showDescriptors')}
            </label>
          )}
          {filters.level === 'model' && (
            <label className="flex items-center gap-1.5 text-xs text-[var(--text-secondary)]" title={t('watchedOnlyHelp')}>
              <input
                type="checkbox"
                checked={filters.watchedOnly}
                onChange={(event) => update({ watchedOnly: event.target.checked })}
              />
              {t('watchedOnly')}
            </label>
          )}
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

      <PeriodCoverageLine filters={filters} coverage={trends.data?.period} />
      <RankingSettings controls={ranking} />
      <RankingStatus controls={ranking} />
      <RankingHint ranking={trends.data?.ranking} />

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
                <TableHeaderCell>{t('liquidityScore')}</TableHeaderCell>
                <TableHeaderCell>{t('p30')}</TableHeaderCell>
                <TableHeaderCell>
                  {periodMode ? t('salesInPeriod') : `${t('salesInWindow')}, ${filters.window} ${t('daysShort')}`}
                </TableHeaderCell>
                <TableHeaderCell>{t('salesPerMonth')}</TableHeaderCell>
                <TableHeaderCell>{t('daysToSell')}</TableHeaderCell>
                <TableHeaderCell>{t('likesColumn')}</TableHeaderCell>
                <TableHeaderCell>{periodMode ? `${t('medianPrice')} ≈` : t('medianPrice')}</TableHeaderCell>
                <TableHeaderCell>{t('askVsSold')}</TableHeaderCell>
                <TableHeaderCell>{t('supplyNow')}</TableHeaderCell>
                <TableHeaderCell>{t('weeklyTrend')}</TableHeaderCell>
              </tr>
            </TableHead>
            <tbody>
              {trends.data.data.map((row) => (
                <TrendTableRow
                  key={row.scope_key}
                  row={row}
                  periodDays={periodDays}
                  onDrill={drill}
                  typeName={typeName}
                  periodMode={periodMode}
                />
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
  periodDays,
  onDrill,
  typeName,
  periodMode,
}: {
  row: TrendRow;
  periodDays?: number;
  onDrill: (row: TrendRow) => void;
  typeName: (id?: string | null) => string;
  /** Sales and price cover calendar months; the price is an approximate mean of medians. */
  periodMode: boolean;
}) {
  const { locale, t } = useI18n();
  const title =
    row.scope === 'model'
      ? `${row.brand} · ${groupLabel({ name: row.name ?? '', is_fallback: row.is_fallback, kind: row.kind }, t)} · ${typeName(row.product_type)}`
      : row.scope === 'type'
        ? `${row.brand} · ${typeName(row.product_type)}`
        : row.brand;
  // The window's price change says nothing about a calendar period.
  const change = periodMode || row.price_change === null ? null : Number(row.price_change);
  return (
    <TableRow>
      <TableCell>
        {row.scope === 'model' && row.group_id ? (
          <span className="flex items-start gap-1.5">
            <WatchStar groupId={row.group_id} />
            <Link className="font-medium text-[var(--accent)] hover:underline" href={`/group?id=${row.group_id}`}>
              {title}
            </Link>
          </span>
        ) : (
          <button type="button" className="text-left font-medium text-[var(--accent)] hover:underline" onClick={() => onDrill(row)}>
            {title}
          </button>
        )}
        <span className="mt-0.5 flex flex-wrap gap-1">
          {row.status === 'auto' && !isServiceGroup(row) && <Badge variant="warning">{t('autoGroup')}</Badge>}
          <GroupKindBadge kind={row.kind} />
          {row.is_new && <Badge variant="info">{t('newBadge')}</Badge>}
          <MetricBadges badges={row.badges} />
          {row.versions > 0 && (
            <Badge variant="muted">
              {t('versionsCount')}: {row.versions}
            </Badge>
          )}
        </span>
      </TableCell>
      <TableCell>
        <span
          className="font-semibold tabular-nums text-[var(--text-primary)]"
          title={row.liquidity_score === null ? t('belowThreshold') : undefined}
        >
          {formatNumber(row.liquidity_score, 0, locale)}
        </span>
      </TableCell>
      <TableCell>
        <P30Cell row={row} />
      </TableCell>
      <TableCell>
        <span className="tabular-nums">{row.sold}</span>
      </TableCell>
      <TableCell>
        <span className="tabular-nums">{formatNumber(row.sales_per_month, 1, locale)}</span>
        {periodDays !== undefined && (
          <span className="block text-xs tabular-nums text-[var(--text-muted)]">
            {t('soldInPeriod')
              .replace('{count}', String(row.period_sold))
              .replace('{period}', periodLabel(periodDays, t))}
          </span>
        )}
      </TableCell>
      <TableCell>
        <span className="tabular-nums">{formatNumber(row.period_median_days, 1, locale)}</span>
      </TableCell>
      <TableCell>
        <span className="tabular-nums" title={t('medianLikes')}>
          {formatNumber(row.period_median_likes, 0, locale)}
        </span>
      </TableCell>
      <TableCell>
        <span className="tabular-nums" title={periodMode ? t('periodPriceNote') : undefined}>
          {periodMode && row.median_price !== null ? '≈ ' : ''}
          {formatCurrency(row.median_price, locale)}
        </span>
        {change !== null && (
          <p className="text-xs tabular-nums text-[var(--text-muted)]" title={t('priceChange')}>
            {change > 0 ? '+' : ''}
            {(change * 100).toFixed(0)}%
          </p>
        )}
      </TableCell>
      <TableCell>
        <AskVsSoldCell price={row.price} />
      </TableCell>
      <TableCell>
        <SupplyCell row={row} />
      </TableCell>
      <TableCell>
        <Sparkline values={row.weekly_sales} label={t('weeklyTrend')} />
      </TableCell>
    </TableRow>
  );
}

/** What a row needs to be ranked and how the four metrics are weighted. */
function RankingHint({ ranking }: { ranking?: TrendRanking }) {
  const { locale, t } = useI18n();
  const { sectionName } = useTypeNames();
  if (!ranking) return null;
  const weight = (value: string) => `${Math.round(Number(value) * 100)}%`;
  const excluded = ranking.excluded_sections.length
    ? t('rankingExcluded').replace('{sections}', ranking.excluded_sections.map(sectionName).join(', '))
    : '';
  const text = t('rankingHint')
    .replace('{period}', periodLabel(ranking.period_days, t))
    .replace('{minSales}', formatNumber(ranking.min_sales_per_month, 1, locale))
    .replace('{minPrice}', formatCurrency(ranking.min_price, locale))
    .replace('{excluded}', excluded)
    .replace('{sales}', weight(ranking.weights.sales))
    .replace('{speed}', weight(ranking.weights.speed))
    .replace('{likes}', weight(ranking.weights.likes))
    .replace('{price}', weight(ranking.weights.price));
  return <p className="max-w-3xl text-xs text-[var(--text-muted)]">{text}</p>;
}

/** The share of "No model" in the scope: of sales in the window or period, or of listings without sales. */
function NoModelLine({
  share,
  window,
  periodMode,
}: {
  share?: NoModelShare;
  window: number;
  periodMode: boolean;
}) {
  const { t } = useI18n();
  if (!share) return null;
  const bySales = share.sold_share !== null;
  const value = bySales ? share.sold_share : share.listings_share;
  if (value === null) return null;
  const salesHelp = periodMode ? t('noModelSharePeriod') : t('noModelShareSales');
  const help = (bySales ? salesHelp : t('noModelShareListings'))
    .replace('{part}', String(bySales ? share.sold : share.listings))
    .replace('{total}', String(bySales ? share.total_sold : share.total_listings))
    .replace('{period}', `${window} ${t('daysShort')}`);
  return (
    <span className="text-xs tabular-nums text-[var(--text-secondary)]" title={help}>
      {t('noModel')}: {formatPercent(value)}
    </span>
  );
}
