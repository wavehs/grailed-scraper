'use client';

import { useEffect, useRef, useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { Plus } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Modal } from '@/components/ui/modal';
import { api, errorMessage, getApi } from '@/lib/api';
import { useI18n } from '@/lib/i18n';
import type { Brand, DesignerSuggestion } from '@/lib/types';

const DESIGNER_DEBOUNCE_MS = 300;
const MIN_QUERY_LENGTH = 2;

/** The header's "Add brand" button; its dialog searches Grailed designers and adds one. */
export function AddBrand({
  writable,
  onAdded,
}: {
  writable: boolean;
  onAdded: (brand: Brand) => void;
}) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  return (
    <>
      <Button
        size="sm"
        icon={<Plus size={13} />}
        onClick={() => setOpen(true)}
        disabled={!writable}
      >
        {t('addBrand')}
      </Button>
      <Modal open={open} onClose={() => setOpen(false)} title={t('addBrand')} maxWidth="max-w-lg">
        <DesignerSearch
          onAdded={(brand) => {
            setOpen(false);
            onAdded(brand);
          }}
        />
      </Modal>
    </>
  );
}

/** A designer search whose Grailed suggestions each add a brand. */
function DesignerSearch({ onAdded }: { onAdded: (brand: Brand) => void }) {
  const { t } = useI18n();
  const inputRef = useRef<HTMLInputElement>(null);
  const [query, setQuery] = useState('');
  const [debounced, setDebounced] = useState('');
  // The dialog focuses its close button after its children mount; the search is what the
  // user came for, so take focus on the next tick.
  useEffect(() => {
    const timer = window.setTimeout(() => inputRef.current?.focus(), 0);
    return () => window.clearTimeout(timer);
  }, []);
  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(query.trim()), DESIGNER_DEBOUNCE_MS);
    return () => window.clearTimeout(timer);
  }, [query]);
  const designers = useQuery({
    queryKey: ['designers', debounced],
    queryFn: ({ signal }) =>
      getApi<{ data: DesignerSuggestion[] }>(
        `/brands/designers?${new URLSearchParams({ q: debounced })}`,
        signal,
      ),
    enabled: debounced.length >= MIN_QUERY_LENGTH,
  });
  const add = useMutation({
    mutationFn: (designer: DesignerSuggestion) =>
      api<Brand>('/brands', 'POST', { name: designer.name, designer: designer.name }),
    onSuccess: onAdded,
  });
  const showResults = debounced.length >= MIN_QUERY_LENGTH && query.trim().length > 0;
  return (
    <div className="space-y-3">
      <label className="block">
        <span className="sr-only">{t('addBrand')}</span>
        <input
          ref={inputRef}
          className="control-sm w-full"
          placeholder={t('addBrandPlaceholder')}
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
      </label>
      {showResults && designers.isFetching && !designers.data && (
        <p className="text-xs text-[var(--text-muted)]">{t('loading')}</p>
      )}
      {showResults && designers.isError && (
        <p role="alert" className="text-xs text-[var(--danger)]">
          {errorMessage(designers.error)}
        </p>
      )}
      {showResults && designers.data && (
        <ul className="max-h-80 overflow-y-auto">
          {designers.data.data.map((designer) => (
            <li
              key={designer.name}
              className="flex min-h-9 items-center justify-between gap-3 rounded-[5px] px-2 hover:bg-[var(--bg-surface-hover)]"
            >
              <span className="min-w-0 truncate text-[13px] text-[var(--text-primary)]">
                {designer.name}{' '}
                <span className="text-xs text-[var(--text-muted)]">
                  · {designer.listings_count} {t('activeListings')}
                </span>
              </span>
              <Button
                size="sm"
                variant="secondary"
                icon={<Plus size={13} />}
                disabled={add.isPending}
                onClick={() => add.mutate(designer)}
              >
                {t('add')}
              </Button>
            </li>
          ))}
          {!designers.data.data.length && (
            <li className="px-2 py-1.5 text-xs text-[var(--text-muted)]">{t('noDesigners')}</li>
          )}
        </ul>
      )}
      {add.isError && (
        <p role="alert" className="text-xs text-[var(--danger)]">
          {errorMessage(add.error)}
        </p>
      )}
    </div>
  );
}
