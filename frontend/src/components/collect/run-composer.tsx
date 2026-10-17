'use client';

import { useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { useMutation } from '@tanstack/react-query';
import { Play } from 'lucide-react';
import { ErrorState, LoadingState } from '@/components/states';
import { Button } from '@/components/ui/button';
import { HelpTip } from '@/components/ui/help-tip';
import { Switch } from '@/components/ui/switch';
import { api } from '@/lib/api';
import { useI18n } from '@/lib/i18n';
import type { Brand, RunStartResponse } from '@/lib/types';

const isPositiveInteger = (value: string) => Number.isInteger(Number(value)) && Number(value) >= 1;

/** Starts a collection: which mapped brands, a one-off full run and an optional rate cap. */
export function RunComposer({
  brands,
  loading,
  onStarted,
}: {
  brands?: Brand[];
  loading: boolean;
  onStarted: () => void;
}) {
  const { t } = useI18n();
  const mapped = useMemo(() => (brands ?? []).filter((brand) => brand.status === 'verified'), [brands]);
  const [selected, setSelected] = useState<number[] | null>(null);
  const [full, setFull] = useState(false);
  const [rpm, setRpm] = useState('');
  const rpmInvalid = rpm !== '' && !isPositiveInteger(rpm);
  useEffect(() => {
    if (selected === null && brands) setSelected(mapped.map((brand) => brand.id));
  }, [brands, mapped, selected]);
  const chosen = selected ?? [];
  const toggle = (id: number) =>
    setSelected(chosen.includes(id) ? chosen.filter((item) => item !== id) : [...chosen, id]);
  const start = useMutation({
    mutationFn: () =>
      api<RunStartResponse>('/parser/run', 'POST', {
        brand_ids: chosen.length < mapped.length ? chosen : null,
        ...(full ? { full: true } : {}),
        ...(rpm !== '' ? { rpm: Number(rpm) } : {}),
      }),
    onSuccess: () => {
      // A full run is a one-off choice; the next start is a delta again.
      setFull(false);
      onStarted();
    },
  });

  return (
    <div className="-mx-4 space-y-4 border-b border-[var(--border-subtle)] px-4 py-5 md:-mx-6 md:px-6">
      <div>
        <h2 className="text-[13px] font-medium text-[var(--text-primary)]">{t('updateData')}</h2>
        <p className="mt-0.5 text-xs text-[var(--text-muted)]">{t('updateDataHelp')}</p>
      </div>
      {loading && <LoadingState />}
      {brands && !mapped.length && (
        <p className="text-xs text-[var(--text-secondary)]">
          {t('noMappedBrands')}{' '}
          <Link className="text-[var(--text-primary)] underline" href="/brands">
            {t('brands')}
          </Link>
        </p>
      )}
      {mapped.length > 0 && (
        <div role="group" aria-label={t('brands')} className="flex flex-wrap gap-1.5">
          {mapped.map((brand) => (
            <button
              key={brand.id}
              type="button"
              className="chip"
              aria-pressed={chosen.includes(brand.id)}
              onClick={() => toggle(brand.id)}
            >
              {brand.name}
              {brand.listings_count ? (
                <span className="tabular-nums text-[var(--text-muted)]">{brand.listings_count}</span>
              ) : null}
            </button>
          ))}
        </div>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <span className="mr-2 flex items-center gap-1">
          <span className="flex items-center gap-2 text-xs text-[var(--text-secondary)]">
            <Switch id="run-full" label={t('fullRun')} checked={full} onChange={setFull} />
            <label htmlFor="run-full" className="cursor-pointer">
              {t('fullRun')}
            </label>
          </span>
          <HelpTip label={t('fullRun')} text={t('fullRunHelp')} />
        </span>
        <label className="flex items-center gap-2 text-xs text-[var(--text-secondary)]">
          {t('runRpm')}
          <input
            className="control-sm w-28"
            type="number"
            min={1}
            placeholder={t('runRpmPlaceholder')}
            value={rpm}
            onChange={(event) => setRpm(event.target.value)}
          />
        </label>
        {rpmInvalid && (
          <span role="alert" className="text-xs text-[var(--danger)]">
            {t('runRpmInvalid')}
          </span>
        )}
        <Button
          className="ml-auto"
          size="sm"
          icon={<Play size={13} />}
          disabled={!chosen.length || rpmInvalid || start.isPending}
          onClick={() => start.mutate()}
        >
          {start.isPending ? t('starting') : t('updateData')}
        </Button>
      </div>
      {start.error && <ErrorState error={start.error} />}
    </div>
  );
}
