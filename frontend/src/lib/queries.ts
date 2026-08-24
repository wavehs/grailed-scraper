import { useQuery } from '@tanstack/react-query';
import { getApi, getHealthApi } from '@/lib/api';
import type {
  ApiHealth,
  BrandAnalyticsList,
  BrandList,
  DashboardRow,
  CursorPage,
  ModelGroupDetail,
  ParserHealth,
  RunList,
  SettingsResponse,
  DashboardProductType,
} from '@/lib/types';

export function useApiHealth() {
  const query = useQuery({
    queryKey: ['api-health'],
    queryFn: ({ signal }) => getApi<ApiHealth>('/health', signal),
    staleTime: 15_000,
    refetchInterval: 30_000,
  });
  return {
    ...query,
    writable: Boolean(query.data),
  };
}

export function useParserHealth() {
  return useQuery({
    queryKey: ['parser-health'],
    queryFn: ({ signal }) => getHealthApi<ParserHealth>('/parser/health', signal),
    staleTime: 5_000,
    refetchInterval: 10_000,
    retry: false,
  });
}

export function useBrandsQuery() {
  return useQuery({
    queryKey: ['brands'],
    queryFn: ({ signal }) => getApi<BrandList>('/brands', signal),
  });
}

export function useRunsQuery(
  limit: number = 50,
  offset: number = 0,
  refetchInterval?: number | false | ((query: any) => number | false),
) {
  return useQuery({
    queryKey: ['runs', limit, offset],
    queryFn: ({ signal }) =>
      getApi<RunList>(`/parser/runs?limit=${limit}&offset=${offset}`, signal),
    refetchInterval: refetchInterval ?? 5_000,
  });
}

export function useDashboardQuery(
  windowDays: number = 90,
  search: string = '',
  scoredOnly: boolean = true,
  cursor: string | null = null,
  sortBy: string = 'demand_score',
  sortDesc: boolean = true,
  brandId?: number,
  productType?: DashboardProductType,
  enabled: boolean = true,
) {
  return useQuery({
    queryKey: [
      'dashboard',
      windowDays,
      search,
      scoredOnly,
      cursor,
      sortBy,
      sortDesc,
      brandId,
      productType,
    ],
    queryFn: ({ signal }) =>
      getApi<CursorPage<DashboardRow>>(
        `/analytics/dashboard?${new URLSearchParams({
          window_days: String(windowDays),
          limit: '50',
          scored_only: String(scoredOnly),
          sort_by: sortBy,
          sort_desc: String(sortDesc),
          search,
          ...(cursor ? { cursor } : {}),
          ...(brandId ? { brand_id: String(brandId) } : {}),
          ...(productType ? { product_type: productType } : {}),
        })}`,
        signal,
      ),
    placeholderData: (previousData) => previousData,
    enabled,
  });
}

export function useBrandDashboardQuery(
  windowDays: number = 90,
  search: string = '',
  scoredOnly: boolean = true,
  sortBy: string = 'demand_score',
  sortDesc: boolean = true,
  productType?: DashboardProductType,
  enabled: boolean = true,
) {
  return useQuery({
    queryKey: ['brand-dashboard', windowDays, search, scoredOnly, sortBy, sortDesc, productType],
    queryFn: ({ signal }) =>
      getApi<BrandAnalyticsList>(
        `/analytics/brands?${new URLSearchParams({
          window_days: String(windowDays),
          limit: '200',
          scored_only: String(scoredOnly),
          sort_by: sortBy,
          sort_desc: String(sortDesc),
          search,
          ...(productType ? { product_type: productType } : {}),
        })}`,
        signal,
      ),
    placeholderData: (previousData) => previousData,
    enabled,
  });
}

export function useModelGroupDetailQuery(
  id: string | number,
  windowDays: number = 90,
  runId?: number,
) {
  return useQuery({
    queryKey: ['model', String(id), windowDays, runId],
    queryFn: ({ signal }) =>
      getApi<ModelGroupDetail>(
        `/analytics/model-groups/${id}?window_days=${windowDays}${runId ? `&run_id=${runId}` : ''}`,
        signal,
      ),
  });
}

export function useSettingsQuery() {
  return useQuery({
    queryKey: ['settings'],
    queryFn: ({ signal }) => getApi<SettingsResponse>('/settings', signal),
  });
}
