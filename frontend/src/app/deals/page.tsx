'use client';

import Link from 'next/link';
import { useEffect, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { ExternalLink, ThumbsDown, ThumbsUp } from 'lucide-react';
import { DealScannerBar, fill } from '@/components/deal-scanner';
import { formatRatio } from '@/components/liquidity-cells';
import { Button } from '@/components/ui/button';
import { DataTable, TableCell, TableHead, TableHeaderCell, TableRow } from '@/components/ui/data-table';
import { PageHeader } from '@/components/ui/page-header';
import { Pager } from '@/components/ui/pager';
import { Segmented } from '@/components/ui/segmented';
import { Toolbar } from '@/components/ui/toolbar';
import { EmptyState, ErrorState, LoadingState } from '@/components/states';
import { api, errorMessage } from '@/lib/api';
import { useI18n } from '@/lib/i18n';
import { DEALS_PAGE, useApiHealth, useDealsQuery } from '@/lib/queries';
import type { DealFilters, DealItem, DealVerdict } from '@/lib/types';
import { formatCurrency, formatDate } from '@/lib/utils';

const VERDICTS: DealFilters['verdict'][] = ['all', 'unrated', 'good', 'bad'];
const SORTS: DealFilters['sort'][] = ['found', 'roi'];

export default function DealsPage() {
  const { t } = useI18n();
  const health = useApiHealth();
  const [filters, setFilters] = useState<DealFilters>({
    verdict: 'all',
    sort: 'found',
    includeInactive: false,
    offset: 0,
  });
  const deals = useDealsQuery(filters);
  const change = (next: Partial<DealFilters>) => setFilters((old) => ({ ...old, offset: 0, ...next }));
  const data = deals.data;
  // A rating or a scan can empty the last page; step back to the page that still has rows.
  const lastOffset = data ? Math.max(0, Math.ceil(data.total / DEALS_PAGE) - 1) * DEALS_PAGE : 0;
  const overflow = !!data && !data.items.length && filters.offset > lastOffset;
  useEffect(() => {
    if (overflow) setFilters((old) => ({ ...old, offset: lastOffset }));
  }, [overflow, lastOffset]);
  return (
    <section aria-labelledby="deals-heading">
      <PageHeader title={t('deals')} headingId="deals-heading" description={t('dealsIntro')} />
      {data && <DealScannerBar scanner={data.scanner} summary={data.summary} writable={health.writable} />}
      <Toolbar>
        <Segmented
          label={t('dealsVerdict')}
          options={VERDICTS.map((value) => ({ value, label: t(`dealsFilter_${value}`) }))}
          value={filters.verdict}
          onChange={(verdict) => change({ verdict })}
        />
        <Segmented
          label={t('dealsSort_found')}
          options={SORTS.map((value) => ({ value, label: t(`dealsSort_${value}`) }))}
          value={filters.sort}
          onChange={(sort) => change({ sort })}
        />
        <button
          type="button"
          className="chip"
          aria-pressed={filters.includeInactive}
          onClick={() => change({ includeInactive: !filters.includeInactive })}
        >
          {t('dealsIncludeInactive')}
        </button>
        {data && (
          <span className="ml-auto text-xs tabular-nums text-[var(--text-muted)]">
            {t('rowsTotal').replace('{count}', String(data.total))}
          </span>
        )}
      </Toolbar>
      {deals.isLoading ? (
        <LoadingState />
      ) : deals.isError || !data ? (
        <ErrorState error={deals.error} retry={() => deals.refetch()} />
      ) : !data.items.length ? (
        <EmptyState message={t('dealsEmpty')} />
      ) : (
        <>
          <DataTable bleed className="[&_td:not(:first-child)]:whitespace-nowrap">
            <TableHead>
              <tr>
                <TableHeaderCell>{t('dealsListing')}</TableHeaderCell>
                <TableHeaderCell>{t('dealsPrice')}</TableHeaderCell>
                <TableHeaderCell>{t('dealsMarket')}</TableHeaderCell>
                <TableHeaderCell>{t('dealsNet')}</TableHeaderCell>
                <TableHeaderCell>{t('dealsRoi')}</TableHeaderCell>
                <TableHeaderCell>{t('dealsScore')}</TableHeaderCell>
                <TableHeaderCell>{t('dealsFound')}</TableHeaderCell>
                <TableHeaderCell>{t('dealsVerdict')}</TableHeaderCell>
              </tr>
            </TableHead>
            <tbody>
              {data.items.map((deal) => (
                <DealRow key={deal.id} deal={deal} writable={health.writable} />
              ))}
            </tbody>
          </DataTable>
          <Pager
            range={fill(t('dealsPage'), {
              from: filters.offset + 1,
              to: Math.min(filters.offset + DEALS_PAGE, data.total),
              total: data.total,
            })}
            hasPrevious={filters.offset > 0}
            hasNext={filters.offset + DEALS_PAGE < data.total}
            onPrevious={() => setFilters((old) => ({ ...old, offset: Math.max(0, old.offset - DEALS_PAGE) }))}
            onNext={() => setFilters((old) => ({ ...old, offset: old.offset + DEALS_PAGE }))}
          />
        </>
      )}
    </section>
  );
}

function DealRow({ deal, writable }: { deal: DealItem; writable: boolean }) {
  const { locale, t } = useI18n();
  const client = useQueryClient();
  const rate = useMutation({
    mutationFn: (verdict: DealVerdict | null) => api<DealItem>(`/deals/${deal.id}`, 'PATCH', { verdict }),
    onSuccess: () => client.invalidateQueries({ queryKey: ['deals'] }),
  });
  // A second click on the chosen verdict clears it.
  const choose = (verdict: DealVerdict) => rate.mutate(deal.verdict === verdict ? null : verdict);
  return (
    <TableRow>
      <TableCell className="min-w-72 max-w-md">
        <div className="flex items-start gap-3">
          {deal.photo && (
            // eslint-disable-next-line @next/next/no-img-element -- remote Grailed photo, static export
            <img src={deal.photo} alt="" loading="lazy" className="h-10 w-10 shrink-0 rounded-[5px] object-cover" />
          )}
          <div className="min-w-0">
            <a
              href={deal.url}
              target="_blank"
              rel="noreferrer noopener"
              className="flex items-center gap-1 text-[var(--text-primary)] hover:underline"
            >
              <span className="line-clamp-2">{deal.title}</span>
              <ExternalLink size={12} className="shrink-0" />
            </a>
            <Link className="text-xs text-[var(--text-secondary)] hover:underline" href={`/group?id=${deal.group_id}`}>
              {deal.brand} · {deal.group}
            </Link>
            <p className="text-xs text-[var(--text-muted)]">
              {[deal.size, t(`dealsBucket_${deal.bucket}`)].filter(Boolean).join(' · ')}
              {deal.listing_status !== 'active' && ` · ${t('dealsSold')}`}
            </p>
          </div>
        </div>
      </TableCell>
      <TableCell>
        <span className="font-medium tabular-nums text-[var(--text-primary)]">{formatCurrency(deal.price, locale)}</span>
        {deal.shipping ? (
          <p className="text-xs text-[var(--text-muted)]">
            {fill(t('dealsShipping'), { amount: formatCurrency(deal.shipping, locale) })}
          </p>
        ) : null}
      </TableCell>
      <TableCell className="tabular-nums">
        {formatCurrency(deal.p25, locale)} / {formatCurrency(deal.p50, locale)}
      </TableCell>
      <TableCell className="tabular-nums">{formatCurrency(deal.net, locale)}</TableCell>
      <TableCell className="font-medium tabular-nums text-[var(--success)]">{formatRatio(deal.roi)}</TableCell>
      <TableCell className="tabular-nums">
        {deal.liquidity_score === null ? '—' : Number(deal.liquidity_score).toFixed(0)}
      </TableCell>
      <TableCell className="text-[var(--text-secondary)]">{formatDate(deal.found_at, locale)}</TableCell>
      <TableCell>
        <div className="flex gap-1">
          <Button
            size="sm"
            variant={deal.verdict === 'good' ? 'primary' : 'ghost'}
            aria-label={t('dealsGood')}
            aria-pressed={deal.verdict === 'good'}
            icon={<ThumbsUp size={13} />}
            disabled={!writable || rate.isPending}
            onClick={() => choose('good')}
          />
          <Button
            size="sm"
            variant={deal.verdict === 'bad' ? 'danger' : 'ghost'}
            aria-label={t('dealsBad')}
            aria-pressed={deal.verdict === 'bad'}
            icon={<ThumbsDown size={13} />}
            disabled={!writable || rate.isPending}
            onClick={() => choose('bad')}
          />
        </div>
        {rate.isError && (
          <p role="alert" className="text-xs text-[var(--danger)]">
            {errorMessage(rate.error)}
          </p>
        )}
      </TableCell>
    </TableRow>
  );
}
