'use client';

import { useEffect, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Compass, Network, Save, Shield } from 'lucide-react';
import { Badge, statusVariant } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { DataTable, TableCell, TableHead, TableHeaderCell, TableRow } from '@/components/ui/data-table';
import { PageHeader } from '@/components/ui/page-header';
import { StatCard } from '@/components/ui/stat-card';
import { ErrorState, LoadingState, Notice } from '@/components/states';
import { api } from '@/lib/api';
import { useI18n } from '@/lib/i18n';
import { useApiHealth, useSettingsQuery } from '@/lib/queries';
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
              {groupName === 'source' && <Compass size={16} />}
              {groupName === 'parser' && <Shield size={16} />}
              {groupName === 'proxy' && <Network size={16} />}
              {groupName === 'privacy' && <Shield size={16} />}
              {t(groupName)}
            </h2>
            <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
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
        <Button
          icon={<Save size={16} />}
          disabled={!health.writable || save.isPending}
        >
          {save.isPending ? t('saving') : t('save')}
        </Button>
      </form>

      <div className="grid gap-5 lg:grid-cols-2">
        {/* Proxy test */}
        <Card className="p-5">
          <h2 className="mb-3 flex items-center gap-2 text-sm font-semibold uppercase tracking-wider text-[var(--text-muted)]">
            <Network size={16} /> {t('proxy')}
          </h2>
          <Button
            variant="secondary"
            icon={<Network size={14} />}
            disabled={!health.writable || proxyTest.isPending}
            onClick={() => proxyTest.mutate()}
          >
            {proxyTest.isPending ? t('testing') : t('testProxies')}
          </Button>
          {proxyTest.data && (
            <div className="mt-4">
              <DataTable>
                <TableHead>
                  <tr>
                    <TableHeaderCell>Proxy</TableHeaderCell>
                    <TableHeaderCell>{t('successRate')}</TableHeaderCell>
                    <TableHeaderCell>{t('status')}</TableHeaderCell>
                  </tr>
                </TableHead>
                <tbody>
                  {proxyTest.data.proxies.map((proxy) => (
                    <TableRow key={proxy.proxy}>
                      <TableCell className="font-mono text-xs">{proxy.proxy}</TableCell>
                      <TableCell>{(proxy.success_rate * 100).toFixed(0)}%</TableCell>
                      <TableCell>
                        <Badge variant={statusVariant(proxy.cooling_down ? 'cooldown' : 'ready')} dot>
                          {proxy.cooling_down ? t('cooldown') : t('ready')}
                        </Badge>
                      </TableCell>
                    </TableRow>
                  ))}
                </tbody>
              </DataTable>
            </div>
          )}
        </Card>

        {/* Discovery */}
        <Card className="p-5">
          <h2 className="mb-3 flex items-center gap-2 text-sm font-semibold uppercase tracking-wider text-[var(--text-muted)]">
            <Compass size={16} /> {t('discovery')}
          </h2>
          <Button
            variant="secondary"
            icon={<Compass size={14} />}
            disabled={!health.writable || discovery.isPending}
            onClick={() => discovery.mutate()}
          >
            {discovery.isPending ? t('refreshing') : t('refreshDiscovery')}
          </Button>
          {discovery.data && (
            <dl className="mt-4 space-y-2 text-sm">
              <div className="flex justify-between">
                <dt className="text-[var(--text-muted)]">{t('status')}</dt>
                <dd>
                  <Badge variant={statusVariant(discovery.data.status)} dot>
                    {t(discovery.data.status)}
                  </Badge>
                </dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-[var(--text-muted)]">{t('method')}</dt>
                <dd className="text-[var(--text-primary)]">{discovery.data.method ?? '—'}</dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-[var(--text-muted)]">{t('schemaFields')}</dt>
                <dd className="text-[var(--text-primary)]">{discovery.data.schema_field_count}</dd>
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
    <label className="block text-sm">
      <div className="mb-1.5 flex items-center gap-2">
        <span className="font-medium text-[var(--text-primary)]">{label}</span>
        <Badge variant="muted">{entry.origin}</Badge>
      </div>
      {typeof entry.value === 'boolean' ? (
        <span className="flex items-center gap-2 text-[var(--text-secondary)]">
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
          className="w-full rounded-lg"
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
          className="w-full rounded-lg"
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
