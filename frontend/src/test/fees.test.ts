import { describe, expect, it } from 'vitest';
import { dollarsToCents, grailedFees, resale, roiPer30Days, scaled } from '@/lib/fees';
import type { FeeSchedule } from '@/lib/types';

// `config/fees.yaml` as `GET /api/fees` serves it; the cases mirror backend/tests/test_fees.py.
const SCHEDULE: FeeSchedule = {
  effective_from: '2026-05-20',
  source: 'https://help.grailed.com',
  commission: {
    tiers: [
      { from_usd: '120', rate: '0.09' },
      { from_usd: '0', rate: '0.06' },
    ],
    minimum_usd: '1.99',
    tier_base: 'price',
  },
  processing: { fixed_usd: '0.49', domestic_rate: '0.0349', international_rate: '0.0499' },
};

describe('grailed fees', () => {
  it.each([
    // 9% from $120, charged on buyer-paid shipping too without a Grailed Label.
    [200, 15, false, false, 1935, 799],
    // 6% below $120.
    [100, 10, false, false, 660, 433],
    // The $1.99 minimum commission.
    [20, 0, false, false, 199, 119],
    // With a Grailed Label the commission skips shipping; processing never does.
    [300, 15, true, false, 2700, 1148],
    [200, 0, false, true, 1800, 1047],
    // The tier starts at exactly $120.
    [120, 0, false, false, 1080, 468],
  ])('follow the schedule: $%s + $%s shipping', (price, ship, label, international, commission, processing) => {
    const fees = grailedFees(SCHEDULE, price * 100, { shipBuyer: ship * 100, grailedLabel: label, international });

    expect(fees).toEqual({ commission, processing, total: commission + processing });
  });

  it('give the net and ROI of a resale', () => {
    const result = resale(SCHEDULE, { buy: 10_000, costsIn: 2_000, expected: 20_000, shipBuyer: 1_500, labelCost: 1_200 });

    expect(result.fees.total).toBe(2_734);
    expect(result.net).toBe(17_566);
    expect(result.roi).toBe(0.4638);
  });

  it('never count a sale as faster than a week', () => {
    expect(roiPer30Days(0.3, 60)).toBe(0.15);
    expect(roiPer30Days(0.3, 2)).toBe(1.2857);
  });

  it.each([
    [{ buy: -100 }],
    [{ expected: 0 }],
    [{ shipBuyer: -500 }],
    [{ buy: 0, costsIn: 0 }],
  ])('reject impossible inputs %o', (overrides) => {
    const input = { buy: 10_000, costsIn: 0, expected: 20_000, shipBuyer: 0, labelCost: 0, ...overrides };

    expect(() => resale(SCHEDULE, input)).toThrow();
  });

  it('parse decimal strings exactly and refuse what is not an amount', () => {
    expect(scaled('0.0349', 1_000_000)).toBe(34_900);
    expect(scaled('120', 100)).toBe(12_000);
    expect(dollarsToCents('12.5')).toBe(1_250);
    expect(dollarsToCents('12,5')).toBe(1_250);
    expect(dollarsToCents('12.345')).toBeNull();
    expect(dollarsToCents('-3')).toBeNull();
    expect(dollarsToCents('')).toBeNull();
  });
});
