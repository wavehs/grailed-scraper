'use client';

import Link from 'next/link';
import { useParams, useSearchParams } from 'next/navigation';
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import {
  ArrowLeft,
  Award,
  BarChart3,
  Clock3,
  Heart,
  PackageCheck,
  Shield,
  ShoppingBag,
  TrendingUp,
} from 'lucide-react';
import { Card } from '@/components/ui/card';
import { PageHeader } from '@/components/ui/page-header';
import { ProgressBar } from '@/components/ui/progress-bar';
import { StatCard } from '@/components/ui/stat-card';
import { EmptyState, ErrorState, LoadingState, Notice } from '@/components/states';
import { useI18n } from '@/lib/i18n';
import { useModelGroupDetailQuery } from '@/lib/queries';
import { formatCurrency, formatDaysOnMarket } from '@/lib/utils';
import type { ListingExample, VariantPerformance } from '@/lib/types';

const pretty = (value: unknown) =>
  typeof value === 'object' ? JSON.stringify(value) : String(value);

export default function ModelDetailClient() {
  const params = useParams<{ id?: string }>();
  const searchParams = useSearchParams();
  const { locale, t } = useI18n();
  const modelId = params?.id || searchParams.get('id') || '';
  const windowDays = searchParams.get('window_days') === '30' ? 30 : 90;
  const runValue = Number(searchParams.get('run_id'));
  const backValue = searchParams.get('back');
  const backHref = backValue?.startsWith('/dashboard') ? backValue : '/dashboard';
  const query = useModelGroupDetailQuery(
    modelId,
    windowDays,
    Number.isInteger(runValue) && runValue > 0 ? runValue : undefined,
  );
  if (query.isLoading) return <LoadingState />;
  if (query.error) return <ErrorState error={query.error} retry={() => query.refetch()} />;
  if (!query.data) return <EmptyState />;
  const data = query.data;
  const m = data.metrics;
  const priceData = data.sold_examples.map((item) => ({
    name: item.sold_at?.slice(0, 10) ?? String(item.id),
    price: item.price / 100,
  }));
  return (
    <section className="space-y-6" aria-labelledby="model-heading">
      <Link
        className="inline-flex min-h-10 items-center gap-2 rounded-lg text-sm font-medium text-[var(--accent)] transition-colors hover:text-[var(--accent-hover)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
        href={backHref}
      >
        <ArrowLeft size={16} />
        {t('backToResults')}
      </Link>
      <PageHeader title={data.name} description={`${data.brand} · ${data.category ?? '—'}`} />
      <p className="text-xs text-[var(--text-muted)]">
        {t('modelVersion')}: {data.model_version} · {t('run')} #{data.run_id} · {data.window_days}d
        · {t('inputDigest')}:{' '}
        <code className="rounded bg-[var(--bg-surface-hover)] px-1.5 py-0.5">
          {data.input_digest.slice(0, 12)}
        </code>
      </p>

      {m.warnings.map((warning) => (
        <Notice error key={warning}>
          {warning === 'truncated'
            ? t('truncatedResult')
            : warning === 'partial'
              ? t('partialResult')
              : warning}
        </Notice>
      ))}

      <div>
        <h2 className="mb-3 text-sm font-semibold uppercase tracking-wider text-[var(--text-muted)]">
          {t('saleEvidence')}
        </h2>
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          <StatCard
            label={t('soldInWindow')}
            value={m.sold_count}
            icon={<ShoppingBag size={18} />}
          />
          <StatCard
            label={t('activeNow')}
            value={m.active_count}
            icon={<PackageCheck size={18} />}
          />
          <StatCard
            label={t('daysToSell')}
            value={
              m.median_days_to_sell === undefined ? '—' : Number(m.median_days_to_sell).toFixed(1)
            }
            icon={<Clock3 size={18} />}
          />
          <StatCard
            label={t('medianLikes')}
            value={m.median_sold_likes === undefined ? '—' : Number(m.median_sold_likes).toFixed(0)}
            icon={<Heart size={18} />}
          />
        </div>
      </div>

      <div>
        <h2 className="mb-3 text-sm font-semibold uppercase tracking-wider text-[var(--text-muted)]">
          {t('bestVariants')}
        </h2>
        <div className="grid gap-5 lg:grid-cols-2">
          <VariantRanking title={t('colors')} rows={data.variant_breakdown.colors} />
          <VariantRanking title={t('sizes')} rows={data.variant_breakdown.sizes} />
        </div>
      </div>

      <div>
        <h2 className="mb-3 text-sm font-semibold uppercase tracking-wider text-[var(--text-muted)]">
          {t('scoringDetails')}
        </h2>
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <StatCard
            label={t('demand')}
            value={m.demand_score ?? t(m.scoring_status)}
            icon={<TrendingUp size={18} />}
          />
          <StatCard
            label={t('liquidity')}
            value={m.liquidity_score ?? t(m.scoring_status)}
            icon={<BarChart3 size={18} />}
          />
          <StatCard
            label={t('medianPrice')}
            value={formatCurrency(m.median_sold_price, locale)}
            icon={<Award size={18} />}
          />
          <StatCard
            label={t('confidence')}
            value={m.confidence_score}
            icon={<Shield size={18} />}
          />
        </div>
      </div>

      {/* Price chart */}
      <Card className="p-5">
        <h2 className="mb-3 text-sm font-semibold uppercase tracking-wider text-[var(--text-muted)]">
          {t('soldExamples')} — {t('medianPrice')}
        </h2>
        <div className="h-64">
          {priceData.length ? (
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={priceData}>
                <CartesianGrid strokeDasharray="3 3" stroke="var(--border-subtle)" />
                <XAxis dataKey="name" tick={{ fill: 'var(--text-muted)', fontSize: 11 }} />
                <YAxis tick={{ fill: 'var(--text-muted)', fontSize: 11 }} />
                <Tooltip
                  contentStyle={{
                    background: 'var(--bg-surface-raised)',
                    border: '1px solid var(--border-default)',
                    borderRadius: '8px',
                    color: 'var(--text-primary)',
                    fontSize: 12,
                  }}
                />
                <Bar dataKey="price" fill="var(--accent)" radius={[3, 3, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          ) : (
            <p className="text-[var(--text-muted)]">{t('noExamples')}</p>
          )}
        </div>
      </Card>

      {/* Score breakdown */}
      <Card className="p-5">
        <h2 className="mb-4 text-sm font-semibold uppercase tracking-wider text-[var(--text-muted)]">
          {t('scoreBreakdown')}
        </h2>
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
          {Object.entries(m.components).map(([name, value]) => {
            const scoreNum = Number(value.score) || 0;
            return (
              <div
                className="rounded-md border border-[var(--border-subtle)] bg-[var(--bg-surface-raised)] p-3"
                key={name}
              >
                <p className="text-xs text-[var(--text-muted)]">{name.replaceAll('_', ' ')}</p>
                <p className="mt-1 font-semibold text-[var(--text-primary)]">
                  {value.score}
                  {value.liquidity_weight ? ` · L×${value.liquidity_weight}` : ''}
                  {value.demand_weight ? ` · D×${value.demand_weight}` : ''}
                  {value.weight ? ` · ${value.weight}` : ''}
                </p>
                <ProgressBar value={scoreNum} max={100} size="sm" className="mt-2" />
              </div>
            );
          })}
        </div>
      </Card>

      {/* Confidence & Quality */}
      <div className="grid gap-5 lg:grid-cols-2">
        <Card className="p-5">
          <h2 className="mb-3 text-sm font-semibold uppercase tracking-wider text-[var(--text-muted)]">
            {t('confidenceFactors')}
          </h2>
          <dl className="space-y-2">
            {Object.entries(m.confidence_factors).map(([key, value]) => (
              <div
                className="flex justify-between gap-3 border-t border-[var(--border-subtle)] pt-2"
                key={key}
              >
                <dt className="text-sm text-[var(--text-muted)]">{key.replaceAll('_', ' ')}</dt>
                <dd className="text-sm font-medium text-[var(--text-primary)]">{pretty(value)}</dd>
              </div>
            ))}
          </dl>
        </Card>
        <Card className="p-5">
          <h2 className="mb-3 text-sm font-semibold uppercase tracking-wider text-[var(--text-muted)]">
            {t('qualitySummary')}
          </h2>
          <dl className="space-y-2">
            {Object.entries(m.quality_summary).map(([key, value]) => (
              <div
                className="flex justify-between gap-3 border-t border-[var(--border-subtle)] pt-2"
                key={key}
              >
                <dt className="text-sm text-[var(--text-muted)]">{key.replaceAll('_', ' ')}</dt>
                <dd className="text-sm font-medium text-[var(--text-primary)]">{pretty(value)}</dd>
              </div>
            ))}
          </dl>
        </Card>
      </div>

      {/* Examples */}
      <div className="grid gap-5 lg:grid-cols-2">
        <ExampleList
          title={t('soldExamples')}
          items={data.sold_examples}
          status="sold"
          open={true}
          money={(cents) => formatCurrency(cents, locale)}
          empty={t('noExamples')}
        />
        <ExampleList
          title={t('activeExamples')}
          items={data.active_examples}
          status="active"
          open={false}
          money={(cents) => formatCurrency(cents, locale)}
          empty={t('noExamples')}
        />
      </div>
    </section>
  );
}

function VariantRanking({ title, rows }: { title: string; rows: VariantPerformance[] }) {
  const { t } = useI18n();
  const renderRows = (items: VariantPerformance[], offset = 0) =>
    items.map((row, index) => (
      <tr
        className={
          offset + index === 0
            ? 'bg-[color-mix(in_srgb,var(--accent)_10%,transparent)] font-semibold'
            : 'border-t border-[var(--border-subtle)]'
        }
        key={row.value ?? '__unknown__'}
      >
        <td className="px-3 py-2 text-[var(--text-primary)]">
          {offset + index === 0 && <Award className="mr-1 inline text-[var(--accent)]" size={14} />}
          {row.value ?? t('unknownVariant')}
        </td>
        <td className="px-3 py-2 text-right tabular-nums">{row.sold_count}</td>
        <td className="px-3 py-2 text-right tabular-nums">{row.active_count}</td>
        <td className="px-3 py-2 text-right tabular-nums">
          {(Number(row.sell_through) * 100).toFixed(1)}%
        </td>
      </tr>
    ));
  return (
    <Card className="overflow-hidden p-0">
      <h3 className="px-5 pt-5 text-sm font-semibold uppercase tracking-wider text-[var(--text-muted)]">
        {title}
      </h3>
      {rows.length ? (
        <div className="overflow-x-auto py-3">
          <table className="w-full text-sm">
            <thead className="text-left text-xs uppercase tracking-wide text-[var(--text-muted)]">
              <tr>
                <th className="px-3 py-2">{t('variant')}</th>
                <th className="px-3 py-2 text-right">{t('sold')}</th>
                <th className="px-3 py-2 text-right">{t('active')}</th>
                <th className="px-3 py-2 text-right">{t('sellThrough')}</th>
              </tr>
            </thead>
            <tbody>{renderRows(rows.slice(0, 10))}</tbody>
          </table>
          {rows.length > 10 && (
            <details className="border-t border-[var(--border-subtle)]">
              <summary className="min-h-10 cursor-pointer px-3 py-2 text-sm font-medium text-[var(--accent)]">
                {t('showMore')} ({rows.length - 10})
              </summary>
              <table className="w-full text-sm">
                <tbody>{renderRows(rows.slice(10), 10)}</tbody>
              </table>
            </details>
          )}
        </div>
      ) : (
        <p className="px-5 py-4 text-sm text-[var(--text-muted)]">{t('noData')}</p>
      )}
    </Card>
  );
}

function ExampleList({
  title,
  items,
  status,
  open,
  money,
  empty,
}: {
  title: string;
  items: ListingExample[];
  status: 'sold' | 'active';
  open: boolean;
  money: (value?: number) => string;
  empty: string;
}) {
  const { locale, t } = useI18n();
  return (
    <Card className="p-5">
      <details open={open}>
        <summary className="flex min-h-10 cursor-pointer items-center justify-between text-sm font-semibold uppercase tracking-wider text-[var(--text-muted)]">
          {title}
          <span className="tabular-nums">{items.length}</span>
        </summary>
        <ul className="mt-3 space-y-2 text-sm">
          {items.length ? (
            items.map((item) => (
              <li
                key={item.id}
                className="flex flex-wrap items-center gap-x-2 gap-y-1 border-t border-[var(--border-subtle)] pt-2"
              >
                <span className="h-1 w-1 rounded-full bg-[var(--accent)]" />
                <a
                  className="min-w-0 flex-1 break-words text-[var(--accent)] hover:underline"
                  href={`https://www.grailed.com/listings/${item.grailed_id}`}
                  rel="noreferrer"
                  target="_blank"
                >
                  {item.title}
                </a>
                {item.days_on_market !== undefined && item.days_on_market !== null && (
                  <>
                    <span className="text-[var(--text-muted)]">·</span>
                    <span className="text-xs font-medium tabular-nums text-[var(--accent)]">
                      {formatDaysOnMarket(item.days_on_market, status, locale)}
                    </span>
                  </>
                )}
                <span className="text-[var(--text-muted)]">·</span>
                <span className="text-[var(--text-secondary)]">{money(item.price)}</span>
                <span className="text-[var(--text-muted)]">·</span>
                <span className="text-[var(--text-muted)]">
                  {item.likes} {t('likes')}
                </span>
              </li>
            ))
          ) : (
            <li className="text-[var(--text-muted)]">{empty}</li>
          )}
        </ul>
      </details>
    </Card>
  );
}
