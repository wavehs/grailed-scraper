'use client';

import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, getApi } from '@/lib/api';
import { Button } from '@/components/ui/button';
import { EmptyState, ErrorState, LoadingState, Notice } from '@/components/states';
import { useI18n } from '@/lib/i18n';
import { useApiHealth } from '@/lib/queries';
import type { Brand, BrandList, Mapping } from '@/lib/types';

export default function BrandsPage() {
  const { t } = useI18n();
  const health = useApiHealth();
  const queryClient = useQueryClient();
  const [search, setSearch] = useState('');
  const [status, setStatus] = useState('all');
  const [aliases, setAliases] = useState<Record<number, string>>({});
  const [notice, setNotice] = useState('');
  const query = useQuery({
    queryKey: ['brands'],
    queryFn: ({ signal }) => getApi<BrandList>('/brands', signal),
  });
  const refresh = () => queryClient.invalidateQueries({ queryKey: ['brands'] });
  const autoMap = useMutation({
    mutationFn: () => api('/brands/auto-map', 'POST', {}),
    onSuccess: () => {
      setNotice(t('success'));
      refresh();
    },
  });
  const updateBrand = useMutation({
    mutationFn: ({ id, body }: { id: number; body: object }) =>
      api<Brand>(`/brands/${id}`, 'PATCH', body),
    onSuccess: () => {
      setNotice(t('updated'));
      refresh();
    },
  });
  const decide = useMutation({
    mutationFn: ({
      brandId,
      mappingId,
      action,
    }: {
      brandId: number;
      mappingId: number;
      action: 'confirm' | 'reject';
    }) => api<Mapping>(`/brands/${brandId}/mappings/${mappingId}`, 'PATCH', { action }),
    onSuccess: () => {
      setNotice(t('success'));
      refresh();
    },
  });
  const error = query.error ?? autoMap.error ?? updateBrand.error ?? decide.error;
  const brands = useMemo(
    () =>
      (query.data?.data ?? []).filter(
        (brand) =>
          (status === 'all' || brand.status === status) &&
          `${brand.name} ${brand.aliases.join(' ')}`.toLowerCase().includes(search.toLowerCase()),
      ),
    [query.data, search, status],
  );
  if (query.isLoading) return <LoadingState />;
  return (
    <section aria-labelledby="brands-heading" className="space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 id="brands-heading" className="text-2xl font-semibold">
            {t('brands')}
          </h1>
          <p className="text-slate-600">{t('brandsIntro')}</p>
        </div>
        <Button onClick={() => autoMap.mutate()} disabled={!health.writable || autoMap.isPending}>
          {autoMap.isPending ? t('mapping') : t('autoMap')}
        </Button>
      </div>
      {!health.isLoading && !health.writable && <Notice error>{t('mockRequired')}</Notice>}
      <Notice>{notice}</Notice>
      {error && <ErrorState error={error} retry={() => query.refetch()} />}
      <div className="grid gap-3 rounded-lg border bg-white p-4 sm:grid-cols-2">
        <label>
          <span className="sr-only">{t('search')}</span>
          <input
            className="w-full rounded border px-3"
            placeholder={t('search')}
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
        </label>
        <label>
          <span className="sr-only">{t('status')}</span>
          <select
            className="w-full rounded border px-3"
            value={status}
            onChange={(event) => setStatus(event.target.value)}
          >
            <option value="all">{t('allStatuses')}</option>
            <option value="verified">{t('verified')}</option>
            <option value="review">{t('review')}</option>
            <option value="unresolved">{t('unresolved')}</option>
          </select>
        </label>
      </div>
      {!brands.length ? (
        <EmptyState message={t('noBrands')} />
      ) : (
        <div className="space-y-4">
          {brands.map((brand) => {
            const aliasValue = aliases[brand.id] ?? brand.aliases.join(', ');
            return (
              <article className="rounded-lg border bg-white p-4" key={brand.id}>
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <h2 className="font-semibold">{brand.name}</h2>
                    <p className="text-sm text-slate-600">
                      {t(brand.status)} · {brand.listings_count} {t('listings')}
                    </p>
                  </div>
                  <label className="flex items-center gap-2 text-sm">
                    <input
                      type="checkbox"
                      checked={brand.include_subbrands}
                      disabled={!health.writable || updateBrand.isPending}
                      onChange={(event) =>
                        updateBrand.mutate({
                          id: brand.id,
                          body: { include_subbrands: event.target.checked },
                        })
                      }
                    />
                    {t('includeSubbrands')}
                  </label>
                </div>
                <form
                  className="mt-3 flex flex-wrap gap-2"
                  onSubmit={(event) => {
                    event.preventDefault();
                    updateBrand.mutate({
                      id: brand.id,
                      body: {
                        aliases: aliasValue
                          .split(',')
                          .map((item) => item.trim())
                          .filter(Boolean),
                      },
                    });
                  }}
                >
                  <label className="min-w-64 flex-1 text-sm">
                    <span className="sr-only">
                      {t('aliases')} — {brand.name}
                    </span>
                    <input
                      className="w-full rounded border px-3"
                      value={aliasValue}
                      disabled={!health.writable}
                      onChange={(event) =>
                        setAliases((current) => ({ ...current, [brand.id]: event.target.value }))
                      }
                      placeholder={t('aliases')}
                    />
                  </label>
                  <Button type="submit" disabled={!health.writable || updateBrand.isPending}>
                    {updateBrand.isPending ? t('saving') : t('save')}
                  </Button>
                </form>
                <div className="mt-4 overflow-x-auto">
                  <table className="w-full text-left text-sm">
                    <thead className="bg-slate-100">
                      <tr>
                        <th className="p-2">{t('grailedFacet')}</th>
                        <th className="p-2">{t('score')}</th>
                        <th className="p-2">{t('listings')}</th>
                        <th className="p-2">{t('status')}</th>
                        <th className="p-2">{t('actions')}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {brand.mappings.map((mapping) => (
                        <tr className="border-t" key={mapping.id}>
                          <td className="p-2">
                            {mapping.source_designer_name}
                            {mapping.is_subbrand ? ` · ${t('subbrand')}` : ''}
                          </td>
                          <td className="p-2">{(Number(mapping.match_score) * 100).toFixed(1)}%</td>
                          <td className="p-2">{mapping.listings_count}</td>
                          <td className="p-2">{t(mapping.state)}</td>
                          <td className="p-2">
                            {mapping.state === 'review' && (
                              <span className="flex gap-3">
                                <button
                                  className="underline"
                                  disabled={!health.writable || decide.isPending}
                                  onClick={() =>
                                    decide.mutate({
                                      brandId: brand.id,
                                      mappingId: mapping.id,
                                      action: 'confirm',
                                    })
                                  }
                                >
                                  {t('confirm')}
                                </button>
                                <button
                                  className="text-red-700 underline"
                                  disabled={!health.writable || decide.isPending}
                                  onClick={() =>
                                    decide.mutate({
                                      brandId: brand.id,
                                      mappingId: mapping.id,
                                      action: 'reject',
                                    })
                                  }
                                >
                                  {t('reject')}
                                </button>
                              </span>
                            )}
                          </td>
                        </tr>
                      ))}
                      {!brand.mappings.length && (
                        <tr>
                          <td className="p-3 text-slate-500" colSpan={5}>
                            {t('noCandidates')}
                          </td>
                        </tr>
                      )}
                    </tbody>
                  </table>
                </div>
              </article>
            );
          })}
        </div>
      )}
    </section>
  );
}
