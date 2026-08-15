'use client';

import { useParams } from 'next/navigation';
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { Card } from '@/components/ui/card';
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
    <section className="space-y-5" aria-labelledby="model-heading">
      <div>
        <h1 id="model-heading" className="text-2xl font-semibold">
          {data.name}
        </h1>
        <p className="text-slate-600">
          {data.brand} · {data.category ?? '—'}
        </p>
        <p className="mt-1 text-sm text-slate-500">
          {t('modelVersion')}: {data.model_version} · {t('run')} #{data.run_id} · {data.window_days}
          d · {t('inputDigest')}: <code>{data.input_digest.slice(0, 12)}</code>
        </p>
      </div>
      {m.warnings.map((warning) => (
        <Notice error key={warning}>
          {warning}
        </Notice>
      ))}
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {[
          [t('opportunity'), m.market_opportunity_score],
          [t('liquidity'), m.liquidity_score],
          [t('medianPrice'), formatCurrency(m.median_sold_price, locale)],
          [t('confidence'), m.confidence_score],
        ].map(([label, value]) => (
          <Card className="p-4" key={label}>
            <p className="text-sm text-slate-600">{label}</p>
            <p className="text-2xl font-bold">{value}</p>
          </Card>
        ))}
      </div>
      <Card className="p-4">
        <h2 className="font-semibold">
          {t('soldExamples')} — {t('medianPrice')}
        </h2>
        <div className="mt-3 h-64">
          {priceData.length ? (
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={priceData}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="name" />
                <YAxis />
                <Tooltip />
                <Bar dataKey="price" fill="#047857" />
              </BarChart>
            </ResponsiveContainer>
          ) : (
            <p className="text-slate-500">{t('noExamples')}</p>
          )}
        </div>
      </Card>
      <Card className="p-4">
        <h2 className="font-semibold">{t('scoreBreakdown')}</h2>
        <dl className="mt-3 grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
          {Object.entries(m.components).map(([name, value]) => (
            <div className="rounded bg-slate-50 p-3" key={name}>
              <dt className="text-sm text-slate-600">{name.replaceAll('_', ' ')}</dt>
              <dd className="font-semibold">
                {value.score} × {value.weight}
                {value.contribution ? ` = ${value.contribution}` : ''}
              </dd>
            </div>
          ))}
        </dl>
      </Card>
      <div className="grid gap-5 lg:grid-cols-2">
        <Card className="p-4">
          <h2 className="font-semibold">{t('confidenceFactors')}</h2>
          <dl className="mt-3 space-y-2">
            {Object.entries(m.confidence_factors).map(([key, value]) => (
              <div className="flex justify-between gap-3 border-t pt-2" key={key}>
                <dt>{key.replaceAll('_', ' ')}</dt>
                <dd className="font-medium">{pretty(value)}</dd>
              </div>
            ))}
          </dl>
        </Card>
        <Card className="p-4">
          <h2 className="font-semibold">{t('qualitySummary')}</h2>
          <dl className="mt-3 space-y-2">
            {Object.entries(m.quality_summary).map(([key, value]) => (
              <div className="flex justify-between gap-3 border-t pt-2" key={key}>
                <dt>{key.replaceAll('_', ' ')}</dt>
                <dd className="font-medium">{pretty(value)}</dd>
              </div>
            ))}
          </dl>
        </Card>
      </div>
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
    <Card className="p-4">
      <h2 className="font-semibold">{title}</h2>
      <ul className="mt-2 space-y-2 text-sm">
        {items.length ? (
          items.map((item) => (
            <li key={item.id} className="border-t pt-2">
              {item.title} · {money(item.price)} · {item.likes} {t('likes')}
            </li>
          ))
        ) : (
          <li className="text-slate-500">{empty}</li>
        )}
      </ul>
    </Card>
  );
}
