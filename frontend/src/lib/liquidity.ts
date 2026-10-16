import type {
  LiquidityPolicy,
  LiquidityPolicyUpdate,
  LiquiditySettings,
  LiquidityWeightKey,
} from '@/lib/types';

export const WEIGHT_KEYS: readonly LiquidityWeightKey[] = ['sales', 'speed', 'likes', 'price'];
export const MAX_WEIGHT = 100;
const DAYS_PER_MONTH = 30;
const DAYS_PER_YEAR = 365;
/** Periods this long and longer read better in months. */
const MONTHS_FROM_DAYS = 180;

type Translate = (key: string) => string;

/** A short period label: "30 d", "6 mo", "1 yr". */
export function periodLabel(days: number, t: Translate): string {
  if (days % DAYS_PER_YEAR === 0)
    return t('periodYears').replace('{n}', String(days / DAYS_PER_YEAR));
  if (days >= MONTHS_FROM_DAYS && days % DAYS_PER_MONTH === 0)
    return t('periodMonths').replace('{n}', String(days / DAYS_PER_MONTH));
  return t('periodDays').replace('{n}', String(days));
}

/** The API response is external data: use it only when it has the expected shape. */
export function isLiquiditySettings(value: unknown): value is LiquiditySettings {
  if (!value || typeof value !== 'object') return false;
  const data = value as Partial<LiquiditySettings>;
  return (
    typeof data.period_days === 'number' &&
    Array.isArray(data.periods) &&
    Array.isArray(data.excluded_sections) &&
    typeof data.weights === 'object' &&
    data.weights !== null
  );
}

/** The form state: every number as typed, weights in percent. */
export type LiquidityDraft = {
  minSales: string;
  minPrice: string;
  excluded: string[];
  weights: Record<LiquidityWeightKey, string>;
};

const trimNumber = (value: number) => String(Number(value.toFixed(2)));

export function draftFromPolicy(policy: LiquidityPolicy): LiquidityDraft {
  return {
    minSales: trimNumber(Number(policy.min_sales_per_month)),
    minPrice: String(policy.min_price_usd),
    excluded: [...policy.excluded_sections].sort(),
    weights: {
      sales: trimNumber(Number(policy.weights.sales) * 100),
      speed: trimNumber(Number(policy.weights.speed) * 100),
      likes: trimNumber(Number(policy.weights.likes) * 100),
      price: trimNumber(Number(policy.weights.price) * 100),
    },
  };
}

export function sameDraft(left: LiquidityDraft, right: LiquidityDraft): boolean {
  const normalize = (draft: LiquidityDraft) =>
    JSON.stringify({ ...draft, excluded: [...draft.excluded].sort() });
  return normalize(left) === normalize(right);
}

function parseNumber(raw: string): number | null {
  if (raw.trim() === '') return null;
  const value = Number(raw);
  return Number.isFinite(value) ? value : null;
}

const isWeight = (value: number | null): value is number =>
  value !== null && value >= 0 && value <= MAX_WEIGHT;

export type DraftCheck =
  { body: LiquidityPolicyUpdate; error: null } | { body: null; error: string };

/** The PUT body for the draft, or the i18n key of the first problem. */
export function draftToUpdate(draft: LiquidityDraft, periodDays: number): DraftCheck {
  const minSales = parseNumber(draft.minSales);
  if (minSales === null || minSales < 0) return { body: null, error: 'liquidityInvalidMinSales' };
  const minPrice = parseNumber(draft.minPrice);
  if (minPrice === null || minPrice < 0 || !Number.isInteger(minPrice))
    return { body: null, error: 'liquidityInvalidMinPrice' };
  const weights = WEIGHT_KEYS.map((key) => parseNumber(draft.weights[key]));
  if (!weights.every(isWeight)) return { body: null, error: 'liquidityInvalidWeight' };
  if (!weights.some((value) => value > 0)) return { body: null, error: 'liquidityWeightsZero' };
  const [sales, speed, likes, price] = weights;
  return {
    body: {
      period_days: periodDays,
      min_sales_per_month: minSales,
      min_price_usd: minPrice,
      excluded_sections: [...draft.excluded],
      weights: { sales, speed, likes, price },
    },
    error: null,
  };
}

/** The saved policy with another ranking period. */
export function withPeriod(policy: LiquidityPolicy, periodDays: number): LiquidityPolicyUpdate {
  return {
    period_days: periodDays,
    min_sales_per_month: policy.min_sales_per_month,
    min_price_usd: policy.min_price_usd,
    excluded_sections: [...policy.excluded_sections],
    weights: { ...policy.weights },
  };
}

/** The share of each weight in the total (0..1), or null while a weight is not a valid number. */
export function weightShares(
  draft: LiquidityDraft,
): { total: number; shares: Record<LiquidityWeightKey, number> } | null {
  const values = WEIGHT_KEYS.map((key) => parseNumber(draft.weights[key]));
  if (!values.every(isWeight)) return null;
  const total = values.reduce((sum, value) => sum + value, 0);
  const share = (index: number) => (total > 0 ? values[index] / total : 0);
  return {
    total,
    shares: { sales: share(0), speed: share(1), likes: share(2), price: share(3) },
  };
}
