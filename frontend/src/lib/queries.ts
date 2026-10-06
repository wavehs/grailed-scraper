import { useQuery } from '@tanstack/react-query';
import { getApi, getHealthApi } from '@/lib/api';
import { useI18n } from '@/lib/i18n';
import type {
  ApiHealth,
  BrandList,
  ParserHealth,
  RunList,
  SettingsResponse,
  GroupDetail,
  GroupList,
  Taxonomy,
  TrendCard,
  TrendFilters,
  TrendList,
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

export function useSettingsQuery() {
  return useQuery({
    queryKey: ['settings'],
    queryFn: ({ signal }) => getApi<SettingsResponse>('/settings', signal),
  });
}

export function useTaxonomyQuery() {
  return useQuery({
    queryKey: ['taxonomy'],
    queryFn: ({ signal }) => getApi<Taxonomy>('/grouping/taxonomy', signal),
    staleTime: Infinity,
  });
}

export function useGroupQuery(id: number | null) {
  return useQuery({
    queryKey: ['group', id],
    queryFn: ({ signal }) => getApi<GroupDetail>(`/groups/${id}`, signal),
    enabled: id !== null,
  });
}

export function useGroupsQuery(brandId?: number, productType?: string, linesOnly = false) {
  return useQuery({
    queryKey: ['groups', brandId, productType, linesOnly],
    queryFn: ({ signal }) =>
      getApi<GroupList>(
        `/groups?${new URLSearchParams({
          limit: '500',
          lines_only: String(linesOnly),
          ...(brandId ? { brand_id: String(brandId) } : {}),
          ...(productType ? { product_type: productType } : {}),
        })}`,
        signal,
      ),
    enabled: brandId !== undefined,
  });
}

/** Localized product-type and section names from the taxonomy. */
export function useTypeNames() {
  const { locale } = useI18n();
  const taxonomy = useTaxonomyQuery();
  const types = new Map((taxonomy.data?.types ?? []).map((item) => [item.id, item]));
  const sections = new Map((taxonomy.data?.sections ?? []).map((item) => [item.id, item]));
  return {
    taxonomy: taxonomy.data,
    typeName: (id?: string | null) => {
      const item = id ? types.get(id) : undefined;
      return item ? item[locale] : (id ?? '—');
    },
    sectionName: (id?: string | null) => {
      const item = id ? sections.get(id) : undefined;
      return item ? item[locale] : (id ?? '—');
    },
  };
}

export function trendParams(filters: TrendFilters, offset = 0): URLSearchParams {
  const params = new URLSearchParams({
    level: filters.level,
    window: String(filters.window),
    sort: filters.sort,
    desc: String(filters.desc),
    limit: '50',
    offset: String(offset),
  });
  if (filters.brandIds.length) params.set('brand_ids', filters.brandIds.join(','));
  if (filters.section) params.set('section', filters.section);
  if (filters.productType) params.set('product_type', filters.productType);
  if (filters.priceMin) params.set('price_min', filters.priceMin);
  if (filters.priceMax) params.set('price_max', filters.priceMax);
  if (filters.newOnly) params.set('new_only', 'true');
  if (filters.minSales) params.set('min_sales', String(filters.minSales));
  if (filters.search.trim()) params.set('search', filters.search.trim());
  return params;
}

export function useTrendsQuery(filters: TrendFilters, offset = 0) {
  return useQuery({
    queryKey: ['trends', filters, offset],
    queryFn: ({ signal }) =>
      getApi<TrendList>(`/trends?${trendParams(filters, offset)}`, signal),
    placeholderData: (previous) => previous,
  });
}

export function useTrendCardQuery(groupId: number | null, window: number) {
  return useQuery({
    queryKey: ['trend-card', groupId, window],
    queryFn: ({ signal }) =>
      getApi<TrendCard>(`/trends/groups/${groupId}?window=${window}`, signal),
    enabled: groupId !== null,
  });
}
