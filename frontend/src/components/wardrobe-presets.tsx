'use client';

import { X } from 'lucide-react';
import { useI18n } from '@/lib/i18n';
import { SEASONS, type Season } from '@/lib/periods';
import type { TrendFilters } from '@/lib/types';

const ICONS: Record<Season, string> = { autumn: '🍂', winter: '❄️', spring: '🌱', summer: '☀️' };

/** What a wardrobe button sets: the season's types and its last finished calendar season. */
export function wardrobePatch(season: Season, types: string[]): Partial<TrendFilters> {
  return { wardrobe: season, productTypes: types, productType: '', section: '', period: season };
}

export const CLEAR_WARDROBE: Partial<TrendFilters> = { wardrobe: null, productTypes: [] };

/** Season wardrobe buttons from `config/seasons.yaml`; seasons without types are hidden. */
export function WardrobePresets({
  presets,
  filters,
  update,
}: {
  presets: Record<string, string[]> | undefined;
  filters: TrendFilters;
  update: (patch: Partial<TrendFilters>) => void;
}) {
  const { t } = useI18n();
  const seasons = SEASONS.filter((season) => (presets?.[season]?.length ?? 0) > 0);
  if (!seasons.length) return null;
  const active = filters.wardrobe;
  return (
    <div className="flex flex-wrap items-center gap-2" role="group" aria-label={t('wardrobe')}>
      <span className="text-xs text-[var(--text-muted)]" title={t('wardrobeHelp')}>
        {t('wardrobe')}
      </span>
      {seasons.map((season) => (
        <button
          key={season}
          type="button"
          aria-pressed={active === season}
          onClick={() => update(wardrobePatch(season, presets?.[season] ?? []))}
          className={`rounded-md border px-2.5 py-1 text-xs ${
            active === season
              ? 'border-[var(--accent)] bg-[var(--accent-soft)] font-semibold text-[var(--accent)]'
              : 'border-[var(--border-default)] text-[var(--text-secondary)]'
          }`}
        >
          {ICONS[season]} {t(`season_${season}`)}
        </button>
      ))}
      {active && (
        <span className="inline-flex items-center gap-1 rounded-full bg-[var(--accent-soft)] px-2 py-0.5 text-xs text-[var(--accent)]">
          {t('wardrobeTypes').replace('{count}', String(filters.productTypes.length))}
          <button
            type="button"
            aria-label={t('wardrobeClear')}
            onClick={() => update(CLEAR_WARDROBE)}
            className="rounded-full p-0.5 hover:bg-[var(--bg-surface-hover)]"
          >
            <X className="h-3 w-3" aria-hidden />
          </button>
        </span>
      )}
    </div>
  );
}
