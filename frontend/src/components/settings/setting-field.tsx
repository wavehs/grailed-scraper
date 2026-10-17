'use client';

import { ScheduleBrandPicker } from '@/components/schedule-settings';
import { SettingRow } from '@/components/settings/settings-group';
import { Badge } from '@/components/ui/badge';
import { Switch } from '@/components/ui/switch';
import { useI18n } from '@/lib/i18n';
import type { SettingEntry, SettingValue } from '@/lib/types';

const selects: Record<string, string[]> = {
  store_seller_identity: ['none', 'hashed', 'plain'],
  deals_scope: ['all', 'watchlist'],
};
const optionalNumbers = new Set(['collect_price_min_usd', 'collect_price_max_usd']);
// The API sends these Decimals as strings; the form edits them as numbers.
export const DECIMAL_SETTINGS = new Set(['deals_min_margin']);
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
export function rangeError(name: string, value: SettingValue, t: (key: string) => string) {
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

/** One editable setting as a row: name, help, where the value comes from and its control. */
export function SettingField({
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
  const id = `setting-${name}`;
  const label = t(name);
  const overridden =
    entry.origin === 'database' && entry.base_value !== undefined && entry.base_value !== entry.value;
  const row = {
    label,
    help: t(`${name}_help`),
    meta: <Badge variant="muted">{t(entry.origin)}</Badge>,
    description: overridden
      ? `${t('overridesBase')}: ${entry.base_value === null ? t('noLimit') : String(entry.base_value)}`
      : undefined,
  };
  if (name === 'schedule_brand_ids')
    return (
      <SettingRow {...row} wide>
        <ScheduleBrandPicker value={Array.isArray(value) ? value : null} onChange={(next) => update(name, next)} />
      </SettingRow>
    );
  if (typeof entry.value === 'boolean')
    return (
      <SettingRow {...row} htmlFor={id}>
        <div className="flex justify-end">
          <Switch id={id} label={label} checked={Boolean(value)} onChange={(checked) => update(name, checked)} />
        </div>
      </SettingRow>
    );
  if (selects[name])
    return (
      <SettingRow {...row} htmlFor={id}>
        <select id={id} className="w-full" value={String(value)} onChange={(event) => update(name, event.target.value)}>
          {selects[name].map((item) => (
            <option value={item} key={item}>
              {t(`${name}_${item}`) === `${name}_${item}` ? item : t(`${name}_${item}`)}
            </option>
          ))}
        </select>
      </SettingRow>
    );
  return (
    <SettingRow {...row} htmlFor={id} error={rangeError(name, value, t)}>
      <NumberOrTextInput id={id} name={name} entry={entry} value={value} update={update} />
    </SettingRow>
  );
}

function NumberOrTextInput({
  id,
  name,
  entry,
  value,
  update,
}: {
  id: string;
  name: string;
  entry: SettingEntry;
  value: SettingValue;
  update: (key: string, value: SettingValue) => void;
}) {
  const { t } = useI18n();
  const numeric = typeof entry.value === 'number' || optionalNumbers.has(name) || DECIMAL_SETTINGS.has(name);
  const limit = bounds[name];
  return (
    <input
      id={id}
      className="w-full"
      type={numeric ? 'number' : 'text'}
      min={limit?.min ?? 0}
      max={limit?.max}
      step={limit && !limit.integer ? 'any' : undefined}
      aria-invalid={rangeError(name, value, t) ? true : undefined}
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
  );
}
