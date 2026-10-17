'use client';

import Link from 'next/link';
import { GroupKindBadge, groupLabel, isServiceGroup } from '@/components/group-editor';
import { AskVsSoldCell, P30Cell, SupplyCell } from '@/components/liquidity-cells';
import { MetricBadges } from '@/components/metric-badges';
import { Sparkline } from '@/components/trend-charts';
import { WatchStar } from '@/components/watch-star';
import { Badge } from '@/components/ui/badge';
import { DataTable, TableCell, TableHead, TableHeaderCell, TableRow } from '@/components/ui/data-table';
import { useI18n } from '@/lib/i18n';
import { periodLabel } from '@/lib/liquidity';
import { useTypeNames } from '@/lib/queries';
import type { TrendLevel, TrendRow } from '@/lib/types';
import { formatCurrency, formatNumber } from '@/lib/utils';

const LEVEL_COLUMN: Record<TrendLevel, string> = { model: 'model', type: 'productType', brand: 'brand' };

/** The ranked list: one row per model, type or brand with its liquidity metrics. */
export function TrendsTable({
  rows,
  level,
  window,
  periodMode,
  periodDays,
  onDrill,
}: {
  rows: TrendRow[];
  level: TrendLevel;
  window: number;
  /** Sales and price cover calendar months; the price is an approximate mean of medians. */
  periodMode: boolean;
  periodDays?: number;
  onDrill: (row: TrendRow) => void;
}) {
  const { t } = useI18n();
  return (
    // Numbers and their notes stay on one line; the model title is the only column that wraps.
    <DataTable bleed className="[&_td:not(:first-child)]:whitespace-nowrap">
      <TableHead>
        <tr>
          <TableHeaderCell>{t(LEVEL_COLUMN[level])}</TableHeaderCell>
          <TableHeaderCell>{t('liquidityScore')}</TableHeaderCell>
          <TableHeaderCell>{t('p30')}</TableHeaderCell>
          <TableHeaderCell>
            {periodMode ? t('salesInPeriod') : `${t('salesInWindow')}, ${window} ${t('daysShort')}`}
          </TableHeaderCell>
          <TableHeaderCell>{t('salesPerMonth')}</TableHeaderCell>
          <TableHeaderCell>{t('gmv90d')}</TableHeaderCell>
          <TableHeaderCell>{t('daysToSell')}</TableHeaderCell>
          <TableHeaderCell>{t('likesColumn')}</TableHeaderCell>
          <TableHeaderCell>{periodMode ? `${t('medianPrice')} ≈` : t('medianPrice')}</TableHeaderCell>
          <TableHeaderCell>{t('askVsSold')}</TableHeaderCell>
          <TableHeaderCell>{t('supplyNow')}</TableHeaderCell>
          <TableHeaderCell>{t('weeklyTrend')}</TableHeaderCell>
        </tr>
      </TableHead>
      <tbody>
        {rows.map((row) => (
          <TrendTableRow key={row.scope_key} row={row} periodDays={periodDays} onDrill={onDrill} periodMode={periodMode} />
        ))}
      </tbody>
    </DataTable>
  );
}

function TrendTableRow({
  row,
  periodDays,
  onDrill,
  periodMode,
}: {
  row: TrendRow;
  periodDays?: number;
  onDrill: (row: TrendRow) => void;
  periodMode: boolean;
}) {
  const { locale, t } = useI18n();
  // The window's price change says nothing about a calendar period.
  const change = periodMode || row.price_change === null ? null : Number(row.price_change);
  // A calendar period has only the approximate median; the day window uses the ranking period's.
  const medianPrice = periodMode ? row.median_price : (row.period_median_price ?? row.median_price);
  return (
    <TableRow className="align-top">
      <TableCell className="min-w-72 max-w-md">
        <RowTitle row={row} onDrill={onDrill} />
      </TableCell>
      <TableCell>
        <span
          className="font-medium tabular-nums text-[var(--text-primary)]"
          title={row.liquidity_score === null ? t('belowThreshold') : undefined}
        >
          {formatNumber(row.liquidity_score, 0, locale)}
        </span>
      </TableCell>
      <TableCell>
        <P30Cell row={row} />
      </TableCell>
      <TableCell>
        <span className="tabular-nums">{row.sold}</span>
      </TableCell>
      <TableCell>
        <span className="tabular-nums">{formatNumber(row.sales_per_month, 1, locale)}</span>
        {periodDays !== undefined && (
          <span className="block text-xs tabular-nums text-[var(--text-muted)]">
            {t('soldInPeriod').replace('{count}', String(row.period_sold)).replace('{period}', periodLabel(periodDays, t))}
          </span>
        )}
      </TableCell>
      <TableCell>
        <span className="tabular-nums">{formatCurrency(row.gmv_90d, locale)}</span>
      </TableCell>
      <TableCell>
        <span className="tabular-nums">{formatNumber(row.period_median_days, 1, locale)}</span>
      </TableCell>
      <TableCell>
        <span className="tabular-nums" title={t('medianLikes')}>
          {formatNumber(row.period_median_likes, 0, locale)}
        </span>
      </TableCell>
      <TableCell>
        <span className="tabular-nums" title={periodMode ? t('periodPriceNote') : undefined}>
          {periodMode && medianPrice !== null ? '≈ ' : ''}
          {formatCurrency(medianPrice, locale)}
        </span>
        {change !== null && <PriceChange change={change} />}
      </TableCell>
      <TableCell>
        <AskVsSoldCell price={row.price} />
      </TableCell>
      <TableCell>
        <SupplyCell row={row} />
      </TableCell>
      <TableCell>
        <Sparkline values={row.weekly_sales} label={t('weeklyTrend')} />
      </TableCell>
    </TableRow>
  );
}

function PriceChange({ change }: { change: number }) {
  const { t } = useI18n();
  const tone = change > 0 ? 'text-[var(--success)]' : change < 0 ? 'text-[var(--danger)]' : 'text-[var(--text-muted)]';
  return (
    <p className={`text-xs tabular-nums ${tone}`} title={t('priceChange')}>
      {change > 0 ? '+' : ''}
      {(change * 100).toFixed(0)}%
    </p>
  );
}

/** The model links to its card; a type or brand row drills down to its models. */
function RowTitle({ row, onDrill }: { row: TrendRow; onDrill: (row: TrendRow) => void }) {
  const { t } = useI18n();
  const { typeName } = useTypeNames();
  const title =
    row.scope === 'model'
      ? `${row.brand} · ${groupLabel({ name: row.name ?? '', is_fallback: row.is_fallback, kind: row.kind }, t)} · ${typeName(row.product_type)}`
      : row.scope === 'type'
        ? `${row.brand} · ${typeName(row.product_type)}`
        : row.brand;
  const titleClass = 'font-medium text-[var(--text-primary)] hover:text-[var(--accent-hover)]';
  return (
    <>
      {row.scope === 'model' && row.group_id ? (
        <span className="flex items-start gap-1.5">
          <WatchStar groupId={row.group_id} />
          <Link className={titleClass} href={`/group?id=${row.group_id}`}>
            {title}
          </Link>
        </span>
      ) : (
        <button type="button" className={`text-left ${titleClass}`} onClick={() => onDrill(row)}>
          {title}
        </button>
      )}
      <span className="mt-1 flex flex-wrap gap-1 empty:hidden">
        {row.status === 'auto' && !isServiceGroup(row) && <Badge variant="warning">{t('autoGroup')}</Badge>}
        <GroupKindBadge kind={row.kind} />
        {row.is_new && <Badge variant="info">{t('newBadge')}</Badge>}
        <MetricBadges badges={row.badges} />
        {row.versions > 0 && (
          <Badge variant="muted">
            {t('versionsCount')}: {row.versions}
          </Badge>
        )}
      </span>
    </>
  );
}
