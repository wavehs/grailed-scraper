'use client';

import { ArrowDownWideNarrow, ArrowUpNarrowWide, Building2, ListFilter, Search, SlidersHorizontal } from 'lucide-react';
import { LiquidityFilters } from '@/components/liquidity-cells';
import { PeriodControl, RankingSettings, type RankingControls } from '@/components/ranking-settings';
import { CLEAR_WARDROBE } from '@/components/wardrobe-presets';
import { MenuCheckbox, Popover, PopoverRow, PopoverSection } from '@/components/ui/popover';
import { Segmented } from '@/components/ui/segmented';
import { Toolbar } from '@/components/ui/toolbar';
import { useI18n } from '@/lib/i18n';
import { useBrandsQuery, useTypeNames } from '@/lib/queries';
import type { TrendFilters, TrendLevel, TrendSort } from '@/lib/types';

type Update = (patch: Partial<TrendFilters>) => void;

export const SORTS: Array<[TrendSort, string]> = [
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
/** What "Clear filters" resets: everything in the filter menu, not the scope or the order. */
const CLEARED: Partial<TrendFilters> = {
  priceMin: '',
  priceMax: '',
  minSales: 0,
  condition: 'all',
  department: '',
  hideLowData: false,
  newOnly: false,
  watchedOnly: false,
};
const COMPACT = 'h-7 min-h-0 py-0 text-xs';

/** How many options of the filter menu differ from their defaults. */
export function activeFilterCount(filters: TrendFilters): number {
  return Object.entries(CLEARED).filter(([key, value]) => filters[key as keyof TrendFilters] !== value).length;
}

/** The list's scope (level, search, brands, section, type), its filter menu and its view menu. */
export function TrendsToolbar({
  filters,
  update,
  search,
  onSearch,
  ranking,
}: {
  filters: TrendFilters;
  update: Update;
  search: string;
  onSearch: (value: string) => void;
  ranking: RankingControls;
}) {
  const { t } = useI18n();
  return (
    <Toolbar>
      <Segmented
        label={t('listLevel')}
        value={filters.level}
        options={LEVELS.map(([value, label]) => ({ value, label: t(label) }))}
        onChange={(level) => update({ level })}
      />
      <label className="relative w-full sm:w-56">
        <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-[var(--text-muted)]" aria-hidden />
        <span className="sr-only">{t('search')}</span>
        <input
          className={`${COMPACT} w-full pl-7`}
          placeholder={t('search')}
          value={search}
          onChange={(event) => onSearch(event.target.value)}
        />
      </label>
      <BrandMenu filters={filters} update={update} />
      <ScopeSelects filters={filters} update={update} />
      <FilterMenu filters={filters} update={update} />
      <div className="ml-auto flex items-center gap-2">
        <RankingSettings controls={ranking} />
        <ViewMenu filters={filters} update={update} ranking={ranking} />
      </div>
    </Toolbar>
  );
}

function BrandMenu({ filters, update }: { filters: TrendFilters; update: Update }) {
  const { t } = useI18n();
  const brands = useBrandsQuery().data?.data ?? [];
  const selected = filters.brandIds;
  const toggle = (id: number) =>
    update({ brandIds: selected.includes(id) ? selected.filter((item) => item !== id) : [...selected, id] });
  return (
    <Popover
      label={selected.length ? `${t('brandsSelected')}: ${selected.length}` : t('allBrands')}
      icon={<Building2 size={13} className="text-[var(--text-muted)]" aria-hidden />}
      className="w-64"
    >
      <button
        type="button"
        className="flex h-8 w-full items-center rounded-[5px] px-2 text-left text-[var(--text-secondary)] hover:bg-[var(--bg-surface-hover)] hover:text-[var(--text-primary)]"
        onClick={() => update({ brandIds: [] })}
      >
        {t('allBrands')}
      </button>
      <div className="border-t border-[var(--border-subtle)] pt-1">
        {brands.map((brand) => (
          <MenuCheckbox
            key={brand.id}
            label={brand.name}
            checked={selected.includes(brand.id)}
            onChange={() => toggle(brand.id)}
          />
        ))}
      </div>
    </Popover>
  );
}

function ScopeSelects({ filters, update }: { filters: TrendFilters; update: Update }) {
  const { locale, t } = useI18n();
  const { taxonomy } = useTypeNames();
  const sections = (taxonomy?.sections ?? []).filter((item) => item.id !== 'other');
  const types = (taxonomy?.types ?? []).filter(
    (item) => item.section !== 'other' && (!filters.section || item.section === filters.section),
  );
  return (
    <>
      <select
        aria-label={t('section')}
        className={COMPACT}
        value={filters.section}
        onChange={(event) => update({ ...CLEAR_WARDROBE, section: event.target.value, productType: '' })}
      >
        <option value="">{t('allSections')}</option>
        {sections.map((item) => (
          <option key={item.id} value={item.id}>
            {item[locale]}
          </option>
        ))}
      </select>
      <select
        aria-label={t('productType')}
        className={COMPACT}
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
    </>
  );
}

function FilterMenu({ filters, update }: { filters: TrendFilters; update: Update }) {
  const { t } = useI18n();
  const count = activeFilterCount(filters);
  const digits = (value: string) => value.replace(/\D/g, '');
  return (
    <Popover label={t('filter')} count={count} icon={<ListFilter size={13} className="text-[var(--text-muted)]" aria-hidden />}>
      <PopoverSection>
        <PopoverRow label={t('priceFrom')} htmlFor="trend-price-min">
          <input
            id="trend-price-min"
            className={`${COMPACT} w-24`}
            inputMode="numeric"
            value={filters.priceMin}
            onChange={(event) => update({ priceMin: digits(event.target.value) })}
          />
        </PopoverRow>
        <PopoverRow label={t('priceTo')} htmlFor="trend-price-max">
          <input
            id="trend-price-max"
            className={`${COMPACT} w-24`}
            inputMode="numeric"
            value={filters.priceMax}
            onChange={(event) => update({ priceMax: digits(event.target.value) })}
          />
        </PopoverRow>
        <PopoverRow label={t('minSales')} htmlFor="trend-min-sales">
          <input
            id="trend-min-sales"
            className={`${COMPACT} w-24`}
            type="number"
            min={0}
            value={filters.minSales}
            onChange={(event) => update({ minSales: Math.max(0, Number(event.target.value) || 0) })}
          />
        </PopoverRow>
      </PopoverSection>
      <PopoverSection>
        <LiquidityFilters filters={filters} update={update} />
        <MenuCheckbox label={t('newOnly')} checked={filters.newOnly} onChange={(newOnly) => update({ newOnly })} />
        {filters.level === 'model' && (
          <MenuCheckbox
            label={t('watchedOnly')}
            title={t('watchedOnlyHelp')}
            checked={filters.watchedOnly}
            onChange={(watchedOnly) => update({ watchedOnly })}
          />
        )}
      </PopoverSection>
      {count > 0 && (
        <PopoverSection>
          <button
            type="button"
            className="flex h-8 w-full items-center rounded-[5px] px-2 text-left text-[var(--text-secondary)] hover:bg-[var(--bg-surface-hover)] hover:text-[var(--text-primary)]"
            onClick={() => update(CLEARED)}
          >
            {t('clearFilters')}
          </button>
        </PopoverSection>
      )}
    </Popover>
  );
}

function ViewMenu({ filters, update, ranking }: { filters: TrendFilters; update: Update; ranking: RankingControls }) {
  const { t } = useI18n();
  const direction = filters.desc ? t('sortDescending') : t('sortAscending');
  return (
    <Popover
      label={t('view')}
      align="end"
      className="w-80"
      icon={<SlidersHorizontal size={13} className="text-[var(--text-muted)]" aria-hidden />}
    >
      <PopoverSection title={t('sortBy')}>
        <div className="flex items-center gap-1.5 px-2 py-1">
          <select
            aria-label={t('sortBy')}
            className={`${COMPACT} flex-1`}
            value={filters.sort}
            onChange={(event) => update({ sort: event.target.value as TrendSort })}
          >
            {SORTS.map(([value, label]) => (
              <option key={value} value={value}>
                {t(label)}
              </option>
            ))}
          </select>
          <button
            type="button"
            className="chip"
            aria-label={direction}
            title={direction}
            onClick={() => update({ desc: !filters.desc })}
          >
            {filters.desc ? <ArrowDownWideNarrow size={14} /> : <ArrowUpNarrowWide size={14} />}
          </button>
        </div>
      </PopoverSection>
      {ranking.settings && (
        <PopoverSection title={t('rankingPeriod')}>
          <div className="px-2 py-1">
            <PeriodControl controls={ranking} />
          </div>
        </PopoverSection>
      )}
      <PopoverSection title={t('display')}>
        <MenuCheckbox
          label={t('includeUnranked')}
          checked={filters.includeUnranked}
          onChange={(includeUnranked) => update({ includeUnranked })}
        />
        {filters.level === 'model' && (
          <MenuCheckbox
            label={t('showDescriptors')}
            title={t('showDescriptorsHelp')}
            checked={filters.descriptors}
            onChange={(descriptors) => update({ descriptors })}
          />
        )}
      </PopoverSection>
    </Popover>
  );
}
