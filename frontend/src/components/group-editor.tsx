'use client';

import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Ban, Check, GitMerge, Pencil, RefreshCcw, Scissors, Split } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { ErrorState, LoadingState, Notice } from '@/components/states';
import { api } from '@/lib/api';
import { useI18n } from '@/lib/i18n';
import { useGroupQuery, useGroupsQuery, useTypeNames } from '@/lib/queries';
import type { GroupDetail, GroupKind, GroupSummary } from '@/lib/types';

type Mode = 'rename' | 'merge' | 'split' | 'line' | 'not-model' | null;

type LabelledGroup = Pick<GroupSummary, 'name' | 'is_fallback'> & { kind?: GroupKind | null };

/** A collaboration line stores the partner name; it reads "Yeezy Gap · no model". */
export function groupLabel(group: LabelledGroup, t: (key: string) => string) {
  if (group.is_fallback) return t('noModel');
  if (group.kind === 'collab') return `${group.name} · ${t('collabNoModel')}`;
  return group.name;
}

/**
 * "No model", a description and a collaboration line are service groups: the API refuses to
 * rename, confirm, merge, re-parent or reject them, or to use them as a merge target or parent.
 * Splitting a model out of them and moving listings into them stay allowed.
 */
export function isServiceGroup(group: Pick<GroupSummary, 'is_fallback'> & { kind?: GroupKind | null }) {
  return group.is_fallback || group.kind === 'descriptor' || group.kind === 'collab';
}

const KIND_BADGES: Partial<Record<GroupKind, { label: string; variant: 'muted' | 'info' | 'warning' }>> = {
  descriptor: { label: 'kindDescriptor', variant: 'muted' },
  collab: { label: 'kindCollab', variant: 'info' },
  review: { label: 'kindReview', variant: 'warning' },
};

/** A badge for groups that are not model lines; a model and "No model" need none. */
export function GroupKindBadge({ kind }: { kind?: GroupKind | null }) {
  const { t } = useI18n();
  const badge = kind ? KIND_BADGES[kind] : undefined;
  if (!badge) return null;
  return (
    <Badge variant={badge.variant} className="whitespace-nowrap">
      <span title={t(`${badge.label}Help`)}>{t(badge.label)}</span>
    </Badge>
  );
}

/** Manual group edits; every edit is stored as a rule and the brand is regrouped. */
export function GroupEditor({
  groupId,
  onChanged,
}: {
  groupId: number;
  onChanged?: (group: GroupDetail | GroupSummary) => void;
}) {
  const { t } = useI18n();
  const client = useQueryClient();
  const { typeName } = useTypeNames();
  const group = useGroupQuery(groupId);
  const siblings = useGroupsQuery(group.data?.brand_id, group.data?.product_type);
  const [mode, setMode] = useState<Mode>(null);
  const [name, setName] = useState('');
  const [phrase, setPhrase] = useState('');
  const [splitName, setSplitName] = useState('');
  const [asVersion, setAsVersion] = useState(true);
  const [target, setTarget] = useState('');
  const [notice, setNotice] = useState('');

  const mutation = useMutation({
    mutationFn: async (): Promise<GroupDetail | GroupSummary> => {
      if (mode === 'rename') return api<GroupDetail>(`/groups/${groupId}`, 'PATCH', { name });
      if (mode === 'merge')
        return api<GroupDetail>(`/groups/${groupId}/merge`, 'POST', { target_id: Number(target) });
      if (mode === 'split')
        return api<GroupDetail>(`/groups/${groupId}/split`, 'POST', {
          phrase,
          name: splitName || null,
          as_version: asVersion,
        });
      if (mode === 'line')
        return api<GroupDetail>(`/groups/${groupId}`, 'PATCH', {
          parent_id: target ? Number(target) : null,
        });
      if (mode === 'not-model') return api<GroupSummary>(`/groups/${groupId}/not-model`, 'POST');
      return api<GroupDetail>(`/groups/${groupId}`, 'PATCH', { status: 'confirmed' });
    },
    onSuccess: (result) => {
      setMode(null);
      setNotice(t('groupUpdated'));
      client.invalidateQueries();
      onChanged?.(result);
    },
  });

  if (group.isLoading) return <LoadingState />;
  if (!group.data) return <ErrorState error={group.error} retry={() => group.refetch()} />;
  const data = group.data;
  const service = isServiceGroup(data);
  const others = (siblings.data?.data ?? []).filter(
    (item) => item.id !== data.id && !isServiceGroup(item),
  );
  const lines = others.filter((item) => item.parent_id === null);
  const open = (next: Mode) => {
    setMode(mode === next ? null : next);
    setName(data.name);
    setTarget(next === 'line' ? String(data.parent_id ?? '') : '');
    setPhrase('');
    setSplitName('');
    setAsVersion(!service);
  };
  const ready =
    (mode === 'rename' && name.trim() && name.trim() !== data.name) ||
    (mode === 'merge' && target) ||
    (mode === 'split' && phrase.trim()) ||
    mode === 'line' ||
    mode === 'not-model';

  return (
    <Card className="space-y-4 p-5">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="text-sm font-semibold uppercase tracking-wider text-[var(--text-muted)]">
          {t('editGroup')}
        </h2>
        <span className="text-sm text-[var(--text-primary)]">
          {data.brand} · {groupLabel(data, t)} · {typeName(data.product_type)}
        </span>
        {data.status === 'auto' && !service && <Badge variant="warning">{t('autoGroup')}</Badge>}
        <GroupKindBadge kind={data.kind} />
      </div>
      {data.status === 'auto' && !service && (
        <p className="text-xs text-[var(--text-muted)]">{t('autoGroupHelp')}</p>
      )}
      {Boolean(data.aliases.length) && (
        <p className="text-xs text-[var(--text-secondary)]">
          {t('otherSpellings')}: {data.aliases.join(', ')}
        </p>
      )}
      {data.parent && (
        <p className="text-xs text-[var(--text-secondary)]">
          {t('modelLine')}: {data.parent.name}
        </p>
      )}
      <Notice>{notice}</Notice>
      {mutation.error && <ErrorState error={mutation.error} />}

      <div className="flex flex-wrap gap-2">
        {!service && (
          <Button size="sm" variant="secondary" icon={<Pencil size={14} />} onClick={() => open('rename')}>
            {t('groupRename')}
          </Button>
        )}
        {data.status === 'auto' && !service && (
          <Button
            size="sm"
            variant="success"
            icon={<Check size={14} />}
            disabled={mutation.isPending}
            onClick={() => {
              setMode(null);
              mutation.mutate();
            }}
          >
            {t('groupConfirm')}
          </Button>
        )}
        {!service && (
          <Button size="sm" variant="secondary" icon={<GitMerge size={14} />} onClick={() => open('merge')}>
            {t('groupMerge')}
          </Button>
        )}
        <Button size="sm" variant="secondary" icon={<Scissors size={14} />} onClick={() => open('split')}>
          {t('groupSplit')}
        </Button>
        {!service && !data.versions.length && (
          <Button size="sm" variant="secondary" icon={<Split size={14} />} onClick={() => open('line')}>
            {t('groupLine')}
          </Button>
        )}
        {!service && (
          <Button size="sm" variant="danger" icon={<Ban size={14} />} onClick={() => open('not-model')}>
            {t('groupNotModel')}
          </Button>
        )}
      </div>

      {mode && (
        <form
          className="space-y-3 rounded-lg border border-[var(--border-subtle)] p-3"
          onSubmit={(event) => {
            event.preventDefault();
            mutation.mutate();
          }}
        >
          {mode === 'rename' && (
            <label className="block text-sm">
              {t('name')}
              <input className="mt-1 w-full rounded-lg" value={name} onChange={(e) => setName(e.target.value)} />
            </label>
          )}
          {mode === 'merge' && (
            <label className="block text-sm">
              <span className="text-xs text-[var(--text-muted)]">{t('groupMergeHelp')}</span>
              <select
                aria-label={t('groupMerge')}
                className="mt-1 w-full rounded-lg"
                value={target}
                onChange={(e) => setTarget(e.target.value)}
              >
                <option value="">—</option>
                {others.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.name} · {item.listings}
                  </option>
                ))}
              </select>
            </label>
          )}
          {mode === 'split' && (
            <div className="space-y-2">
              <p className="text-xs text-[var(--text-muted)]">{t('groupSplitHelp')}</p>
              <label className="block text-sm">
                {t('groupSplitPhrase')}
                <input className="mt-1 w-full rounded-lg" value={phrase} onChange={(e) => setPhrase(e.target.value)} />
              </label>
              <label className="block text-sm">
                {t('groupSplitName')}
                <input
                  className="mt-1 w-full rounded-lg"
                  value={splitName}
                  onChange={(e) => setSplitName(e.target.value)}
                />
              </label>
              {!service && (
                <label className="flex items-center gap-2 text-sm">
                  <input type="checkbox" checked={asVersion} onChange={(e) => setAsVersion(e.target.checked)} />
                  {t('groupSplitAsVersion')}
                </label>
              )}
            </div>
          )}
          {mode === 'line' && (
            <select
              aria-label={t('modelLine')}
              className="w-full rounded-lg"
              value={target}
              onChange={(e) => setTarget(e.target.value)}
            >
              <option value="">{t('standaloneModel')}</option>
              {lines.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name}
                </option>
              ))}
            </select>
          )}
          {mode === 'not-model' && (
            <p className="text-sm text-[var(--text-secondary)]">{t('groupNotModelHelp')}</p>
          )}
          <div className="flex gap-2">
            <Button size="sm" disabled={!ready || mutation.isPending}>
              {t('groupApply')}
            </Button>
            <Button size="sm" variant="ghost" type="button" onClick={() => setMode(null)}>
              {t('cancel')}
            </Button>
          </div>
        </form>
      )}
    </Card>
  );
}

/** Move one listing into another group of its brand, or back to automatic grouping. */
export function MoveListing({ listingId, brandId }: { listingId: number; brandId: number }) {
  const { t } = useI18n();
  const client = useQueryClient();
  const { typeName } = useTypeNames();
  const [open, setOpen] = useState(false);
  const groups = useGroupsQuery(open ? brandId : undefined);
  const move = useMutation({
    mutationFn: (groupId: number | null) =>
      api(`/listings/${listingId}/group`, 'PUT', { group_id: groupId }),
    onSuccess: () => {
      setOpen(false);
      client.invalidateQueries();
    },
  });
  if (!open)
    return (
      <Button size="sm" variant="ghost" onClick={() => setOpen(true)}>
        {t('moveListing')}
      </Button>
    );
  return (
    <select
      aria-label={t('moveListingTo')}
      className="max-w-56 rounded-lg text-xs"
      defaultValue=""
      disabled={move.isPending || groups.isLoading}
      onChange={(event) => {
        const value = event.target.value;
        if (value) move.mutate(value === 'auto' ? null : Number(value));
      }}
    >
      <option value="">{t('moveListingTo')}</option>
      <option value="auto">{t('moveListingAuto')}</option>
      {(groups.data?.data ?? []).map((item) => (
        <option key={item.id} value={item.id}>
          {typeName(item.product_type)} · {groupLabel(item, t)}
        </option>
      ))}
    </select>
  );
}

/** Regroup all brands with the current dictionaries and manual rules. */
export function RegroupButton({ brandIds }: { brandIds?: number[] }) {
  const { t } = useI18n();
  const client = useQueryClient();
  const [notice, setNotice] = useState('');
  const regroup = useMutation({
    mutationFn: () => api('/grouping/regroup', 'POST', { brand_ids: brandIds ?? null, full: true }),
    onSuccess: () => {
      setNotice(t('regrouped'));
      client.invalidateQueries();
    },
  });
  return (
    <span className="inline-flex items-center gap-2">
      {notice && <span className="text-xs text-[var(--success)]">{notice}</span>}
      <Button
        size="sm"
        variant="secondary"
        icon={<RefreshCcw size={14} />}
        disabled={regroup.isPending}
        onClick={() => regroup.mutate()}
      >
        {regroup.isPending ? t('regrouping') : t('regroup')}
      </Button>
    </span>
  );
}
