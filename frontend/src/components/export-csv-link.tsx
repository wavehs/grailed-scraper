'use client';

import { Download } from 'lucide-react';
import { apiHref } from '@/lib/api';
import { useI18n } from '@/lib/i18n';
import { trendParams } from '@/lib/queries';
import type { TrendFilters } from '@/lib/types';

/** The export URL: the list's filters and order, every row (no page). */
export function trendExportHref(filters: TrendFilters): string {
  const params = trendParams(filters);
  params.delete('limit');
  params.delete('offset');
  return apiHref(`/trends/export.csv?${params}`);
}

export function ExportCsvLink({ filters }: { filters: TrendFilters }) {
  const { t } = useI18n();
  return (
    <a
      href={trendExportHref(filters)}
      download
      title={t('exportCsvHelp')}
      className="inline-flex h-7 items-center gap-1.5 rounded-[5px] border border-[var(--border-default)] bg-[var(--bg-control)] px-2 text-xs font-medium text-[var(--text-primary)] transition-colors hover:bg-[var(--bg-control-hover)]"
    >
      <Download size={14} aria-hidden />
      {t('exportCsv')}
    </a>
  );
}
