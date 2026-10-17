'use client';

import Link from 'next/link';
import { useQuery } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { groupLabel } from '@/components/group-editor';
import { Badge, statusVariant } from '@/components/ui/badge';
import {
  DataTable,
  TableCell,
  TableHead,
  TableHeaderCell,
  TableRow,
} from '@/components/ui/data-table';
import { PageHeader } from '@/components/ui/page-header';
import { Pager } from '@/components/ui/pager';
import { SearchInput } from '@/components/ui/search-input';
import { Toolbar } from '@/components/ui/toolbar';
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
  const resetPages = () => {
    setCursors([null]);
    setPage(0);
  };
  const rows = query.data?.data ?? [];
  return (
    <section aria-labelledby="catalog-heading">
      <PageHeader
        title={t('catalog')}
        headingId="catalog-heading"
        description={t('catalogIntro')}
      />
      <Toolbar>
        <SearchInput
          label={t('search')}
          placeholder={t('catalogSearch')}
          className="sm:w-72"
          value={search}
          onChange={setSearch}
        />
        <select
          aria-label={t('status')}
          className="control-sm"
          value={status}
          onChange={(event) => {
            setStatus(event.target.value);
            resetPages();
          }}
        >
          <option value="">{t('allStatuses')}</option>
          <option value="active">{t('active')}</option>
          <option value="sold">{t('sold')}</option>
          <option value="removed_pending">{t('removedPending')}</option>
          <option value="removed">{t('removed')}</option>
        </select>
        <select
          aria-label={t('productType')}
          className="control-sm"
          value={productType}
          onChange={(event) => {
            setProductType(event.target.value);
            resetPages();
          }}
        >
          <option value="">{t('allTypes')}</option>
          {(taxonomy?.types ?? []).map((item) => (
            <option key={item.id} value={item.id}>
              {item[locale]}
            </option>
          ))}
        </select>
        {query.data && (
          <span className="ml-auto text-xs tabular-nums text-[var(--text-muted)]">
            {/* Cursor pages have no total; this counts the rows on the current page. */}
            {t('shown')}: {rows.length}
          </span>
        )}
      </Toolbar>
      {query.isLoading ? (
        <LoadingState />
      ) : query.error ? (
        <ErrorState error={query.error} retry={() => query.refetch()} />
      ) : (
        <>
          {!rows.length ? (
            <EmptyState />
          ) : (
            <DataTable bleed className="[&_td:not(:first-child)]:whitespace-nowrap">
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
                {rows.map((item) => (
                  <TableRow key={item.id}>
                    <TableCell className="min-w-72 max-w-md">
                      <a
                        className="text-[var(--text-primary)] hover:underline"
                        href={item.url}
                        rel="noreferrer"
                        target="_blank"
                      >
                        {item.title}
                      </a>
                      <p className="truncate text-xs text-[var(--text-muted)]">
                        {item.brand} · {typeName(item.product_type)} · {item.size ?? '—'} ·{' '}
                        {item.color ?? '—'} · #{item.grailed_id}
                      </p>
                    </TableCell>
                    <TableCell>
                      {item.model_group_id ? (
                        <Link
                          className="text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:underline"
                          href={`/group?id=${item.model_group_id}`}
                        >
                          {groupLabel(
                            {
                              name: item.model_name ?? '',
                              is_fallback: item.is_fallback,
                              kind: item.kind,
                            },
                            t,
                          )}
                        </Link>
                      ) : (
                        '—'
                      )}
                    </TableCell>
                    <TableCell>
                      <Badge variant={statusVariant(item.status)}>{t(item.status)}</Badge>
                    </TableCell>
                    <TableCell>
                      <span className="tabular-nums text-[var(--text-secondary)]">
                        {formatDaysOnMarket(item.days_on_market, item.status, locale)}
                      </span>
                    </TableCell>
                    <TableCell>{formatCurrency(item.price, locale)}</TableCell>
                    <TableCell>
                      <span className="tabular-nums">{item.model_sold_count}</span>
                      <span className="text-[var(--text-muted)]">
                        {' '}
                        / {item.model_active_count} {t('activeShort')}
                      </span>
                    </TableCell>
                    <TableCell className="text-[var(--text-secondary)]">
                      {new Date(item.last_seen_at).toLocaleDateString(locale)}
                    </TableCell>
                  </TableRow>
                ))}
              </tbody>
            </DataTable>
          )}
          {/* Outside the empty state, so an empty later page can still go back. */}
          <Pager
            hasPrevious={page > 0}
            hasNext={Boolean(query.data?.next_cursor)}
            busy={query.isFetching}
            onPrevious={() => setPage((current) => Math.max(0, current - 1))}
            onNext={() => {
              const next = query.data?.next_cursor;
              if (!next) return;
              setCursors((current) => [...current.slice(0, page + 1), next]);
              setPage((current) => current + 1);
            }}
          />
        </>
      )}
    </section>
  );
}
