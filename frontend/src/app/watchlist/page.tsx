'use client';

import Link from 'next/link';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Trash2 } from 'lucide-react';
import { formatRatio } from '@/components/liquidity-cells';
import { WatchDelta, score, signed } from '@/components/watch-changes';
import { Button } from '@/components/ui/button';
import { DataTable, TableCell, TableHead, TableHeaderCell, TableRow } from '@/components/ui/data-table';
import { PageHeader } from '@/components/ui/page-header';
import { EmptyState, ErrorState, LoadingState } from '@/components/states';
import { api, errorMessage } from '@/lib/api';
import { useI18n } from '@/lib/i18n';
import { useWatchlistQuery } from '@/lib/queries';
import type { WatchItem } from '@/lib/types';
import { formatCurrency, formatDate } from '@/lib/utils';

export default function WatchlistPage() {
  const { t } = useI18n();
  const watchlist = useWatchlistQuery();
  const rows = watchlist.data?.data ?? [];
  return (
    <section aria-labelledby="watchlist-heading">
      <PageHeader
        title={t('watchlist')}
        headingId="watchlist-heading"
        description={t('watchlistIntro')}
        actions={
          rows.length > 0 && (
            <span className="text-xs tabular-nums text-[var(--text-muted)]">
              {t('rowsTotal').replace('{count}', String(rows.length))}
            </span>
          )
        }
      />
      {watchlist.isLoading ? (
        <LoadingState />
      ) : watchlist.isError ? (
        <ErrorState error={watchlist.error} retry={() => watchlist.refetch()} />
      ) : !rows.length ? (
        <EmptyState message={t('watchEmpty')} />
      ) : (
        <DataTable bleed className="[&_td:not(:first-child)]:whitespace-nowrap">
          <TableHead>
            <tr>
              <TableHeaderCell>{t('watchModel')}</TableHeaderCell>
              <TableHeaderCell>{t('watchAdded')}</TableHeaderCell>
              <TableHeaderCell>{t('watchScore')}</TableHeaderCell>
              <TableHeaderCell>{t('watchPrice')}</TableHeaderCell>
              <TableHeaderCell>{t('watchSupply')}</TableHeaderCell>
              <TableHeaderCell>{t('watchSold')}</TableHeaderCell>
              <TableHeaderCell>{t('watchNote')}</TableHeaderCell>
              <TableHeaderCell>
                <span className="sr-only">{t('actions')}</span>
              </TableHeaderCell>
            </tr>
          </TableHead>
          <tbody>
            {rows.map((item) => (
              <WatchRow key={item.group_id} item={item} />
            ))}
          </tbody>
        </DataTable>
      )}
    </section>
  );
}

function WatchRow({ item }: { item: WatchItem }) {
  const { locale, t } = useI18n();
  const client = useQueryClient();
  const refresh = () => {
    client.invalidateQueries({ queryKey: ['watchlist'] });
    client.invalidateQueries({ queryKey: ['trends'] });
  };
  const save = useMutation({
    mutationFn: (note: string) => api<WatchItem>(`/watchlist/${item.group_id}`, 'PATCH', { note }),
    onSuccess: refresh,
  });
  const remove = useMutation({
    mutationFn: () => api<void>(`/watchlist/${item.group_id}`, 'DELETE'),
    onSuccess: refresh,
  });
  const { baseline, current, changes } = item;
  return (
    <TableRow>
      <TableCell>
        <Link className="text-[var(--text-primary)] hover:underline" href={`/group?id=${item.group_id}`}>
          {item.brand} · {item.name}
        </Link>
        {item.retired && <p className="text-xs text-[var(--text-muted)]">{t('watchRetired')}</p>}
      </TableCell>
      <TableCell className="text-[var(--text-secondary)]">{formatDate(item.added_at, locale)}</TableCell>
      {baseline && current && changes ? (
        <>
          <TableCell>
            <WatchDelta
              before={score(baseline.liquidity_score)}
              after={score(current.liquidity_score)}
              change={signed(changes.liquidity_score === null ? null : Number(changes.liquidity_score))}
            />
          </TableCell>
          <TableCell>
            <WatchDelta
              before={formatCurrency(baseline.median_price, locale)}
              after={formatCurrency(current.median_price, locale)}
              change={formatRatio(changes.median_price, true)}
            />
          </TableCell>
          <TableCell>
            <WatchDelta before={String(baseline.active_now)} after={String(current.active_now)} change={signed(changes.active_now)} />
          </TableCell>
          <TableCell>
            <WatchDelta before={String(baseline.sold_30d)} after={String(current.sold_30d)} change={signed(changes.sold_30d)} />
          </TableCell>
        </>
      ) : (
        <TableCell colSpan={4}>
          <span className="text-xs text-[var(--text-muted)]">{t('watchNoHistory')}</span>
        </TableCell>
      )}
      <TableCell>
        <input
          aria-label={t('watchNote')}
          className="control-sm w-44"
          placeholder={t('watchNotePlaceholder')}
          maxLength={500}
          defaultValue={item.note ?? ''}
          onBlur={(event) => {
            if (event.target.value.trim() !== (item.note ?? '')) save.mutate(event.target.value);
          }}
        />
        {(save.isError || remove.isError) && (
          <p role="alert" className="text-xs text-[var(--danger)]">
            {errorMessage(save.error ?? remove.error)}
          </p>
        )}
      </TableCell>
      <TableCell>
        <Button
          size="sm"
          variant="ghost"
          icon={<Trash2 size={13} />}
          disabled={remove.isPending}
          onClick={() => remove.mutate()}
        >
          {t('watchRemove')}
        </Button>
      </TableCell>
    </TableRow>
  );
}
