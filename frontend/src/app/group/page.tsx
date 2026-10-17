'use client';

import Link from 'next/link';
import { Suspense, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { GroupEditor, groupLabel } from '@/components/group-editor';
import { AskVsSoldPanel } from '@/components/ask-vs-sold';
import { GroupProperties } from '@/components/group/group-properties';
import { ListingTable, MetricTable, VariantTable } from '@/components/group/group-tables';
import { WatchStar } from '@/components/watch-star';
import { MonthCharts } from '@/components/month-charts';
import { ResaleCalculator } from '@/components/resale-calculator';
import { WeeklyCharts } from '@/components/trend-charts';
import { PageHeader } from '@/components/ui/page-header';
import { ContentSection } from '@/components/ui/section';
import { Segmented } from '@/components/ui/segmented';
import { EmptyState, ErrorState, LoadingState } from '@/components/states';
import { useI18n } from '@/lib/i18n';
import { useTrendCardQuery, useTypeNames } from '@/lib/queries';
import type { TrendCondition, TrendRow } from '@/lib/types';

const WINDOWS = [7, 30, 90] as const;
type WindowDays = (typeof WINDOWS)[number];

/** Expected days to sell from P30, else the observed median. */
function daysToSell(metrics: TrendRow): number | null {
  const value = metrics.expected_days ?? metrics.median_days_to_sell;
  return value === null || value === undefined ? null : Number(value);
}

function GroupCard() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const { t } = useI18n();
  const { typeName } = useTypeNames();
  const id = Number(searchParams.get('id'));
  const groupId = Number.isInteger(id) && id > 0 ? id : null;
  const [windowDays, setWindowDays] = useState<WindowDays>(30);
  const [condition, setCondition] = useState<TrendCondition>('all');
  const card = useTrendCardQuery(groupId, windowDays, condition);
  if (groupId === null) return <EmptyState />;
  if (card.isLoading) return <LoadingState />;
  if (!card.data) return <ErrorState error={card.error} retry={() => card.refetch()} />;
  const { group, metrics } = card.data;
  const title = `${group.brand} · ${groupLabel(group, t)} · ${typeName(group.product_type)}`;
  return (
    <section aria-labelledby="group-heading">
      <PageHeader
        title={title}
        headingId="group-heading"
        breadcrumb={
          <Link className="hover:text-[var(--text-primary)]" href="/trends">
            {t('trends')}
          </Link>
        }
        description={group.parent ? `${t('modelLine')}: ${group.parent.name}` : undefined}
        actions={
          <>
            <WatchStar groupId={group.id} />
            <Segmented
              label={t('trendWindow')}
              value={windowDays}
              options={WINDOWS.map((value) => ({ value, label: `${value} ${t('daysShort')}` }))}
              onChange={setWindowDays}
            />
          </>
        }
      />
      {/* Linear's issue layout: the content column and a property panel on the right. */}
      {/* The panel comes first on phones and moves to the right from lg. */}
      <div className="-mx-4 -mb-10 flex flex-col md:-mx-6 lg:flex-row">
        <div className="order-2 min-w-0 flex-1 space-y-6 px-4 pb-10 pt-5 md:px-6 lg:order-1">
          {metrics ? (
            <>
              <ContentSection>
                <WeeklyCharts
                  sales={metrics.weekly_sales}
                  prices={card.data.weekly_median_price}
                  computedAt={card.data.computed_at}
                />
              </ContentSection>
              {card.data.months.length > 0 && (
                <ContentSection>
                  <MonthCharts months={card.data.months} overlay={card.data.season_overlay} />
                </ContentSection>
              )}
              <div className="grid gap-4 border-t border-[var(--border-subtle)] pt-5 xl:grid-cols-2">
                <AskVsSoldPanel price={metrics.price} condition={condition} onCondition={setCondition} />
                <ResaleCalculator
                  defaultExpected={metrics.price?.p50 ?? metrics.median_price}
                  daysToSell={daysToSell(metrics)}
                />
              </div>
            </>
          ) : (
            <EmptyState />
          )}

          {Boolean(card.data.versions.length) && (
            <ContentSection title={t('versionsTable')}>
              <MetricTable rows={card.data.versions} />
            </ContentSection>
          )}
          <div className="grid gap-6 border-t border-[var(--border-subtle)] pt-5 xl:grid-cols-2 [&>section]:border-t-0 [&>section]:pt-0">
            <VariantTable title={t('popularColors')} rows={card.data.colors} />
            <VariantTable title={t('popularSizes')} rows={card.data.sizes} sizes />
          </div>
          <ListingTable title={t('recentSales')} rows={card.data.recent_sales} brandId={group.brand_id} sold />
          <ListingTable title={t('activeListingsNow')} rows={card.data.active_listings} brandId={group.brand_id} />
          {card.data.type_metrics && (
            <ContentSection title={t('typeContext')}>
              <MetricTable
                rows={[{ ...card.data.type_metrics, name: typeName(card.data.type_metrics.product_type) }]}
              />
            </ContentSection>
          )}
          <div className="border-t border-[var(--border-subtle)] pt-5">
            <GroupEditor
              groupId={group.id}
              onChanged={(changed) => {
                if (changed.id !== group.id) router.push(`/group?id=${changed.id}`);
              }}
            />
          </div>
        </div>
        <aside className="order-1 shrink-0 border-b border-[var(--border-subtle)] bg-[var(--bg-sidebar)] px-4 py-5 md:px-6 lg:order-2 lg:w-[300px] lg:border-b-0 lg:border-l lg:px-5 lg:pb-10">
          <div className="lg:sticky lg:top-[calc(var(--topbar-height)+1.25rem)]">
            <GroupProperties group={group} metrics={metrics} windowDays={windowDays} />
          </div>
        </aside>
      </div>
    </section>
  );
}

export default function GroupPage() {
  return (
    <Suspense fallback={<LoadingState />}>
      <GroupCard />
    </Suspense>
  );
}
