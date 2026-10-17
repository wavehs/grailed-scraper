'use client';

import { useState } from 'react';
import {
  Bar,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { useI18n } from '@/lib/i18n';
import type { MonthPoint, SeasonOverlay } from '@/lib/types';
import { formatCurrency } from '@/lib/utils';

const axisTick = { fill: 'var(--text-muted)', fontSize: 11 };
const NEW_COLOR = 'var(--accent)';
const USED_COLOR = 'var(--info)';
const NORM_COLOR = 'var(--warning)';

export type MonthRow = {
  label: string;
  partial: boolean;
  soldNew: number;
  soldUsed: number;
  /** Sales the type's season profile expects at the model's average month. */
  norm: number | null;
  p50New: number | null;
  p50Used: number | null;
};

/** Chart rows; the norm is the mean of finished months times the type's index of that month. */
export function monthRows(months: MonthPoint[], overlay: SeasonOverlay | null, locale: string): MonthRow[] {
  const finished = months.filter((month) => !month.partial);
  const mean = finished.length ? finished.reduce((sum, month) => sum + month.sold, 0) / finished.length : 0;
  return months.map((month) => {
    const [year, number] = month.month.split('-').map(Number);
    const index = overlay?.si_abs[number - 1] ?? null;
    return {
      label: new Date(Date.UTC(year, number - 1, 1)).toLocaleDateString(locale, {
        month: 'short',
        year: '2-digit',
        timeZone: 'UTC',
      }),
      partial: month.partial,
      soldNew: month.sold_new,
      soldUsed: month.sold_used,
      norm: index === null || mean === 0 ? null : Math.round(mean * index * 10) / 10,
      p50New: month.p50_new === null ? null : month.p50_new / 100,
      p50Used: month.p50_used === null ? null : month.p50_used / 100,
    };
  });
}

/** The card's year: monthly sales by condition against the season norm, and monthly medians. */
export function MonthCharts({ months, overlay }: { months: MonthPoint[]; overlay: SeasonOverlay | null }) {
  const { locale, t } = useI18n();
  const [table, setTable] = useState(false);
  const rows = monthRows(months, overlay, locale);
  const hasNorm = rows.some((row) => row.norm !== null);
  const money = (value: number) => formatCurrency(Math.round(value * 100), locale);
  return (
    <div className="space-y-3">
      <div className="grid gap-5 lg:grid-cols-2">
        <figure className="space-y-2">
          <figcaption className="text-[13px] font-medium text-[var(--text-primary)]">{t('monthlySales')}</figcaption>
          <div className="h-56">
            <ResponsiveContainer width="100%" height="100%">
              <ComposedChart data={rows} margin={{ top: 4, right: 4, bottom: 0, left: -20 }}>
                <CartesianGrid vertical={false} stroke="var(--border-subtle)" />
                <XAxis dataKey="label" tick={axisTick} tickLine={false} axisLine={false} interval={1} />
                <YAxis allowDecimals={false} tick={axisTick} tickLine={false} axisLine={false} />
                <Tooltip cursor={{ fill: 'var(--bg-surface-hover)' }} />
                <Legend wrapperStyle={{ fontSize: 11 }} />
                <Bar dataKey="soldNew" name={t('conditionNew')} stackId="sold" fill={NEW_COLOR} maxBarSize={24} />
                <Bar dataKey="soldUsed" name={t('conditionUsed')} stackId="sold" fill={USED_COLOR} maxBarSize={24} radius={[4, 4, 0, 0]} />
                {hasNorm && (
                  <Line dataKey="norm" name={t('seasonNorm')} stroke={NORM_COLOR} strokeDasharray="4 3" strokeWidth={2} dot={false} connectNulls />
                )}
              </ComposedChart>
            </ResponsiveContainer>
          </div>
          {overlay && (
            <p className="text-xs text-[var(--text-muted)]">
              {t('seasonNormHelp')}
              {overlay.months_covered < 24 && ` · ${t('seasonOneYear')}`}
            </p>
          )}
        </figure>
        <figure className="space-y-2">
          <figcaption className="text-[13px] font-medium text-[var(--text-primary)]">{t('monthlyMedianPrice')}</figcaption>
          <div className="h-56">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={rows} margin={{ top: 8, right: 8, bottom: 0, left: -4 }}>
                <CartesianGrid vertical={false} stroke="var(--border-subtle)" />
                <XAxis dataKey="label" tick={axisTick} tickLine={false} axisLine={false} interval={1} />
                <YAxis tick={axisTick} tickLine={false} axisLine={false} tickFormatter={(value: number) => `$${Math.round(value)}`} width={56} />
                <Tooltip formatter={(value) => money(Number(value))} />
                <Legend wrapperStyle={{ fontSize: 11 }} />
                <Line dataKey="p50New" name={t('conditionNew')} stroke={NEW_COLOR} strokeWidth={2} connectNulls dot={{ r: 3 }} />
                <Line dataKey="p50Used" name={t('conditionUsed')} stroke={USED_COLOR} strokeWidth={2} connectNulls dot={{ r: 3 }} />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </figure>
      </div>
      <button
        type="button"
        className="text-xs text-[var(--accent)] hover:underline"
        aria-expanded={table}
        onClick={() => setTable((value) => !value)}
      >
        {table ? t('hideTable') : t('showTable')}
      </button>
      {table && (
        <table className="w-full max-w-2xl text-xs">
          <thead>
            <tr className="text-left text-[var(--text-muted)]">
              <th className="py-1 font-medium">{t('month')}</th>
              <th className="py-1 font-medium">{t('conditionNew')}</th>
              <th className="py-1 font-medium">{t('conditionUsed')}</th>
              {hasNorm && <th className="py-1 font-medium">{t('seasonNorm')}</th>}
              <th className="py-1 font-medium">p50 {t('conditionNew')}</th>
              <th className="py-1 font-medium">p50 {t('conditionUsed')}</th>
            </tr>
          </thead>
          <tbody className="tabular-nums text-[var(--text-secondary)]">
            {rows.map((row) => (
              <tr key={row.label} className="border-t border-[var(--border-subtle)]">
                <td className="py-1">
                  {row.label}
                  {row.partial && ` · ${t('partialMonth')}`}
                </td>
                <td className="py-1">{row.soldNew}</td>
                <td className="py-1">{row.soldUsed}</td>
                {hasNorm && <td className="py-1">{row.norm ?? '—'}</td>}
                <td className="py-1">{row.p50New === null ? '—' : money(row.p50New)}</td>
                <td className="py-1">{row.p50Used === null ? '—' : money(row.p50Used)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
