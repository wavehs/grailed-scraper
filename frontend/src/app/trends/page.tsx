'use client';

import { useEffect, useState } from 'react';
import { RegroupButton } from '@/components/group-editor';
import { ExportCsvLink } from '@/components/export-csv-link';
import { PeriodCoverageLine, PeriodPicker } from '@/components/period-picker';
import { RankingStatus, useRankingControls } from '@/components/ranking-settings';
import { SORTS, TrendsToolbar } from '@/components/trends/trends-toolbar';
import { TrendsTable } from '@/components/trends/trends-table';
import { CLEAR_WARDROBE, WardrobePresets } from '@/components/wardrobe-presets';
import { Button } from '@/components/ui/button';
import { PageHeader } from '@/components/ui/page-header';
import { Toolbar } from '@/components/ui/toolbar';
import { EmptyState, ErrorState, LoadingState } from '@/components/states';
import { useI18n } from '@/lib/i18n';
import { periodLabel } from '@/lib/liquidity';
import { useTrendsQuery, useTypeNames } from '@/lib/queries';
import type { NoModelShare, TrendFilters, TrendRanking, TrendRow } from '@/lib/types';
import { formatCurrency, formatDate, formatNumber, formatPercent } from '@/lib/utils';

const STORAGE_KEY = 'gla-trend-filters';
const PAGE_SIZE = 50;
const SEARCH_DEBOUNCE_MS = 300;
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
  newOnly: false,
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

function loadFilters(): TrendFilters {
  try {
    const saved = window.sessionStorage.getItem(STORAGE_KEY);
    if (!saved) return DEFAULT_FILTERS;
    const parsed = JSON.parse(saved) as Record<string, unknown>;
    // Keep only known filters: an older session may hold keys this version dropped.
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

/** Filters kept in the session, with the search box debounced into them. */
function useTrendFilters() {
  const [filters, setFilters] = useState<TrendFilters>(DEFAULT_FILTERS);
  const [search, setSearch] = useState('');
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
      SEARCH_DEBOUNCE_MS,
    );
    return () => window.clearTimeout(timer);
  }, [search]);
  const update = (patch: Partial<TrendFilters>) => setFilters((current) => ({ ...current, ...patch }));
  return { filters, update, search, setSearch };
}

export default function TrendsPage() {
  const { locale, t } = useI18n();
  const { taxonomy } = useTypeNames();
  const { filters, update, search, setSearch } = useTrendFilters();
  const [page, setPage] = useState(0);
  useEffect(() => setPage(0), [filters]);
  const trends = useTrendsQuery(filters, page * PAGE_SIZE);
  const ranking = useRankingControls();
  const periodDays = trends.data?.ranking?.period_days ?? ranking.settings?.period_days;
  const drill = (row: TrendRow) => {
    if (row.scope === 'brand') update({ level: 'model', brandIds: [row.brand_id] });
    if (row.scope === 'type')
      update({ ...CLEAR_WARDROBE, level: 'model', brandIds: [row.brand_id], productType: row.product_type ?? '' });
  };
  const total = trends.data?.total ?? 0;
  const periodMode = filters.period !== 'window' && Boolean(trends.data?.period);

  return (
    <section aria-labelledby="trends-heading">
      <PageHeader
        title={t('trends')}
        headingId="trends-heading"
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
      <TrendsToolbar filters={filters} update={update} search={search} onSearch={setSearch} ranking={ranking} />
      <Toolbar className="gap-x-4">
        <PeriodPicker filters={filters} update={update} />
        <WardrobePresets presets={taxonomy?.seasons} filters={filters} update={update} />
      </Toolbar>

      <div className="space-y-1.5 py-3 empty:hidden">
        <PeriodCoverageLine filters={filters} coverage={trends.data?.period} />
        <RankingStatus controls={ranking} />
        <RankingHint ranking={trends.data?.ranking} />
      </div>

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
          <TrendsTable
            rows={trends.data.data}
            level={filters.level}
            window={filters.window}
            periodMode={periodMode}
            periodDays={periodDays}
            onDrill={drill}
          />
          <Pagination page={page} total={total} onPage={setPage} />
        </>
      )}
    </section>
  );
}

/** The row range and previous/next buttons under the list; hidden when one page holds every row. */
function Pagination({ page, total, onPage }: { page: number; total: number; onPage: (page: number) => void }) {
  const { t } = useI18n();
  const hasNext = (page + 1) * PAGE_SIZE < total;
  if (page === 0 && !hasNext) return null;
  return (
    <div className="flex items-center justify-end gap-2 pt-3 text-xs">
      <span className="tabular-nums text-[var(--text-muted)]">
        {page * PAGE_SIZE + 1}–{Math.min((page + 1) * PAGE_SIZE, total)} / {total}
      </span>
      <Button variant="secondary" size="sm" disabled={page === 0} onClick={() => onPage(page - 1)}>
        {t('previous')}
      </Button>
      <Button variant="secondary" size="sm" disabled={!hasNext} onClick={() => onPage(page + 1)}>
        {t('next')}
      </Button>
    </div>
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
