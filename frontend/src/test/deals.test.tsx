import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import DealsPage from '@/app/deals/page';
import { fill } from '@/components/deal-scanner';
import { messages } from '@/lib/i18n';
import { dealsEn, dealsRu } from '@/lib/i18n-deals';
import type { DealItem, DealsResponse } from '@/lib/types';
import { renderApp } from '@/test/render';

const json = (body: unknown, status = 200) =>
  Promise.resolve(new Response(JSON.stringify(body), { status }));
const health = { status: 'ok', service: 'test', source_mode: 'live', request_id: 'test' };
const deal = (id: number, verdict: DealItem['verdict']): DealItem => ({
  id,
  listing_id: id,
  grailed_id: 1000 + id,
  title: `Geobasket ${id}`,
  url: `https://www.grailed.com/listings/${1000 + id}`,
  photo: null,
  size: '43',
  condition: 'is_gently_used',
  listing_status: 'active',
  seller_rating: '4.9',
  seller_ratings: 20,
  brand: 'Rick Owens',
  group_id: 7,
  group: 'Geobasket',
  bucket: 'used',
  price: 30_000,
  shipping: 1_500,
  p25: 35_000,
  p50: 50_000,
  net: 9_025,
  roi: '0.2865',
  liquidity_score: '70.0',
  found_at: '2026-10-15T10:00:00Z',
  updated_at: '2026-10-15T10:00:00Z',
  verdict,
});
const response = (items: DealItem[]): DealsResponse => ({
  items,
  total: items.length,
  summary: { total: 3, good: 2, bad: 1, unrated: 0, hit_rate: '0.6667' },
  scanner: {
    enabled: false,
    interval_minutes: 20,
    scope: 'all',
    running: false,
    last: {
      started_at: '2026-10-15T10:00:00Z',
      finished_at: '2026-10-15T10:01:00Z',
      brands: 5,
      hits: 3400,
      stored: 3400,
      failed: {},
      truncated: ['Rick Owens'],
      deals: { evaluated: 3400, deals: 2, new: 2, dropped: 0, reasons: {} },
    },
    last_error: null,
    skipped_at: null,
    brands: [{ brand_id: 1, brand: 'Rick Owens', scanned_to: '2026-10-15T10:00:00Z', hits: 12, error: null }],
  },
});

function stubFetch(items: DealItem[]) {
  const calls: { url: string; method: string; body?: string }[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn((url: string, init?: RequestInit) => {
      const method = init?.method ?? 'GET';
      calls.push({ url: String(url), method, body: init?.body as string | undefined });
      if (String(url).endsWith('/health')) return json(health);
      if (method === 'POST') return json({ started: true }, 202);
      if (method === 'PATCH') return json(items[0]);
      return json(response(items));
    }),
  );
  return calls;
}

afterEach(() => vi.unstubAllGlobals());

describe('deals', () => {
  it('fills templates and keeps both locales in step', () => {
    expect(fill('{a} of {b}, {a}', { a: 1, b: 'x' })).toBe('1 of x, 1');
    expect(Object.keys(dealsRu).sort()).toEqual(Object.keys(dealsEn).sort());
  });

  it('shows the deals, the hit rate and the scanner', async () => {
    stubFetch([deal(1, null), deal(2, 'good')]);
    renderApp(<DealsPage />);

    expect(await screen.findByText('Geobasket 1')).toBeInTheDocument();
    expect(screen.getByText('67%')).toBeInTheDocument();
    expect(screen.getByText(dealsEn.dealsScannerOffHelp)).toBeInTheDocument();
    expect(screen.getByText(fill(dealsEn.dealsTruncated, { brands: 'Rick Owens' }))).toBeInTheDocument();
    expect(screen.getAllByText('29%')).toHaveLength(2);
    expect(screen.getByRole('link', { name: /Geobasket 1/ })).toHaveAttribute(
      'href',
      'https://www.grailed.com/listings/1001',
    );
  });

  it('rates a deal and clears a verdict with a second click', async () => {
    const calls = stubFetch([deal(1, null), deal(2, 'good')]);
    renderApp(<DealsPage />);

    const rows = await screen.findAllByRole('row');
    const first = rows.find((row) => within(row).queryByText('Geobasket 1'));
    const second = rows.find((row) => within(row).queryByText('Geobasket 2'));
    const good = within(first!).getByRole('button', { name: dealsEn.dealsGood });
    await waitFor(() => expect(good).toBeEnabled());
    await userEvent.click(good);
    await userEvent.click(within(second!).getByRole('button', { name: dealsEn.dealsGood }));

    await waitFor(() => expect(calls.filter((call) => call.method === 'PATCH')).toHaveLength(2));
    const patches = calls.filter((call) => call.method === 'PATCH');
    expect(patches[0].url).toContain('/deals/1');
    expect(JSON.parse(patches[0].body!)).toEqual({ verdict: 'good' });
    expect(patches[1].url).toContain('/deals/2');
    expect(JSON.parse(patches[1].body!)).toEqual({ verdict: null });
  });

  it('filters by verdict and starts a scan', async () => {
    const calls = stubFetch([deal(1, null)]);
    renderApp(<DealsPage />);

    await userEvent.click(await screen.findByRole('button', { name: dealsEn.dealsFilter_unrated }));
    await waitFor(() => expect(calls.some((call) => call.url.includes('verdict=unrated'))).toBe(true));
    const scan = screen.getByRole('button', { name: dealsEn.dealsScanNow });
    await waitFor(() => expect(scan).toBeEnabled());
    await userEvent.click(scan);

    expect(await screen.findByText(dealsEn.dealsScanStarted)).toBeInTheDocument();
    expect(calls.some((call) => call.method === 'POST' && call.url.endsWith('/deals/scan'))).toBe(true);
  });

  it('steps back a page when the one shown has emptied', async () => {
    const calls: string[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn((url: string) => {
        calls.push(String(url));
        if (String(url).endsWith('/health')) return json(health);
        // The first page reports 101 deals; by the time page two loads, one was rated away.
        if (String(url).includes('offset=100')) return json({ ...response([]), total: 100 });
        return json({ ...response([deal(1, null)]), total: calls.some((item) => item.includes('offset=100')) ? 100 : 101 });
      }),
    );
    renderApp(<DealsPage />);

    await userEvent.click(await screen.findByRole('button', { name: messages.en.next }));

    await waitFor(() => expect(calls.filter((url) => url.includes('offset=0')).length).toBeGreaterThan(1));
    expect(await screen.findByText('Geobasket 1')).toBeInTheDocument();
    expect(screen.queryByText(dealsEn.dealsEmpty)).not.toBeInTheDocument();
  });

  it('says how to get deals when there are none', async () => {
    stubFetch([]);
    renderApp(<DealsPage />);

    expect(await screen.findByText(dealsEn.dealsEmpty)).toBeInTheDocument();
  });
});
