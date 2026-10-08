'use client';

import Link from 'next/link';
import { Search } from 'lucide-react';
import { useQuery } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { groupLabel } from '@/components/group-editor';
import { Badge, statusVariant } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import {
  DataTable,
  TableCell,
  TableHead,
  TableHeaderCell,
  TableRow,
} from '@/components/ui/data-table';
import { PageHeader } from '@/components/ui/page-header';
import { EmptyState, ErrorState, LoadingState } from '@/components/states';
import { getApi } from '@/lib/api';
import { useI18n } from '@/lib/i18n';
import { useTypeNames } from '@/lib/queries';
import type { CatalogListingList } from '@/lib/types';
import { formatCurrency, formatDaysOnMarket } from '@/lib/utils';

export default function ListingsPage() {
  const { locale, t } = useI18n();
  const [search, setSearch] = useState('');
  const [debouncedSearch, setDebouncedSearch] = useState('');
  const [status, setStatus] = useState('');
  const [productType, setProductType] = useState('');
  const { taxonomy, typeName } = useTypeNames();
  const [cursors, setCursors] = useState<Array<string | null>>([null]);
  const [page, setPage] = useState(0);
  const cursor = cursors[page] ?? null;
  useEffect(() => {
    const timer = window.setTimeout(() => {
      setDebouncedSearch(search.trim());
      setCursors([null]);
      setPage(0);
    }, 300);
    return () => window.clearTimeout(timer);
  }, [search]);
  const query = useQuery({
    queryKey: ['listing-catalog', debouncedSearch, status, productType, cursor],
    queryFn: ({ signal }) => {
      const params = new URLSearchParams({ search: debouncedSearch });
      if (status) params.set('status', status);
      if (productType) params.set('product_type', productType);
      if (cursor) params.set('cursor', cursor);
      return getApi<CatalogListingList>(`/listings?${params}`, signal);
    },
  });
  return (
    <section className="space-y-5" aria-labelledby="catalog-heading">
      <PageHeader title={t('catalog')} description={t('catalogIntro')} />
      <Card className="flex flex-wrap gap-3 p-4">
        <label className="relative min-w-64 flex-1">
          <Search
            size={16}
            className="absolute left-3 top-1/2 -translate-y-1/2 text-[var(--text-muted)]"
          />
          <span className="sr-only">{t('search')}</span>
          <input
            className="w-full pl-9"
            placeholder={t('catalogSearch')}
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
        </label>
        <label>
          <span className="sr-only">{t('status')}</span>
          <select
            value={status}
            onChange={(event) => {
              setStatus(event.target.value);
              setCursors([null]);
              setPage(0);
            }}
          >
            <option value="">{t('allStatuses')}</option>
            <option value="active">{t('active')}</option>
            <option value="sold">{t('sold')}</option>
            <option value="removed_pending">{t('removedPending')}</option>
            <option value="removed">{t('removed')}</option>
          </select>
        </label>
        <label>
          <span className="sr-only">{t('productType')}</span>
          <select
            aria-label={t('productType')}
            value={productType}
            onChange={(event) => {
              setProductType(event.target.value);
              setCursors([null]);
              setPage(0);
            }}
          >
            <option value="">{t('allTypes')}</option>
            {(taxonomy?.types ?? []).map((item) => (
              <option key={item.id} value={item.id}>
                {item[locale]}
              </option>
            ))}
          </select>
        </label>
      </Card>
      {query.isLoading ? (
        <LoadingState />
      ) : query.error ? (
        <ErrorState error={query.error} retry={() => query.refetch()} />
      ) : (
        <>
          <p className="text-sm text-[var(--text-muted)]">
            {t('shown')}:{' '}
            <span className="tabular-nums text-[var(--text-primary)]">
              {query.data?.data.length ?? 0}
            </span>
          </p>
          <DataTable>
            <TableHead>
              <tr>
                <TableHeaderCell>{t('listing')}</TableHeaderCell>
                <TableHeaderCell>{t('model')}</TableHeaderCell>
                <TableHeaderCell>{t('status')}</TableHeaderCell>
                <TableHeaderCell>{t('timeOnMarket')}</TableHeaderCell>
                <TableHeaderCell>{t('price')}</TableHeaderCell>
                <TableHeaderCell>{t('modelSales')}</TableHeaderCell>
                <TableHeaderCell>{t('lastSeen')}</TableHeaderCell>
              </tr>
            </TableHead>
            <tbody>
              {query.data?.data.map((item) => (
                <TableRow key={item.id}>
                  <TableCell>
                    <a
                      className="font-medium text-[var(--accent)] hover:underline"
                      href={item.url}
                      rel="noreferrer"
                      target="_blank"
                    >
                      {item.title}
                    </a>
                    <p className="text-xs text-[var(--text-muted)]">
                      {item.brand} · {typeName(item.product_type)} · {item.size ?? '—'} ·{' '}
                      {item.color ?? '—'} · #{item.grailed_id}
                    </p>
                  </TableCell>
                  <TableCell>
                    {item.model_group_id ? (
                      <Link
                        className="text-[var(--accent)] hover:underline"
                        href={`/group?id=${item.model_group_id}`}
                      >
                        {groupLabel({ name: item.model_name ?? '', is_fallback: item.is_fallback, kind: item.kind }, t)}
                      </Link>
                    ) : (
                      '—'
                    )}
                  </TableCell>
                  <TableCell>
                    <Badge variant={statusVariant(item.status)}>{t(item.status)}</Badge>
                  </TableCell>
                  <TableCell>
                    <span className="text-xs tabular-nums text-[var(--text-secondary)]">
                      {formatDaysOnMarket(item.days_on_market, item.status, locale)}
                    </span>
                  </TableCell>
                  <TableCell>{formatCurrency(item.price, locale)}</TableCell>
                  <TableCell>
                    <span className="tabular-nums">{item.model_sold_count}</span> /{' '}
                    {item.model_active_count} {t('activeShort')}
                  </TableCell>
                  <TableCell>{new Date(item.last_seen_at).toLocaleDateString(locale)}</TableCell>
                </TableRow>
              ))}
            </tbody>
          </DataTable>
          {!query.data?.data.length && <EmptyState />}
          {(page > 0 || query.data?.next_cursor) && (
            <div className="flex items-center justify-end gap-2">
              <Button
                variant="secondary"
                disabled={page === 0 || query.isFetching}
                onClick={() => setPage((current) => Math.max(0, current - 1))}
              >
                {t('previous')}
              </Button>
              <Button
                variant="secondary"
                disabled={!query.data?.next_cursor || query.isFetching}
                onClick={() => {
                  const next = query.data?.next_cursor;
                  if (!next) return;
                  setCursors((current) => [...current.slice(0, page + 1), next]);
                  setPage((current) => current + 1);
                }}
              >
                {t('next')}
              </Button>
            </div>
          )}
        </>
      )}
    </section>
  );
}
