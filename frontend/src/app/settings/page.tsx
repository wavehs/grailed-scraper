'use client';

import { useEffect, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { BadgePercent, CalendarClock, Compass, Save, Shield, SlidersHorizontal } from 'lucide-react';
import { Badge, statusVariant } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { PageHeader } from '@/components/ui/page-header';
import { HelpTip } from '@/components/ui/help-tip';
import { ScheduleBrandPicker, ScheduleCard } from '@/components/schedule-settings';
import { ErrorState, LoadingState, Notice } from '@/components/states';
import { api } from '@/lib/api';
import { useI18n } from '@/lib/i18n';
import { useApiHealth, useParserHealth, useSettingsQuery } from '@/lib/queries';
import { formatDate } from '@/lib/utils';
import type { DiscoveryResponse, SettingEntry, SettingValue, SettingsResponse } from '@/lib/types';

const selects: Record<string, string[]> = {
  store_seller_identity: ['none', 'hashed', 'plain'],
  deals_scope: ['all', 'watchlist'],
};
const optionalNumbers = new Set(['collect_price_min_usd', 'collect_price_max_usd']);
// The API sends these Decimals as strings; the form edits them as numbers.
const decimals = new Set(['deals_min_margin']);
type Bounds = { min: number; max?: number; integer: boolean };
// Mirrors the API's validation so an out-of-range value never reaches a 422.
const bounds: Record<string, Bounds> = {
  requests_per_minute: { min: 1, integer: true },
  max_concurrent_requests: { min: 1, max: 3, integer: true },
  sold_history_days: { min: 30, max: 3650, integer: true },
  collect_price_min_usd: { min: 0, integer: false },
  collect_price_max_usd: { min: 1, integer: false },
  schedule_hour: { min: 0, max: 23, integer: true },
  schedule_rpm: { min: 1, integer: true },
  deals_interval_minutes: { min: 5, max: 1440, integer: true },
  deals_rpm: { min: 1, integer: true },
  deals_min_margin: { min: 0, max: 10, integer: false },
};

/** The message for an invalid number, or null when the value is acceptable. */
function rangeError(name: string, value: SettingValue, t: (key: string) => string) {
  const limit = bounds[name];
  if (!limit || (value === null && optionalNumbers.has(name))) return null;
  const number = typeof value === 'number' ? value : Number.NaN;
  const valid =
    Number.isFinite(number) &&
    number >= limit.min &&
    (limit.max === undefined || number <= limit.max) &&
    (!limit.integer || Number.isInteger(number));
  if (valid) return null;
  const key =
    limit.max !== undefined ? 'numberOutOfRange' : limit.integer ? 'wholeAtLeast' : 'numberAtLeast';
  return t(key).replace('{min}', String(limit.min)).replace('{max}', String(limit.max));
}
const groupIcons: Record<string, JSX.Element> = {
  collection: <SlidersHorizontal size={16} />,
  privacy: <Shield size={16} />,
  schedule: <CalendarClock size={16} />,
  deals: <BadgePercent size={16} />,
};

export default function SettingsPage() {
  const { t, locale } = useI18n();
  const client = useQueryClient();
  const health = useApiHealth();
  const parserHealth = useParserHealth();
  const [values, setValues] = useState<Record<string, SettingValue>>({});
  const [notice, setNotice] = useState('');
  const [confirmPlain, setConfirmPlain] = useState(false);
  const settings = useSettingsQuery();
  useEffect(() => {
    if (settings.data)
      setValues(
        Object.fromEntries(
          Object.values(settings.data.groups).flatMap((group) =>
            Object.entries(group).map(([key, entry]) => [
              key,
              decimals.has(key) ? Number(entry.value) : entry.value,
            ]),
          ),
        ),
      );
  }, [settings.data]);
  const save = useMutation({
    mutationFn: () =>
      api<SettingsResponse>('/settings', 'PATCH', {
        ...values,
        confirm_plain_seller_identity: confirmPlain,
      }),
    onSuccess: (data) => {
      client.setQueryData(['settings'], data);
      setNotice(t('updated'));
      client.invalidateQueries({ queryKey: ['parser-health'] });
      // The hour, rate and switch change what the schedule card reports.
      client.invalidateQueries({ queryKey: ['schedule'] });
      client.invalidateQueries({ queryKey: ['deals'] });
    },
  });
  const discovery = useMutation({
    mutationFn: () => api<DiscoveryResponse>('/parser/discovery/refresh', 'POST', { force: true }),
    onSuccess: () => {
      setNotice(t('success'));
      client.invalidateQueries({ queryKey: ['parser-health'] });
    },
  });
  const error = settings.error ?? save.error ?? discovery.error;
  if (settings.isLoading) return <LoadingState />;
  if (!settings.data) return <ErrorState error={error} retry={() => settings.refetch()} />;
  const update = (key: string, value: SettingValue) =>
    setValues((old) => ({ ...old, [key]: value }));
  const source = parserHealth.data?.discovery;
  const invalid = Object.entries(values).some(([key, value]) => rangeError(key, value, t));
  return (
    <section className="space-y-6" aria-labelledby="settings-heading">
      <PageHeader title={t('settings')} description={t('settingsIntro')} />
      <Notice>{notice}</Notice>
      {error && <ErrorState error={error} retry={() => settings.refetch()} />}
      <form
        className="space-y-5"
        onSubmit={(event) => {
          event.preventDefault();
          save.mutate();
        }}
      >
        {Object.entries(settings.data.groups).map(([groupName, group]) => (
          <Card className="p-5" key={groupName}>
            <h2 className="mb-4 flex items-center gap-2 text-sm font-semibold uppercase tracking-wider text-[var(--text-muted)]">
              {groupIcons[groupName]}
              {t(groupName)}
            </h2>
            <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
              {Object.entries(group).map(([key, entry]) => (
                <SettingField
                  entry={entry}
                  key={key}
                  name={key}
                  value={key in values ? values[key] : entry.value}
                  update={update}
                />
              ))}
            </div>
          </Card>
        ))}
        {values.store_seller_identity === 'plain' && (
          <Notice error>
            <label className="flex items-start gap-2">
              <input
                type="checkbox"
                checked={confirmPlain}
                onChange={(event) => setConfirmPlain(event.target.checked)}
              />
              <span>{t('confirmPlainSellerIdentity')}</span>
            </label>
          </Notice>
        )}
        <Button icon={<Save size={16} />} disabled={!health.writable || invalid || save.isPending}>
          {save.isPending ? t('saving') : t('save')}
        </Button>
      </form>

      <ScheduleCard writable={health.writable} />

      <Card className="space-y-3 p-5">
        <h2 className="flex items-center gap-2 text-sm font-semibold uppercase tracking-wider text-[var(--text-muted)]">
          <Compass size={16} /> {t('discovery')}
        </h2>
        <p className="text-sm text-[var(--text-secondary)]">{t('discoveryHelp')}</p>
        {source && (
          <p className="flex flex-wrap items-center gap-2 text-sm text-[var(--text-secondary)]">
            <Badge variant={statusVariant(source.status)} dot>
              {t(source.status)}
            </Badge>
            {source.discovered_at && formatDate(source.discovered_at, locale)}
          </p>
        )}
        <Button
          variant="secondary"
          icon={<Compass size={14} />}
          disabled={!health.writable || discovery.isPending}
          onClick={() => discovery.mutate()}
        >
          {discovery.isPending ? t('refreshing') : t('refreshDiscovery')}
        </Button>
      </Card>
    </section>
  );
}

function SettingField({
  name,
  entry,
  value,
  update,
}: {
  name: string;
  entry: SettingEntry;
  value: SettingValue;
  update: (key: string, value: SettingValue) => void;
}) {
  const { t } = useI18n();
  const label = t(name);
  const numeric = typeof entry.value === 'number' || optionalNumbers.has(name) || decimals.has(name);
  const limit = bounds[name];
  const problem = rangeError(name, value, t);
  const header = (
    <div className="mb-1.5 flex items-center gap-2">
      <span className="font-medium text-[var(--text-primary)]">{label}</span>
      <HelpTip label={label} text={t(`${name}_help`)} />
      <Badge variant="muted">{t(entry.origin)}</Badge>
    </div>
  );
  if (name === 'schedule_brand_ids')
    // Brand buttons inside a <label> would all be activated by a click on its text.
    return (
      <div className="block text-sm md:col-span-2 xl:col-span-3">
        {header}
        <ScheduleBrandPicker
          value={Array.isArray(value) ? value : null}
          onChange={(next) => update(name, next)}
        />
      </div>
    );
  return (
    <label className="block text-sm">
      {header}
      {entry.origin === 'database' &&
      entry.base_value !== undefined &&
      entry.base_value !== entry.value ? (
        <p className="mb-1.5 text-xs text-[var(--text-secondary)]">
          {t('overridesBase')}:{' '}
          {entry.base_value === null ? t('noLimit') : String(entry.base_value)}
        </p>
      ) : null}
      {typeof entry.value === 'boolean' ? (
        <span className="flex items-center gap-2 text-[var(--text-secondary)]">
          <input
            type="checkbox"
            checked={Boolean(value)}
            onChange={(event) => update(name, event.target.checked)}
          />
          {t(value ? 'yes' : 'no')}
        </span>
      ) : selects[name] ? (
        <select
          className="w-full rounded-lg"
          value={String(value)}
          onChange={(event) => update(name, event.target.value)}
        >
          {selects[name].map((item) => (
            <option value={item} key={item}>
              {t(`${name}_${item}`) === `${name}_${item}` ? item : t(`${name}_${item}`)}
            </option>
          ))}
        </select>
      ) : (
        <input
          className="w-full rounded-lg"
          type={numeric ? 'number' : 'text'}
          min={limit?.min ?? 0}
          max={limit?.max}
          step={limit && !limit.integer ? 'any' : undefined}
          aria-invalid={problem ? true : undefined}
          placeholder={optionalNumbers.has(name) ? t('noLimit') : undefined}
          value={value === null || Number.isNaN(value) ? '' : String(value)}
          onChange={(event) => {
            const raw = event.target.value;
            if (!numeric) return update(name, raw);
            // An emptied required field is invalid, not zero.
            if (raw === '') return update(name, optionalNumbers.has(name) ? null : Number.NaN);
            update(name, Number(raw));
          }}
        />
      )}
      {problem && (
        <p role="alert" className="mt-1 text-xs text-[var(--danger)]">
          {problem}
        </p>
      )}
    </label>
  );
}
