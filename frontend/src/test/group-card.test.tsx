import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import GroupPage from '@/app/group/page';
import { monthRows } from '@/components/month-charts';
import type { MonthPoint } from '@/lib/types';
import { renderApp } from '@/test/render';
import { navigation } from '@/test/setup';

const json = (body: unknown) => Promise.resolve(new Response(JSON.stringify(body), { status: 200 }));
const group = {
  id: 7,
  brand_id: 1,
  brand: 'Rick Owens',
  product_type: 'parka',
  slug: 'sisyphus',
  name: 'Sisyphus',
  aliases: [],
  parent_id: null,
  status: 'confirmed',
  source: 'user',
  is_fallback: false,
  kind: 'model',
  parent: null,
  versions: [],
  listings: 40,
};
const price = {
  n: 24,
  gmv: 2_400_000,
  p25: 80_000,
  p50: 100_000,
  p75: 120_000,
  offer_discount: '0.08',
  total_markdown: '0.2',
  days_at_final_price: '6.00',
  fresh_n: 7,
  fresh_ask_p50: 130_000,
  fresh_ask_premium: '0.3',
  approximate: false,
};
const metrics = {
  scope: 'model',
  scope_key: 'model:7',
  group_id: 7,
  brand_id: 1,
  brand: 'Rick Owens',
  product_type: 'parka',
  section: 'outerwear',
  name: 'Sisyphus',
  status: 'confirmed',
  is_fallback: false,
  kind: 'model',
  versions: 0,
  listings: 40,
  sold: 6,
  sold_7d: 1,
  sold_30d: 6,
  sold_prev_30d: 5,
  sold_90d: 24,
  growth: '1.2',
  speed: null,
  trend_score: '30',
  median_days_to_sell: '40.00',
  sell_through_30d: '0.1',
  median_price: 100_000,
  price_change: null,
  active_now: 30,
  new_listings_14d: 2,
  is_new: false,
  first_seen_at: null,
  weekly_sales: Array(12).fill(1),
  expected_days: '30.00',
  price,
};
const month = (value: string, sold: number, partial = false): MonthPoint => ({
  month: value,
  partial,
  sold,
  sold_new: sold - 1,
  sold_used: 1,
  p50: 100_000,
  p50_new: 110_000,
  p50_used: 90_000,
});
const months = [
  ...Array.from({ length: 10 }, (_, index) => month(`2025-${String(index + 1).padStart(2, '0')}`, 4)),
  month('2025-11', 4),
  month('2025-12', 2, true),
];
const overlay = { profile_key: 'type:parka:*', si_abs: [1.5, ...Array(11).fill(1)], months_covered: 11, stable: null };
const fees = {
  effective_from: '2026-05-20',
  source: 'https://help.grailed.com',
  commission: {
    tiers: [
      { from_usd: '0', rate: '0.06' },
      { from_usd: '120', rate: '0.09' },
    ],
    minimum_usd: '1.99',
    tier_base: 'price',
  },
  processing: { fixed_usd: '0.49', domestic_rate: '0.0349', international_rate: '0.0499' },
};

function stubCard() {
  const fetchMock = vi.fn((input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes('/trends/groups/7'))
      return json({
        group,
        metrics,
        computed_at: '2025-12-10T00:00:00Z',
        weekly_median_price: Array(12).fill(100_000),
        colors: [],
        sizes: [
          { value: '48', sold: 9, active: 3, sell_through: '0.75', p30: '0.62', p30_n: 12, hot: true },
          { value: '52', sold: 1, active: 6, sell_through: '0.14', p30: null, p30_n: null, hot: false },
        ],
        versions: [],
        type_metrics: null,
        recent_sales: [],
        active_listings: [],
        months,
        season_overlay: overlay,
      });
    if (url.endsWith('/fees')) return json(fees);
    if (url.endsWith('/groups/7')) return json(group);
    if (url.endsWith('/grouping/taxonomy')) return json({ version: '1', sections: [], types: [] });
    return json({ data: [], total: 0 });
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

describe('model card', () => {
  it('scale the season norm to the model’s average finished month', () => {
    const rows = monthRows(months, overlay, 'en');

    expect(rows[0].norm).toBe(6);
    expect(rows[1].norm).toBe(4);
    expect(rows[11].partial).toBe(true);
    expect(monthRows(months, null, 'en').every((row) => row.norm === null)).toBe(true);
  });

  it('show ask vs sold, hot sizes and switch the condition', async () => {
    const fetchMock = stubCard();
    navigation.search = 'id=7';
    renderApp(<GroupPage />);

    expect(await screen.findByText('Sales by month')).toBeInTheDocument();
    expect(screen.getByText('$800 · $1,000 · $1,200')).toBeInTheDocument();
    expect(screen.getByText('$1,300 (+30%, n=7)')).toBeInTheDocument();
    expect(screen.getByText(/hot/)).toBeInTheDocument();
    expect(screen.getByText('62% · n=12')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: 'New' }));
    await waitFor(() =>
      expect(fetchMock.mock.calls.some(([input]) => String(input).includes('condition=new'))).toBe(true),
    );
  });

  it('price a resale with the served fee schedule', async () => {
    stubCard();
    navigation.search = 'id=7';
    renderApp(<GroupPage />);
    await screen.findByText('Resale calculator');

    await userEvent.type(screen.getByLabelText('Buy price, $'), '100');
    await userEvent.clear(screen.getByLabelText('Inbound costs, $'));
    await userEvent.type(screen.getByLabelText('Inbound costs, $'), '20');
    await userEvent.type(screen.getByLabelText('Sale price, $'), '200');
    await userEvent.clear(screen.getByLabelText('Buyer pays shipping, $'));
    await userEvent.type(screen.getByLabelText('Buyer pays shipping, $'), '15');
    await userEvent.clear(screen.getByLabelText('Label cost, $'));
    await userEvent.type(screen.getByLabelText('Label cost, $'), '12');

    const result = await screen.findByLabelText('Resale result');
    expect(within(result).getByText('$176')).toBeInTheDocument();
    expect(within(result).getByText('+46%')).toBeInTheDocument();
    // ROI 0.4638 over 30 expected days.
    expect(within(result).getByText('+46% · 30 d')).toBeInTheDocument();
    expect(screen.getByText('Fees as of 2026-05-20 (config/fees.yaml).')).toBeInTheDocument();
  });
});
