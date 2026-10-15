import { describe, expect, it } from 'vitest';
import { trendExportHref } from '@/components/export-csv-link';
import type { TrendFilters } from '@/lib/types';

const filters: TrendFilters = {
  level: 'model',
  brandIds: [3, 7],
  section: '',
  productType: '',
  wardrobe: null,
  productTypes: ['parka', 'coat'],
  window: 90,
  period: 'window',
  monthFrom: '',
  monthTo: '',
  sort: 'liquidity',
  desc: true,
  priceMin: '',
  priceMax: '',
  newOnly: false,
  minSales: 0,
  search: ' Geo ',
  descriptors: false,
  condition: 'used',
  department: '',
  hideLowData: true,
  watchedOnly: true,
};

describe('trendExportHref', () => {
  it('carries the list filters and order but no page', () => {
    const url = new URL(trendExportHref(filters));

    expect(url.pathname.endsWith('/trends/export.csv')).toBe(true);
    expect(Object.fromEntries(url.searchParams)).toMatchObject({
      level: 'model',
      window: '90',
      sort: 'liquidity',
      desc: 'true',
      brand_ids: '3,7',
      product_type: 'parka,coat',
      search: 'Geo',
      condition: 'used',
      hide_low_data: 'true',
      watched_only: 'true',
    });
    expect(url.searchParams.has('limit')).toBe(false);
    expect(url.searchParams.has('offset')).toBe(false);
  });
});
