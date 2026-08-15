import { useQuery } from '@tanstack/react-query';
import { getApi, getHealthApi } from '@/lib/api';
import type {
  ApiHealth,
  BrandList,
  DashboardRow,
  ModelGroupDetail,
  ModelRule,
  ParserHealth,
  RunList,
  SettingsResponse,
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

export function useRunsQuery(limit: number = 50, offset: number = 0, refetchInterval?: number | false | ((query: any) => number | false)) {
  return useQuery({
    queryKey: ['runs', limit, offset],
    queryFn: ({ signal }) =>
      getApi<RunList>(`/parser/runs?limit=${limit}&offset=${offset}`, signal),
    refetchInterval: refetchInterval ?? 5_000,
  });
}

export function useDashboardQuery(windowDays: number = 90) {
  return useQuery({
    queryKey: ['dashboard', windowDays],
    queryFn: ({ signal }) =>
      getApi<{ data: DashboardRow[] }>(`/analytics/dashboard?window_days=${windowDays}`, signal),
  });
}

export function useModelGroupDetailQuery(id: string | number) {
  return useQuery({
    queryKey: ['model', String(id)],
    queryFn: ({ signal }) =>
      getApi<ModelGroupDetail>(`/analytics/model-groups/${id}`, signal),
  });
}

export function useSettingsQuery() {
  return useQuery({
    queryKey: ['settings'],
    queryFn: ({ signal }) => getApi<SettingsResponse>('/settings', signal),
  });
}

export function useModelRulesQuery() {
  return useQuery({
    queryKey: ['rules'],
    queryFn: ({ signal }) => getApi<ModelRule[]>('/model-rules', signal),
  });
}
