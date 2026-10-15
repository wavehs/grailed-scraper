import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import CollectPage from '@/app/collect/page';
import SettingsPage from '@/app/settings/page';
import { renderApp } from '@/test/render';

const json = (body: unknown, status = 200) =>
  Promise.resolve(new Response(JSON.stringify(body), { status }));
const health = { status: 'ok', service: 'test', source_mode: 'live', request_id: 'test' };
const brands = [
  {
    id: 1,
    name: 'Rick Owens',
    aliases: [],
    include_subbrands: false,
    status: 'verified',
    mappings: [],
  },
  {
    id: 2,
    name: 'Chrome Hearts',
    aliases: [],
    include_subbrands: false,
    status: 'verified',
    mappings: [],
  },
];
const schedule = {
  enabled: true,
  hour: 4,
  rpm: 90,
  brand_ids: null,
  full_weekday: 6,
  state: 'idle',
  reason: 'paused',
  slot: '2026-10-15',
  next_slot_at: '2026-10-16T04:00:00+03:00',
  paused_until: '2026-10-15T15:00:00+03:00',
  last_run: {
    id: 7,
    status: 'partial',
    slot: '2026-10-15',
    full: false,
    attempts: 1,
    stop_reason: 'throttled',
    created_at: '2026-10-15T04:00:00+03:00',
    finished_at: '2026-10-15T09:00:00+03:00',
  },
};
const run = {
  id: 11,
  mode: 'full',
  status: 'pending',
  phase: 'planning',
  degraded: false,
  requests_made: 0,
  warnings: [],
  created_at: '2026-10-15T09:00:00Z',
};

beforeEach(() => {
  window.localStorage.clear();
});

describe('daily schedule', () => {
  it('starts a full run at its own rate from the collect page', async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith('/brands')) return json({ data: brands });
      if (url.includes('/parser/runs?')) return json({ data: [], total: 0, limit: 30, offset: 0 });
      if (url.endsWith('/parser/run') && init?.method === 'POST') return json({ run });
      return json({});
    });
    vi.stubGlobal('fetch', fetchMock);
    renderApp(<CollectPage />);
    await screen.findByRole('button', { name: /Chrome Hearts/ });

    await userEvent.click(screen.getByLabelText('Full run'));
    await userEvent.type(screen.getByLabelText('Rate, requests/min'), '30');
    await userEvent.click(screen.getByRole('button', { name: 'Update data' }));

    await waitFor(() => {
      const call = fetchMock.mock.calls.find(
        ([input, init]) => String(input).endsWith('/parser/run') && init?.method === 'POST',
      );
      expect(JSON.parse(String(call?.[1]?.body))).toEqual({ brand_ids: null, full: true, rpm: 30 });
    });
  });

  it('picks scheduled brands and clears the throttling pause', async () => {
    const groups = {
      schedule: {
        schedule_enabled: { value: true, origin: 'database' },
        schedule_hour: { value: 4, origin: 'default' },
        schedule_rpm: { value: 90, origin: 'default' },
        schedule_brand_ids: { value: null, origin: 'default' },
      },
    };
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith('/health')) return json(health);
      if (url.endsWith('/settings')) return json({ groups });
      if (url.endsWith('/brands')) return json({ data: brands });
      if (url.endsWith('/parser/schedule/pause') && init?.method === 'DELETE')
        return Promise.resolve(new Response(null, { status: 204 }));
      if (url.endsWith('/parser/schedule')) return json(schedule);
      return json({});
    });
    vi.stubGlobal('fetch', fetchMock);
    renderApp(<SettingsPage />);

    expect(
      await screen.findByText('Paused after Grailed throttled the last run.'),
    ).toBeInTheDocument();
    expect(screen.getByText(/all brands with collected listings/)).toBeInTheDocument();
    await userEvent.click(await screen.findByRole('button', { name: 'Chrome Hearts' }));
    await userEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => {
      const call = fetchMock.mock.calls.find(
        ([input, init]) => String(input).endsWith('/settings') && init?.method === 'PATCH',
      );
      expect(JSON.parse(String(call?.[1]?.body))).toMatchObject({ schedule_brand_ids: [2] });
    });

    await userEvent.click(screen.getByRole('button', { name: 'Resume now' }));
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(
          ([input, init]) =>
            String(input).endsWith('/parser/schedule/pause') && init?.method === 'DELETE',
        ),
      ).toBe(true),
    );
    expect(await screen.findByText(/Pause cleared/)).toBeInTheDocument();
  });
});
