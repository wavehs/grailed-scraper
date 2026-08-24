'use client';

import { Suspense } from 'react';
import { useSearchParams } from 'next/navigation';
import { Dashboard } from '@/components/dashboard';
import { LoadingState } from '@/components/states';
import type { DashboardProductType } from '@/lib/types';

const PRODUCT_TYPES = new Set<DashboardProductType>(['footwear', 'clothing', 'accessories']);

function DashboardContent() {
  const searchParams = useSearchParams();
  const brandId = Number(searchParams.get('brand_id'));
  const productType = searchParams.get('product_type') ?? '';
  const viewMode = searchParams.get('view_mode') === 'brands' ? 'brands' : 'models';
  return (
    <Dashboard
      initialWindowDays={searchParams.get('window_days') === '30' ? 30 : 90}
      initialSearch={searchParams.get('search') ?? ''}
      initialLowData={searchParams.get('low_data') === 'true'}
      initialBrandId={Number.isInteger(brandId) && brandId > 0 ? brandId : undefined}
      initialProductType={
        PRODUCT_TYPES.has(productType as DashboardProductType)
          ? (productType as DashboardProductType)
          : undefined
      }
      initialViewMode={viewMode}
    />
  );
}

export default function DashboardPage() {
  return (
    <Suspense fallback={<LoadingState />}>
      <DashboardContent />
    </Suspense>
  );
}
