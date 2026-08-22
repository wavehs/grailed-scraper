import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import BrandsPage from '@/app/brands/page';
import ParserRunsPage from '@/app/parser-runs/page';
import SettingsPage from '@/app/settings/page';
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
              model_version: 'market-v4',
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

  it('performs dry-run planning before starting a parser run', async () => {
    let runCalls = 0;
    vi.stubGlobal(
      'fetch',
      vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.endsWith('/health')) return json(health);
        if (url.endsWith('/brands'))
          return json({
            data: [
              {
                ...brand,
                status: 'verified',
                mappings: [{ ...brand.mappings[0], state: 'verified' }],
              },
            ],
          });
        if (url.includes('/parser/runs?'))
          return json({ data: [], total: 0, limit: 50, offset: 0 });
        if (url.endsWith('/parser/run') && init?.method === 'POST') {
          runCalls += 1;
          if (runCalls === 1)
            return json({
              dry_run: true,
              plan: {
                mode: 'delta',
                confirmation_token: 'confirmed-plan',
                budget: {
                  estimated_requests: 4,
                  estimated_hits: 400,
                  limit: 5000,
                  over_limit: false,
                },
                warnings: [],
                tasks: [],
              },
            });
          return json({
            dry_run: false,
            run: {
              id: 11,
              mode: 'delta',
              status: 'pending',
              phase: 'planning',
              dry_run: false,
              degraded: false,
              requests_made: 0,
              warnings: [],
              created_at: new Date().toISOString(),
            },
          });
        }
        if (url.endsWith('/parser/runs/11/progress'))
          return json({
            status: 'pending',
            phase: 'planning',
            degraded: false,
            brands_total: 1,
            brands_completed: 0,
            tasks_total: 2,
            tasks_done: 0,
            hits_fetched: 0,
            requests_made: 0,
            warnings: [],
          });
        return json({});
      }),
    );
    renderApp(<ParserRunsPage />);
    await userEvent.click(await screen.findByRole('button', { name: 'Max' }));
    expect(screen.getByText('All available listings will be collected.')).toBeInTheDocument();
    await userEvent.click(await screen.findByRole('button', { name: 'Check volume and continue' }));
    expect(await screen.findByText('Collection plan')).toBeInTheDocument();
    expect(screen.queryByLabelText('Request budget')).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Start run' }));
    expect(await screen.findByText('Run #11')).toBeInTheDocument();
    expect(runCalls).toBe(2);
    const confirmed = vi
      .mocked(fetch)
      .mock.calls.filter(([input]) => String(input).endsWith('/parser/run'))[1];
    const planned = vi
      .mocked(fetch)
      .mock.calls.filter(([input]) => String(input).endsWith('/parser/run'))[0];
    expect(JSON.parse(String(planned[1]?.body))).toMatchObject({ collect_all: true });
    expect(JSON.parse(String(planned[1]?.body))).not.toHaveProperty('max_items_per_brand');
    const confirmedPayload = JSON.parse(String(confirmed[1]?.body));
    expect(confirmedPayload).toMatchObject({
      dry_run: false,
      confirmation_token: 'confirmed-plan',
    });
    expect(confirmedPayload).not.toHaveProperty('max_requests');
  });

  it('confirms run deletion and collected-data cleanup', async () => {
    let finishClear: (() => void) | undefined;
    const clearResponse = new Promise<Response>((resolve) => {
      finishClear = () =>
        resolve(new Response(JSON.stringify({ listings_deleted: 12, runs_deleted: 1 })));
    });
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith('/health')) return json(health);
      if (url.endsWith('/brands')) return json({ data: [{ ...brand, status: 'verified' }] });
      if (url.includes('/parser/runs?'))
        return json({
          data: [
            {
              id: 7,
              mode: 'full',
              status: 'completed',
              phase: 'done',
              dry_run: false,
              degraded: false,
              coverage: 1,
              requests_made: 10,
              warnings: [],
              created_at: new Date().toISOString(),
            },
          ],
          total: 1,
          limit: 50,
          offset: 0,
        });
      if (url.endsWith('/parser/runs/7') && init?.method === 'DELETE')
        return Promise.resolve(new Response(null, { status: 204 }));
      if (url.endsWith('/parser/history/clear') && init?.method === 'POST')
        return json({ runs_deleted: 1 });
      if (url.endsWith('/parser/data/clear') && init?.method === 'POST') return clearResponse;
      return json({});
    });
    vi.stubGlobal('fetch', fetchMock);
    renderApp(<ParserRunsPage />);

    await userEvent.click(await screen.findByRole('button', { name: 'Delete' }));
    const deleteDialog = screen.getByRole('dialog', { name: 'Delete parser run?' });
    await userEvent.click(within(deleteDialog).getByRole('button', { name: 'Delete' }));
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining('/parser/runs/7'),
        expect.objectContaining({ method: 'DELETE' }),
      ),
    );

    await userEvent.click(screen.getByRole('button', { name: 'Delete all run history' }));
    const historyDialog = screen.getByRole('dialog', {
      name: 'Delete all parser run history?',
    });
    await userEvent.click(
      within(historyDialog).getByRole('button', { name: 'Delete all run history' }),
    );
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining('/parser/history/clear'),
        expect.objectContaining({ method: 'POST' }),
      ),
    );

    await userEvent.click(screen.getByRole('button', { name: 'Clear collected data' }));
    const clearDialog = screen.getByRole('dialog', { name: 'Clear collected data?' });
    await userEvent.click(
      within(clearDialog).getByRole('button', { name: 'Clear collected data' }),
    );
    expect(within(clearDialog).getByRole('progressbar')).toHaveAccessibleName('Clearing database…');
    finishClear?.();
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining('/parser/data/clear'),
        expect.objectContaining({ method: 'POST' }),
      ),
    );
  });

  it('edits safe settings and sends a flat validated patch', async () => {
    const groups = {
      source: { fetch_tier_preferred: { value: 'T1', origin: 'default' } },
      parser: { requests_per_minute: { value: 90, origin: 'default' } },
      proxy: { proxy_enabled: { value: false, origin: 'default' } },
      discovery: { discovery_ttl_hours: { value: 12, origin: 'default' } },
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
