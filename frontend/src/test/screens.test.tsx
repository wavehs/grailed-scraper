import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import BrandsPage from '@/app/brands/page';
import CollectPage from '@/app/collect/page';
import SettingsPage from '@/app/settings/page';
import GroupPage from '@/app/group/page';
import ListingsPage from '@/app/listings/page';
import TrendsPage from '@/app/trends/page';
import { GroupEditor } from '@/components/group-editor';
import { HealthBanner } from '@/components/health-banner';
import { HelpTip } from '@/components/ui/help-tip';
import { renderApp } from '@/test/render';
import { navigation } from '@/test/setup';

const json = (body: unknown, status = 200) =>
  Promise.resolve(new Response(JSON.stringify(body), { status }));
const health = { status: 'ok', service: 'test', source_mode: 'live', request_id: 'test' };
const brand = {
  id: 1,
  name: 'Rick Owens',
  aliases: ['RO'],
  include_subbrands: false,
  listings_count: 400,
  status: 'review',
  mappings: [
    {
      id: 3,
      source_designer_name: 'Rick Owens',
      listings_count: 400,
      match_score: '0.92',
      match_method: 'fuzzy',
      is_subbrand: false,
      state: 'review',
    },
  ],
};

beforeEach(() => window.localStorage.clear());

describe('stage 10 screens', () => {
  it('shows trends of every brand and filters by type, window and price', async () => {
    const row = {
      scope: 'model',
      scope_key: 'model:5',
      group_id: 5,
      brand_id: 1,
      brand: 'Balenciaga',
      product_type: 'lowtop_sneakers',
      section: 'footwear',
      name: 'Track',
      status: 'auto',
      is_fallback: false,
      versions: 2,
      listings: 40,
      sold: 12,
      sold_7d: 4,
      sold_30d: 12,
      sold_prev_30d: 3,
      sold_90d: 20,
      growth: '3.2500',
      speed: '0.7500',
      trend_score: '146.25',
      median_days_to_sell: '10.00',
      sell_through_30d: '0.600000',
      median_price: 52000,
      price_change: '0.1000',
      active_now: 8,
      new_listings_14d: 3,
      is_new: true,
      first_seen_at: '2026-09-01T00:00:00Z',
      weekly_sales: [0, 0, 0, 0, 0, 0, 0, 0, 1, 2, 3, 4],
    };
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith('/brands')) return json({ data: [{ ...brand, id: 1, name: 'Balenciaga' }] });
      if (url.endsWith('/grouping/taxonomy'))
        return json({
          version: 'taxonomy-v1',
          sections: [{ id: 'footwear', ru: 'Обувь', en: 'Footwear' }],
          types: [
            { id: 'lowtop_sneakers', section: 'footwear', ru: 'Кроссовки', en: 'Low-top sneakers' },
          ],
        });
      if (url.includes('/trends?'))
        return json({ data: [row], total: 1, computed_at: '2026-10-01T12:00:00Z' });
      return json({});
    });
    vi.stubGlobal('fetch', fetchMock);
    renderApp(<TrendsPage />);
    const link = await screen.findByRole('link', {
      name: 'Balenciaga · Track · Low-top sneakers',
    });
    expect(link).toHaveAttribute('href', '/group?id=5');
    expect(screen.getByText('146')).toBeInTheDocument();
    expect(screen.getByText('×3.25')).toBeInTheDocument();
    expect(screen.getByText('new')).toBeInTheDocument();
    expect(screen.getByRole('img', { name: /Sales by week/ })).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText('Product type'), 'lowtop_sneakers');
    await userEvent.click(screen.getByRole('button', { name: '90 d' }));
    await userEvent.type(screen.getByLabelText('Price to, $'), '600');
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(([input]) => {
          const url = String(input);
          return (
            url.includes('product_type=lowtop_sneakers') &&
            url.includes('window=90') &&
            url.includes('price_max=600')
          );
        }),
      ).toBe(true),
    );
    await userEvent.click(screen.getByRole('button', { name: 'Brands' }));
    await waitFor(() =>
      expect(fetchMock.mock.calls.some(([input]) => String(input).includes('level=brand'))).toBe(
        true,
      ),
    );
  });

  it('shows the group card with versions, variants, sales links and edits', async () => {
    const group = {
      id: 1,
      brand_id: 1,
      brand: 'Rick Owens',
      product_type: 'hitop_sneakers',
      slug: 'geobasket',
      name: 'Geobasket',
      aliases: [],
      parent_id: null,
      status: 'confirmed',
      source: 'seed',
      is_fallback: false,
      listings: 20,
      sold: 12,
      active: 8,
      parent: null,
      versions: [],
    };
    const metric = {
      scope: 'model',
      scope_key: 'model:1',
      group_id: 1,
      brand_id: 1,
      brand: 'Rick Owens',
      product_type: 'hitop_sneakers',
      section: 'footwear',
      name: 'Geobasket',
      status: 'confirmed',
      is_fallback: false,
      versions: 1,
      listings: 20,
      sold: 12,
      sold_7d: 2,
      sold_30d: 12,
      sold_prev_30d: 6,
      sold_90d: 30,
      growth: '1.8571',
      speed: '0.6000',
      trend_score: '66.86',
      median_days_to_sell: '20.00',
      sell_through_30d: '0.600000',
      median_price: 90000,
      price_change: null,
      active_now: 8,
      new_listings_14d: 1,
      is_new: false,
      first_seen_at: null,
      weekly_sales: [1, 2, 3, 1, 2, 3, 1, 2, 3, 1, 2, 3],
    };
    vi.stubGlobal(
      'fetch',
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes('/trends/groups/1'))
          return json({
            group,
            metrics: metric,
            computed_at: '2026-10-01T12:00:00Z',
            weekly_median_price: Array(12).fill(90000),
            colors: [{ value: 'milk', sold: 9, active: 2, sell_through: '0.818182' }],
            sizes: [{ value: 'us 10', sold: 5, active: 1, sell_through: '0.833333' }],
            versions: [
              { ...metric, scope_key: 'model:2', group_id: 2, name: 'Mega Geobasket', sold: 3 },
            ],
            type_metrics: null,
            recent_sales: [
              {
                id: 9,
                grailed_id: 999,
                url: 'https://www.grailed.com/listings/999',
                title: 'Rick Owens Geobasket Milk',
                price: 88000,
                status: 'sold',
                sold_at: '2026-09-30T00:00:00Z',
                created_at: '2026-09-10T00:00:00Z',
                days_to_sell: 20,
                size: 'us 10',
                color: 'milk',
                group_id: 1,
                group_name: 'Geobasket',
                relisted: true,
              },
            ],
            active_listings: [],
          });
        if (url.endsWith('/groups/1')) return json(group);
        if (url.includes('/groups?')) return json({ data: [group], total: 1 });
        if (url.endsWith('/grouping/taxonomy'))
          return json({
            version: 'taxonomy-v1',
            sections: [],
            types: [
              { id: 'hitop_sneakers', section: 'footwear', ru: 'Кеды', en: 'High-top sneakers' },
            ],
          });
        return json({});
      }),
    );
    navigation.search = 'id=1';
    renderApp(<GroupPage />);
    expect(
      await screen.findByRole('heading', { name: 'Rick Owens · Geobasket · High-top sneakers' }),
    ).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Mega Geobasket' })).toHaveAttribute(
      'href',
      '/group?id=2',
    );
    expect(screen.getByText('milk')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /Rick Owens Geobasket Milk/ })).toHaveAttribute(
      'href',
      'https://www.grailed.com/listings/999',
    );
    expect(screen.getByText(/relisted/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Move' })).toBeInTheDocument();
    expect(await screen.findByText('Edit group')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Show as table' }));
    expect(screen.getAllByRole('row').length).toBeGreaterThan(12);
  });

  it('confirms, merges and rejects groups through durable edit rules', async () => {
    const group = {
      id: 7,
      brand_id: 1,
      brand: 'Chrome Hearts',
      product_type: 'tshirt',
      slug: 'neck-logo',
      name: 'Neck Logo',
      aliases: ['neck logo'],
      parent_id: null,
      status: 'auto',
      source: 'mined',
      is_fallback: false,
      listings: 12,
      sold: 5,
      active: 7,
      parent: null,
      versions: [],
    };
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith('/grouping/taxonomy'))
        return json({
          version: 'taxonomy-v1',
          sections: [{ id: 'tops', ru: 'Верх', en: 'Tops' }],
          types: [{ id: 'tshirt', section: 'tops', ru: 'Футболка', en: 'T-shirt' }],
        });
      if (url.includes('/groups?'))
        return json({
          data: [group, { ...group, id: 8, name: 'Neck Logo Tee', status: 'confirmed' }],
          total: 2,
        });
      if (url.endsWith('/groups/7') && init?.method === 'PATCH')
        return json({ ...group, status: 'confirmed' });
      if (url.endsWith('/groups/7/merge')) return json({ ...group, id: 8 });
      if (url.endsWith('/groups/7')) return json(group);
      return json({});
    });
    vi.stubGlobal('fetch', fetchMock);
    const changed = vi.fn();
    renderApp(<GroupEditor groupId={7} onChanged={changed} />);
    expect(await screen.findByText('Chrome Hearts · Neck Logo · T-shirt')).toBeInTheDocument();
    expect(screen.getByText('auto')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Confirm' }));
    await waitFor(() => {
      const call = fetchMock.mock.calls.find(
        ([input, init]) => String(input).endsWith('/groups/7') && init?.method === 'PATCH',
      );
      expect(JSON.parse(String(call?.[1]?.body))).toEqual({ status: 'confirmed' });
    });
    await userEvent.click(screen.getByRole('button', { name: 'Merge into…' }));
    await userEvent.selectOptions(await screen.findByLabelText('Merge into…'), '8');
    await userEvent.click(screen.getByRole('button', { name: 'Apply' }));
    await waitFor(() => expect(changed).toHaveBeenCalledWith(expect.objectContaining({ id: 8 })));
    const merge = fetchMock.mock.calls.find(([input]) => String(input).endsWith('/merge'));
    expect(JSON.parse(String(merge?.[1]?.body))).toEqual({ target_id: 8 });
  });

  it('lists catalog listings with model links, filters and cursor pages', async () => {
    const listing = {
      id: 7,
      grailed_id: 9001,
      url: 'https://www.grailed.com/listings/9001',
      title: 'Geobasket Milk',
      brand: 'Rick Owens',
      brand_id: 1,
      product_type: 'hightop_sneakers',
      status: 'sold',
      size: '42',
      color: 'Milk',
      price: 650,
      last_seen_at: '2026-10-01T12:00:00Z',
      days_on_market: 12,
      model_group_id: 5,
      model_name: 'Geobasket',
      is_fallback: false,
      model_sold_count: 14,
      model_active_count: 3,
    };
    const fallback = {
      ...listing,
      id: 8,
      grailed_id: 9002,
      title: 'Leather jacket',
      status: 'active',
      model_group_id: 6,
      is_fallback: true,
    };
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith('/grouping/taxonomy'))
        return json({
          version: 'taxonomy-v1',
          sections: [{ id: 'footwear', ru: 'Обувь', en: 'Footwear' }],
          types: [
            { id: 'hightop_sneakers', section: 'footwear', ru: 'Кеды', en: 'High-top sneakers' },
          ],
        });
      if (url.includes('cursor=page-2'))
        return json({ data: [fallback], limit: 50, next_cursor: null });
      if (url.includes('/listings?'))
        return json({ data: [listing], limit: 50, next_cursor: 'page-2' });
      return json({});
    });
    vi.stubGlobal('fetch', fetchMock);
    renderApp(<ListingsPage />);
    const title = await screen.findByRole('link', { name: 'Geobasket Milk' });
    expect(title).toHaveAttribute('href', listing.url);
    const row = title.closest('tr') as HTMLElement;
    expect(within(row).getByRole('link', { name: 'Geobasket' })).toHaveAttribute(
      'href',
      '/group?id=5',
    );
    expect(within(row).getByText('Sold')).toBeInTheDocument();
    expect(
      await within(row).findByText(/Rick Owens · High-top sneakers · 42 · Milk · #9001/),
    ).toBeInTheDocument();
    const fetched = (part: string) =>
      fetchMock.mock.calls.some(([input]) => String(input).includes(part));
    await userEvent.selectOptions(screen.getByLabelText('Status'), 'sold');
    await userEvent.selectOptions(screen.getByLabelText('Product type'), 'hightop_sneakers');
    await waitFor(() => expect(fetched('status=sold&product_type=hightop_sneakers')).toBe(true));
    await userEvent.type(screen.getByPlaceholderText('Search by product or brand'), 'geobasket');
    await waitFor(() => expect(fetched('search=geobasket')).toBe(true));
    await userEvent.click(await screen.findByRole('button', { name: 'Next' }));
    const second = await screen.findByRole('link', { name: 'No model' });
    expect(second).toHaveAttribute('href', '/group?id=6');
    expect(screen.getByRole('button', { name: 'Next' })).toBeDisabled();
    await userEvent.click(screen.getByRole('button', { name: 'Previous' }));
    expect(await screen.findByRole('link', { name: 'Geobasket Milk' })).toBeInTheDocument();
  });

  it('opens setting help on click', async () => {
    renderApp(<HelpTip label="Limit" text="Maximum requests for this run." />);
    const help = screen.getByRole('button', { name: 'Help' });
    await userEvent.click(help);
    expect(screen.getByRole('tooltip')).toHaveTextContent('Maximum requests for this run.');
    expect(help).toHaveAttribute('aria-expanded', 'true');
  });

  it('announces parser degradation and its actionable reason', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() =>
        json({
          status: 'unavailable',
          reasons: ['live_compliance_not_acknowledged'],
        }),
      ),
    );
    renderApp(<HealthBanner />);
    expect(await screen.findByRole('alert')).toHaveTextContent('Parser unavailable');
    expect(screen.getByRole('alert')).toHaveTextContent(
      'Live access is blocked until compliance is acknowledged',
    );
  });

  it('refreshes an expired source connection from the warning banner', async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith('/parser/health'))
        return json({ status: 'degraded', reasons: ['credentials_stale'] });
      if (url.endsWith('/parser/discovery/refresh') && init?.method === 'POST')
        return json({ status: 'ready' });
      return json({});
    });
    vi.stubGlobal('fetch', fetchMock);
    renderApp(<HealthBanner />);
    expect(await screen.findByText('Source connection needs an update')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Update now' }));
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining('/parser/discovery/refresh'),
        expect.objectContaining({ method: 'POST' }),
      ),
    );
  });

  it('searches brands and submits a mapping confirmation', async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith('/health')) return json(health);
      if (url.endsWith('/brands') && (!init?.method || init.method === 'GET'))
        return json({ data: [brand] });
      if (url.includes('/mappings/3')) return json({ ...brand.mappings[0], state: 'verified' });
      return json({});
    });
    vi.stubGlobal('fetch', fetchMock);
    renderApp(<BrandsPage />);
    expect(await screen.findByRole('heading', { name: 'Rick Owens' })).toBeInTheDocument();
    await userEvent.type(screen.getByPlaceholderText('Search'), 'missing');
    expect(screen.getByText('No brands found.')).toBeInTheDocument();
    await userEvent.clear(screen.getByPlaceholderText('Search'));
    await userEvent.click(await screen.findByRole('button', { name: 'Confirm' }));
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining('/brands/1/mappings/3'),
        expect.objectContaining({ method: 'PATCH' }),
      ),
    );
  });

  it('collects the selected brands with one click', async () => {
    const verified = {
      ...brand,
      status: 'verified',
      mappings: [{ ...brand.mappings[0], state: 'verified' }],
    };
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith('/parser/health')) return json({ status: 'ready', reasons: [] });
      if (url.endsWith('/brands'))
        return json({ data: [verified, { ...verified, id: 2, name: 'Chrome Hearts' }] });
      if (url.includes('/parser/runs?')) return json({ data: [], total: 0, limit: 30, offset: 0 });
      if (url.endsWith('/parser/run') && init?.method === 'POST')
        return json({
          run: {
            id: 11,
            mode: 'full',
            status: 'pending',
            phase: 'planning',
            degraded: false,
            requests_made: 0,
            warnings: [],
            created_at: new Date().toISOString(),
          },
        });
      return json({});
    });
    vi.stubGlobal('fetch', fetchMock);
    renderApp(<CollectPage />);
    const all = await screen.findByRole('button', { name: 'Update data' });
    await userEvent.click(all);
    await userEvent.click(screen.getByRole('button', { name: /Chrome Hearts/ }));
    await userEvent.click(screen.getByRole('button', { name: 'Update data' }));
    await waitFor(() => {
      const calls = fetchMock.mock.calls.filter(
        ([input, init]) => String(input).endsWith('/parser/run') && init?.method === 'POST',
      );
      expect(calls).toHaveLength(2);
      expect(JSON.parse(String(calls[0][1]?.body))).toEqual({ brand_ids: null });
      expect(JSON.parse(String(calls[1][1]?.body))).toEqual({ brand_ids: [1] });
    });
    expect(
      fetchMock.mock.calls.some(([input]) => String(input).includes('confirmation_token')),
    ).toBe(false);
  });

  it('asks for the one-time compliance acknowledgement before collecting', async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith('/parser/health'))
        return json({ status: 'unavailable', reasons: ['live_compliance_not_acknowledged'] }, 503);
      if (url.endsWith('/brands')) return json({ data: [{ ...brand, status: 'verified' }] });
      if (url.includes('/parser/runs?')) return json({ data: [], total: 0, limit: 30, offset: 0 });
      if (url.endsWith('/settings') && init?.method === 'PATCH') return json({ groups: {} });
      return json({});
    });
    vi.stubGlobal('fetch', fetchMock);
    renderApp(<CollectPage />);
    await userEvent.click(
      await screen.findByRole('button', { name: 'I understand, enable collection' }),
    );
    await waitFor(() => {
      const call = fetchMock.mock.calls.find(
        ([input, init]) => String(input).endsWith('/settings') && init?.method === 'PATCH',
      );
      expect(JSON.parse(String(call?.[1]?.body))).toEqual({ live_compliance_acknowledged: true });
    });
    expect(screen.getByRole('button', { name: 'Update data' })).toBeDisabled();
  });

  it('clears collected data after confirmation', async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith('/parser/health')) return json({ status: 'ready', reasons: [] });
      if (url.endsWith('/brands')) return json({ data: [{ ...brand, status: 'verified' }] });
      if (url.includes('/parser/runs?')) return json({ data: [], total: 0, limit: 30, offset: 0 });
      if (url.endsWith('/parser/data/clear') && init?.method === 'POST')
        return json({ listings_deleted: 12, runs_deleted: 1 });
      return json({});
    });
    vi.stubGlobal('fetch', fetchMock);
    renderApp(<CollectPage />);
    await userEvent.click(await screen.findByRole('button', { name: 'Clear collected data' }));
    const dialog = screen.getByRole('dialog', { name: 'Clear collected data' });
    await userEvent.click(within(dialog).getByRole('button', { name: 'Delete' }));
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining('/parser/data/clear'),
        expect.objectContaining({ method: 'POST' }),
      ),
    );
  });

  it('adds a brand from a Grailed designer suggestion', async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith('/health')) return json(health);
      if (url.includes('/brands/designers?'))
        return json({ data: [{ name: 'Enfants Riches Déprimés', listings_count: 812 }] });
      if (url.endsWith('/brands') && init?.method === 'POST')
        return json({ ...brand, id: 5, name: 'Enfants Riches Déprimés' }, 201);
      if (url.endsWith('/brands')) return json({ data: [brand] });
      return json({});
    });
    vi.stubGlobal('fetch', fetchMock);
    renderApp(<BrandsPage />);
    await userEvent.type(
      await screen.findByPlaceholderText('Start typing a designer, e.g. Chrome Hearts'),
      'enfants',
    );
    expect(await screen.findByText(/812/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Add' }));
    await waitFor(() => {
      const call = fetchMock.mock.calls.find(
        ([input, init]) => String(input).endsWith('/brands') && init?.method === 'POST',
      );
      expect(JSON.parse(String(call?.[1]?.body))).toEqual({
        name: 'Enfants Riches Déprimés',
        designer: 'Enfants Riches Déprimés',
      });
    });
  });

  it('edits safe settings and sends a flat validated patch', async () => {
    const groups = {
      collection: {
        requests_per_minute: { value: 90, origin: 'default' },
        sold_history_days: { value: 365, origin: 'default' },
      },
      privacy: { store_seller_identity: { value: 'hashed', origin: 'default' } },
    };
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith('/health')) return json(health);
      if (url.endsWith('/settings')) return json({ groups });
      return json({});
    });
    vi.stubGlobal('fetch', fetchMock);
    renderApp(<SettingsPage />);
    const rpm = await screen.findByLabelText(/requests per minute/i);
    await userEvent.clear(rpm);
    await userEvent.type(rpm, '24');
    await userEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => {
      const call = fetchMock.mock.calls.find(
        ([input, init]) => String(input).endsWith('/settings') && init?.method === 'PATCH',
      );
      expect(call).toBeDefined();
      expect(JSON.parse(String(call?.[1]?.body))).toMatchObject({ requests_per_minute: 24 });
    });
  });

  it('requires an explicit warning confirmation before saving plain seller identity', async () => {
    const groups = {
      privacy: { store_seller_identity: { value: 'hashed', origin: 'default' } },
    };
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith('/health')) return json(health);
      if (url.endsWith('/settings')) return json({ groups });
      return json({});
    });
    vi.stubGlobal('fetch', fetchMock);
    renderApp(<SettingsPage />);
    await userEvent.selectOptions(
      await screen.findByLabelText(/seller identity storage/i),
      'plain',
    );
    const confirmation = screen.getByLabelText(/plain mode stores a public seller identifier/i);
    expect(confirmation).not.toBeChecked();
    await userEvent.click(confirmation);
    await userEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => {
      const call = fetchMock.mock.calls.find(
        ([input, init]) => String(input).endsWith('/settings') && init?.method === 'PATCH',
      );
      expect(JSON.parse(String(call?.[1]?.body))).toMatchObject({
        store_seller_identity: 'plain',
        confirm_plain_seller_identity: true,
      });
    });
  });
});
