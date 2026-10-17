'use client';

import { formatRatio } from '@/components/liquidity-cells';
import { Card } from '@/components/ui/card';
import { Segmented } from '@/components/ui/segmented';
import { useI18n } from '@/lib/i18n';
import type { PriceBucket, TrendCondition } from '@/lib/types';
import { formatCurrency } from '@/lib/utils';

const CONDITIONS: Array<[TrendCondition, string]> = [
  ['all', 'conditionAll'],
  ['new', 'conditionNew'],
  ['used', 'conditionUsed'],
];

/** Ask vs Sold of 90 days in one condition bucket (docs/METRICS.md §7). */
export function AskVsSoldPanel({
  price,
  condition,
  onCondition,
}: {
  price: PriceBucket | null | undefined;
  condition: TrendCondition;
  onCondition: (value: TrendCondition) => void;
}) {
  const { locale, t } = useI18n();
  const money = (cents: number | null) => formatCurrency(cents, locale);
  const days = price?.days_at_final_price ?? null;
  const rows: Array<[string, string]> = price
    ? [
        [t('soldQuartiles'), `${money(price.p25)} · ${money(price.p50)} · ${money(price.p75)}`],
        [
          t('freshAsks'),
          price.fresh_ask_p50 === null
            ? '—'
            : `${money(price.fresh_ask_p50)} (${formatRatio(price.fresh_ask_premium, true)}, n=${price.fresh_n})`,
        ],
        [t('offerDiscount'), formatRatio(price.offer_discount)],
        [t('totalMarkdown'), formatRatio(price.total_markdown)],
        [t('daysAtFinalPrice'), days === null ? '—' : `${Number(days).toFixed(0)} ${t('daysShort')}`],
      ]
    : [];
  return (
    <Card className="space-y-3 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-[13px] font-medium text-[var(--text-primary)]" title={t('askVsSoldHelp')}>
          {t('askVsSold')}
        </h2>
        <Segmented
          label={t('conditionFilter')}
          value={condition}
          options={CONDITIONS.map(([value, label]) => ({ value, label: t(label) }))}
          onChange={onCondition}
        />
      </div>
      {!price || price.p50 === null ? (
        <p className="text-sm text-[var(--text-muted)]">{t('noData')}</p>
      ) : (
        <>
          <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-[13px]">
            {rows.map(([label, value]) => (
              <div key={label} className="contents">
                <dt className="text-[var(--text-muted)]">{label}</dt>
                <dd className="tabular-nums text-[var(--text-primary)]">{value}</dd>
              </div>
            ))}
          </dl>
          <p className="text-xs text-[var(--text-muted)]">
            {t('askVsSoldBasis').replace('{count}', String(price.n))}
            {price.approximate && ` · ${t('approximateHelp')}`}
          </p>
        </>
      )}
    </Card>
  );
}
