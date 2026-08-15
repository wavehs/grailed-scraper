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
