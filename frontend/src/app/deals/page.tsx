'use client';

import Link from 'next/link';
import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { ExternalLink, ThumbsDown, ThumbsUp } from 'lucide-react';
import { DealScannerCard, fill } from '@/components/deal-scanner';
import { formatRatio } from '@/components/liquidity-cells';
import { Button } from '@/components/ui/button';
import { DataTable, TableCell, TableHead, TableHeaderCell, TableRow } from '@/components/ui/data-table';
import { PageHeader } from '@/components/ui/page-header';
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
  return (
    <div className="space-y-4">
      <PageHeader title={t('deals')} description={t('dealsIntro')} />
      {data && <DealScannerCard scanner={data.scanner} summary={data.summary} writable={health.writable} />}
      <div className="flex flex-wrap items-center gap-3 text-sm">
        <Segmented
          label={t('dealsVerdict')}
          options={VERDICTS}
          value={filters.verdict}
          text={(item) => t(`dealsFilter_${item}`)}
          onChange={(verdict) => change({ verdict })}
        />
        <Segmented
          label={t('dealsSort_found')}
          options={SORTS}
          value={filters.sort}
          text={(item) => t(`dealsSort_${item}`)}
          onChange={(sort) => change({ sort })}
        />
        <label className="flex items-center gap-2 text-[var(--text-secondary)]">
          <input
            type="checkbox"
            checked={filters.includeInactive}
            onChange={(event) => change({ includeInactive: event.target.checked })}
          />
          {t('dealsIncludeInactive')}
        </label>
      </div>
      {deals.isLoading ? (
        <LoadingState />
      ) : deals.isError || !data ? (
        <ErrorState error={deals.error} retry={() => deals.refetch()} />
      ) : !data.items.length ? (
        <EmptyState message={t('dealsEmpty')} />
      ) : (
        <>
          <DataTable>
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
            offset={filters.offset}
            total={data.total}
            onChange={(offset) => setFilters((old) => ({ ...old, offset }))}
          />
        </>
      )}
    </div>
  );
}

function Segmented<T extends string>({
  label,
  options,
  value,
  text,
  onChange,
}: {
  label: string;
  options: T[];
  value: T;
  text: (item: T) => string;
  onChange: (item: T) => void;
}) {
  return (
    <div role="group" aria-label={label} className="flex overflow-hidden rounded-md border border-[var(--border-default)]">
      {options.map((item) => (
        <button
          key={item}
          type="button"
          aria-pressed={item === value}
          onClick={() => onChange(item)}
          className={`px-2.5 py-1 text-xs ${
            item === value ? 'bg-[var(--accent-soft)] text-[var(--accent)]' : 'text-[var(--text-secondary)]'
          }`}
        >
          {text(item)}
        </button>
      ))}
    </div>
  );
}

function Pager({ offset, total, onChange }: { offset: number; total: number; onChange: (offset: number) => void }) {
  const { t } = useI18n();
  if (total <= DEALS_PAGE) return null;
  const to = Math.min(offset + DEALS_PAGE, total);
  return (
    <div className="flex items-center gap-3 text-sm text-[var(--text-secondary)]">
      <Button size="sm" variant="secondary" disabled={offset === 0} onClick={() => onChange(Math.max(0, offset - DEALS_PAGE))}>
        {t('dealsPrev')}
      </Button>
      {fill(t('dealsPage'), { from: offset + 1, to, total })}
      <Button size="sm" variant="secondary" disabled={to >= total} onClick={() => onChange(offset + DEALS_PAGE)}>
        {t('dealsNext')}
      </Button>
    </div>
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
      <TableCell>
        <div className="flex items-start gap-3">
          {deal.photo && (
            // eslint-disable-next-line @next/next/no-img-element -- remote Grailed photo, static export
            <img src={deal.photo} alt="" loading="lazy" className="h-12 w-12 shrink-0 rounded object-cover" />
          )}
          <div className="min-w-0">
            <a
              href={deal.url}
              target="_blank"
              rel="noreferrer noopener"
              className="flex items-center gap-1 font-medium text-[var(--accent)] hover:underline"
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
        <span className="font-semibold tabular-nums">{formatCurrency(deal.price, locale)}</span>
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
      <TableCell className="font-semibold tabular-nums text-[var(--success)]">{formatRatio(deal.roi)}</TableCell>
      <TableCell className="tabular-nums">
        {deal.liquidity_score === null ? '—' : Number(deal.liquidity_score).toFixed(0)}
      </TableCell>
      <TableCell className="text-xs">{formatDate(deal.found_at, locale)}</TableCell>
      <TableCell>
        <div className="flex gap-1">
          <Button
            size="sm"
            variant={deal.verdict === 'good' ? 'primary' : 'ghost'}
            aria-label={t('dealsGood')}
            aria-pressed={deal.verdict === 'good'}
            icon={<ThumbsUp size={14} />}
            disabled={!writable || rate.isPending}
            onClick={() => choose('good')}
          />
          <Button
            size="sm"
            variant={deal.verdict === 'bad' ? 'danger' : 'ghost'}
            aria-label={t('dealsBad')}
            aria-pressed={deal.verdict === 'bad'}
            icon={<ThumbsDown size={14} />}
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
