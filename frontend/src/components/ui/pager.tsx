'use client';

import { type ReactNode } from 'react';
import { Button } from '@/components/ui/button';
import { useI18n } from '@/lib/i18n';

/** Previous/next buttons under a list, with an optional range label on the left. */
export function Pager({
  range,
  hasPrevious,
  hasNext,
  busy = false,
  onPrevious,
  onNext,
}: {
  range?: ReactNode;
  hasPrevious: boolean;
  hasNext: boolean;
  busy?: boolean;
  onPrevious: () => void;
  onNext: () => void;
}) {
  const { t } = useI18n();
  if (!hasPrevious && !hasNext) return null;
  return (
    <div className="flex items-center justify-end gap-2 pt-3 text-xs">
      {range && <span className="tabular-nums text-[var(--text-muted)]">{range}</span>}
      <Button variant="secondary" size="sm" disabled={!hasPrevious || busy} onClick={onPrevious}>
        {t('previous')}
      </Button>
      <Button variant="secondary" size="sm" disabled={!hasNext || busy} onClick={onNext}>
        {t('next')}
      </Button>
    </div>
  );
}
