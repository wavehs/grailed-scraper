'use client';

import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Check, Trash2, X } from 'lucide-react';
import { ErrorState } from '@/components/states';
import { Badge, statusVariant } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { DataTable, TableCell, TableHead, TableHeaderCell, TableRow } from '@/components/ui/data-table';
import { ContentSection, Property, PropertyList } from '@/components/ui/section';
import { Switch } from '@/components/ui/switch';
import { api } from '@/lib/api';
import { useI18n } from '@/lib/i18n';
import type { Brand, Mapping } from '@/lib/types';

type Decision = { mappingId: number; action: 'confirm' | 'reject' };

/** Brand edits; each success refreshes the brand list and reports through `onNotice`. */
function useBrandEdits(brandId: number, onNotice: (text: string) => void) {
  const { t } = useI18n();
  const client = useQueryClient();
  const refresh = () => client.invalidateQueries({ queryKey: ['brands'] });
  const update = useMutation({
    mutationFn: (body: object) => api<Brand>(`/brands/${brandId}`, 'PATCH', body),
    onSuccess: () => {
      onNotice(t('updated'));
      refresh();
    },
  });
  const decide = useMutation({
    mutationFn: ({ mappingId, action }: Decision) =>
      api<Mapping>(`/brands/${brandId}/mappings/${mappingId}`, 'PATCH', { action }),
    onSuccess: () => {
      onNotice(t('success'));
      refresh();
    },
  });
  return { update, decide };
}

/** The selected brand: its properties, aliases and the Grailed designers mapped to it. */
export function BrandDetail({
  brand,
  writable,
  onNotice,
  onDelete,
}: {
  brand: Brand;
  writable: boolean;
  onNotice: (text: string) => void;
  onDelete: () => void;
}) {
  const { t } = useI18n();
  const { update, decide } = useBrandEdits(brand.id, onNotice);
  const [aliases, setAliases] = useState(brand.aliases.join(', '));
  const busy = !writable || update.isPending;
  return (
    <div className="max-w-3xl space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex min-w-0 items-center gap-2">
          <h2 className="truncate text-base font-medium text-[var(--text-primary)]">{brand.name}</h2>
          <Badge variant={statusVariant(brand.status)} dot>
            {t(brand.status)}
          </Badge>
        </div>
        <Button variant="ghost" size="sm" icon={<Trash2 size={13} />} disabled={!writable} onClick={onDelete}>
          {t('delete')}
        </Button>
      </div>

      <PropertyList label={t('properties')}>
        <Property label={t('listings')}>{brand.listings_count}</Property>
        <Property label={t('includeSubbrands')}>
          <Switch
            label={t('includeSubbrands')}
            checked={brand.include_subbrands}
            disabled={busy}
            onChange={(checked) => update.mutate({ include_subbrands: checked })}
          />
        </Property>
      </PropertyList>

      <ContentSection title={t('aliases')}>
        <form
          className="flex flex-wrap gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            update.mutate({
              aliases: aliases
                .split(',')
                .map((item) => item.trim())
                .filter(Boolean),
            });
          }}
        >
          <label className="min-w-56 flex-1">
            <span className="sr-only">
              {t('aliases')} — {brand.name}
            </span>
            <input
              className="w-full"
              value={aliases}
              disabled={!writable}
              onChange={(event) => setAliases(event.target.value)}
              placeholder={t('aliases')}
            />
          </label>
          <Button type="submit" variant="secondary" disabled={busy}>
            {update.isPending ? t('saving') : t('save')}
          </Button>
        </form>
      </ContentSection>

      <ContentSection title={t('grailedFacet')}>
        <MappingTable
          mappings={brand.mappings}
          disabled={!writable || decide.isPending}
          onDecide={(decision) => decide.mutate(decision)}
        />
      </ContentSection>
      {(update.error ?? decide.error) && <ErrorState error={update.error ?? decide.error} />}
    </div>
  );
}

function MappingTable({
  mappings,
  disabled,
  onDecide,
}: {
  mappings: Mapping[];
  disabled: boolean;
  onDecide: (decision: Decision) => void;
}) {
  const { t } = useI18n();
  if (!mappings.length) return <p className="text-xs text-[var(--text-muted)]">{t('noCandidates')}</p>;
  return (
    <DataTable className="[&_td]:whitespace-nowrap">
      <TableHead>
        <tr>
          <TableHeaderCell>{t('grailedFacet')}</TableHeaderCell>
          <TableHeaderCell>{t('score')}</TableHeaderCell>
          <TableHeaderCell>{t('listings')}</TableHeaderCell>
          <TableHeaderCell>{t('status')}</TableHeaderCell>
          <TableHeaderCell>
            <span className="sr-only">{t('actions')}</span>
          </TableHeaderCell>
        </tr>
      </TableHead>
      <tbody>
        {mappings.map((mapping) => (
          <TableRow key={mapping.id}>
            <TableCell>
              <span className="text-[var(--text-primary)]">{mapping.source_designer_name}</span>
              {mapping.is_subbrand && (
                <Badge variant="muted" className="ml-2">
                  {t('subbrand')}
                </Badge>
              )}
            </TableCell>
            <TableCell className="tabular-nums">{(Number(mapping.match_score) * 100).toFixed(1)}%</TableCell>
            <TableCell className="tabular-nums">{mapping.listings_count}</TableCell>
            <TableCell>
              <Badge variant={statusVariant(mapping.state)} dot>
                {t(mapping.state)}
              </Badge>
            </TableCell>
            <TableCell className="text-right">
              {mapping.state === 'review' && (
                <span className="inline-flex gap-1">
                  <Button
                    variant="success"
                    size="sm"
                    icon={<Check size={13} />}
                    disabled={disabled}
                    onClick={() => onDecide({ mappingId: mapping.id, action: 'confirm' })}
                  >
                    {t('confirm')}
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    icon={<X size={13} />}
                    disabled={disabled}
                    onClick={() => onDecide({ mappingId: mapping.id, action: 'reject' })}
                  >
                    {t('reject')}
                  </Button>
                </span>
              )}
            </TableCell>
          </TableRow>
        ))}
      </tbody>
    </DataTable>
  );
}
