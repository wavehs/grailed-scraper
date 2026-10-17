'use client';

import { useEffect, useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { Plus } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { api, errorMessage, getApi } from '@/lib/api';
import { useI18n } from '@/lib/i18n';
import type { Brand, DesignerSuggestion } from '@/lib/types';

const DESIGNER_DEBOUNCE_MS = 300;
const MIN_QUERY_LENGTH = 2;

/** A designer search in the toolbar; its Grailed suggestions open below and add a brand. */
export function AddBrand({
  writable,
  onAdded,
}: {
  writable: boolean;
  onAdded: (brand: Brand) => void;
}) {
  const { t } = useI18n();
  const [query, setQuery] = useState('');
  const [debounced, setDebounced] = useState('');
  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(query.trim()), DESIGNER_DEBOUNCE_MS);
    return () => window.clearTimeout(timer);
  }, [query]);
  const designers = useQuery({
    queryKey: ['designers', debounced],
    queryFn: ({ signal }) =>
      getApi<{ data: DesignerSuggestion[] }>(`/brands/designers?${new URLSearchParams({ q: debounced })}`, signal),
    enabled: debounced.length >= MIN_QUERY_LENGTH,
  });
  const add = useMutation({
    mutationFn: (designer: DesignerSuggestion) =>
      api<Brand>('/brands', 'POST', { name: designer.name, designer: designer.name }),
    onSuccess: (brand) => {
      setQuery('');
      onAdded(brand);
    },
  });
  const open = Boolean(designers.data) && debounced.length >= MIN_QUERY_LENGTH && query.trim().length > 0;
  return (
    <div className="relative w-full sm:w-80">
      <label>
        <Plus size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-[var(--text-muted)]" aria-hidden />
        <span className="sr-only">{t('addBrand')}</span>
        <input
          className="control-sm w-full pl-7"
          placeholder={t('addBrandPlaceholder')}
          value={query}
          disabled={!writable}
          onChange={(event) => setQuery(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Escape') setQuery('');
          }}
        />
      </label>
      {open && designers.data && (
        <ul className="absolute inset-x-0 top-full z-30 mt-1.5 max-h-80 overflow-y-auto rounded-lg bg-[var(--bg-surface-raised)] p-1 shadow-[var(--shadow-float)] animate-scale-in">
          {designers.data.data.map((designer) => (
            <li key={designer.name} className="flex min-h-8 items-center justify-between gap-3 rounded-[5px] px-2 hover:bg-[var(--bg-surface-hover)]">
              <span className="min-w-0 truncate text-[13px] text-[var(--text-primary)]">
                {designer.name}{' '}
                <span className="text-xs text-[var(--text-muted)]">
                  · {designer.listings_count} {t('activeListings')}
                </span>
              </span>
              <Button size="sm" variant="secondary" icon={<Plus size={13} />} disabled={add.isPending} onClick={() => add.mutate(designer)}>
                {t('add')}
              </Button>
            </li>
          ))}
          {!designers.data.data.length && <li className="px-2 py-1.5 text-xs text-[var(--text-muted)]">{t('noDesigners')}</li>}
        </ul>
      )}
      {add.isError && (
        <p role="alert" className="mt-1 text-xs text-[var(--danger)]">
          {errorMessage(add.error)}
        </p>
      )}
    </div>
  );
}
