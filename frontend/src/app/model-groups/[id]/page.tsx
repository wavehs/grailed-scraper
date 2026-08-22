'use client';

import { useParams } from 'next/navigation';
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { Award, BarChart3, Shield, TrendingUp } from 'lucide-react';
import { Card } from '@/components/ui/card';
import { PageHeader } from '@/components/ui/page-header';
import { ProgressBar } from '@/components/ui/progress-bar';
import { StatCard } from '@/components/ui/stat-card';
import { EmptyState, ErrorState, LoadingState, Notice } from '@/components/states';
import { useI18n } from '@/lib/i18n';
import { useModelGroupDetailQuery } from '@/lib/queries';
import { formatCurrency } from '@/lib/utils';
import type { ListingExample, ModelGroupDetail } from '@/lib/types';

const pretty = (value: unknown) =>
  typeof value === 'object' ? JSON.stringify(value) : String(value);

export default function ModelDetail() {
  const params = useParams<{ id: string }>();
  const { locale, t } = useI18n();
  const query = useModelGroupDetailQuery(params.id);
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
      <PageHeader
        title={data.name}
        description={`${data.brand} · ${data.category ?? '—'}`}
      />
      <p className="text-xs text-[var(--text-muted)]">
        {t('modelVersion')}: {data.model_version} · {t('run')} #{data.run_id} · {data.window_days}
        d · {t('inputDigest')}: <code className="rounded bg-[var(--bg-surface-hover)] px-1.5 py-0.5">{data.input_digest.slice(0, 12)}</code>
      </p>

      {m.warnings.map((warning) => (
        <Notice error key={warning}>
          {warning}
        </Notice>
      ))}

      {/* Score cards */}
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <StatCard
          label={t('opportunity')}
          value={m.market_opportunity_score}
          icon={<TrendingUp size={18} />}
        />
        <StatCard
          label={t('liquidity')}
          value={m.liquidity_score}
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
              <div className="rounded-md border border-[var(--border-subtle)] bg-[var(--bg-surface-raised)] p-3" key={name}>
                <p className="text-xs text-[var(--text-muted)]">
                  {name.replaceAll('_', ' ')}
                </p>
                <p className="mt-1 font-semibold text-[var(--text-primary)]">
                  {value.score} × {value.weight}
                  {value.contribution ? ` = ${value.contribution}` : ''}
                </p>
                <ProgressBar value={scoreNum * 10} max={100} size="sm" className="mt-2" />
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
              <div className="flex justify-between gap-3 border-t border-[var(--border-subtle)] pt-2" key={key}>
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
              <div className="flex justify-between gap-3 border-t border-[var(--border-subtle)] pt-2" key={key}>
                <dt className="text-sm text-[var(--text-muted)]">{key.replaceAll('_', ' ')}</dt>
                <dd className="text-sm font-medium text-[var(--text-primary)]">{pretty(value)}</dd>
              </div>
            ))}
          </dl>
        </Card>
      </div>

      {/* Examples */}
      <div className="grid gap-5 lg:grid-cols-2">
        {[
          [t('soldExamples'), data.sold_examples],
          [t('activeExamples'), data.active_examples],
        ].map(([title, items]) => (
          <ExampleList
            key={String(title)}
            title={String(title)}
            items={items as ListingExample[]}
            money={(cents) => formatCurrency(cents, locale)}
            empty={t('noExamples')}
          />
        ))}
      </div>
    </section>
  );
}

function ExampleList({
  title,
  items,
  money,
  empty,
}: {
  title: string;
  items: ListingExample[];
  money: (value?: number) => string;
  empty: string;
}) {
  const { t } = useI18n();
  return (
    <Card className="p-5">
      <h2 className="mb-3 text-sm font-semibold uppercase tracking-wider text-[var(--text-muted)]">
        {title}
      </h2>
      <ul className="space-y-2 text-sm">
        {items.length ? (
          items.map((item) => (
            <li key={item.id} className="flex items-center gap-2 border-t border-[var(--border-subtle)] pt-2">
              <span className="h-1 w-1 rounded-full bg-[var(--accent)]" />
              <a
                className="text-[var(--accent)] hover:underline"
                href={`https://www.grailed.com/listings/${item.grailed_id}`}
                rel="noreferrer"
                target="_blank"
              >
                {item.title}
              </a>
              <span className="text-[var(--text-muted)]">·</span>
              <span className="text-[var(--text-secondary)]">{money(item.price)}</span>
              <span className="text-[var(--text-muted)]">·</span>
              <span className="text-[var(--text-muted)]">{item.likes} {t('likes')}</span>
            </li>
          ))
        ) : (
          <li className="text-[var(--text-muted)]">{empty}</li>
        )}
      </ul>
    </Card>
  );
}
