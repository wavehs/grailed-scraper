const apiUrl = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000/api';

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
    public readonly code = 'request_failed',
    public readonly details?: unknown,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

async function responseError(response: Response): Promise<ApiError> {
  const body = await response.text();
  try {
    const payload = JSON.parse(body) as {
      detail?: string;
      error?: { message?: string; code?: string; details?: unknown };
    };
    return new ApiError(
      payload.error?.message ?? payload.detail ?? `API request failed: ${response.status}`,
      response.status,
      payload.error?.code,
      payload.error?.details,
    );
  } catch {
    return new ApiError(body || `API request failed: ${response.status}`, response.status);
  }
}

const wait = (delay: number, signal?: AbortSignal) =>
  new Promise<void>((resolve, reject) => {
    const timer = window.setTimeout(resolve, delay);
    signal?.addEventListener(
      'abort',
      () => {
        window.clearTimeout(timer);
        reject(new DOMException('Aborted', 'AbortError'));
      },
      { once: true },
    );
  });

export async function requestApi<T>(
  path: string,
  options: RequestInit = {},
  retries = options.method && options.method !== 'GET' ? 0 : 2,
  acceptedStatuses: number[] = [],
): Promise<T> {
  try {
    const response = await fetch(`${apiUrl}${path}`, {
      cache: 'no-store',
      ...options,
      headers: {
        ...(options.body === undefined ? {} : { 'Content-Type': 'application/json' }),
        ...options.headers,
      },
    });
    if (!response.ok && !acceptedStatuses.includes(response.status))
      throw await responseError(response);
    if (response.status === 204) return undefined as T;
    const text = await response.text();
    return (text ? JSON.parse(text) : undefined) as T;
  } catch (error) {
    if (options.signal?.aborted) throw error;
    const retryable = !(error instanceof ApiError) || error.status >= 500;
    if (!retryable || retries <= 0) throw error;
    await wait(250 * 2 ** (2 - retries) + Math.random() * 80, options.signal ?? undefined);
    return requestApi<T>(path, options, retries - 1, acceptedStatuses);
  }
}

export const getApi = <T>(path: string, signal?: AbortSignal) =>
  requestApi<T>(path, { method: 'GET', signal });

export const getHealthApi = <T>(path: string, signal?: AbortSignal) =>
  requestApi<T>(path, { method: 'GET', signal }, 2, [503]);

export const api = <T>(
  path: string,
  method: 'POST' | 'PATCH' | 'DELETE',
  body?: unknown,
  signal?: AbortSignal,
) =>
  requestApi<T>(path, {
    method,
    body: body === undefined ? undefined : JSON.stringify(body),
    signal,
  });

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : 'Request failed';
}
