import { describe, expect, it } from 'vitest';
import {
  coverageLine,
  isValidRange,
  lastTwelveMonths,
  periodLabel,
  periodRange,
  seasonRange,
} from '@/lib/periods';
import type { PeriodCoverage } from '@/lib/types';

const OCT_15_2026 = new Date(Date.UTC(2026, 9, 15));
const TEXTS = {
  noData: '{month} — no data',
  fromDay: '{month} from day {day}',
  toDay: '{month} to day {day}',
  lateBrands: 'brands starting later: {count}',
};

describe('season presets', () => {
  it('pick the last season that has fully ended', () => {
    expect(seasonRange('autumn', OCT_15_2026)).toEqual({ from: '2025-09', to: '2025-11' });
    expect(seasonRange('summer', OCT_15_2026)).toEqual({ from: '2026-06', to: '2026-08' });
    expect(seasonRange('spring', OCT_15_2026)).toEqual({ from: '2026-03', to: '2026-05' });
    expect(seasonRange('winter', OCT_15_2026)).toEqual({ from: '2025-12', to: '2026-02' });
  });

  it('skip a season that ends in the current month', () => {
    const lateNovember = new Date(Date.UTC(2026, 10, 30));
    const december = new Date(Date.UTC(2026, 11, 1));

    expect(seasonRange('autumn', lateNovember)).toEqual({ from: '2025-09', to: '2025-11' });
    expect(seasonRange('autumn', december)).toEqual({ from: '2026-09', to: '2026-11' });
  });

  it('cross the year for winter in January', () => {
    expect(seasonRange('winter', new Date(Date.UTC(2027, 0, 10)))).toEqual({ from: '2025-12', to: '2026-02' });
  });
});

describe('period ranges', () => {
  it('take the twelve complete months before the current one', () => {
    expect(lastTwelveMonths(OCT_15_2026)).toEqual({ from: '2025-10', to: '2026-09' });
    expect(lastTwelveMonths(new Date(Date.UTC(2026, 0, 1)))).toEqual({ from: '2025-01', to: '2025-12' });
  });

  it('keep the day window or resolve a preset', () => {
    expect(periodRange('window', '', '', OCT_15_2026)).toBeNull();
    expect(periodRange('12m', '', '', OCT_15_2026)).toEqual({ from: '2025-10', to: '2026-09' });
    expect(periodRange('autumn', '', '', OCT_15_2026)).toEqual({ from: '2025-09', to: '2025-11' });
  });

  it('accept only an ascending custom range of at most 24 months', () => {
    expect(periodRange('range', '2026-01', '2026-03', OCT_15_2026)).toEqual({ from: '2026-01', to: '2026-03' });
    expect(periodRange('range', '2026-03', '2026-01', OCT_15_2026)).toBeNull();
    expect(periodRange('range', '2026-01', '', OCT_15_2026)).toBeNull();
    expect(isValidRange('2024-01', '2025-12')).toBe(true);
    expect(isValidRange('2024-01', '2026-01')).toBe(false);
    expect(isValidRange('2026-13', '2026-12')).toBe(false);
  });
});

describe('period labels', () => {
  it('name seasons with one or two years', () => {
    expect(periodLabel({ from: '2025-09', to: '2025-11' }, 'осень', 'ru')).toBe('осень 2025');
    expect(periodLabel({ from: '2025-12', to: '2026-02' }, 'зима', 'ru')).toBe('зима 2025/26');
  });

  it('name other ranges by their months', () => {
    expect(periodLabel({ from: '2025-10', to: '2026-09' }, null, 'en')).toBe('October 2025 – September 2026');
    expect(periodLabel({ from: '2026-03', to: '2026-03' }, null, 'ru')).toBe('март 2026');
  });
});

describe('coverage line', () => {
  const coverage: PeriodCoverage = {
    month_from: '2025-09',
    month_to: '2025-11',
    data_from: '2025-10-09',
    data_to: '2026-10-15',
    missing: ['2025-09'],
    partial: [{ month: '2025-10', from_day: 9, to_day: 31 }],
    late_brands: 0,
  };

  it('lists missing and partial months', () => {
    expect(coverageLine(coverage, TEXTS, 'en')).toBe('September 2025 — no data · October 2025 from day 9');
  });

  it('reports a month cut at its end and brands that start later', () => {
    const current = {
      ...coverage,
      missing: [],
      partial: [{ month: '2026-10', from_day: 1, to_day: 15 }],
      late_brands: 2,
    };

    expect(coverageLine(current, TEXTS, 'en')).toBe('October 2026 to day 15 · brands starting later: 2');
  });

  it('is empty when the data covers the whole period', () => {
    expect(coverageLine({ ...coverage, missing: [], partial: [] }, TEXTS, 'en')).toBe('');
  });
});
