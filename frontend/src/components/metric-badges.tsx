'use client';

import { useI18n } from '@/lib/i18n';

/** Codes from backend services/metrics/badges.py, in display order. */
const BADGES: Array<[string, string]> = [
  ['low_data', '❔'],
  ['hot', '🔥'],
  ['stable', '💎'],
  ['slow', '🐢'],
  ['saturated', '⚠️'],
];

/** A model's badges as emoji with their rule in the tooltip; unknown codes are skipped. */
export function MetricBadges({ badges }: { badges: readonly string[] | undefined }) {
  const { t } = useI18n();
  const shown = BADGES.filter(([code]) => badges?.includes(code));
  if (shown.length === 0) return null;
  return (
    <span className="inline-flex gap-0.5 text-sm leading-none">
      {shown.map(([code, emoji]) => (
        <span key={code} role="img" aria-label={t(`badge_${code}`)} title={t(`badge_${code}_help`)}>
          {emoji}
        </span>
      ))}
    </span>
  );
}
