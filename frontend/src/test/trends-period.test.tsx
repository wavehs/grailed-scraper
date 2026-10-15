import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import TrendsPage from '@/app/trends/page';
import { lastTwelveMonths, seasonRange } from '@/lib/periods';
import { renderApp } from '@/test/render';

const json = (body: unknown) => Promise.resolve(new Response(JSON.stringify(body), { status: 200 }));
const row = {
  scope: 'model',
  scope_key: 'model:7',
  group_id: 7,
  brand_id: 1,
  brand: 'Rick Owens',
  product_type: 'hightop_sneakers',
  section: 'footwear',
  name: 'Geobasket',
  status: 'confirmed',
  is_fallback: false,
  kind: 'model',
  versions: 0,
  listings: 120,
  sold: 41,
  sold_7d: 3,
  sold_30d: 12,
  sold_prev_30d: 10,
  sold_90d: 30,
  growth: '1.1800',
  speed: '0.6000',
  trend_score: '40.00',
  median_days_to_sell: '20.00',
  sell_through_30d: '0.100000',
  median_price: 61_234,
  price_change: '0.2500',
  active_now: 90,
  new_listings_14d: 4,
  is_new: false,
  first_seen_at: null,
  weekly_sales: [1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1],
  liquidity_score: null,
  p30: null,
  p30_raw: null,
  p30_n: null,
  expected_days: null,
  active_effective: null,
  active_fresh: null,
  gmv_90d: null,
  department: null,
  price: null,
};

function stubTrends() {
  const fetchMock = vi.fn((input: RequestInfo | URL) => {
    const url = new URL(String(input), 'http://localhost');
    if (url.pathname.endsWith('/trends')) {
      const from = url.searchParams.get('month_from');
      const to = url.searchParams.get('month_to');
      const period = from
        ? {
            month_from: from,
            month_to: to,
            data_from: '2025-10-09',
            data_to: '2026-10-15',
            missing: [from],
            partial: [],
            late_brands: 1,
          }
        : null;
      return json({ data: [row], total: 1, computed_at: '2026-10-15T00:00:00Z', period });
    }
    if (url.pathname.endsWith('/brands')) return json({ data: [] });
    return json({});
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

function requested(fetchMock: ReturnType<typeof stubTrends>, params: Record<string, string>): boolean {
  return fetchMock.mock.calls.some(([input]) => {
    const url = new URL(String(input), 'http://localhost');
    return Object.entries(params).every(([key, value]) => url.searchParams.get(key) === value);
  });
}

beforeEach(() => {
  window.localStorage.clear();
  window.sessionStorage.clear();
});

describe('trends period presets', () => {
  it('request the last finished season and show its coverage', async () => {
    const fetchMock = stubTrends();
    renderApp(<TrendsPage />);
    await screen.findByText('Sales, 30 d');
    const autumn = seasonRange('autumn', new Date());

    await userEvent.click(screen.getByRole('button', { name: 'Autumn' }));

    await waitFor(() =>
      expect(requested(fetchMock, { month_from: autumn.from, month_to: autumn.to })).toBe(true),
    );
    expect(await screen.findByText('Sales in period')).toBeInTheDocument();
    expect(screen.getByText(`autumn ${autumn.from.slice(0, 4)}`)).toBeInTheDocument();
    expect(screen.getByText(/— no data · brands collected later: 1/)).toBeInTheDocument();
    expect(screen.getByText('≈ $612')).toBeInTheDocument();
    // The window's price change is hidden for a calendar period.
    expect(screen.queryByText('+25%')).not.toBeInTheDocument();
  });

  it('send a custom range only when it is valid and return to the day window', async () => {
    const fetchMock = stubTrends();
    renderApp(<TrendsPage />);
    await screen.findByText('Sales, 30 d');

    await userEvent.click(screen.getByRole('button', { name: '12 mo' }));
    const year = lastTwelveMonths(new Date());
    await waitFor(() => expect(requested(fetchMock, { month_from: year.from, month_to: year.to })).toBe(true));

    await userEvent.click(screen.getByRole('button', { name: 'Months' }));
    await userEvent.type(screen.getByLabelText('First month'), '2026-05');
    await userEvent.type(screen.getByLabelText('Last month'), '2026-03');
    expect(await screen.findByText('Choose up to 24 months in order')).toBeInTheDocument();
    expect(requested(fetchMock, { month_from: '2026-05' })).toBe(false);

    await userEvent.clear(screen.getByLabelText('Last month'));
    await userEvent.type(screen.getByLabelText('Last month'), '2026-07');
    await waitFor(() => expect(requested(fetchMock, { month_from: '2026-05', month_to: '2026-07' })).toBe(true));

    fetchMock.mockClear();
    await userEvent.click(screen.getByRole('button', { name: '90 d' }));
    await waitFor(() => expect(requested(fetchMock, { window: '90' })).toBe(true));
    expect(fetchMock.mock.calls.some(([input]) => String(input).includes('month_from'))).toBe(false);
  });
});
