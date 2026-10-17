'use client';

import Link from 'next/link';
import { ExternalLink } from 'lucide-react';
import { MoveListing, groupLabel } from '@/components/group-editor';
import { formatRatio } from '@/components/liquidity-cells';
import { DataTable, TableCell, TableHead, TableHeaderCell, TableRow } from '@/components/ui/data-table';
import { ContentSection } from '@/components/ui/section';
import { useI18n } from '@/lib/i18n';
import { isLiquiditySettings, periodLabel } from '@/lib/liquidity';
import { useLiquiditySettingsQuery } from '@/lib/queries';
import type { TrendListing, TrendRow, TrendVariant } from '@/lib/types';
import { formatCurrency, formatNumber, formatPercent } from '@/lib/utils';

/** Appends the ranking period to a label ("Likes, 30 d"); the label alone until it is known. */
export function useRankingPeriodLabel() {
  const { t } = useI18n();
  const settings = useLiquiditySettingsQuery();
  const periodDays = isLiquiditySettings(settings.data) ? settings.data.period_days : null;
  return (label: string) => (periodDays === null ? label : `${label}, ${periodLabel(periodDays, t)}`);
}

export function MetricTable({ rows }: { rows: TrendRow[] }) {
  const { locale, t } = useI18n();
  const forPeriod = useRankingPeriodLabel();
  return (
    <DataTable>
      <TableHead>
        <tr>
          <TableHeaderCell>{t('name')}</TableHeaderCell>
          <TableHeaderCell>{forPeriod(t('liquidityScore'))}</TableHeaderCell>
          <TableHeaderCell>{t('salesInWindow')}</TableHeaderCell>
          <TableHeaderCell>{t('daysToSell')}</TableHeaderCell>
          <TableHeaderCell>{forPeriod(t('likesColumn'))}</TableHeaderCell>
          <TableHeaderCell>{t('medianPrice')}</TableHeaderCell>
          <TableHeaderCell>{t('supplyNow')}</TableHeaderCell>
        </tr>
      </TableHead>
      <tbody>
        {rows.map((row) => (
          <TableRow key={row.scope_key}>
            <TableCell>
              {row.group_id ? (
                <Link className="text-[var(--text-primary)] hover:text-[var(--accent-hover)]" href={`/group?id=${row.group_id}`}>
                  {row.name}
                </Link>
              ) : (
                row.name
              )}
            </TableCell>
            <TableCell className="tabular-nums">
              <span title={row.liquidity_score === null ? t('belowThreshold') : undefined}>
                {formatNumber(row.liquidity_score, 0, locale)}
              </span>
            </TableCell>
            <TableCell className="tabular-nums">{row.sold}</TableCell>
            <TableCell className="tabular-nums">{formatNumber(row.median_days_to_sell, 1, locale)}</TableCell>
            <TableCell className="tabular-nums">{formatNumber(row.period_median_likes, 0, locale)}</TableCell>
            <TableCell className="tabular-nums">{formatCurrency(row.median_price, locale)}</TableCell>
            <TableCell className="tabular-nums">{row.active_now}</TableCell>
          </TableRow>
        ))}
      </tbody>
    </DataTable>
  );
}

export function VariantTable({ title, rows, sizes = false }: { title: string; rows: TrendVariant[]; sizes?: boolean }) {
  const { t } = useI18n();
  const showP30 = sizes && rows.some((row) => (row.p30 ?? null) !== null);
  return (
    <ContentSection title={title} description={t('variantHelp')}>
      {!rows.length ? (
        <p className="text-xs text-[var(--text-muted)]">{t('noData')}</p>
      ) : (
        <DataTable>
          <TableHead>
            <tr>
              <TableHeaderCell>{title}</TableHeaderCell>
              <TableHeaderCell>{t('sold')}</TableHeaderCell>
              <TableHeaderCell>{t('active')}</TableHeaderCell>
              <TableHeaderCell>{t('sellThrough')}</TableHeaderCell>
              {showP30 && <TableHeaderCell>{t('sizeP30')}</TableHeaderCell>}
            </tr>
          </TableHead>
          <tbody className="tabular-nums">
            {rows.map((row) => (
              <TableRow key={row.value} className="h-9">
                <TableCell className="py-1.5 text-[var(--text-primary)]">
                  {row.value}
                  {row.hot && (
                    <span className="ml-1.5 text-xs text-[var(--warning)]" title={t('hotSizeHelp')}>
                      🔥 {t('hotSize')}
                    </span>
                  )}
                </TableCell>
                <TableCell className="py-1.5">{row.sold}</TableCell>
                <TableCell className="py-1.5">{row.active}</TableCell>
                <TableCell className="py-1.5">{formatPercent(row.sell_through)}</TableCell>
                {showP30 && (
                  <TableCell className="py-1.5">
                    {(row.p30 ?? null) === null ? '—' : `${formatRatio(row.p30)} · n=${row.p30_n ?? 0}`}
                  </TableCell>
                )}
              </TableRow>
            ))}
          </tbody>
        </DataTable>
      )}
    </ContentSection>
  );
}

export function ListingTable({
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
    <ContentSection title={title}>
      {!rows.length ? (
        <p className="text-xs text-[var(--text-muted)]">{t('noData')}</p>
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
                  <a className="inline-flex items-center gap-1 text-[var(--text-primary)] hover:text-[var(--accent-hover)]" href={row.url} target="_blank" rel="noreferrer" title={t('openOnGrailed')}>
                    {row.title}
                    <ExternalLink size={12} className="text-[var(--text-muted)]" />
                  </a>
                  <p className="text-xs text-[var(--text-muted)]">
                    {[row.group_name && groupLabel({ name: row.group_name, is_fallback: row.kind === 'none', kind: row.kind }, t), row.size, row.color].filter(Boolean).join(' · ')}
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
    </ContentSection>
  );
}
