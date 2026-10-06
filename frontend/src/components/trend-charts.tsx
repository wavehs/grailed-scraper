'use client';

import { useState } from 'react';
import {
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { useI18n } from '@/lib/i18n';
import { formatCurrency } from '@/lib/utils';

const WEEK_MS = 7 * 24 * 60 * 60 * 1000;

/** Twelve weekly columns: past weeks recede, the current week carries the accent. */
export function Sparkline({ values, label }: { values: number[]; label: string }) {
  const width = 84;
  const height = 24;
  const bar = 5;
  const gap = 2;
  const max = Math.max(...values, 1);
  return (
    <svg
      role="img"
      aria-label={`${label}: ${values.join(', ')}`}
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      className="shrink-0"
    >
      <title>{`${label}: ${values.join(', ')}`}</title>
      <line x1={0} x2={width} y1={height - 0.5} y2={height - 0.5} stroke="var(--border-subtle)" />
      {values.map((value, index) => {
        const barHeight = value ? Math.max((value / max) * (height - 2), 2) : 0;
        return (
          <rect
            key={index}
            x={index * (bar + gap)}
            y={height - barHeight}
            width={bar}
            height={barHeight}
            rx={1}
            fill={index === values.length - 1 ? 'var(--accent)' : 'var(--border-strong)'}
          />
        );
      })}
    </svg>
  );
}

function weekLabels(computedAt: string | null, count: number, locale: string): string[] {
  const end = computedAt ? new Date(computedAt).getTime() : Date.now();
  return Array.from({ length: count }, (_, index) =>
    new Date(end - (count - 1 - index) * WEEK_MS).toLocaleDateString(locale, {
      day: 'numeric',
      month: 'short',
    }),
  );
}

const axisTick = { fill: 'var(--text-muted)', fontSize: 11 };

function ChartTooltip({
  active,
  payload,
  label,
  format,
}: {
  active?: boolean;
  payload?: Array<{ value?: number | null }>;
  label?: string;
  format: (value: number) => string;
}) {
  const value = payload?.[0]?.value;
  if (!active || value === undefined || value === null) return null;
  return (
    <div className="rounded-md border border-[var(--border-default)] bg-[var(--bg-surface-raised)] px-2.5 py-1.5 text-xs shadow-sm">
      <p className="text-[var(--text-muted)]">{label}</p>
      <p className="font-semibold tabular-nums text-[var(--text-primary)]">{format(value)}</p>
    </div>
  );
}

/** Weekly sales (columns) and weekly median sale price (line): two charts, never one with two axes. */
export function WeeklyCharts({
  sales,
  prices,
  computedAt,
}: {
  sales: number[];
  prices: Array<number | null>;
  computedAt: string | null;
}) {
  const { locale, t } = useI18n();
  const [table, setTable] = useState(false);
  const labels = weekLabels(computedAt, sales.length, locale);
  const rows = labels.map((week, index) => ({
    week,
    sales: sales[index] ?? 0,
    price: prices[index] === null || prices[index] === undefined ? null : prices[index]! / 100,
  }));
  return (
    <div className="space-y-3">
      <div className="grid gap-5 lg:grid-cols-2">
        <figure className="space-y-2">
          <figcaption className="text-sm font-medium text-[var(--text-primary)]">
            {t('weeklySales')}
          </figcaption>
          <div className="h-52">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={rows} margin={{ top: 4, right: 4, bottom: 0, left: -20 }}>
                <CartesianGrid vertical={false} stroke="var(--border-subtle)" />
                <XAxis dataKey="week" tick={axisTick} tickLine={false} axisLine={false} interval={1} />
                <YAxis allowDecimals={false} tick={axisTick} tickLine={false} axisLine={false} />
                <Tooltip
                  cursor={{ fill: 'var(--bg-surface-hover)' }}
                  content={<ChartTooltip format={(value) => `${value} ${t('salesShort')}`} />}
                />
                <Bar dataKey="sales" fill="var(--accent)" maxBarSize={24} radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </figure>
        <figure className="space-y-2">
          <figcaption className="text-sm font-medium text-[var(--text-primary)]">
            {t('weeklyMedianPrice')}
          </figcaption>
          <div className="h-52">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={rows} margin={{ top: 8, right: 8, bottom: 0, left: -4 }}>
                <CartesianGrid vertical={false} stroke="var(--border-subtle)" />
                <XAxis dataKey="week" tick={axisTick} tickLine={false} axisLine={false} interval={1} />
                <YAxis
                  tick={axisTick}
                  tickLine={false}
                  axisLine={false}
                  tickFormatter={(value: number) => `$${Math.round(value)}`}
                  width={56}
                />
                <Tooltip
                  cursor={{ stroke: 'var(--border-strong)', strokeWidth: 1 }}
                  content={
                    <ChartTooltip format={(value) => formatCurrency(Math.round(value * 100), locale)} />
                  }
                />
                <Line
                  dataKey="price"
                  type="monotone"
                  stroke="var(--accent)"
                  strokeWidth={2}
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  connectNulls
                  dot={{ r: 4, fill: 'var(--accent)', stroke: 'var(--bg-surface)', strokeWidth: 2 }}
                  activeDot={{ r: 5, fill: 'var(--accent)', stroke: 'var(--bg-surface)', strokeWidth: 2 }}
                />
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
        <table className="w-full max-w-md text-xs">
          <thead>
            <tr className="text-left text-[var(--text-muted)]">
              <th className="py-1 font-medium">{t('week')}</th>
              <th className="py-1 font-medium">{t('weeklySales')}</th>
              <th className="py-1 font-medium">{t('weeklyMedianPrice')}</th>
            </tr>
          </thead>
          <tbody className="tabular-nums text-[var(--text-secondary)]">
            {rows.map((row) => (
              <tr key={row.week} className="border-t border-[var(--border-subtle)]">
                <td className="py-1">{row.week}</td>
                <td className="py-1">{row.sales}</td>
                <td className="py-1">
                  {row.price === null ? '—' : formatCurrency(Math.round(row.price * 100), locale)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
