/**
 * Grailed seller fees and a resale's net and ROI — the frontend twin of
 * `backend/app/services/metrics/fees.py` (v2 plan 2.6/3.4), tested on the same cases.
 *
 * Amounts are integer cents and rates integer millionths, so rounding matches Python's
 * `Decimal.quantize(ROUND_HALF_UP)` exactly. The schedule comes from `GET /api/fees`.
 */
import type { FeeSchedule } from '@/lib/types';

const RATE_SCALE = 1_000_000;
const RATIO_SCALE = 10_000;
const MIN_DAYS_TO_SELL = 7;
const THIRTY_DAYS = 30;

export type Fees = { commission: number; processing: number; total: number };
export type Resale = { fees: Fees; net: number; roi: number };
export type FeeOptions = { shipBuyer?: number; grailedLabel?: boolean; international?: boolean };
export type ResaleInput = {
  buy: number;
  costsIn: number;
  expected: number;
  shipBuyer: number;
  labelCost: number;
  grailedLabel?: boolean;
  international?: boolean;
};

/** "0.0349" → 34 900 at a scale of 1e6; "1.99" → 199 at 100. Throws on anything else. */
export function scaled(value: string, scale: number): number {
  const match = /^(\d+)(?:\.(\d+))?$/.exec(value.trim());
  if (!match) throw new Error(`not a decimal: ${value}`);
  const digits = String(scale).length - 1;
  const fraction = (match[2] ?? '').padEnd(digits, '0');
  if (fraction.length > digits && /[1-9]/.test(fraction.slice(digits))) {
    throw new Error(`too precise: ${value}`);
  }
  return Number(match[1]) * scale + Number(fraction.slice(0, digits) || 0);
}

/** Dollars typed by a person ("12.5" or "12,5") to cents; null when it is not an amount. */
export function dollarsToCents(value: string): number | null {
  try {
    return scaled(value.replace(',', '.'), 100);
  } catch {
    return null;
  }
}

/** `numerator / denominator` rounded half away from zero, like ROUND_HALF_UP. */
function divideRounded(numerator: number, denominator: number): number {
  const quotient = Math.floor((Math.abs(numerator) * 2 + denominator) / (denominator * 2));
  return numerator < 0 ? -quotient : quotient;
}

function require(condition: boolean, message: string): void {
  if (!condition) throw new Error(message);
}

function commissionRate(schedule: FeeSchedule, baseCents: number): number {
  const tiers = [...schedule.commission.tiers].sort((a, b) => scaled(a.from_usd, 100) - scaled(b.from_usd, 100));
  let rate = scaled(tiers[0].rate, RATE_SCALE);
  for (const tier of tiers) if (baseCents >= scaled(tier.from_usd, 100)) rate = scaled(tier.rate, RATE_SCALE);
  return rate;
}

export function grailedFees(schedule: FeeSchedule, price: number, options: FeeOptions = {}): Fees {
  const { shipBuyer = 0, grailedLabel = false, international = false } = options;
  require(price > 0, 'price must be positive');
  require(shipBuyer >= 0, 'shipping cannot be negative');
  const withShipping = price + (grailedLabel ? 0 : shipBuyer);
  const base = schedule.commission.tier_base === 'price' ? price : withShipping;
  const commission = Math.max(
    scaled(schedule.commission.minimum_usd, 100),
    divideRounded(commissionRate(schedule, base) * withShipping, RATE_SCALE),
  );
  const rates = schedule.processing;
  const rate = scaled(international ? rates.international_rate : rates.domestic_rate, RATE_SCALE);
  const processing = divideRounded(rate * (price + shipBuyer), RATE_SCALE) + scaled(rates.fixed_usd, 100);
  return { commission, processing, total: commission + processing };
}

/** `net = E + ship_buyer − fees − label_cost`; `ROI = net / (B + T_in) − 1`, four places. */
export function resale(schedule: FeeSchedule, input: ResaleInput): Resale {
  const { buy, costsIn, expected, shipBuyer, labelCost } = input;
  require(Math.min(buy, costsIn, labelCost) >= 0, 'costs cannot be negative');
  require(buy + costsIn > 0, 'the purchase must cost something');
  const fees = grailedFees(schedule, expected, {
    shipBuyer,
    grailedLabel: input.grailedLabel,
    international: input.international,
  });
  const net = expected + shipBuyer - fees.total - labelCost;
  const cost = buy + costsIn;
  return { fees, net, roi: divideRounded((net - cost) * RATIO_SCALE, cost) / RATIO_SCALE };
}

/** ROI scaled to a 30-day turn: `ROI · 30 / max(E[T], 7)`. */
export function roiPer30Days(roi: number, daysToSell: number): number {
  const roiUnits = Math.round(roi * RATIO_SCALE);
  const days = Math.round(Math.max(daysToSell, MIN_DAYS_TO_SELL) * 100);
  return divideRounded(roiUnits * THIRTY_DAYS * 100, days) / RATIO_SCALE;
}
