'use client';

import { useState } from 'react';
import { formatRatio } from '@/components/liquidity-cells';
import { Card } from '@/components/ui/card';
import { dollarsToCents, resale, roiPer30Days, type Resale } from '@/lib/fees';
import { useI18n } from '@/lib/i18n';
import { useFeesQuery } from '@/lib/queries';
import type { FeeSchedule } from '@/lib/types';
import { formatCurrency } from '@/lib/utils';

type Field = 'buy' | 'costsIn' | 'expected' | 'shipBuyer' | 'labelCost';
type Form = Record<Field, string> & { grailedLabel: boolean; international: boolean };

const FIELDS: Array<[Field, string]> = [
  ['buy', 'calcBuy'],
  ['costsIn', 'calcCostsIn'],
  ['expected', 'calcExpected'],
  ['shipBuyer', 'calcShipBuyer'],
  ['labelCost', 'calcLabelCost'],
];
const EMPTY: Form = {
  buy: '',
  costsIn: '0',
  expected: '',
  shipBuyer: '0',
  labelCost: '0',
  grailedLabel: false,
  international: false,
};

/** The resale for the form, or null while it is incomplete or impossible. */
export function computeResale(schedule: FeeSchedule, form: Form, defaultExpected: number | null): Resale | null {
  const cents = (field: Field) => dollarsToCents(form[field]);
  const expected = form.expected.trim() ? cents('expected') : defaultExpected;
  const values = [cents('buy'), cents('costsIn'), expected, cents('shipBuyer'), cents('labelCost')];
  if (values.some((value) => value === null)) return null;
  const [buy, costsIn, sale, shipBuyer, labelCost] = values as number[];
  try {
    return resale(schedule, {
      buy,
      costsIn,
      expected: sale,
      shipBuyer,
      labelCost,
      grailedLabel: form.grailedLabel,
      international: form.international,
    });
  } catch {
    return null;
  }
}

/** Net and ROI of buying this model and reselling it on Grailed (v2 plan 2.6). */
export function ResaleCalculator({
  defaultExpected,
  daysToSell,
}: {
  defaultExpected: number | null;
  daysToSell: number | null;
}) {
  const { locale, t } = useI18n();
  const fees = useFeesQuery();
  const [form, setForm] = useState<Form>(EMPTY);
  const schedule = fees.data;
  const result = schedule ? computeResale(schedule, form, defaultExpected) : null;
  const money = (cents: number) => formatCurrency(cents, locale);
  return (
    <Card className="space-y-3 p-4">
      <h2 className="text-sm font-semibold text-[var(--text-primary)]">{t('calculator')}</h2>
      <div className="grid gap-2 sm:grid-cols-2">
        {FIELDS.map(([field, label]) => (
          <label key={field} className="flex items-center justify-between gap-2 text-xs text-[var(--text-muted)]">
            {t(label)}
            <input
              className="w-24 text-right text-sm tabular-nums"
              inputMode="decimal"
              value={form[field]}
              placeholder={field === 'expected' && defaultExpected !== null ? String(defaultExpected / 100) : undefined}
              onChange={(event) => setForm({ ...form, [field]: event.target.value })}
            />
          </label>
        ))}
        <label className="flex items-center gap-2 text-xs text-[var(--text-muted)]">
          <input
            type="checkbox"
            checked={form.grailedLabel}
            onChange={(event) => setForm({ ...form, grailedLabel: event.target.checked })}
          />
          {t('calcGrailedLabel')}
        </label>
        <label className="flex items-center gap-2 text-xs text-[var(--text-muted)]">
          <input
            type="checkbox"
            checked={form.international}
            onChange={(event) => setForm({ ...form, international: event.target.checked })}
          />
          {t('calcInternational')}
        </label>
      </div>
      {!schedule ? (
        <p className="text-sm text-[var(--text-muted)]">{fees.isError ? t('calcNoFees') : t('loading')}</p>
      ) : !result ? (
        <p className="text-sm text-[var(--text-muted)]">{t('calcIncomplete')}</p>
      ) : (
        <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm" aria-label={t('calcResult')}>
          <dt className="text-[var(--text-muted)]">{t('calcFees')}</dt>
          <dd className="tabular-nums">
            {money(result.fees.total)} ({money(result.fees.commission)} + {money(result.fees.processing)})
          </dd>
          <dt className="text-[var(--text-muted)]">{t('calcNet')}</dt>
          <dd className="tabular-nums font-semibold">{money(result.net)}</dd>
          <dt className="text-[var(--text-muted)]">ROI</dt>
          <dd className="tabular-nums font-semibold">{formatRatio(String(result.roi), true)}</dd>
          {daysToSell !== null && (
            <>
              <dt className="text-[var(--text-muted)]">{t('calcRoi30')}</dt>
              <dd className="tabular-nums">
                {formatRatio(String(roiPer30Days(result.roi, daysToSell)), true)} · {Math.round(daysToSell)} {t('daysShort')}
              </dd>
            </>
          )}
        </dl>
      )}
      {schedule && (
        <p className="text-xs text-[var(--text-muted)]">
          {t('calcFeesAsOf').replace('{date}', schedule.effective_from)}
        </p>
      )}
    </Card>
  );
}
