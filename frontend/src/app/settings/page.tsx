'use client';

import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { ErrorState, LoadingState, Notice } from '@/components/states';
import { api, getApi } from '@/lib/api';
import { useI18n } from '@/lib/i18n';
import { useApiHealth } from '@/lib/queries';
import type { DiscoveryResponse, ProxyStatus, SettingEntry, SettingsResponse } from '@/lib/types';

type ProxyTest = { enabled: boolean; direct_fallback_allowed: boolean; proxies: ProxyStatus[] };
const selects: Record<string, string[]> = {
  fetch_tier_preferred: ['T1', 'T2', 'T3'],
  algolia_pagination_strategy: ['auto', 'browse', 'keyset', 'range_split'],
  algolia_attributes_mode: ['full', 'lean'],
  parser_mode: ['delta', 'full'],
  proxy_rotation_mode: ['round_robin', 'random', 'weighted'],
  store_seller_identity: ['none', 'hashed', 'plain'],
};

export default function SettingsPage() {
  const { t } = useI18n();
  const client = useQueryClient();
  const health = useApiHealth();
  const [values, setValues] = useState<Record<string, string | number | boolean>>({});
  const [notice, setNotice] = useState('');
  const [confirmPlain, setConfirmPlain] = useState(false);
  const settings = useQuery({
    queryKey: ['settings'],
    queryFn: ({ signal }) => getApi<SettingsResponse>('/settings', signal),
  });
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
      client.invalidateQueries({ queryKey: ['api-health'] });
      client.invalidateQueries({ queryKey: ['parser-health'] });
    },
  });
  const proxyTest = useMutation({
    mutationFn: () => api<ProxyTest>('/settings/proxies/test', 'POST'),
    onSuccess: () => setNotice(t('success')),
  });
  const discovery = useMutation({
    mutationFn: () => api<DiscoveryResponse>('/parser/discovery/refresh', 'POST', { force: true }),
    onSuccess: () => {
      setNotice(t('success'));
      client.invalidateQueries({ queryKey: ['parser-health'] });
    },
  });
  const error = settings.error ?? save.error ?? proxyTest.error ?? discovery.error;
  if (settings.isLoading) return <LoadingState />;
  if (!settings.data) return <ErrorState error={error} retry={() => settings.refetch()} />;
  const update = (key: string, value: string | number | boolean) =>
    setValues((old) => ({ ...old, [key]: value }));
  return (
    <section className="space-y-5" aria-labelledby="settings-heading">
      <div>
        <h1 id="settings-heading" className="text-2xl font-semibold">
          {t('settings')}
        </h1>
        <p className="text-slate-600">{t('settingsIntro')}</p>
      </div>
      <Notice>{notice}</Notice>
      {error && <ErrorState error={error} retry={() => settings.refetch()} />}
      <form
        className="space-y-4"
        onSubmit={(event) => {
          event.preventDefault();
          save.mutate();
        }}
      >
        {Object.entries(settings.data.groups).map(([groupName, group]) => (
          <Card className="p-4" key={groupName}>
            <h2 className="text-lg font-semibold">{t(groupName)}</h2>
            <div className="mt-3 grid gap-4 md:grid-cols-2 xl:grid-cols-3">
              {Object.entries(group).map(([key, entry]) => (
                <SettingField
                  entry={entry}
                  key={key}
                  name={key}
                  value={values[key] ?? entry.value}
                  update={update}
                  locked={false}
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
        <Button disabled={!health.writable || save.isPending}>
          {save.isPending ? t('saving') : t('save')}
        </Button>
      </form>
      <div className="grid gap-4 lg:grid-cols-2">
        <Card className="p-4">
          <h2 className="font-semibold">{t('proxy')}</h2>
          <Button
            className="mt-3"
            disabled={!health.writable || proxyTest.isPending}
            onClick={() => proxyTest.mutate()}
          >
            {proxyTest.isPending ? t('testing') : t('testProxies')}
          </Button>
          {proxyTest.data && (
            <div className="mt-3 overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead>
                  <tr>
                    <th className="p-2">Proxy</th>
                    <th className="p-2">{t('successRate')}</th>
                    <th className="p-2">{t('status')}</th>
                  </tr>
                </thead>
                <tbody>
                  {proxyTest.data.proxies.map((proxy) => (
                    <tr className="border-t" key={proxy.proxy}>
                      <td className="p-2">{proxy.proxy}</td>
                      <td className="p-2">{(proxy.success_rate * 100).toFixed(0)}%</td>
                      <td className="p-2">{proxy.cooling_down ? t('cooldown') : t('ready')}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
        <Card className="p-4">
          <h2 className="font-semibold">{t('discovery')}</h2>
          <Button
            className="mt-3"
            disabled={!health.writable || discovery.isPending}
            onClick={() => discovery.mutate()}
          >
            {discovery.isPending ? t('refreshing') : t('refreshDiscovery')}
          </Button>
          {discovery.data && (
            <dl className="mt-3 space-y-2 text-sm">
              <div className="flex justify-between">
                <dt>{t('status')}</dt>
                <dd>{t(discovery.data.status)}</dd>
              </div>
              <div className="flex justify-between">
                <dt>{t('method')}</dt>
                <dd>{discovery.data.method ?? '—'}</dd>
              </div>
              <div className="flex justify-between">
                <dt>{t('schemaFields')}</dt>
                <dd>{discovery.data.schema_field_count}</dd>
              </div>
            </dl>
          )}
        </Card>
      </div>
    </section>
  );
}

function SettingField({
  name,
  entry,
  value,
  update,
  locked,
}: {
  name: string;
  entry: SettingEntry;
  value: string | number | boolean;
  update: (key: string, value: string | number | boolean) => void;
  locked: boolean;
}) {
  const { t } = useI18n();
  const label = t(name);
  return (
    <label className="text-sm">
      <span className="font-medium">{label}</span>
      <span className="ml-2 rounded bg-slate-100 px-1.5 py-0.5 text-xs">
        {t('origin')}: {entry.origin}
      </span>
      {typeof entry.value === 'boolean' ? (
        <span className="mt-2 flex items-center gap-2">
          <input
            type="checkbox"
            checked={Boolean(value)}
            disabled={locked}
            onChange={(event) => update(name, event.target.checked)}
          />
          {String(Boolean(value))}
        </span>
      ) : selects[name] ? (
        <select
          className="mt-1 w-full rounded border px-2"
          value={String(value)}
          disabled={locked}
          onChange={(event) => update(name, event.target.value)}
        >
          {selects[name].map((item) => (
            <option value={item} key={item}>
              {item}
            </option>
          ))}
        </select>
      ) : (
        <input
          className="mt-1 w-full rounded border px-2"
          type="number"
          value={String(value)}
          disabled={locked}
          onChange={(event) =>
            update(
              name,
              typeof entry.value === 'number' ? Number(event.target.value) : event.target.value,
            )
          }
        />
      )}
    </label>
  );
}
