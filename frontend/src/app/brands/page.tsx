'use client';

import { useMemo, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Check, Save, Search, Wand2, X } from 'lucide-react';
import { api } from '@/lib/api';
import { Badge, statusVariant } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { DataTable, TableCell, TableHead, TableHeaderCell, TableRow } from '@/components/ui/data-table';
import { PageHeader } from '@/components/ui/page-header';
import { EmptyState, ErrorState, LoadingState, Notice } from '@/components/states';
import { useI18n } from '@/lib/i18n';
import { useApiHealth, useBrandsQuery } from '@/lib/queries';
import type { Brand, BrandList, Mapping } from '@/lib/types';

export default function BrandsPage() {
  const { t } = useI18n();
  const health = useApiHealth();
  const queryClient = useQueryClient();
  const [search, setSearch] = useState('');
  const [status, setStatus] = useState('all');
  const [aliases, setAliases] = useState<Record<number, string>>({});
  const [notice, setNotice] = useState('');
  const query = useBrandsQuery();
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
    <section aria-labelledby="brands-heading" className="space-y-6">
      <PageHeader
        title={t('brands')}
        description={t('brandsIntro')}
        actions={
          <Button
            icon={<Wand2 size={16} />}
            onClick={() => autoMap.mutate()}
            disabled={!health.writable || autoMap.isPending}
          >
            {autoMap.isPending ? t('mapping') : t('autoMap')}
          </Button>
        }
      />
      <Notice>{notice}</Notice>
      {error && <ErrorState error={error} retry={() => query.refetch()} />}

      {/* Filters */}
      <Card className="p-4">
        <div className="grid gap-3 sm:grid-cols-2">
          <label className="relative">
            <Search
              size={16}
              className="absolute left-3 top-1/2 -translate-y-1/2 text-[var(--text-muted)]"
            />
            <input
              className="w-full rounded-lg pl-9"
              placeholder={t('search')}
              value={search}
              onChange={(event) => setSearch(event.target.value)}
            />
          </label>
          <select
            className="w-full rounded-lg"
            value={status}
            onChange={(event) => setStatus(event.target.value)}
          >
            <option value="all">{t('allStatuses')}</option>
            <option value="verified">{t('verified')}</option>
            <option value="review">{t('review')}</option>
            <option value="unresolved">{t('unresolved')}</option>
          </select>
        </div>
      </Card>

      {/* Brand list */}
      {!brands.length ? (
        <EmptyState message={t('noBrands')} />
      ) : (
        <div className="space-y-4">
          {brands.map((brand) => {
            const aliasValue = aliases[brand.id] ?? brand.aliases.join(', ');
            return (
              <Card className="p-5 animate-slide-up" key={brand.id}>
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <div className="flex items-center gap-2">
                      <h2 className="font-semibold text-[var(--text-primary)]">{brand.name}</h2>
                      <Badge variant={statusVariant(brand.status)} dot>
                        {t(brand.status)}
                      </Badge>
                    </div>
                    <p className="mt-0.5 text-xs text-[var(--text-muted)]">
                      {brand.listings_count} {t('listings')}
                    </p>
                  </div>
                  <label className="flex items-center gap-2 text-sm text-[var(--text-secondary)]">
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

                {/* Aliases form */}
                <form
                  className="mt-4 flex flex-wrap gap-2"
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
                      className="w-full rounded-lg"
                      value={aliasValue}
                      disabled={!health.writable}
                      onChange={(event) =>
                        setAliases((current) => ({ ...current, [brand.id]: event.target.value }))
                      }
                      placeholder={t('aliases')}
                    />
                  </label>
                  <Button
                    type="submit"
                    variant="secondary"
                    icon={<Save size={14} />}
                    disabled={!health.writable || updateBrand.isPending}
                  >
                    {updateBrand.isPending ? t('saving') : t('save')}
                  </Button>
                </form>

                {/* Mappings table */}
                <div className="mt-4">
                  <DataTable>
                    <TableHead>
                      <tr>
                        <TableHeaderCell>{t('grailedFacet')}</TableHeaderCell>
                        <TableHeaderCell>{t('score')}</TableHeaderCell>
                        <TableHeaderCell>{t('listings')}</TableHeaderCell>
                        <TableHeaderCell>{t('status')}</TableHeaderCell>
                        <TableHeaderCell>{t('actions')}</TableHeaderCell>
                      </tr>
                    </TableHead>
                    <tbody>
                      {brand.mappings.map((mapping) => (
                        <TableRow key={mapping.id}>
                          <TableCell>
                            <span className="text-[var(--text-primary)]">
                              {mapping.source_designer_name}
                            </span>
                            {mapping.is_subbrand && (
                              <Badge variant="muted" className="ml-2">
                                {t('subbrand')}
                              </Badge>
                            )}
                          </TableCell>
                          <TableCell>
                            {(Number(mapping.match_score) * 100).toFixed(1)}%
                          </TableCell>
                          <TableCell>{mapping.listings_count}</TableCell>
                          <TableCell>
                            <Badge variant={statusVariant(mapping.state)} dot>
                              {t(mapping.state)}
                            </Badge>
                          </TableCell>
                          <TableCell>
                            {mapping.state === 'review' && (
                              <span className="flex gap-2">
                                <Button
                                  variant="success"
                                  size="sm"
                                  icon={<Check size={14} />}
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
                                </Button>
                                <Button
                                  variant="danger"
                                  size="sm"
                                  icon={<X size={14} />}
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
                                </Button>
                              </span>
                            )}
                          </TableCell>
                        </TableRow>
                      ))}
                      {!brand.mappings.length && (
                        <TableRow>
                          <TableCell className="text-[var(--text-muted)]" colSpan={5}>
                            {t('noCandidates')}
                          </TableCell>
                        </TableRow>
                      )}
                    </tbody>
                  </DataTable>
                </div>
              </Card>
            );
          })}
        </div>
      )}
    </section>
  );
}
