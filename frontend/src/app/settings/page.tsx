'use client';

import { useEffect, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Compass, Save, Shield, SlidersHorizontal } from 'lucide-react';
import { Badge, statusVariant } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { PageHeader } from '@/components/ui/page-header';
import { HelpTip } from '@/components/ui/help-tip';
import { ErrorState, LoadingState, Notice } from '@/components/states';
import { api } from '@/lib/api';
import { useI18n } from '@/lib/i18n';
import { useApiHealth, useParserHealth, useSettingsQuery } from '@/lib/queries';
import { formatDate } from '@/lib/utils';
import type { DiscoveryResponse, SettingEntry, SettingValue, SettingsResponse } from '@/lib/types';

const selects: Record<string, string[]> = {
  store_seller_identity: ['none', 'hashed', 'plain'],
};
const optionalNumbers = new Set(['collect_price_min_usd', 'collect_price_max_usd']);
const groupIcons: Record<string, JSX.Element> = {
  collection: <SlidersHorizontal size={16} />,
  privacy: <Shield size={16} />,
  compliance: <Shield size={16} />,
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
            Object.entries(group).map(([key, entry]) => [key, entry.value]),
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
  const update = (key: string, value: SettingValue) => setValues((old) => ({ ...old, [key]: value }));
  const source = parserHealth.data?.discovery;
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
        <Button icon={<Save size={16} />} disabled={!health.writable || save.isPending}>
          {save.isPending ? t('saving') : t('save')}
        </Button>
      </form>

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
  const numeric = typeof entry.value === 'number' || optionalNumbers.has(name);
  return (
    <label className="block text-sm">
      <div className="mb-1.5 flex items-center gap-2">
        <span className="font-medium text-[var(--text-primary)]">{label}</span>
        <HelpTip label={label} text={t(`${name}_help`)} />
        <Badge variant="muted">{t(entry.origin)}</Badge>
      </div>
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
          min={0}
          placeholder={optionalNumbers.has(name) ? t('noLimit') : undefined}
          value={value === null ? '' : String(value)}
          onChange={(event) => {
            const raw = event.target.value;
            if (!numeric) return update(name, raw);
            update(name, raw === '' && optionalNumbers.has(name) ? null : Number(raw));
          }}
        />
      )}
    </label>
  );
}
