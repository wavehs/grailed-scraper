import { describe, expect, it } from 'vitest';
import {
  draftFromPolicy,
  draftToUpdate,
  isLiquiditySettings,
  periodLabel,
  sameDraft,
  weightShares,
  withPeriod,
} from '@/lib/liquidity';

const t = (key: string) =>
  ({ periodDays: '{n} d', periodMonths: '{n} mo', periodYears: '{n} yr' })[key] ?? key;
const policy = {
  period_days: 30,
  min_sales_per_month: '2.5',
  min_price_usd: 150,
  excluded_sections: ['other', 'dresses'],
  weights: { sales: '0.4000', speed: '0.2500', likes: '0.2000', price: '0.1500' },
};

describe('liquidity helpers', () => {
  it('labels periods in days, months and years', () => {
    expect([7, 30, 90, 180, 365].map((days) => periodLabel(days, t))).toEqual([
      '7 d',
      '30 d',
      '90 d',
      '6 mo',
      '1 yr',
    ]);
  });

  it('turns a policy into a percent draft and back into a PUT body', () => {
    const draft = draftFromPolicy(policy);
    expect(draft).toEqual({
      minSales: '2.5',
      minPrice: '150',
      excluded: ['dresses', 'other'],
      weights: { sales: '40', speed: '25', likes: '20', price: '15' },
    });
    expect(sameDraft(draft, { ...draft, excluded: ['other', 'dresses'] })).toBe(true);
    expect(draftToUpdate(draft, 60).body).toEqual({
      period_days: 60,
      min_sales_per_month: 2.5,
      min_price_usd: 150,
      excluded_sections: ['dresses', 'other'],
      weights: { sales: 40, speed: 25, likes: 20, price: 15 },
    });
  });

  it('rejects empty, negative, fractional and all-zero values', () => {
    const draft = draftFromPolicy(policy);
    const zero = { sales: '0', speed: '0', likes: '0', price: '0' };
    expect(draftToUpdate({ ...draft, minSales: '' }, 30).error).toBe('liquidityInvalidMinSales');
    expect(draftToUpdate({ ...draft, minSales: '-1' }, 30).error).toBe('liquidityInvalidMinSales');
    expect(draftToUpdate({ ...draft, minPrice: '9.5' }, 30).error).toBe('liquidityInvalidMinPrice');
    expect(draftToUpdate({ ...draft, weights: { ...zero, sales: '101' } }, 30).error).toBe(
      'liquidityInvalidWeight',
    );
    expect(draftToUpdate({ ...draft, weights: zero }, 30).error).toBe('liquidityWeightsZero');
  });

  it('shows normalized shares of weights that do not add up to 100', () => {
    const draft = draftFromPolicy(policy);
    const result = weightShares({
      ...draft,
      weights: { sales: '50', speed: '30', likes: '20', price: '0' },
    });
    expect(result?.total).toBe(100);
    expect(weightShares({ ...draft, weights: { ...draft.weights, sales: '60' } })?.shares.sales).toBe(
      0.5,
    );
    expect(weightShares({ ...draft, weights: { ...draft.weights, sales: 'x' } })).toBeNull();
  });

  it('keeps the saved policy when only the period changes', () => {
    expect(withPeriod(policy, 365)).toEqual({ ...policy, period_days: 365 });
  });

  it('accepts only a settings-shaped response', () => {
    expect(isLiquiditySettings({})).toBe(false);
    expect(isLiquiditySettings(null)).toBe(false);
    expect(
      isLiquiditySettings({ ...policy, saved: false, periods: [30], defaults: policy, ranked: null }),
    ).toBe(true);
  });
});
