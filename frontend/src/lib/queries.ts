import { useQuery } from '@tanstack/react-query';
import { getApi, getHealthApi } from '@/lib/api';
import type { ApiHealth, ParserHealth } from '@/lib/types';

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
