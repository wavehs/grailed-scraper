import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import WatchlistPage from '@/app/watchlist/page';
import { signed } from '@/components/watch-changes';
import { WatchStar } from '@/components/watch-star';
import { watchlistEn, watchlistRu } from '@/lib/i18n-watchlist';
import type { WatchItem } from '@/lib/types';
import { renderApp } from '@/test/render';

const json = (body: unknown) => Promise.resolve(new Response(JSON.stringify(body), { status: 200 }));
const point = (score: string, price: number, sold: number, active: number) => ({
  as_of: '2026-10-01',
  liquidity_score: score,
  median_price: price,
  sold_30d: sold,
  active_now: active,
  active_effective: null,
});
const item: WatchItem = {
  group_id: 7,
  brand: 'Rick Owens',
  name: 'Sisyphus',
  product_type: 'parka',
  retired: false,
  added_at: '2026-10-01T10:00:00Z',
  note: 'size 50 only',
  baseline: point('60.0', 100_000, 4, 30),
  current: point('72.5', 110_000, 7, 25),
  changes: { liquidity_score: '12.5', median_price: '0.1', sold_30d: 3, active_now: -5, active_effective: null },
};

function stubFetch(items: WatchItem[]) {
  const calls: { url: string; method: string; body?: string }[] = [];
  const fetchMock = vi.fn((url: string, init?: RequestInit) => {
    calls.push({ url: String(url), method: init?.method ?? 'GET', body: init?.body as string | undefined });
    if (init?.method === 'DELETE') return Promise.resolve(new Response(null, { status: 204 }));
    if (init?.method === 'PUT') return json(items[0] ?? item);
    return json({ data: items });
  });
  vi.stubGlobal('fetch', fetchMock);
  return calls;
}

afterEach(() => vi.unstubAllGlobals());

describe('watchlist', () => {
  it('formats signed changes', () => {
    expect(signed(3)).toBe('+3');
    expect(signed(-5)).toBe('−5');
    expect(signed(0)).toBe('0');
    expect(signed(null)).toBe('—');
  });

  it('adds an unwatched model with the star', async () => {
    const calls = stubFetch([]);
    renderApp(<WatchStar groupId={7} />);

    const star = await screen.findByRole('button', { name: watchlistEn.watch });
    await waitFor(() => expect(star).toBeEnabled());
    await userEvent.click(star);

    await waitFor(() => expect(calls.some((call) => call.method === 'PUT')).toBe(true));
    const put = calls.find((call) => call.method === 'PUT');
    expect(put?.url).toContain('/watchlist/7');
  });

  it('removes a watched model with the star', async () => {
    const calls = stubFetch([item]);
    renderApp(<WatchStar groupId={7} />);

    const star = await screen.findByRole('button', { name: watchlistEn.unwatch });
    expect(star).toHaveAttribute('aria-pressed', 'true');
    await userEvent.click(star);

    await waitFor(() => expect(calls.some((call) => call.method === 'DELETE')).toBe(true));
  });

  it('lists watched models with their changes and saves a note on blur', async () => {
    const calls = stubFetch([item]);
    renderApp(<WatchlistPage />);

    expect(await screen.findByRole('link', { name: /Sisyphus/ })).toHaveAttribute('href', '/group?id=7');
    expect(screen.getByText('(+13)')).toBeInTheDocument();
    expect(screen.getByText('(+10%)')).toBeInTheDocument();
    expect(screen.getByText('(+3)')).toBeInTheDocument();
    expect(screen.getByText('(−5)')).toBeInTheDocument();

    const note = screen.getByRole('textbox', { name: watchlistEn.watchNote });
    await userEvent.clear(note);
    await userEvent.type(note, 'any size');
    note.blur();

    await waitFor(() => expect(calls.some((call) => call.method === 'PUT')).toBe(true));
    expect(calls.find((call) => call.method === 'PUT')?.body).toBe(JSON.stringify({ note: 'any size' }));
  });

  it('shows an empty state without watched models', async () => {
    stubFetch([]);
    renderApp(<WatchlistPage />);

    expect(await screen.findByText(watchlistEn.watchEmpty)).toBeInTheDocument();
  });

  it('has every string in both languages', () => {
    expect(Object.keys(watchlistRu).sort()).toEqual(Object.keys(watchlistEn).sort());
  });
});
