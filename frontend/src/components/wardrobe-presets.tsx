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
    <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label={t('wardrobe')}>
      <span className="mr-0.5 text-xs text-[var(--text-muted)]" title={t('wardrobeHelp')}>
        {t('wardrobe')}
      </span>
      {seasons.map((season) => (
        <button
          key={season}
          type="button"
          aria-pressed={active === season}
          onClick={() => update(wardrobePatch(season, presets?.[season] ?? []))}
          className="chip"
        >
          {ICONS[season]} {t(`season_${season}`)}
        </button>
      ))}
      {active && (
        <span className="inline-flex h-6 items-center gap-1 rounded-[5px] bg-[var(--accent-soft)] pl-2 pr-1 text-xs text-[var(--text-primary)]">
          {t('wardrobeTypes').replace('{count}', String(filters.productTypes.length))}
          <button
            type="button"
            aria-label={t('wardrobeClear')}
            onClick={() => update(CLEAR_WARDROBE)}
            className="rounded-[3px] p-0.5 text-[var(--text-secondary)] hover:bg-[var(--bg-control-hover)] hover:text-[var(--text-primary)]"
          >
            <X className="h-3 w-3" aria-hidden />
          </button>
        </span>
      )}
    </div>
  );
}
