'use client';

import { useEffect, useState } from 'react';
import { RotateCcw, Save, SlidersHorizontal } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Notice } from '@/components/states';
import { errorMessage } from '@/lib/api';
import { useI18n } from '@/lib/i18n';
import {
  MAX_WEIGHT,
  WEIGHT_KEYS,
  draftFromPolicy,
  draftToUpdate,
  isLiquiditySettings,
  periodLabel,
  sameDraft,
  weightShares,
  withPeriod,
  type LiquidityDraft,
} from '@/lib/liquidity';
import {
  useLiquiditySettingsQuery,
  useResetLiquiditySettings,
  useSaveLiquiditySettings,
  useTypeNames,
} from '@/lib/queries';
import type { LiquidityPolicyUpdate, LiquiditySettings, LiquidityWeightKey } from '@/lib/types';
import { formatPercent } from '@/lib/utils';

/** How long "Ranking recalculated: N" stays on screen. */
const RANKED_NOTICE_MS = 5_000;
const WEIGHT_LABELS: Record<LiquidityWeightKey, string> = {
  sales: 'weightSales',
  speed: 'weightSpeed',
  likes: 'weightLikes',
  price: 'weightPrice',
};

export type RankingControls = {
  settings: LiquiditySettings | null;
  busy: boolean;
  error: unknown;
  /** Rows scored by the last save or reset while its notice is shown. */
  ranked: number | null;
  save: (policy: LiquidityPolicyUpdate) => void;
  reset: () => void;
};

/** The saved liquidity policy and the save and reset actions shared by the period and panel. */
export function useRankingControls(): RankingControls {
  const query = useLiquiditySettingsQuery();
  const saveMutation = useSaveLiquiditySettings();
  const resetMutation = useResetLiquiditySettings();
  const [notice, setNotice] = useState<{ ranked: number; at: number } | null>(null);
  useEffect(() => {
    if (!notice) return;
    const timer = window.setTimeout(() => setNotice(null), RANKED_NOTICE_MS);
    return () => window.clearTimeout(timer);
  }, [notice]);
  const done = (data: LiquiditySettings) => setNotice({ ranked: data.ranked ?? 0, at: Date.now() });
  return {
    settings: isLiquiditySettings(query.data) ? query.data : null,
    busy: saveMutation.isPending || resetMutation.isPending,
    error: saveMutation.error ?? resetMutation.error ?? query.error,
    ranked: notice?.ranked ?? null,
    save: (policy) => {
      resetMutation.reset();
      setNotice(null);
      saveMutation.mutate(policy, { onSuccess: done });
    },
    reset: () => {
      saveMutation.reset();
      setNotice(null);
      resetMutation.mutate(undefined, { onSuccess: done });
    },
  };
}

/** Segmented buttons that save the ranking period at once. */
export function PeriodControl({ controls }: { controls: RankingControls }) {
  const { t } = useI18n();
  const { settings, busy, save } = controls;
  if (!settings) return null;
  return (
    <div
      className="inline-flex flex-wrap rounded-lg border border-[var(--border-default)] p-0.5"
      role="group"
      aria-label={t('rankingPeriod')}
      title={t('rankingPeriodHelp')}
    >
      {settings.periods.map((days) => (
        <button
          key={days}
          type="button"
          aria-pressed={settings.period_days === days}
          disabled={busy}
          onClick={() => settings.period_days !== days && save(withPeriod(settings, days))}
          className={`rounded-md px-2.5 py-1 text-xs tabular-nums disabled:cursor-not-allowed disabled:opacity-60 ${
            settings.period_days === days
              ? 'bg-[var(--accent-soft)] font-semibold text-[var(--accent)]'
              : 'text-[var(--text-secondary)]'
          }`}
        >
          {periodLabel(days, t)}
        </button>
      ))}
    </div>
  );
}

/** Pending, error and "recalculated" lines for both the period buttons and the panel. */
export function RankingStatus({ controls }: { controls: RankingControls }) {
  const { t } = useI18n();
  if (controls.busy)
    return (
      <p role="status" className="text-xs text-[var(--text-secondary)]">
        {t('reranking')}
      </p>
    );
  if (controls.error) return <Notice error>{errorMessage(controls.error)}</Notice>;
  if (controls.ranked !== null)
    return <Notice>{t('rankingRecalculated').replace('{count}', String(controls.ranked))}</Notice>;
  return null;
}

/** A collapsible panel with the threshold, excluded sections and weights of the ranking. */
export function RankingSettings({ controls }: { controls: RankingControls }) {
  const { t } = useI18n();
  const { settings } = controls;
  if (!settings) return null;
  return (
    <Card className="p-0">
      <details>
        <summary className="flex cursor-pointer items-center gap-2 px-4 py-3 text-sm font-medium text-[var(--text-primary)]">
          <SlidersHorizontal size={16} className="text-[var(--text-muted)]" />
          {t('rankingSettings')}
          {!settings.saved && <Badge variant="muted">{t('defaultsInUse')}</Badge>}
        </summary>
        <RankingForm
          // A new saved policy starts a fresh form.
          key={JSON.stringify(draftFromPolicy(settings))}
          settings={settings}
          controls={controls}
        />
      </details>
    </Card>
  );
}

function RankingForm({ settings, controls }: { settings: LiquiditySettings; controls: RankingControls }) {
  const { t } = useI18n();
  const [draft, setDraft] = useState<LiquidityDraft>(() => draftFromPolicy(settings));
  const check = draftToUpdate(draft, settings.period_days);
  const changed = !sameDraft(draft, draftFromPolicy(settings));
  const patch = (next: Partial<LiquidityDraft>) => setDraft((current) => ({ ...current, ...next }));
  return (
    <form
      className="space-y-4 border-t border-[var(--border-subtle)] p-4"
      onSubmit={(event) => {
        event.preventDefault();
        if (check.body) controls.save(check.body);
      }}
    >
      <p className="text-xs text-[var(--text-muted)]">{t('rankingSettingsHelp')}</p>
      <div className="flex flex-wrap gap-4">
        <NumberField
          label={t('minSalesPerMonth')}
          value={draft.minSales}
          step={0.5}
          onChange={(minSales) => patch({ minSales })}
        />
        <NumberField
          label={t('minPriceUsd')}
          value={draft.minPrice}
          step={1}
          onChange={(minPrice) => patch({ minPrice })}
        />
      </div>
      <SectionChecklist excluded={draft.excluded} onChange={(excluded) => patch({ excluded })} />
      <WeightFields draft={draft} onChange={(weights) => patch({ weights })} />
      {changed && check.error && (
        <p role="alert" className="text-xs text-[var(--danger)]">
          {t(check.error)}
        </p>
      )}
      <div className="flex flex-wrap gap-2">
        <Button icon={<Save size={16} />} disabled={!changed || !check.body || controls.busy}>
          {controls.busy ? t('saving') : t('save')}
        </Button>
        <Button
          type="button"
          variant="secondary"
          icon={<RotateCcw size={14} />}
          disabled={!settings.saved || controls.busy}
          onClick={controls.reset}
        >
          {t('resetDefaults')}
        </Button>
      </div>
    </form>
  );
}

function NumberField({
  label,
  value,
  step,
  onChange,
}: {
  label: string;
  value: string;
  step: number;
  onChange: (value: string) => void;
}) {
  return (
    <label className="block text-sm">
      <span className="mb-1.5 block font-medium text-[var(--text-primary)]">{label}</span>
      <input
        className="w-36 rounded-lg"
        type="number"
        min={0}
        step={step}
        value={value}
        onChange={(event) => onChange(event.target.value)}
      />
    </label>
  );
}

function SectionChecklist({
  excluded,
  onChange,
}: {
  excluded: string[];
  onChange: (excluded: string[]) => void;
}) {
  const { locale, t } = useI18n();
  const { taxonomy } = useTypeNames();
  const sections = taxonomy?.sections ?? [];
  if (!sections.length) return null;
  const toggle = (id: string) =>
    onChange(excluded.includes(id) ? excluded.filter((item) => item !== id) : [...excluded, id]);
  return (
    <fieldset className="space-y-1.5">
      <legend className="mb-1.5 text-sm font-medium text-[var(--text-primary)]">{t('excludedSections')}</legend>
      <div className="flex flex-wrap gap-x-4 gap-y-1.5">
        {sections.map((section) => (
          <label key={section.id} className="flex items-center gap-1.5 text-sm text-[var(--text-secondary)]">
            <input type="checkbox" checked={excluded.includes(section.id)} onChange={() => toggle(section.id)} />
            {section[locale]}
          </label>
        ))}
      </div>
    </fieldset>
  );
}

function WeightFields({
  draft,
  onChange,
}: {
  draft: LiquidityDraft;
  onChange: (weights: LiquidityDraft['weights']) => void;
}) {
  const { locale, t } = useI18n();
  const shares = weightShares(draft);
  return (
    <fieldset className="space-y-1.5">
      <legend className="text-sm font-medium text-[var(--text-primary)]">{t('rankingWeights')}</legend>
      <p className="text-xs text-[var(--text-muted)]">{t('rankingWeightsHelp')}</p>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {WEIGHT_KEYS.map((key) => (
          <div key={key} className="text-sm">
            <label className="flex flex-col gap-1.5 text-[var(--text-secondary)]">
              {t(WEIGHT_LABELS[key])}
              <input
                className="w-24 rounded-lg"
                type="number"
                min={0}
                max={MAX_WEIGHT}
                step={1}
                value={draft.weights[key]}
                onChange={(event) => onChange({ ...draft.weights, [key]: event.target.value })}
              />
            </label>
            <span className="mt-1 block text-xs tabular-nums text-[var(--text-muted)]">
              {shares ? t('weightShare').replace('{share}', formatPercent(shares.shares[key])) : '—'}
            </span>
          </div>
        ))}
      </div>
      {shares && (
        <p className="text-xs tabular-nums text-[var(--text-muted)]">
          {t('weightsTotal').replace('{total}', new Intl.NumberFormat(locale).format(shares.total))}
        </p>
      )}
    </fieldset>
  );
}
