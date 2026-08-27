import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import AiGroupingPage from '@/app/ai-grouping/page';
import { renderApp } from '@/test/render';

const json = (body: unknown, status = 200) =>
  Promise.resolve(new Response(JSON.stringify(body), { status }));

const preflight = {
  mode: 'canary',
  gemini_configured: true,
  listing_count: 10_000,
  unique_input_count: 7_421,
  estimated_input_tokens: 800_000,
  estimated_output_tokens: 200_000,
  estimated_cost_usd: '0.08',
  budget_cap_usd: '0.50',
  can_start: true,
  data_fields: ['input_hash', 'brand', 'category', 'subcategory', 'title', 'locked_product_type'],
  api_key: 'must-never-be-rendered',
};

const completedRun = {
  id: 7,
  mode: 'canary',
  status: 'completed',
  cheap_model: 'gemini-2.5-flash-lite',
  strong_model: 'gemini-2.5-flash',
  prompt_version: 'ai-grouping-v1',
  grouping_version: 'ai-v1',
  budget_cap_usd: '0.50',
  estimated_cost_usd: '0.08',
  actual_cost_usd: '0.07',
  input_tokens: 780_000,
  output_tokens: 190_000,
  total_items: 10_000,
  unique_inputs: 7_421,
  resolved_items: 9_600,
  ambiguous_items: 250,
  unique_fallback_items: 150,
  failed_items: 0,
  warnings: [],
  created_at: '2026-08-24T18:00:00Z',
  finished_at: '2026-08-24T19:00:00Z',
  rollback_allowed: false,
  progress_percent: 100,
  examples: [
    {
      listing_id: 9,
      title: 'Chrome Hearts Cross Hat',
      old_group: 'Chrome Hearts Cross',
      new_group: 'Chrome Hearts Cross Hat',
      product_type: 'hat',
      confidence: '0.98',
    },
  ],
};

function mockApi(runs: unknown[] = [], detail?: unknown, preflightValue = preflight) {
  return vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.includes('/ai-grouping/preflight?')) return json(preflightValue);
    if (url.endsWith('/ai-grouping/runs') && init?.method === 'POST') return json(completedRun);
    if (/\/ai-grouping\/runs\/\d+$/.test(url)) return json(detail ?? runs[0]);
    if (url.includes('/ai-grouping/runs'))
      return json({ data: runs, total: runs.length, limit: 50, offset: 0 });
    return json({});
  });
}

beforeEach(() => window.localStorage.clear());

describe('AI grouping page', () => {
  it('shows preflight and the exact safe Google disclosure without rendering a secret', async () => {
    vi.stubGlobal('fetch', mockApi());
    renderApp(<AiGroupingPage />);

    expect(await screen.findByText('10,000')).toBeInTheDocument();
    expect(screen.getByText('7,421')).toBeInTheDocument();
    expect(screen.getByText('$0.08')).toBeInTheDocument();
    expect(
      screen.getByText(
        'Brand, category, subcategory, title, an opaque hash, and a derived locked product type are sent to Google.',
      ),
    ).toBeInTheDocument();
    expect(screen.getByText('Gemini configured')).toBeInTheDocument();
    expect(screen.queryByText('must-never-be-rendered')).not.toBeInTheDocument();
  });

  it('starts the canary with its fixed $0.50 cap', async () => {
    const fetchMock = mockApi();
    vi.stubGlobal('fetch', fetchMock);
    renderApp(<AiGroupingPage />);

    await userEvent.click(await screen.findByRole('button', { name: 'Start canary · $0.50 max' }));

    await waitFor(() => {
      const request = fetchMock.mock.calls.find(
        ([input, init]) => String(input).endsWith('/ai-grouping/runs') && init?.method === 'POST',
      );
      expect(request).toBeDefined();
      expect(JSON.parse(String(request?.[1]?.body))).toEqual({
        mode: 'canary',
        budget_cap_usd: '0.50',
      });
    });
  });

  it('uses the smaller remaining canary cap returned by preflight', async () => {
    const fetchMock = mockApi([], undefined, { ...preflight, budget_cap_usd: '0.42' });
    vi.stubGlobal('fetch', fetchMock);
    renderApp(<AiGroupingPage />);

    await userEvent.click(await screen.findByRole('button', { name: 'Start canary · $0.42 max' }));

    await waitFor(() => {
      const request = fetchMock.mock.calls.find(
        ([input, init]) => String(input).endsWith('/ai-grouping/runs') && init?.method === 'POST',
      );
      expect(JSON.parse(String(request?.[1]?.body))).toEqual({
        mode: 'canary',
        budget_cap_usd: '0.42',
      });
    });
  });

  it('shows active progress and disables every start action', async () => {
    const active = {
      ...completedRun,
      status: 'running',
      progress_percent: 42,
      resolved_items: 4_200,
      actual_cost_usd: '0.03',
      rollback_allowed: false,
      finished_at: undefined,
    };
    vi.stubGlobal('fetch', mockApi([active], active));
    renderApp(<AiGroupingPage />);

    expect(await screen.findByRole('progressbar', { name: 'Grouping progress' })).toHaveAttribute(
      'aria-valuenow',
      '42',
    );
    expect(screen.getByText('4,200 / 10,000')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Start canary · $0.50 max' })).toBeDisabled();
    expect(screen.getByRole('button', { name: /process remaining/i })).toBeDisabled();
    expect(screen.getByRole('button', { name: /process pending/i })).toBeDisabled();
  });

  it('does not offer rollback unless the server allows it', async () => {
    vi.stubGlobal('fetch', mockApi([completedRun]));
    renderApp(<AiGroupingPage />);

    expect(await screen.findByText('Chrome Hearts Cross Hat')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Roll back' })).not.toBeInTheDocument();
  });

  it('rolls back an eligible run only after a user click', async () => {
    const eligible = { ...completedRun, rollback_allowed: true };
    const fetchMock = mockApi([eligible]);
    vi.stubGlobal('fetch', fetchMock);
    renderApp(<AiGroupingPage />);

    await userEvent.click(await screen.findByRole('button', { name: 'Roll back' }));
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(
          ([input, init]) =>
            String(input).endsWith('/ai-grouping/runs/7/rollback') && init?.method === 'POST',
        ),
      ).toBe(true),
    );
  });
});
