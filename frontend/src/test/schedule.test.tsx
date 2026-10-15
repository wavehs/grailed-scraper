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

  it('blocks saving an hour outside 0–23 and refreshes the schedule after a save', async () => {
    const groups = {
      schedule: {
        schedule_hour: { value: 4, origin: 'default' },
        schedule_rpm: { value: 90, origin: 'default' },
      },
    };
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith('/health')) return json(health);
      if (url.endsWith('/settings')) return json({ groups });
      if (url.endsWith('/brands')) return json({ data: brands });
      if (url.endsWith('/parser/schedule')) return json(schedule);
      return json({});
    });
    vi.stubGlobal('fetch', fetchMock);
    renderApp(<SettingsPage />);
    const hour = await screen.findByLabelText(/Start hour/);

    await userEvent.clear(hour);
    await userEvent.type(hour, '24');
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled();
    expect(screen.getByText('Enter a whole number from 0 to 23.')).toBeInTheDocument();

    await userEvent.clear(hour);
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled();
    await userEvent.type(hour, '5');
    const schedules = () =>
      fetchMock.mock.calls.filter(([input]) => String(input).endsWith('/parser/schedule')).length;
    const before = schedules();
    await userEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => {
      const call = fetchMock.mock.calls.find(
        ([input, init]) => String(input).endsWith('/settings') && init?.method === 'PATCH',
      );
      expect(JSON.parse(String(call?.[1]?.body))).toMatchObject({ schedule_hour: 5 });
    });
    await waitFor(() => expect(schedules()).toBeGreaterThan(before));
  });

  it('rejects a zero rate and clears "Full run" after a start', async () => {
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

    await userEvent.type(screen.getByLabelText('Rate, requests/min'), '0');
    expect(screen.getByRole('button', { name: 'Update data' })).toBeDisabled();
    expect(screen.getByText('The rate is a whole number of at least 1.')).toBeInTheDocument();

    await userEvent.clear(screen.getByLabelText('Rate, requests/min'));
    await userEvent.click(screen.getByLabelText('Full run'));
    await userEvent.click(screen.getByRole('button', { name: 'Update data' }));

    await waitFor(() => expect(screen.getByLabelText('Full run')).not.toBeChecked());
  });

  it('explains a slot without brands and shows a failed backup', async () => {
    const status = {
      ...schedule,
      reason: 'no_brands',
      paused_until: null,
      last_run: { ...schedule.last_run, full: true, maintenance_error: 'OSError' },
    };
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith('/health')) return json(health);
      if (url.endsWith('/settings')) return json({ groups: {} });
      if (url.endsWith('/parser/schedule')) return json(status);
      return json({});
    });
    vi.stubGlobal('fetch', fetchMock);
    renderApp(<SettingsPage />);

    expect(await screen.findByText(/No brand has collected listings yet/)).toBeInTheDocument();
    expect(screen.getByText(/Backup or retention failed: OSError/)).toBeInTheDocument();
  });

  it('shows an error instead of hiding the schedule when it fails to load', async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith('/health')) return json(health);
      if (url.endsWith('/settings')) return json({ groups: {} });
      if (url.endsWith('/parser/schedule'))
        return json({ error: { code: 'boom', message: 'Schedule is broken' } }, 500);
      return json({});
    });
    vi.stubGlobal('fetch', fetchMock);
    renderApp(<SettingsPage />);

    expect(await screen.findByText(/Schedule is broken/)).toBeInTheDocument();
  });
});
