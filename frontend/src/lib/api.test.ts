import { describe, expect, it, vi } from 'vitest';
import { getApi } from '@/lib/api';

describe('api client', () => {
  it('retries transient GET failures and returns normalized JSON', async () => {
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockResolvedValueOnce(
          new Response('{"error":{"message":"busy","code":"busy"}}', { status: 503 }),
        )
        .mockResolvedValueOnce(new Response('{"ok":true}', { status: 200 })),
    );
    await expect(getApi<{ ok: boolean }>('/health')).resolves.toEqual({ ok: true });
    expect(fetch).toHaveBeenCalledTimes(2);
  });

  it('does not retry 4xx and preserves the API error code', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response('{"error":{"message":"invalid","code":"validation_error"}}', {
          status: 422,
        }),
      ),
    );
    await expect(getApi('/bad')).rejects.toMatchObject({
      status: 422,
      code: 'validation_error',
      message: 'invalid',
    });
    expect(fetch).toHaveBeenCalledTimes(1);
  });
});
