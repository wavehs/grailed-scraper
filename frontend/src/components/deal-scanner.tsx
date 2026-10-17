'use client';

import { type ReactNode } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Radar } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { HelpTip } from '@/components/ui/help-tip';
import { Popover } from '@/components/ui/popover';
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

/** One inline figure of the scanner strip: a muted label over its value. */
function ScannerStat({ label, help, children }: { label: string; help?: string; children: ReactNode }) {
  return (
    <div className="min-w-0">
      <p className="flex items-center gap-1 text-xs text-[var(--text-muted)]">
        {label}
        {help && <HelpTip label={label} text={help} />}
      </p>
      <div className="text-[13px] text-[var(--text-primary)]">{children}</div>
    </div>
  );
}

/** The scanner's state under the top bar: schedule, hit rate, last scan and a manual run. */
export function DealScannerBar({
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
    <div className="-mx-4 space-y-2 border-b border-[var(--border-subtle)] px-4 py-3 md:-mx-6 md:px-6">
      <div className="flex flex-wrap items-center gap-x-8 gap-y-3">
        <ScannerStat label={t('dealsScanner')}>
          <Badge variant={scanner.enabled ? 'success' : 'muted'} dot>
            {scanner.enabled
              ? fill(t('dealsScannerOn'), { minutes: scanner.interval_minutes })
              : t('dealsScannerOff')}
          </Badge>
        </ScannerStat>
        <ScannerStat label={t('dealsHitRate')} help={t('dealsHitRateHelp')}>
          <span className="font-medium tabular-nums">{formatRatio(summary.hit_rate)}</span>{' '}
          <span className="text-xs text-[var(--text-secondary)]">
            {fill(t('dealsRated'), { good: summary.good, bad: summary.bad, unrated: summary.unrated })}
          </span>
        </ScannerStat>
        {last && (
          <ScannerStat label={t('dealsLastScan')}>
            {formatDate(last.finished_at, locale)}{' '}
            <span className="text-xs text-[var(--text-secondary)]">
              {fill(t('dealsLastScanSummary'), {
                brands: last.brands,
                hits: last.hits,
                deals: last.deals?.deals ?? 0,
                new: last.deals?.new ?? 0,
              })}
            </span>
          </ScannerStat>
        )}
        <div className="ml-auto flex items-center gap-2">
          {scanner.brands.length > 0 && (
            <Popover label={t('dealsBrandScans')} align="end" className="w-80">
              <ul className="space-y-1 p-1.5 text-xs text-[var(--text-secondary)]">
                {scanner.brands.map((brand) => (
                  <li key={brand.brand_id} className="flex justify-between gap-3">
                    <span className="truncate text-[var(--text-body)]">{brand.brand}</span>
                    <span className="shrink-0 tabular-nums">
                      {formatDate(brand.scanned_to, locale)} · {brand.hits}
                      {brand.error && <span className="text-[var(--danger)]"> · {brand.error}</span>}
                    </span>
                  </li>
                ))}
              </ul>
            </Popover>
          )}
          <Button
            size="sm"
            variant="secondary"
            icon={<Radar size={13} />}
            disabled={!writable || scanner.running || scan.isPending}
            onClick={() => scan.mutate()}
          >
            {scanner.running ? t('dealsScanning') : t('dealsScanNow')}
          </Button>
        </div>
      </div>
      {!scanner.enabled && <p className="text-xs text-[var(--text-muted)]">{t('dealsScannerOffHelp')}</p>}
      {scanner.skipped_at && (
        <p className="text-xs text-[var(--text-muted)]">
          {fill(t('dealsSkipped'), { time: formatDate(scanner.skipped_at, locale) })}
        </p>
      )}
      {truncated.length > 0 && (
        <p className="text-xs text-[var(--warning)]">{fill(t('dealsTruncated'), { brands: truncated.join(', ') })}</p>
      )}
      {last?.aborted && <p className="text-xs text-[var(--warning)]">{t('dealsAborted')}</p>}
      {scanner.last_error && (
        <Notice error>
          {t('dealsScanError')}: {scanner.last_error}
        </Notice>
      )}
      {scan.error && <ErrorState error={scan.error} />}
      {scan.isSuccess && <Notice>{t('dealsScanStarted')}</Notice>}
    </div>
  );
}
