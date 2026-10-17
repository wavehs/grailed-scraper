import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import TrendsPage from '@/app/trends/page';
import { formatRatio } from '@/components/liquidity-cells';
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
  sold: 12,
  sold_7d: 3,
  sold_30d: 12,
  sold_prev_30d: 10,
  sold_90d: 30,
  growth: '1.1800',
  speed: '0.6000',
  trend_score: '40.00',
  median_days_to_sell: '20.00',
  sell_through_30d: '0.100000',
  median_price: 50000,
  price_change: null,
  active_now: 90,
  new_listings_14d: 4,
  is_new: false,
  first_seen_at: null,
  weekly_sales: [1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1],
  liquidity_score: '81.50',
  p30: '0.300000',
  p30_raw: '0.300000',
  p30_n: 20,
  expected_days: '84.11',
  active_effective: 30,
  active_fresh: 12,
  gmv_90d: 1_500_000,
  department: 'menswear',
  price: {
    n: 3,
    gmv: 180_000,
    p25: 45_000,
    p50: 50_000,
    p75: 56_000,
    offer_discount: null,
    total_markdown: null,
    days_at_final_price: null,
    fresh_n: 5,
    fresh_ask_p50: 70_000,
    fresh_ask_premium: '0.4000',
    approximate: true,
  },
};

beforeEach(() => {
  window.localStorage.clear();
  window.sessionStorage.clear();
});

describe('trends liquidity columns', () => {
  it('shows LS, P30, ask vs sold, supply breakdown and GMV', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes('/trends?')) return json({ data: [row], total: 1, computed_at: '2026-10-15T00:00:00Z' });
        if (url.endsWith('/brands')) return json({ data: [] });
        return json({});
      }),
    );
    renderApp(<TrendsPage />);

    expect(await screen.findByText('82')).toBeInTheDocument();
    expect(screen.getByText('30%')).toBeInTheDocument();
    expect(screen.getByText(/n=20 · 84 expected days/)).toBeInTheDocument();
    expect(screen.getByText('≈ $500')).toBeInTheDocument();
    expect(screen.getByText('fresh asks +40%')).toBeInTheDocument();
    expect(screen.getByText('30 effective · 12 fresh')).toBeInTheDocument();
    expect(screen.getByText('$15,000')).toBeInTheDocument();
  });

  it('sends condition, department, low-data and liquidity sort to the API', async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/trends?')) return json({ data: [row], total: 1, computed_at: '2026-10-15T00:00:00Z' });
      if (url.endsWith('/brands')) return json({ data: [] });
      return json({});
    });
    vi.stubGlobal('fetch', fetchMock);
    renderApp(<TrendsPage />);
    await screen.findByText('82');

    await userEvent.click(screen.getByRole('button', { name: 'Filter' }));
    await userEvent.click(screen.getByRole('button', { name: 'Used' }));
    await userEvent.selectOptions(screen.getByLabelText('Department'), 'womenswear');
    await userEvent.click(screen.getByLabelText('Hide low data'));
    await userEvent.click(screen.getByRole('button', { name: 'View' }));
    await userEvent.selectOptions(screen.getByLabelText('Sort by'), 'liquidity');

    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(([input]) => {
          const url = String(input);
          return (
            url.includes('condition=used') &&
            url.includes('department=womenswear') &&
            url.includes('hide_low_data=true') &&
            url.includes('sort=liquidity')
          );
        }),
      ).toBe(true),
    );
  });

  it('formats ratios as whole signed percentages', () => {
    expect(formatRatio('1.5', true)).toBe('+150%');
    expect(formatRatio('-0.124', true)).toBe('-12%');
    expect(formatRatio(null)).toBe('—');
  });
});
