'use client';

import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Radar } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { HelpTip } from '@/components/ui/help-tip';
import { ErrorState, Notice } from '@/components/states';
import { formatRatio } from '@/components/liquidity-cells';
import { api } from '@/lib/api';
import { useI18n } from '@/lib/i18n';
import type { DealScanner, DealSummary } from '@/lib/types';
import { formatDate } from '@/lib/utils';

/** Put values into a `{name}` template string. */
export function fill(template: string, values: Record<string, string | number>): string {
  return Object.entries(values).reduce(
    (text, [name, value]) => text.replaceAll(`{${name}}`, String(value)),
    template,
  );
}

export function DealScannerCard({
  scanner,
  summary,
  writable,
}: {
  scanner: DealScanner;
  summary: DealSummary;
  writable: boolean;
}) {
  const { t, locale } = useI18n();
  const client = useQueryClient();
  const scan = useMutation({
    mutationFn: () => api<{ started: boolean }>('/deals/scan', 'POST'),
    onSuccess: () => client.invalidateQueries({ queryKey: ['deals'] }),
  });
  const last = scanner.last;
  const truncated = last?.truncated ?? [];
  return (
    <Card className="space-y-3 p-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="flex items-center gap-2 text-sm font-semibold uppercase tracking-wider text-[var(--text-muted)]">
          <Radar size={16} /> {t('dealsScanner')}
          <Badge variant={scanner.enabled ? 'success' : 'muted'} dot>
            {scanner.enabled
              ? fill(t('dealsScannerOn'), { minutes: scanner.interval_minutes })
              : t('dealsScannerOff')}
          </Badge>
        </h2>
        <Button
          size="sm"
          variant="secondary"
          icon={<Radar size={14} />}
          disabled={!writable || scanner.running || scan.isPending}
          onClick={() => scan.mutate()}
        >
          {scanner.running ? t('dealsScanning') : t('dealsScanNow')}
        </Button>
      </div>
      {!scanner.enabled && <p className="text-sm text-[var(--text-secondary)]">{t('dealsScannerOffHelp')}</p>}
      <dl className="grid gap-2 text-sm sm:grid-cols-2">
        <div>
          <dt className="flex items-center gap-1 text-[var(--text-muted)]">
            {t('dealsHitRate')} <HelpTip label={t('dealsHitRate')} text={t('dealsHitRateHelp')} />
          </dt>
          <dd className="text-[var(--text-primary)]">
            <span className="text-lg font-semibold tabular-nums">{formatRatio(summary.hit_rate)}</span>{' '}
            <span className="text-xs text-[var(--text-secondary)]">
              {fill(t('dealsRated'), { good: summary.good, bad: summary.bad, unrated: summary.unrated })}
            </span>
          </dd>
        </div>
        {last && (
          <div>
            <dt className="text-[var(--text-muted)]">{t('dealsLastScan')}</dt>
            <dd className="text-[var(--text-primary)]">
              {formatDate(last.finished_at, locale)}
              <p className="text-xs text-[var(--text-secondary)]">
                {fill(t('dealsLastScanSummary'), {
                  brands: last.brands,
                  hits: last.hits,
                  deals: last.deals?.deals ?? 0,
                  new: last.deals?.new ?? 0,
                })}
              </p>
            </dd>
          </div>
        )}
      </dl>
      {scanner.skipped_at && (
        <p className="text-xs text-[var(--text-muted)]">
          {fill(t('dealsSkipped'), { time: formatDate(scanner.skipped_at, locale) })}
        </p>
      )}
      {truncated.length > 0 && (
        <p className="text-xs text-[var(--warning)]">{fill(t('dealsTruncated'), { brands: truncated.join(', ') })}</p>
      )}
      {scanner.last_error && (
        <Notice error>
          {t('dealsScanError')}: {scanner.last_error}
        </Notice>
      )}
      {scan.error && <ErrorState error={scan.error} />}
      {scan.isSuccess && <Notice>{t('dealsScanStarted')}</Notice>}
      {scanner.brands.length > 0 && (
        <details className="text-xs text-[var(--text-secondary)]">
          <summary className="cursor-pointer">{t('dealsBrandScans')}</summary>
          <ul className="mt-2 space-y-1">
            {scanner.brands.map((brand) => (
              <li key={brand.brand_id}>
                {brand.brand}: {formatDate(brand.scanned_to, locale)} · {brand.hits}
                {brand.error && <span className="text-[var(--danger)]"> · {brand.error}</span>}
              </li>
            ))}
          </ul>
        </details>
      )}
    </Card>
  );
}
