'use client';

import { GroupKindBadge, isServiceGroup } from '@/components/group-editor';
import { useRankingPeriodLabel } from '@/components/group/group-tables';
import { MetricBadges } from '@/components/metric-badges';
import { WatchSummary } from '@/components/watch-changes';
import { useWatchItem } from '@/components/watch-star';
import { Badge } from '@/components/ui/badge';
import { Property, PropertyList } from '@/components/ui/section';
import { useI18n } from '@/lib/i18n';
import type { GroupKind, TrendRow } from '@/lib/types';
import { formatCurrency, formatNumber } from '@/lib/utils';

type PanelGroup = { id: number; status: string; kind?: GroupKind | null; is_fallback: boolean };

/** The right-hand panel of a group: its status badges, headline metrics and watch history. */
export function GroupProperties({
  group,
  metrics,
  windowDays,
}: {
  group: PanelGroup;
  metrics: TrendRow | null;
  windowDays: number;
}) {
  const { locale, t } = useI18n();
  const forPeriod = useRankingPeriodLabel();
  const watched = useWatchItem(group.id);
  return (
    <div className="space-y-5">
      <div className="flex flex-wrap gap-1 empty:hidden">
        {group.status === 'auto' && !isServiceGroup(group) && <Badge variant="warning">{t('autoGroup')}</Badge>}
        <GroupKindBadge kind={group.kind} />
        {metrics?.is_new && <Badge variant="info">{t('newBadge')}</Badge>}
        <MetricBadges badges={metrics?.badges} />
      </div>
      {metrics && (
        <div>
          <p className="mb-1 text-xs font-medium text-[var(--text-secondary)]">{t('properties')}</p>
          <PropertyList label={t('properties')}>
            <Property
              label={forPeriod(t('liquidityScore'))}
              title={metrics.liquidity_score === null ? t('belowThreshold') : undefined}
            >
              {formatNumber(metrics.liquidity_score, 0, locale)}
            </Property>
            <Property label={`${t('salesInWindow')}, ${windowDays} ${t('daysShort')}`}>{metrics.sold}</Property>
            <Property label={t('daysToSell')}>{formatNumber(metrics.median_days_to_sell, 1, locale)}</Property>
            <Property label={forPeriod(t('likesColumn'))}>{formatNumber(metrics.period_median_likes, 0, locale)}</Property>
            <Property label={t('medianPrice')}>{formatCurrency(metrics.median_price, locale)}</Property>
            <Property label={t('supplyNow')}>{metrics.active_now}</Property>
          </PropertyList>
        </div>
      )}
      {watched && (
        <div className="border-t border-[var(--border-subtle)] pt-4">
          <p className="mb-2 text-xs font-medium text-[var(--text-secondary)]">{t('watchlist')}</p>
          <WatchSummary item={watched} />
        </div>
      )}
    </div>
  );
}
