'use client';

import { Segmented } from '@/components/ui/segmented';
import { useI18n } from '@/lib/i18n';
import {
  SEASONS,
  coverageLine,
  isValidRange,
  periodLabel,
  periodRange,
  type Season,
  type TrendPeriod,
} from '@/lib/periods';
import type { PeriodCoverage, TrendFilters } from '@/lib/types';

const WINDOWS = [7, 30, 90] as const;
const PRESETS: Array<[TrendPeriod, string]> = [
  ...SEASONS.map((season): [TrendPeriod, string] => [season, `season_${season}`]),
  ['12m', 'periodLast12'],
  ['range', 'periodRange'],
];

/** The 7/30/90-day window or a calendar period: a season, the last 12 months or a month range. */
export function PeriodPicker({
  filters,
  update,
}: {
  filters: TrendFilters;
  update: (patch: Partial<TrendFilters>) => void;
}) {
  const { t } = useI18n();
  const rangeInvalid =
    filters.period === 'range' &&
    Boolean(filters.monthFrom && filters.monthTo) &&
    !isValidRange(filters.monthFrom, filters.monthTo);
  return (
    <div className="flex flex-wrap items-center gap-2">
      <Segmented
        label={t('trendWindow')}
        value={filters.period === 'window' ? filters.window : null}
        options={WINDOWS.map((value) => ({ value, label: `${value} ${t('daysShort')}` }))}
        onChange={(value) => update({ window: value, period: 'window' })}
      />
      <Segmented
        label={t('trendPeriod')}
        value={filters.period}
        options={PRESETS.map(([value, label]) => ({ value, label: t(label) }))}
        onChange={(period) => update({ period })}
      />
      {filters.period === 'range' && (
        <span className="flex items-center gap-1 text-xs text-[var(--text-muted)]">
          <input
            type="month"
            aria-label={t('periodFrom')}
            className="h-7 min-h-0 py-0 text-xs"
            value={filters.monthFrom}
            onChange={(event) => update({ monthFrom: event.target.value })}
          />
          –
          <input
            type="month"
            aria-label={t('periodTo')}
            className="h-7 min-h-0 py-0 text-xs"
            value={filters.monthTo}
            onChange={(event) => update({ monthTo: event.target.value })}
          />
          {rangeInvalid && <span className="text-[var(--danger)]">{t('periodInvalid')}</span>}
        </span>
      )}
    </div>
  );
}

/** The chosen period and the months of it that the collected sales do not cover. */
export function PeriodCoverageLine({ filters, coverage }: { filters: TrendFilters; coverage?: PeriodCoverage | null }) {
  const { locale, t } = useI18n();
  const months = periodRange(filters.period, filters.monthFrom, filters.monthTo);
  if (!months || !coverage) return null;
  const season = (SEASONS as string[]).includes(filters.period) ? t(`season_${filters.period as Season}`) : null;
  const gaps = coverageLine(
    coverage,
    {
      noData: t('coverageNoData'),
      fromDay: t('coverageFromDay'),
      toDay: t('coverageToDay'),
      lateBrands: t('coverageLateBrands'),
    },
    locale,
  );
  return (
    <p className="text-xs text-[var(--text-secondary)]" role="status">
      <span className="text-[var(--text-body)]">{periodLabel(months, season?.toLocaleLowerCase(locale) ?? null, locale)}</span>
      {gaps && <span className="text-[var(--warning)]"> · {gaps}</span>}
      <span className="text-[var(--text-muted)]"> · {t('periodPriceNote')}</span>
    </p>
  );
}
