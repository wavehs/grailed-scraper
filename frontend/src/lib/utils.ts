import { type ClassValue, clsx } from 'clsx';
import { twMerge } from 'tailwind-merge';

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function formatPercent(value?: string | number | null): string {
  if (value === undefined || value === null || value === '') return '—';
  const num = Number(value);
  if (Number.isNaN(num)) return '—';
  const multiplier = Math.abs(num) <= 1 ? 100 : 1;
  return `${(num * multiplier).toFixed(1)}%`;
}

export function formatCurrency(cents?: number | null, locale: string = 'en-US'): string {
  if (cents === undefined || cents === null) return '—';
  return new Intl.NumberFormat(locale, {
    style: 'currency',
    currency: 'USD',
    maximumFractionDigits: 0,
  }).format(cents / 100);
}

export function formatDate(value?: string | null, locale: string = 'en-US'): string {
  if (!value) return '—';
  try {
    return new Date(value).toLocaleString(locale);
  } catch {
    return value;
  }
}

export function formatDaysOnMarket(
  days?: number | null,
  status: string = 'sold',
  locale: string = 'en',
): string {
  if (days === undefined || days === null) return '—';
  const isRu = locale.startsWith('ru');
  if (status === 'active') {
    if (days < 1) {
      return isRu ? '< 1 дн. в продаже' : 'Active < 1 d';
    }
    return isRu ? `${days} дн. в продаже` : `Active ${days} d`;
  }
  if (days < 1) {
    return isRu ? '< 1 дн.' : '< 1 d';
  }
  return isRu ? `${days} дн.` : `${days} d`;
}
