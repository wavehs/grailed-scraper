'use client';

import Link from 'next/link';
import { Suspense, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { ArrowLeft, Clock3, ExternalLink, PackageCheck, Percent, ShoppingBag, TrendingUp } from 'lucide-react';
import { GroupEditor, MoveListing, groupLabel } from '@/components/group-editor';
import { WeeklyCharts } from '@/components/trend-charts';
import { Badge } from '@/components/ui/badge';
import { Card } from '@/components/ui/card';
import { DataTable, TableCell, TableHead, TableHeaderCell, TableRow } from '@/components/ui/data-table';
import { PageHeader } from '@/components/ui/page-header';
import { StatCard } from '@/components/ui/stat-card';
import { EmptyState, ErrorState, LoadingState } from '@/components/states';
import { useI18n } from '@/lib/i18n';
import { useTrendCardQuery, useTypeNames } from '@/lib/queries';
import type { TrendListing, TrendRow, TrendVariant } from '@/lib/types';
import { formatCurrency, formatPercent } from '@/lib/utils';

function GroupCard() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const { locale, t } = useI18n();
  const { typeName } = useTypeNames();
  const id = Number(searchParams.get('id'));
  const groupId = Number.isInteger(id) && id > 0 ? id : null;
  const [windowDays, setWindowDays] = useState<7 | 30 | 90>(30);
  const card = useTrendCardQuery(groupId, windowDays);
  if (groupId === null) return <EmptyState />;
  if (card.isLoading) return <LoadingState />;
  if (!card.data) return <ErrorState error={card.error} retry={() => card.refetch()} />;
  const { group, metrics } = card.data;
  const title = `${group.brand} · ${groupLabel(group, t)} · ${typeName(group.product_type)}`;
  return (
    <section className="space-y-6" aria-labelledby="group-heading">
      <Link className="inline-flex items-center gap-2 text-sm text-[var(--accent)] hover:underline" href="/trends">
        <ArrowLeft size={16} />
        {t('backToTrends')}
      </Link>
      <PageHeader
        title={title}
        description={group.parent ? `${t('modelLine')}: ${group.parent.name}` : undefined}
        actions={
          <div className="inline-flex rounded-lg border border-[var(--border-default)] p-0.5" role="group" aria-label={t('trendWindow')}>
            {([7, 30, 90] as const).map((value) => (
              <button
                key={value}
                type="button"
                aria-pressed={windowDays === value}
                onClick={() => setWindowDays(value)}
                className={`rounded-md px-2.5 py-1 text-xs tabular-nums ${
                  windowDays === value
                    ? 'bg-[var(--accent-soft)] font-semibold text-[var(--accent)]'
                    : 'text-[var(--text-secondary)]'
                }`}
              >
                {value} {t('daysShort')}
              </button>
            ))}
          </div>
        }
      />
      <div className="flex flex-wrap gap-1">
        {group.status === 'auto' && <Badge variant="warning">{t('autoGroup')}</Badge>}
        {metrics?.is_new && <Badge variant="info">{t('newBadge')}</Badge>}
      </div>

      {metrics ? (
        <>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
            <StatCard
              label={t('trendScore')}
              value={metrics.trend_score === null ? '—' : Number(metrics.trend_score).toFixed(0)}
              icon={<TrendingUp size={18} />}
            />
            <StatCard
              label={`${t('salesInWindow')}, ${windowDays} ${t('daysShort')}`}
              value={`${metrics.sold} · ×${Number(metrics.growth).toFixed(2)}`}
              icon={<ShoppingBag size={18} />}
            />
            <StatCard
              label={t('timeToSell')}
              value={
                metrics.median_days_to_sell === null
                  ? '—'
                  : `${Number(metrics.median_days_to_sell).toFixed(0)} ${t('daysShort')}`
              }
              icon={<Clock3 size={18} />}
            />
            <StatCard label={t('sellThrough')} value={formatPercent(metrics.sell_through_30d)} icon={<Percent size={18} />} />
            <StatCard
              label={t('supplyNow')}
              value={`${metrics.active_now} · +${metrics.new_listings_14d}`}
              icon={<PackageCheck size={18} />}
            />
          </div>
          <p className="text-xs text-[var(--text-muted)]">
            {t('medianPrice')}: {formatCurrency(metrics.median_price, locale)}
            {metrics.price_change !== null &&
              ` (${Number(metrics.price_change) > 0 ? '+' : ''}${(Number(metrics.price_change) * 100).toFixed(0)}% ${t('priceChange')})`}
            {' · '}
            {t('salesGrowth')}: {metrics.sold_30d} / {metrics.sold_prev_30d} {t('vsPrevious')}
          </p>
          <Card className="p-5">
            <WeeklyCharts
              sales={metrics.weekly_sales}
              prices={card.data.weekly_median_price}
              computedAt={card.data.computed_at}
            />
          </Card>
        </>
      ) : (
        <EmptyState />
      )}

      {Boolean(card.data.versions.length) && (
        <div className="space-y-2">
          <h2 className="text-sm font-semibold uppercase tracking-wider text-[var(--text-muted)]">
            {t('versionsTable')}
          </h2>
          <MetricTable rows={card.data.versions} />
        </div>
      )}

      <div className="grid gap-5 lg:grid-cols-2">
        <VariantTable title={t('popularColors')} rows={card.data.colors} />
        <VariantTable title={t('popularSizes')} rows={card.data.sizes} />
      </div>

      <ListingTable title={t('recentSales')} rows={card.data.recent_sales} brandId={group.brand_id} sold />
      <ListingTable title={t('activeListingsNow')} rows={card.data.active_listings} brandId={group.brand_id} />

      {card.data.type_metrics && (
        <div className="space-y-2">
          <h2 className="text-sm font-semibold uppercase tracking-wider text-[var(--text-muted)]">
            {t('typeContext')}
          </h2>
          <MetricTable rows={[{ ...card.data.type_metrics, name: typeName(card.data.type_metrics.product_type) }]} />
        </div>
      )}

      <GroupEditor
        groupId={group.id}
        onChanged={(changed) => {
          if (changed.id !== group.id) router.push(`/group?id=${changed.id}`);
        }}
      />
    </section>
  );
}

function MetricTable({ rows }: { rows: TrendRow[] }) {
  const { locale, t } = useI18n();
  return (
    <DataTable>
      <TableHead>
        <tr>
          <TableHeaderCell>{t('name')}</TableHeaderCell>
          <TableHeaderCell>{t('trendScore')}</TableHeaderCell>
          <TableHeaderCell>{t('salesInWindow')}</TableHeaderCell>
          <TableHeaderCell>{t('salesGrowth')}</TableHeaderCell>
          <TableHeaderCell>{t('timeToSell')}</TableHeaderCell>
          <TableHeaderCell>{t('sellThrough')}</TableHeaderCell>
          <TableHeaderCell>{t('medianPrice')}</TableHeaderCell>
          <TableHeaderCell>{t('supplyNow')}</TableHeaderCell>
        </tr>
      </TableHead>
      <tbody>
        {rows.map((row) => (
          <TableRow key={row.scope_key}>
            <TableCell>
              {row.group_id ? (
                <Link className="text-[var(--accent)] hover:underline" href={`/group?id=${row.group_id}`}>
                  {row.name}
                </Link>
              ) : (
                row.name
              )}
            </TableCell>
            <TableCell className="tabular-nums">
              {row.trend_score === null ? '—' : Number(row.trend_score).toFixed(0)}
            </TableCell>
            <TableCell className="tabular-nums">{row.sold}</TableCell>
            <TableCell className="tabular-nums">×{Number(row.growth).toFixed(2)}</TableCell>
            <TableCell className="tabular-nums">
              {row.median_days_to_sell === null ? '—' : `${Number(row.median_days_to_sell).toFixed(0)} ${t('daysShort')}`}
            </TableCell>
            <TableCell className="tabular-nums">{formatPercent(row.sell_through_30d)}</TableCell>
            <TableCell className="tabular-nums">{formatCurrency(row.median_price, locale)}</TableCell>
            <TableCell className="tabular-nums">{row.active_now}</TableCell>
          </TableRow>
        ))}
      </tbody>
    </DataTable>
  );
}

function VariantTable({ title, rows }: { title: string; rows: TrendVariant[] }) {
  const { t } = useI18n();
  return (
    <Card className="space-y-2 p-4">
      <h2 className="text-sm font-semibold text-[var(--text-primary)]">{title}</h2>
      <p className="text-xs text-[var(--text-muted)]">{t('variantHelp')}</p>
      {!rows.length ? (
        <p className="text-sm text-[var(--text-muted)]">{t('noData')}</p>
      ) : (
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs text-[var(--text-muted)]">
              <th className="py-1 font-medium">{title}</th>
              <th className="py-1 font-medium">{t('sold')}</th>
              <th className="py-1 font-medium">{t('active')}</th>
              <th className="py-1 font-medium">{t('sellThrough')}</th>
            </tr>
          </thead>
          <tbody className="tabular-nums text-[var(--text-secondary)]">
            {rows.map((row) => (
              <tr key={row.value} className="border-t border-[var(--border-subtle)]">
                <td className="py-1 text-[var(--text-primary)]">{row.value}</td>
                <td className="py-1">{row.sold}</td>
                <td className="py-1">{row.active}</td>
                <td className="py-1">{formatPercent(row.sell_through)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Card>
  );
}

function ListingTable({
  title,
  rows,
  brandId,
  sold = false,
}: {
  title: string;
  rows: TrendListing[];
  brandId: number;
  sold?: boolean;
}) {
  const { locale, t } = useI18n();
  return (
    <div className="space-y-2">
      <h2 className="text-sm font-semibold uppercase tracking-wider text-[var(--text-muted)]">{title}</h2>
      {!rows.length ? (
        <EmptyState />
      ) : (
        <DataTable>
          <TableHead>
            <tr>
              <TableHeaderCell>{t('listing')}</TableHeaderCell>
              <TableHeaderCell>{t('price')}</TableHeaderCell>
              <TableHeaderCell>{sold ? t('timeToSell') : t('found')}</TableHeaderCell>
              <TableHeaderCell>{t('actions')}</TableHeaderCell>
            </tr>
          </TableHead>
          <tbody>
            {rows.map((row) => (
              <TableRow key={row.id}>
                <TableCell>
                  <a className="inline-flex items-center gap-1 text-[var(--accent)] hover:underline" href={row.url} target="_blank" rel="noreferrer" title={t('openOnGrailed')}>
                    {row.title}
                    <ExternalLink size={12} />
                  </a>
                  <p className="text-xs text-[var(--text-muted)]">
                    {[row.group_name, row.size, row.color].filter(Boolean).join(' · ')}
                    {row.relisted && ` · ${t('relisted')}`}
                  </p>
                </TableCell>
                <TableCell className="tabular-nums">{formatCurrency(row.price, locale)}</TableCell>
                <TableCell className="tabular-nums">
                  {sold
                    ? row.days_to_sell === null
                      ? '—'
                      : `${row.days_to_sell} ${t('daysShort')}`
                    : row.created_at
                      ? new Date(row.created_at).toLocaleDateString(locale)
                      : '—'}
                  {sold && row.sold_at && (
                    <p className="text-xs text-[var(--text-muted)]">{new Date(row.sold_at).toLocaleDateString(locale)}</p>
                  )}
                </TableCell>
                <TableCell>
                  <MoveListing listingId={row.id} brandId={brandId} />
                </TableCell>
              </TableRow>
            ))}
          </tbody>
        </DataTable>
      )}
    </div>
  );
}

export default function GroupPage() {
  return (
    <Suspense fallback={<LoadingState />}>
      <GroupCard />
    </Suspense>
  );
}
