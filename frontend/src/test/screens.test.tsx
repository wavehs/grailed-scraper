import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import BrandsPage from '@/app/brands/page';
import ParserRunsPage from '@/app/parser-runs/page';
import SettingsPage from '@/app/settings/page';
import { HealthBanner } from '@/components/health-banner';
import { renderApp } from '@/test/render';

const json = (body: unknown, status = 200) =>
  Promise.resolve(new Response(JSON.stringify(body), { status }));
const health = { status: 'ok', service: 'test', source_mode: 'mock', request_id: 'test' };
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
        if (url.endsWith('/brands')) return json({ data: [brand] });
        if (url.includes('/parser/runs?'))
          return json({ data: [], total: 0, limit: 50, offset: 0 });
        if (url.endsWith('/parser/run') && init?.method === 'POST') {
          runCalls += 1;
          if (runCalls === 1)
            return json({
              dry_run: true,
              plan: {
                mode: 'delta',
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
    await userEvent.click(await screen.findByRole('button', { name: 'Dry run' }));
    expect(await screen.findByText('Budget')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Start run' }));
    expect(await screen.findByText('Run #11')).toBeInTheDocument();
    expect(runCalls).toBe(2);
  });

  it('edits safe settings and sends a flat validated patch', async () => {
    const groups = {
      source: { source_mode: { value: 'mock', origin: 'default' } },
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
