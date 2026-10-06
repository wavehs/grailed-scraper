import '@testing-library/jest-dom/vitest';
import { afterEach, vi } from 'vitest';
import { cleanup } from '@testing-library/react';

afterEach(() => cleanup());

Object.defineProperty(window, 'ResizeObserver', {
  writable: true,
  value: class ResizeObserver {
    observe() {}
    unobserve() {}
    disconnect() {}
  },
});

export const routerPush = vi.fn();
/** Query string returned by the mocked useSearchParams; tests may change it. */
export const navigation = { search: '' };

afterEach(() => {
  navigation.search = '';
});

vi.mock('next/navigation', () => ({
  usePathname: () => '/trends',
  useParams: () => ({ id: '1' }),
  useSearchParams: () => new URLSearchParams(navigation.search),
  useRouter: () => ({ push: routerPush, replace: routerPush, back: vi.fn() }),
}));
