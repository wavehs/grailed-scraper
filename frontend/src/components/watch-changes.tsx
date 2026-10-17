'use client';

import { formatRatio } from '@/components/liquidity-cells';
import { Property, PropertyList } from '@/components/ui/section';
import { useI18n } from '@/lib/i18n';
import type { WatchItem } from '@/lib/types';
import { formatCurrency, formatDate } from '@/lib/utils';

/** "+5" / "−3" / "0"; a dash without a value. */
export function signed(value: number | null | undefined, digits = 0): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—';
  const text = Math.abs(value).toFixed(digits);
  if (Number(text) === 0) return '0';
  return `${value > 0 ? '+' : '−'}${text}`;
}

/** A liquidity score rounded to a whole number. */
export function score(value: string | null | undefined): string {
  return value === null || value === undefined ? '—' : Number(value).toFixed(0);
}

/** One measure: then → now (change). */
export function WatchDelta({ before, after, change }: { before: string; after: string; change: string }) {
  return (
    <span className="whitespace-nowrap tabular-nums">
      <span className="text-[var(--text-muted)]">{before} →</span> {after}
      <span className="ml-1 text-xs text-[var(--text-muted)]">({change})</span>
    </span>
  );
}

/** Score, price and supply now against the day the model was added, as panel properties. */
export function WatchSummary({ item }: { item: WatchItem }) {
  const { locale, t } = useI18n();
  const { baseline, current, changes } = item;
  if (!baseline || !current || !changes) {
    return <p className="text-xs text-[var(--text-muted)]">{t('watchNoHistory')}</p>;
  }
  return (
    <div className="space-y-1">
      <p className="text-xs text-[var(--text-muted)]">
        {t('watchSince').replace('{date}', formatDate(baseline.as_of, locale))}
      </p>
      <PropertyList label={t('watchlist')}>
        <Property label={t('liquidityScore')}>
          <WatchDelta
            before={score(baseline.liquidity_score)}
            after={score(current.liquidity_score)}
            change={signed(changes.liquidity_score === null ? null : Number(changes.liquidity_score))}
          />
        </Property>
        <Property label={t('watchPrice')}>
          <WatchDelta
            before={formatCurrency(baseline.median_price, locale)}
            after={formatCurrency(current.median_price, locale)}
            change={formatRatio(changes.median_price, true)}
          />
        </Property>
        <Property label={t('watchSupply')}>
          <WatchDelta before={String(baseline.active_now)} after={String(current.active_now)} change={signed(changes.active_now)} />
        </Property>
      </PropertyList>
    </div>
  );
}
