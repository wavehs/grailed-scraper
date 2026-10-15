/**
 * Calendar periods of the trends list (v2 plan 3.2): seasons, the last 12 months, a custom range.
 * Months are "YYYY-MM" strings in UTC, like the API's ``month_from``/``month_to``.
 */

import type { Locale } from '@/lib/i18n';
import type { PeriodCoverage } from '@/lib/types';

export type Season = 'autumn' | 'winter' | 'spring' | 'summer';
export type TrendPeriod = 'window' | Season | '12m' | 'range';
export type MonthRange = { from: string; to: string };

export const SEASONS: Season[] = ['autumn', 'winter', 'spring', 'summer'];
/** The API rejects longer periods. */
export const MAX_PERIOD_MONTHS = 24;
const SEASON_FIRST_MONTH: Record<Season, number> = { spring: 3, summer: 6, autumn: 9, winter: 12 };
const SEASON_MONTHS = 3;
const YEAR_MONTHS = 12;
const MONTH_PATTERN = /^(\d{4})-(0[1-9]|1[0-2])$/;

/** Months since year 0, so that month arithmetic is plain integer arithmetic. */
function monthIndex(year: number, month: number): number {
  return year * YEAR_MONTHS + (month - 1);
}

function monthKey(index: number): string {
  const year = Math.floor(index / YEAR_MONTHS);
  const month = (index % YEAR_MONTHS) + 1;
  return `${String(year).padStart(4, '0')}-${String(month).padStart(2, '0')}`;
}

function parseMonth(value: string): number | null {
  const match = MONTH_PATTERN.exec(value);
  return match ? monthIndex(Number(match[1]), Number(match[2])) : null;
}

function currentMonth(today: Date): number {
  return monthIndex(today.getUTCFullYear(), today.getUTCMonth() + 1);
}

/** The latest occurrence of the season that has fully ended before the current month. */
export function seasonRange(season: Season, today: Date): MonthRange {
  const current = currentMonth(today);
  let first = monthIndex(today.getUTCFullYear(), SEASON_FIRST_MONTH[season]);
  while (first + SEASON_MONTHS - 1 >= current) first -= YEAR_MONTHS;
  return { from: monthKey(first), to: monthKey(first + SEASON_MONTHS - 1) };
}

/** The twelve complete months before the current one. */
export function lastTwelveMonths(today: Date): MonthRange {
  const current = currentMonth(today);
  return { from: monthKey(current - YEAR_MONTHS), to: monthKey(current - 1) };
}

/** Number of months in a custom range, or null when it is not a valid ascending range. */
export function rangeLength(from: string, to: string): number | null {
  const first = parseMonth(from);
  const last = parseMonth(to);
  if (first === null || last === null || first > last) return null;
  return last - first + 1;
}

export function isValidRange(from: string, to: string): boolean {
  const length = rangeLength(from, to);
  return length !== null && length <= MAX_PERIOD_MONTHS;
}

/** The months to request, or null when the list keeps its 7/30/90-day window. */
export function periodRange(
  period: TrendPeriod,
  monthFrom: string,
  monthTo: string,
  today: Date = new Date(),
): MonthRange | null {
  if (period === 'window') return null;
  if (period === '12m') return lastTwelveMonths(today);
  if (period === 'range') return isValidRange(monthFrom, monthTo) ? { from: monthFrom, to: monthTo } : null;
  return seasonRange(period, today);
}

function monthName(value: string, locale: Locale): string {
  const index = parseMonth(value);
  if (index === null) return value;
  const date = new Date(Date.UTC(Math.floor(index / YEAR_MONTHS), index % YEAR_MONTHS, 1));
  return new Intl.DateTimeFormat(locale, { month: 'long', timeZone: 'UTC' }).format(date);
}

function yearOf(value: string): string {
  return value.slice(0, 4);
}

/** "сентябрь 2025" / "September 2025". */
export function monthLabel(value: string, locale: Locale): string {
  return `${monthName(value, locale)} ${yearOf(value)}`;
}

/** "осень 2025", "зима 2025/26", or "окт 2025 – сен 2026" for other ranges. */
export function periodLabel(range: MonthRange, seasonName: string | null, locale: Locale): string {
  const fromYear = yearOf(range.from);
  const toYear = yearOf(range.to);
  if (seasonName) {
    return fromYear === toYear ? `${seasonName} ${fromYear}` : `${seasonName} ${fromYear}/${toYear.slice(2)}`;
  }
  if (range.from === range.to) return monthLabel(range.from, locale);
  return `${monthLabel(range.from, locale)} – ${monthLabel(range.to, locale)}`;
}

export type CoverageTexts = { noData: string; fromDay: string; toDay: string; lateBrands: string };

/**
 * One line about the months the collected data does not cover, e.g.
 * "сентябрь 2025 — нет данных · октябрь 2025 с 9-го"; empty when the whole period is covered.
 * ``texts`` carry the localized templates with ``{month}``, ``{day}`` and ``{count}``.
 */
export function coverageLine(coverage: PeriodCoverage, texts: CoverageTexts, locale: Locale): string {
  const parts = coverage.missing.map((month) => texts.noData.replace('{month}', monthLabel(month, locale)));
  for (const item of coverage.partial) {
    const month = monthLabel(item.month, locale);
    if (item.from_day > 1) parts.push(texts.fromDay.replace('{month}', month).replace('{day}', String(item.from_day)));
    else parts.push(texts.toDay.replace('{month}', month).replace('{day}', String(item.to_day)));
  }
  if (coverage.late_brands > 0) parts.push(texts.lateBrands.replace('{count}', String(coverage.late_brands)));
  return parts.join(' · ');
}
