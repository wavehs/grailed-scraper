'use client';

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

function toggleClass(active: boolean): string {
  return `rounded-md px-2.5 py-1 text-xs tabular-nums ${
    active ? 'bg-[var(--accent-soft)] font-semibold text-[var(--accent)]' : 'text-[var(--text-secondary)]'
  }`;
}

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
      <div className="inline-flex rounded-lg border border-[var(--border-default)] p-0.5" role="group" aria-label={t('trendWindow')}>
        {WINDOWS.map((value) => {
          const active = filters.period === 'window' && filters.window === value;
          return (
            <button
              key={value}
              type="button"
              aria-pressed={active}
              onClick={() => update({ window: value, period: 'window' })}
              className={toggleClass(active)}
            >
              {value} {t('daysShort')}
            </button>
          );
        })}
      </div>
      <div className="inline-flex rounded-lg border border-[var(--border-default)] p-0.5" role="group" aria-label={t('trendPeriod')}>
        {PRESETS.map(([value, label]) => (
          <button
            key={value}
            type="button"
            aria-pressed={filters.period === value}
            onClick={() => update({ period: value })}
            className={toggleClass(filters.period === value)}
          >
            {t(label)}
          </button>
        ))}
      </div>
      {filters.period === 'range' && (
        <span className="flex items-center gap-1 text-xs text-[var(--text-muted)]">
          <input
            type="month"
            aria-label={t('periodFrom')}
            className="text-sm"
            value={filters.monthFrom}
            onChange={(event) => update({ monthFrom: event.target.value })}
          />
          –
          <input
            type="month"
            aria-label={t('periodTo')}
            className="text-sm"
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
      <span className="font-medium">{periodLabel(months, season?.toLocaleLowerCase(locale) ?? null, locale)}</span>
      {gaps && <span className="text-[var(--warning)]"> · {gaps}</span>}
      <span className="text-[var(--text-muted)]"> · {t('periodPriceNote')}</span>
    </p>
  );
}
