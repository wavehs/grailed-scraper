import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import TrendsPage from '@/app/trends/page';
import { seasonRange } from '@/lib/periods';
import { renderApp } from '@/test/render';

const json = (body: unknown) => Promise.resolve(new Response(JSON.stringify(body), { status: 200 }));
const taxonomy = {
  version: '1',
  sections: [{ id: 'outerwear', ru: 'Верхняя одежда', en: 'Outerwear' }],
  types: [
    { id: 'parka', section: 'outerwear', ru: 'Парка', en: 'Parka' },
    { id: 'coat', section: 'outerwear', ru: 'Пальто', en: 'Coat' },
  ],
  seasons: { autumn: ['parka', 'coat'], winter: ['parka'], spring: [], summer: [] },
};

function stubApi() {
  const fetchMock = vi.fn((input: RequestInfo | URL) => {
    const url = new URL(String(input), 'http://localhost');
    if (url.pathname.endsWith('/trends')) return json({ data: [], total: 0, computed_at: null, period: null });
    if (url.pathname.endsWith('/grouping/taxonomy')) return json(taxonomy);
    if (url.pathname.endsWith('/brands')) return json({ data: [] });
    return json({});
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

function lastTrendsUrl(fetchMock: ReturnType<typeof stubApi>): URL {
  const urls = fetchMock.mock.calls
    .map(([input]) => new URL(String(input), 'http://localhost'))
    .filter((url) => url.pathname.endsWith('/trends'));
  return urls[urls.length - 1];
}

beforeEach(() => {
  window.localStorage.clear();
  window.sessionStorage.clear();
});

describe('wardrobe season presets', () => {
  it('show only seasons with types and filter the season’s types over its period', async () => {
    const fetchMock = stubApi();
    renderApp(<TrendsPage />);
    const group = await screen.findByRole('group', { name: 'Wardrobe' });
    expect(within(group).queryByRole('button', { name: /Spring/ })).not.toBeInTheDocument();

    await userEvent.click(within(group).getByRole('button', { name: '🍂 Autumn' }));

    const autumn = seasonRange('autumn', new Date());
    await waitFor(() => expect(lastTrendsUrl(fetchMock).searchParams.get('product_type')).toBe('parka,coat'));
    expect(lastTrendsUrl(fetchMock).searchParams.get('month_from')).toBe(autumn.from);
    expect(within(group).getByText('2 types')).toBeInTheDocument();
  });

  it('give the selects back control when a type is chosen or the preset is cleared', async () => {
    const fetchMock = stubApi();
    renderApp(<TrendsPage />);
    const group = await screen.findByRole('group', { name: 'Wardrobe' });

    await userEvent.click(within(group).getByRole('button', { name: '❄️ Winter' }));
    await waitFor(() => expect(lastTrendsUrl(fetchMock).searchParams.get('product_type')).toBe('parka'));
    await userEvent.selectOptions(screen.getByLabelText('Product type'), 'coat');
    await waitFor(() => expect(lastTrendsUrl(fetchMock).searchParams.get('product_type')).toBe('coat'));
    expect(within(group).queryByText(/types$/)).not.toBeInTheDocument();

    await userEvent.click(within(group).getByRole('button', { name: '🍂 Autumn' }));
    await userEvent.click(within(group).getByRole('button', { name: 'Clear the wardrobe preset' }));
    await waitFor(() => expect(lastTrendsUrl(fetchMock).searchParams.has('product_type')).toBe(false));
  });
});
