'use client';

import { MenuCheckbox, PopoverRow } from '@/components/ui/popover';
import { Segmented } from '@/components/ui/segmented';
import { useI18n } from '@/lib/i18n';
import type { PriceBucket, TrendCondition, TrendDepartment, TrendFilters, TrendRow } from '@/lib/types';
import { formatCurrency } from '@/lib/utils';

const CONDITIONS: Array<[TrendCondition, string]> = [
  ['all', 'conditionAll'],
  ['new', 'conditionNew'],
  ['used', 'conditionUsed'],
];
const DEPARTMENTS: Array<[TrendDepartment, string]> = [
  ['', 'departmentAll'],
  ['menswear', 'departmentMen'],
  ['womenswear', 'departmentWomen'],
];

/** A ratio as a whole percentage, signed when asked: 0.3 → "+30%". Ratios above 1 stay ratios. */
export function formatRatio(value: string | null | undefined, signed = false): string {
  if (value === null || value === undefined) return '—';
  const percent = Math.round(Number(value) * 100);
  if (Number.isNaN(percent)) return '—';
  return `${signed && percent > 0 ? '+' : ''}${percent}%`;
}

export function LiquidityScoreCell({ row }: { row: TrendRow }) {
  const { t } = useI18n();
  const score = row.liquidity_score ?? null;
  return (
    <span className="font-semibold tabular-nums text-[var(--text-primary)]" title={t('liquidityScoreHelp')}>
      {score === null ? '—' : Number(score).toFixed(0)}
    </span>
  );
}

export function P30Cell({ row }: { row: TrendRow }) {
  const { t } = useI18n();
  if ((row.p30 ?? null) === null) return <span>—</span>;
  return (
    <span title={t('p30Help')}>
      <span className="tabular-nums">{formatRatio(row.p30)}</span>
      <sup className="ml-0.5 text-[10px] text-[var(--text-muted)]">{t('beta')}</sup>
      <p className="text-xs tabular-nums text-[var(--text-muted)]">
        n={row.p30_n ?? 0}
        {(row.expected_days ?? null) !== null && ` · ${Number(row.expected_days).toFixed(0)} ${t('expectedDays')}`}
      </p>
    </span>
  );
}

export function AskVsSoldCell({ price }: { price: PriceBucket | null }) {
  const { locale, t } = useI18n();
  if (!price || price.p50 === null) return <span>—</span>;
  return (
    <span title={price.approximate ? t('approximateHelp') : t('askVsSoldHelp')}>
      <span className="tabular-nums">
        {price.approximate && `${t('approximate')} `}
        {formatCurrency(price.p50, locale)}
      </span>
      {price.fresh_ask_premium !== null && (
        <p className="text-xs tabular-nums text-[var(--text-muted)]">
          {t('freshPremium')} {formatRatio(price.fresh_ask_premium, true)}
        </p>
      )}
    </span>
  );
}

export function SupplyCell({ row }: { row: TrendRow }) {
  const { t } = useI18n();
  return (
    <span title={t('supplyHelp')}>
      <span className="tabular-nums">{row.active_now}</span>
      {(row.active_effective ?? null) !== null && (
        <p className="text-xs tabular-nums text-[var(--text-muted)]">
          {row.active_effective} {t('supplyEffective')} · {row.active_fresh ?? 0} {t('supplyFresh')}
        </p>
      )}
      <p className="text-xs tabular-nums text-[var(--text-muted)]">
        +{row.new_listings_14d} {t('newListings14d')}
      </p>
    </span>
  );
}

/** Condition, department and "hide low data" rows of the trends filter menu. */
export function LiquidityFilters({
  filters,
  update,
}: {
  filters: TrendFilters;
  update: (patch: Partial<TrendFilters>) => void;
}) {
  const { t } = useI18n();
  return (
    <>
      <PopoverRow label={t('conditionFilter')}>
        <Segmented
          label={t('conditionFilter')}
          value={filters.condition}
          options={CONDITIONS.map(([value, label]) => ({ value, label: t(label) }))}
          onChange={(condition) => update({ condition })}
        />
      </PopoverRow>
      <PopoverRow label={t('departmentFilter')} htmlFor="trend-department">
        <select
          id="trend-department"
          className="h-7 min-h-0 py-0 text-xs"
          value={filters.department}
          onChange={(event) => update({ department: event.target.value as TrendDepartment })}
        >
          {DEPARTMENTS.map(([value, label]) => (
            <option key={value || 'all'} value={value}>
              {t(label)}
            </option>
          ))}
        </select>
      </PopoverRow>
      <MenuCheckbox
        label={t('hideLowData')}
        title={t('hideLowDataHelp')}
        checked={filters.hideLowData}
        onChange={(hideLowData) => update({ hideLowData })}
      />
    </>
  );
}
