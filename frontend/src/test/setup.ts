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

vi.mock('next/navigation', () => ({
  usePathname: () => '/dashboard',
  useParams: () => ({ id: '1' }),
  useSearchParams: () => new URLSearchParams(),
}));
