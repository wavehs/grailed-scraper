import { Dashboard } from '@/components/dashboard';
import type { DashboardProductType } from '@/lib/types';

const PRODUCT_TYPES = new Set<DashboardProductType>(['footwear', 'clothing', 'accessories']);

export default async function DashboardPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const params = await searchParams;
  const offsetValue = Number(params.offset);
  const brandId = Number(params.brand_id);
  const productType = typeof params.product_type === 'string' ? params.product_type : '';
  return (
    <Dashboard
      initialWindowDays={params.window_days === '30' ? 30 : 90}
      initialSearch={typeof params.search === 'string' ? params.search : ''}
      initialLowData={params.low_data === 'true'}
      initialOffset={Number.isInteger(offsetValue) && offsetValue >= 0 ? offsetValue : 0}
      initialBrandId={Number.isInteger(brandId) && brandId > 0 ? brandId : undefined}
      initialProductType={
        PRODUCT_TYPES.has(productType as DashboardProductType)
          ? (productType as DashboardProductType)
          : undefined
      }
    />
  );
}
