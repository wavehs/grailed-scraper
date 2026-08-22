'use client';

import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import {
  BookOpen,
  Edit3,
  Eye,
  Plus,
  Power,
  PowerOff,
  Save,
  Trash2,
  X,
} from 'lucide-react';
import { Badge, statusVariant } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { PageHeader } from '@/components/ui/page-header';
import { EmptyState, ErrorState, LoadingState, Notice } from '@/components/states';
import { api, getApi } from '@/lib/api';
import { useI18n } from '@/lib/i18n';
import { useApiHealth, useBrandsQuery, useModelRulesQuery } from '@/lib/queries';
import type { ModelRule, RuleMatch } from '@/lib/types';

const keywords = (value: string) =>
  value
    .split(',')
    .map((item) => item.trim())
    .filter(Boolean);

export default function ModelRulesPage() {
  const { t } = useI18n();
  const health = useApiHealth();
  const client = useQueryClient();
  const [editing, setEditing] = useState<ModelRule | null>(null);
  const [matches, setMatches] = useState<Record<number, RuleMatch[]>>({});
  const [notice, setNotice] = useState('');
  const rules = useModelRulesQuery();
  const brands = useBrandsQuery();
  const refresh = () => client.invalidateQueries({ queryKey: ['rules'] });
  const save = useMutation({
    mutationFn: ({ id, body }: { id?: number; body: object }) =>
      id
        ? api<ModelRule>(`/model-rules/${id}`, 'PATCH', body)
        : api<ModelRule>('/model-rules', 'POST', body),
    onSuccess: () => {
      setEditing(null);
      setNotice(t('success'));
      refresh();
    },
  });
  const remove = useMutation({
    mutationFn: (id: number) => api<void>(`/model-rules/${id}`, 'DELETE'),
    onSuccess: () => {
      setNotice(t('success'));
      refresh();
    },
  });
  const loadMatches = useMutation({
    mutationFn: (id: number) => getApi<RuleMatch[]>(`/model-rules/${id}/matches`),
    onSuccess: (data: RuleMatch[], id: number) =>
      setMatches((old) => ({
        ...old,
        [id]: data,
      })),
  });
  const error = rules.error ?? brands.error ?? save.error ?? remove.error ?? loadMatches.error;
  if (rules.isLoading && brands.isLoading) return <LoadingState />;
  return (
    <section className="space-y-6" aria-labelledby="rules-heading">
      <PageHeader title={t('modelRules')} description={t('modelRulesIntro')} />
      <Notice>{notice}</Notice>
      {error && (
        <ErrorState
          error={error}
          retry={() => {
            rules.refetch();
            brands.refetch();
          }}
        />
      )}

      {/* Create/Edit form */}
      <Card className="p-5">
        <h2 className="mb-4 flex items-center gap-2 text-sm font-semibold uppercase tracking-wider text-[var(--text-muted)]">
          {editing ? <Edit3 size={16} /> : <Plus size={16} />}
          {editing ? t('editRule') : t('createRule')}
        </h2>
        <form
          key={editing?.id ?? 'new'}
          className="grid gap-4 md:grid-cols-2 xl:grid-cols-5"
          onSubmit={(event) => {
            event.preventDefault();
            const form = new FormData(event.currentTarget);
            save.mutate({
              id: editing?.id,
              body: {
                ...(!editing ? { brand_id: Number(form.get('brand')) } : {}),
                name: String(form.get('name')),
                include_keywords: keywords(String(form.get('include'))),
                exclude_keywords: keywords(String(form.get('exclude'))),
                category: String(form.get('category') ?? '').trim() || null,
              },
            });
            if (!editing) event.currentTarget.reset();
          }}
        >
          <label className="block text-sm">
            <span className="mb-1.5 block text-xs font-medium uppercase tracking-wider text-[var(--text-muted)]">
              {t('brand')}
            </span>
            <select
              name="brand"
              disabled={!!editing}
              defaultValue={editing?.brand_id}
              className="w-full rounded-lg"
            >
              {brands.data?.data.map((brand) => (
                <option value={brand.id} key={brand.id}>
                  {brand.name}
                </option>
              ))}
            </select>
          </label>
          <label className="block text-sm">
            <span className="mb-1.5 block text-xs font-medium uppercase tracking-wider text-[var(--text-muted)]">
              {t('name')}
            </span>
            <input
              name="name"
              required
              defaultValue={editing?.name}
              className="w-full rounded-lg"
            />
          </label>
          <label className="block text-sm">
            <span className="mb-1.5 block text-xs font-medium uppercase tracking-wider text-[var(--text-muted)]">
              {t('includeKeywords')}
            </span>
            <input
              name="include"
              defaultValue={editing?.include_keywords.join(', ')}
              className="w-full rounded-lg"
            />
          </label>
          <label className="block text-sm">
            <span className="mb-1.5 block text-xs font-medium uppercase tracking-wider text-[var(--text-muted)]">
              {t('excludeKeywords')}
            </span>
            <input
              name="exclude"
              defaultValue={editing?.exclude_keywords.join(', ')}
              className="w-full rounded-lg"
            />
          </label>
          <div className="flex items-end gap-2">
            <label className="flex-1 text-sm">
              <span className="mb-1.5 block text-xs font-medium uppercase tracking-wider text-[var(--text-muted)]">
                {t('category')}
              </span>
              <input
                name="category"
                defaultValue={editing?.category}
                className="w-full rounded-lg"
              />
            </label>
            <Button
              icon={<Save size={14} />}
              disabled={!health.writable || save.isPending}
            >
              {save.isPending ? t('saving') : t('save')}
            </Button>
            {editing && (
              <Button variant="ghost" icon={<X size={14} />} onClick={() => setEditing(null)}>
                {t('cancel')}
              </Button>
            )}
          </div>
        </form>
      </Card>

      {/* Rules list */}
      {!rules.data?.length ? (
        <EmptyState message={t('noRules')} />
      ) : (
        <div className="space-y-3">
          {rules.data.map((rule) => (
            <Card className="p-5 animate-slide-up" key={rule.id}>
              <div className="flex flex-wrap justify-between gap-3">
                <div>
                  <div className="flex items-center gap-2">
                    <BookOpen size={16} className="text-[var(--accent)]" />
                    <h2 className="font-semibold text-[var(--text-primary)]">{rule.name}</h2>
                    <Badge variant={rule.is_active ? 'success' : 'muted'} dot>
                      {rule.is_active ? t('enable') : t('disable')}
                    </Badge>
                  </div>
                  <p className="mt-1 text-xs text-[var(--text-muted)]">
                    {t('includeKeywords')}: {rule.include_keywords.join(', ') || '—'} ·{' '}
                    {t('excludeKeywords')}: {rule.exclude_keywords.join(', ') || '—'} ·{' '}
                    {rule.matches_count} {t('matches')}
                  </p>
                </div>
                <div className="flex flex-wrap gap-2">
                  <Button
                    variant="ghost"
                    size="sm"
                    icon={<Edit3 size={14} />}
                    onClick={() => setEditing(rule)}
                  >
                    {t('editRule')}
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    icon={<Eye size={14} />}
                    onClick={() => loadMatches.mutate(rule.id)}
                  >
                    {t('matches')}
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    icon={rule.is_active ? <PowerOff size={14} /> : <Power size={14} />}
                    disabled={!health.writable}
                    onClick={() =>
                      save.mutate({ id: rule.id, body: { is_active: !rule.is_active } })
                    }
                  >
                    {rule.is_active ? t('disable') : t('enable')}
                  </Button>
                  <Button
                    variant="danger"
                    size="sm"
                    icon={<Trash2 size={14} />}
                    disabled={!health.writable || remove.isPending}
                    onClick={() => remove.mutate(rule.id)}
                  >
                    {t('delete')}
                  </Button>
                </div>
              </div>
              {matches[rule.id] && (
                <ul className="mt-4 border-t border-[var(--border-subtle)] pt-3 space-y-1 text-sm">
                  {matches[rule.id].length ? (
                    matches[rule.id].map((item) => (
                      <li key={item.id} className="flex items-center gap-2 text-[var(--text-secondary)]">
                        <span className="h-1 w-1 rounded-full bg-[var(--accent)]" />
                        {item.title} · <Badge variant={statusVariant(item.status)}>{t(item.status)}</Badge>
                      </li>
                    ))
                  ) : (
                    <li className="text-[var(--text-muted)]">{t('noMatches')}</li>
                  )}
                </ul>
              )}
            </Card>
          ))}
        </div>
      )}
    </section>
  );
}
