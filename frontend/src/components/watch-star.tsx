'use client';

import { useMutation, useQueryClient } from '@tanstack/react-query';
import { api, errorMessage } from '@/lib/api';
import { useI18n } from '@/lib/i18n';
import { useWatchlistQuery } from '@/lib/queries';
import type { WatchItem } from '@/lib/types';

/** The watched models; an empty list until (or unless) the API answers with one. */
function useWatchItems(): { items: WatchItem[]; isLoading: boolean } {
  const watchlist = useWatchlistQuery();
  const data = watchlist.data?.data;
  return { items: Array.isArray(data) ? data : [], isLoading: watchlist.isLoading };
}

/** The watchlist entry of a group, if it is watched. */
export function useWatchItem(groupId: number | null): WatchItem | undefined {
  const { items } = useWatchItems();
  return groupId === null ? undefined : items.find((item) => item.group_id === groupId);
}

/** A star that adds the model to the watchlist or removes it. */
export function WatchStar({ groupId }: { groupId: number }) {
  const { t } = useI18n();
  const client = useQueryClient();
  const { items, isLoading } = useWatchItems();
  const watched = items.some((item) => item.group_id === groupId);
  const toggle = useMutation({
    mutationFn: async (): Promise<void> => {
      if (watched) await api<void>(`/watchlist/${groupId}`, 'DELETE');
      else await api<WatchItem>(`/watchlist/${groupId}`, 'PUT', {});
    },
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ['watchlist'] });
      client.invalidateQueries({ queryKey: ['trends'] });
    },
  });
  const label = t(watched ? 'unwatch' : 'watch');
  return (
    <span className="inline-flex items-center gap-1">
      <button
        type="button"
        aria-pressed={watched}
        aria-label={label}
        title={label}
        disabled={toggle.isPending || isLoading}
        onClick={() => toggle.mutate()}
        className={`text-base leading-none disabled:opacity-40 ${
          watched ? 'text-[var(--warning)]' : 'text-[var(--text-muted)] hover:text-[var(--text-primary)]'
        }`}
      >
        {watched ? '★' : '☆'}
      </button>
      {toggle.isError && (
        <span role="alert" className="text-xs text-[var(--danger)]">
          {errorMessage(toggle.error)}
        </span>
      )}
    </span>
  );
}
