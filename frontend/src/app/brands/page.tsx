'use client';

import { useMemo, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Wand2 } from 'lucide-react';
import { AddBrand } from '@/components/brands/add-brand';
import { BrandDetail } from '@/components/brands/brand-detail';
import { Badge, StatusDot, statusVariant } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Modal } from '@/components/ui/modal';
import { PageHeader } from '@/components/ui/page-header';
import { SearchInput } from '@/components/ui/search-input';
import { Segmented } from '@/components/ui/segmented';
import { Toolbar } from '@/components/ui/toolbar';
import { EmptyState, ErrorState, LoadingState, Notice } from '@/components/states';
import { api } from '@/lib/api';
import { useI18n } from '@/lib/i18n';
import { useApiHealth, useBrandsQuery } from '@/lib/queries';
import type { Brand } from '@/lib/types';
import { cn } from '@/lib/utils';

const STATUSES = ['all', 'verified', 'review', 'unresolved'] as const;
type StatusFilter = (typeof STATUSES)[number];

export default function BrandsPage() {
  const { t } = useI18n();
  const health = useApiHealth();
  const client = useQueryClient();
  const query = useBrandsQuery();
  const [search, setSearch] = useState('');
  const [status, setStatus] = useState<StatusFilter>('all');
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [deleting, setDeleting] = useState<Brand | null>(null);
  const [notice, setNotice] = useState('');
  const autoMap = useMutation({
    mutationFn: () => api('/brands/auto-map', 'POST', {}),
    onSuccess: () => {
      setNotice(t('success'));
      client.invalidateQueries({ queryKey: ['brands'] });
    },
  });
  const removeBrand = useMutation({
    mutationFn: (id: number) => api<void>(`/brands/${id}`, 'DELETE'),
    onSuccess: () => {
      setDeleting(null);
      setSelectedId(null);
      setNotice(t('brandDeleted'));
      client.invalidateQueries();
    },
  });
  const brands = useMemo(
    () =>
      (query.data?.data ?? []).filter(
        (brand) =>
          (status === 'all' || brand.status === status) &&
          `${brand.name} ${brand.aliases.join(' ')}`.toLowerCase().includes(search.toLowerCase()),
      ),
    [query.data, search, status],
  );
  const selected = brands.find((brand) => brand.id === selectedId) ?? brands[0];
  const error = query.error ?? autoMap.error ?? removeBrand.error;
  if (query.isLoading) return <LoadingState />;
  return (
    <section aria-labelledby="brands-heading">
      <PageHeader
        title={t('brands')}
        headingId="brands-heading"
        description={t('brandsIntro')}
        actions={
          <>
            <Button
              size="sm"
              variant="secondary"
              icon={<Wand2 size={13} />}
              onClick={() => autoMap.mutate()}
              disabled={!health.writable || autoMap.isPending}
            >
              {autoMap.isPending ? t('mapping') : t('autoMap')}
            </Button>
            <AddBrand
              writable={health.writable}
              onAdded={(brand) => {
                setNotice(`${t('brandAdded')}: ${brand.name}`);
                setSelectedId(brand.id);
                client.invalidateQueries({ queryKey: ['brands'] });
              }}
            />
          </>
        }
      />
      <Toolbar>
        <SearchInput label={t('search')} value={search} onChange={setSearch} />
        <Segmented
          label={t('status')}
          options={STATUSES.map((value) => ({ value, label: t(value === 'all' ? 'allStatuses' : value) }))}
          value={status}
          onChange={setStatus}
        />
      </Toolbar>
      {(notice || error) && (
        <div className="space-y-2 py-3">
          <Notice>{notice}</Notice>
          {error && <ErrorState error={error} retry={() => query.refetch()} />}
        </div>
      )}

      {!brands.length ? (
        <EmptyState message={t('noBrands')} />
      ) : (
        <div className="-mx-4 -mb-10 flex flex-col md:-mx-6 lg:flex-row">
          <BrandList brands={brands} selectedId={selected?.id} onSelect={setSelectedId} />
          {selected && (
            <div className="min-w-0 flex-1 px-4 py-5 md:px-6">
              <BrandDetail
                key={selected.id}
                brand={selected}
                writable={health.writable}
                onNotice={setNotice}
                onDelete={() => setDeleting(selected)}
              />
            </div>
          )}
        </div>
      )}
      <Modal
        open={deleting !== null}
        onClose={() => setDeleting(null)}
        title={`${t('deleteBrand')}: ${deleting?.name ?? ''}`}
        maxWidth="max-w-md"
      >
        <p className="mb-4 text-[13px] text-[var(--text-secondary)]">{t('deleteBrandHelp')}</p>
        <div className="flex justify-end gap-2">
          <Button variant="secondary" onClick={() => setDeleting(null)}>
            {t('cancel')}
          </Button>
          <Button
            variant="danger"
            disabled={removeBrand.isPending}
            onClick={() => deleting && removeBrand.mutate(deleting.id)}
          >
            {t('delete')}
          </Button>
        </div>
      </Modal>
    </section>
  );
}

/** The left column: one 40px row per brand with its status and listing count. */
function BrandList({
  brands,
  selectedId,
  onSelect,
}: {
  brands: Brand[];
  selectedId?: number;
  onSelect: (id: number) => void;
}) {
  const { t } = useI18n();
  return (
    <ul
      aria-label={t('brands')}
      className="border-b border-[var(--border-subtle)] lg:min-h-[calc(100dvh-var(--topbar-height)*2)] lg:w-[340px] lg:shrink-0 lg:border-b-0 lg:border-r"
    >
      {brands.map((brand) => {
        const pending = brand.mappings.filter((mapping) => mapping.state === 'review').length;
        const active = brand.id === selectedId;
        return (
          <li key={brand.id}>
            <button
              type="button"
              aria-current={active ? 'true' : undefined}
              onClick={() => onSelect(brand.id)}
              className={cn(
                'flex h-10 w-full items-center gap-2 border-b border-[var(--border-subtle)] px-4 text-left transition-colors duration-75 md:px-6',
                active ? 'bg-[var(--bg-selected)]' : 'hover:bg-[var(--bg-surface)]',
              )}
            >
              <StatusDot variant={statusVariant(brand.status)} />
              <span className="min-w-0 flex-1 truncate text-[13px] text-[var(--text-primary)]">{brand.name}</span>
              {pending > 0 && <Badge variant="warning">{pending}</Badge>}
              <span className="text-xs tabular-nums text-[var(--text-muted)]">{brand.listings_count}</span>
            </button>
          </li>
        );
      })}
    </ul>
  );
}
