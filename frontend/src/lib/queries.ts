import { useQuery } from '@tanstack/react-query';
import { getApi } from '@/lib/api';
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
    writable: query.data?.source_mode === 'mock' || query.data?.source_mode === 'replay',
  };
}

export function useParserHealth() {
  return useQuery({
    queryKey: ['parser-health'],
    queryFn: ({ signal }) => getApi<ParserHealth>('/parser/health', signal),
    staleTime: 5_000,
    refetchInterval: 10_000,
    retry: false,
  });
}
