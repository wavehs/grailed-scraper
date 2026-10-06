import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import BrandsPage from '@/app/brands/page';
import CollectPage from '@/app/collect/page';
import SettingsPage from '@/app/settings/page';
import ModelDetailClient from '@/app/model-groups/[id]/model-detail-client';
import { Dashboard } from '@/components/dashboard';
import { HealthBanner } from '@/components/health-banner';
import { HelpTip } from '@/components/ui/help-tip';
import { renderApp } from '@/test/render';

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
  it('renders Decimal scores from the live analytics API', async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith('/parser/health'))
        return json({
          status: 'ready',
          reasons: [],
          transports: { T1: true },
          discovery: { status: 'valid' },
          schema: { active_alerts: 0, alerts: [] },
        });
      if (url.includes('/parser/runs?')) return json({ data: [], total: 0, limit: 5, offset: 0 });
      if (url.endsWith('/brands')) return json({ data: [{ ...brand, name: 'Chrome Hearts' }] });
      if (url.includes('/analytics/dashboard?'))
        return json({
          data: [
            {
              id: 1,
              name: 'Dagger Necklace',
              brand_name: 'Chrome Hearts',
              available_sizes: [],
              available_conditions: [],
              sold_count: 24,
              exact_sold_count: 24,
              active_count: 111,
              median_sold_price: 45000,
              liquidity_score: '72.72',
              demand_score: '66.84',
              price_score: '0.00',
              confidence_score: '58.10',
              market_opportunity_score: '66.84',
              scoring_status: 'scored',
              model_version: 'market-v5',
              window_days: 30,
              run_id: 3,
            },
          ],
          total: 1,
          limit: 200,
          offset: 0,
        });
      return json({});
    });
    vi.stubGlobal('fetch', fetchMock);
    renderApp(<Dashboard />);
    expect(await screen.findByRole('link', { name: 'Dagger Necklace' })).toBeInTheDocument();
    expect(screen.getAllByText('66.8').length).toBeGreaterThan(0);
    await userEvent.selectOptions(screen.getByLabelText('Brand'), '1');
    await userEvent.selectOptions(screen.getByLabelText('Product type'), 'accessories');
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(([input]) => {
          const url = String(input);
          return url.includes('brand_id=1') && url.includes('product_type=accessories');
        }),
      ).toBe(true),
    );
  });

  it('switches to brand analytics view and drills down on click', async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith('/parser/health'))
        return json({
          status: 'ready',
          reasons: [],
          transports: { T1: true },
          discovery: { status: 'valid' },
          schema: { active_alerts: 0, alerts: [] },
        });
      if (url.includes('/parser/runs?')) return json({ data: [], total: 0, limit: 5, offset: 0 });
      if (url.endsWith('/brands')) return json({ data: [{ ...brand, name: 'Chrome Hearts' }] });
      if (url.includes('/analytics/brands?'))
        return json({
          data: [
            {
              id: 1,
              name: 'Chrome Hearts',
              groups_count: 5,
              sold_count: 50,
              exact_sold_count: 50,
              active_count: 100,
              median_sold_price: 60000,
              median_days_to_sell: '14.0',
              median_sold_likes: '25.0',
              demand_score: '82.50',
              liquidity_score: '78.00',
              confidence_score: '80.00',
              scoring_status: 'scored',
            },
          ],
          total: 1,
          limit: 200,
          offset: 0,
        });
      if (url.includes('/analytics/dashboard?'))
        return json({
          data: [
            {
              id: 1,
              name: 'Dagger Necklace',
              brand_name: 'Chrome Hearts',
              available_sizes: [],
              available_conditions: [],
              sold_count: 24,
              exact_sold_count: 24,
              active_count: 111,
              median_sold_price: 45000,
              liquidity_score: '72.72',
              demand_score: '66.84',
              price_score: '0.00',
              confidence_score: '58.10',
              scoring_status: 'scored',
              model_version: 'market-v5',
              window_days: 90,
              run_id: 3,
            },
          ],
          total: 1,
          limit: 200,
          offset: 0,
        });
      return json({});
    });
    vi.stubGlobal('fetch', fetchMock);
    renderApp(<Dashboard />);
    expect(await screen.findByRole('button', { name: 'By brands' })).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'By brands' }));
    expect(await screen.findByRole('button', { name: 'Chrome Hearts' })).toBeInTheDocument();
    expect(screen.getByText('82.5')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Chrome Hearts' }));
    expect(await screen.findByRole('link', { name: 'Dagger Necklace' })).toBeInTheDocument();
  });

  it('opens setting help on click', async () => {
    renderApp(<HelpTip label="Limit" text="Maximum requests for this run." />);
    const help = screen.getByRole('button', { name: 'Help' });
    await userEvent.click(help);
    expect(screen.getByRole('tooltip')).toHaveTextContent('Maximum requests for this run.');
    expect(help).toHaveAttribute('aria-expanded', 'true');
  });

  it('shows the best-selling colors and sizes for a model group', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() =>
        json({
          id: 1,
          name: 'Geobasket',
          brand: 'Rick Owens',
          category: 'footwear',
          group_type: 'resolved',
          model_version: 'market-v5',
          window_days: 90,
          run_id: 3,
          input_digest: 'abc123',
          variant_breakdown: {
            colors: [
              { value: 'black', sold_count: 4, active_count: 2, sell_through: '0.666667' },
            ],
            sizes: [{ value: '42', sold_count: 3, active_count: 1, sell_through: '0.750000' }],
          },
          metrics: {
            sold_count: 4,
            exact_sold_count: 4,
            active_count: 2,
            sell_through: '0.666667',
            median_sold_price: 50000,
            median_days_to_sell: '12',
            median_sold_likes: '20',
            liquidity_score: '50',
            demand_score: '50',
            price_score: '0',
            confidence_score: '90',
            market_opportunity_score: '50',
            scoring_status: 'scored',
            components: {},
            confidence_factors: {},
            quality_summary: {},
            warnings: [],
          },
          sold_examples: [],
          active_examples: [],
        }),
      ),
    );
    renderApp(<ModelDetailClient />);
    expect(await screen.findByText('Best-selling variants')).toBeInTheDocument();
    expect(screen.getByText('black')).toBeInTheDocument();
    expect(screen.getByText('42')).toBeInTheDocument();
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
