'use client';

import { useEffect, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Compass } from 'lucide-react';
import { LanguageSelect } from '@/components/language-select';
import { ScheduleStatus } from '@/components/schedule-settings';
import { DECIMAL_SETTINGS, SettingField, rangeError } from '@/components/settings/setting-field';
import { SettingRow, SettingsGroup } from '@/components/settings/settings-group';
import { Badge, statusVariant } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { PageHeader } from '@/components/ui/page-header';
import { ErrorState, LoadingState, Notice } from '@/components/states';
import { api } from '@/lib/api';
import { useI18n } from '@/lib/i18n';
import { useApiHealth, useParserHealth, useSettingsQuery } from '@/lib/queries';
import { formatDate } from '@/lib/utils';
import type { DiscoveryResponse, SettingValue, SettingsResponse } from '@/lib/types';

/** Flat form values from the grouped settings; Decimal strings become numbers. */
function formValues(data: SettingsResponse): Record<string, SettingValue> {
  return Object.fromEntries(
    Object.values(data.groups).flatMap((group) =>
      Object.entries(group).map(([key, entry]) => [
        key,
        DECIMAL_SETTINGS.has(key) ? Number(entry.value) : entry.value,
      ]),
    ),
  );
}

export default function SettingsPage() {
  const { t } = useI18n();
  const client = useQueryClient();
  const health = useApiHealth();
  const [values, setValues] = useState<Record<string, SettingValue>>({});
  const [notice, setNotice] = useState('');
  const [confirmPlain, setConfirmPlain] = useState(false);
  const settings = useSettingsQuery();
  useEffect(() => {
    if (settings.data) setValues(formValues(settings.data));
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
      // The hour, rate and switch change what the schedule status reports.
      client.invalidateQueries({ queryKey: ['schedule'] });
      client.invalidateQueries({ queryKey: ['deals'] });
    },
  });
  if (settings.isLoading) return <LoadingState />;
  if (!settings.data) return <ErrorState error={settings.error} retry={() => settings.refetch()} />;
  const update = (key: string, value: SettingValue) => setValues((old) => ({ ...old, [key]: value }));
  const invalid = Object.entries(values).some(([key, value]) => rangeError(key, value, t));

  return (
    <section aria-labelledby="settings-heading">
      <PageHeader title={t('settings')} headingId="settings-heading" description={t('settingsIntro')} />
      <div className="mx-auto max-w-[640px] space-y-8 py-8">
        <SettingsGroup title={t('interface')}>
          <SettingRow label={t('language')} htmlFor="app-language" description={t('languageHelp')}>
            <LanguageSelect className="w-full" />
          </SettingRow>
        </SettingsGroup>

        <form
          className="space-y-8"
          onSubmit={(event) => {
            event.preventDefault();
            save.mutate();
          }}
        >
          {Object.entries(settings.data.groups).map(([groupName, group]) => (
            <SettingsGroup key={groupName} title={t(groupName)}>
              {Object.entries(group).map(([key, entry]) => (
                <SettingField
                  key={key}
                  name={key}
                  entry={entry}
                  value={key in values ? values[key] : entry.value}
                  update={update}
                />
              ))}
            </SettingsGroup>
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
          <div className="flex items-center justify-end gap-3">
            <Notice>{notice}</Notice>
            {save.error && <ErrorState error={save.error} />}
            <Button disabled={!health.writable || invalid || save.isPending}>
              {save.isPending ? t('saving') : t('save')}
            </Button>
          </div>
        </form>

        <ScheduleStatus writable={health.writable} />
        <DiscoveryGroup writable={health.writable} onNotice={setNotice} />
      </div>
    </section>
  );
}

/** The connection to Grailed's search: its state and a forced refresh. */
function DiscoveryGroup({ writable, onNotice }: { writable: boolean; onNotice: (text: string) => void }) {
  const { t, locale } = useI18n();
  const client = useQueryClient();
  const source = useParserHealth().data?.discovery;
  const discovery = useMutation({
    mutationFn: () => api<DiscoveryResponse>('/parser/discovery/refresh', 'POST', { force: true }),
    onSuccess: () => {
      onNotice(t('success'));
      client.invalidateQueries({ queryKey: ['parser-health'] });
    },
  });
  return (
    <SettingsGroup title={t('discovery')} description={t('discoveryHelp')}>
      <SettingRow
        label={t('status')}
        meta={
          source && (
            <Badge variant={statusVariant(source.status)}>{t(`discovery_${source.status}`)}</Badge>
          )
        }
        description={source?.discovered_at ? formatDate(source.discovered_at, locale) : undefined}
        error={discovery.error ? String((discovery.error as Error).message) : null}
      >
        <div className="flex justify-end">
          <Button
            variant="secondary"
            size="sm"
            icon={<Compass size={13} />}
            disabled={!writable || discovery.isPending}
            onClick={() => discovery.mutate()}
          >
            {discovery.isPending ? t('refreshing') : t('refreshDiscovery')}
          </Button>
        </div>
      </SettingRow>
    </SettingsGroup>
  );
}
