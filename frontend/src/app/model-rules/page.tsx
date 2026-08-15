'use client';

import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
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
    <section className="space-y-5" aria-labelledby="rules-heading">
      <div>
        <h1 id="rules-heading" className="text-2xl font-semibold">
          {t('modelRules')}
        </h1>
        <p className="text-slate-600">{t('modelRulesIntro')}</p>
      </div>
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
      <Card className="p-4">
        <h2 className="font-semibold">{editing ? t('editRule') : t('createRule')}</h2>
        <form
          key={editing?.id ?? 'new'}
          className="mt-3 grid gap-3 md:grid-cols-2 xl:grid-cols-5"
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
          <label className="text-sm">
            {t('brand')}
            <select
              name="brand"
              disabled={!!editing}
              defaultValue={editing?.brand_id}
              className="mt-1 w-full rounded border px-2"
            >
              {brands.data?.data.map((brand) => (
                <option value={brand.id} key={brand.id}>
                  {brand.name}
                </option>
              ))}
            </select>
          </label>
          <label className="text-sm">
            {t('name')}
            <input
              name="name"
              required
              defaultValue={editing?.name}
              className="mt-1 w-full rounded border px-2"
            />
          </label>
          <label className="text-sm">
            {t('includeKeywords')}
            <input
              name="include"
              defaultValue={editing?.include_keywords.join(', ')}
              className="mt-1 w-full rounded border px-2"
            />
          </label>
          <label className="text-sm">
            {t('excludeKeywords')}
            <input
              name="exclude"
              defaultValue={editing?.exclude_keywords.join(', ')}
              className="mt-1 w-full rounded border px-2"
            />
          </label>
          <div className="flex items-end gap-2">
            <label className="flex-1 text-sm">
              {t('category')}
              <input
                name="category"
                defaultValue={editing?.category}
                className="mt-1 w-full rounded border px-2"
              />
            </label>
            <Button disabled={!health.writable || save.isPending}>
              {save.isPending ? t('saving') : t('save')}
            </Button>
            {editing && (
              <button type="button" className="underline" onClick={() => setEditing(null)}>
                {t('cancel')}
              </button>
            )}
          </div>
        </form>
      </Card>
      {!rules.data?.length ? (
        <EmptyState message={t('noRules')} />
      ) : (
        <div className="space-y-3">
          {rules.data.map((rule) => (
            <Card className="p-4" key={rule.id}>
              <div className="flex flex-wrap justify-between gap-3">
                <div>
                  <h2 className="font-semibold">{rule.name}</h2>
                  <p className="text-sm text-slate-600">
                    {t('includeKeywords')}: {rule.include_keywords.join(', ') || '—'} ·{' '}
                    {t('excludeKeywords')}: {rule.exclude_keywords.join(', ') || '—'} ·{' '}
                    {rule.matches_count} {t('matches')}
                  </p>
                </div>
                <div className="flex flex-wrap gap-3 text-sm">
                  <button className="underline" onClick={() => setEditing(rule)}>
                    {t('editRule')}
                  </button>
                  <button className="underline" onClick={() => loadMatches.mutate(rule.id)}>
                    {t('matches')}
                  </button>
                  <button
                    className="underline"
                    disabled={!health.writable}
                    onClick={() =>
                      save.mutate({ id: rule.id, body: { is_active: !rule.is_active } })
                    }
                  >
                    {rule.is_active ? t('disable') : t('enable')}
                  </button>
                  <button
                    className="text-red-700 underline"
                    disabled={!health.writable || remove.isPending}
                    onClick={() => remove.mutate(rule.id)}
                  >
                    {t('delete')}
                  </button>
                </div>
              </div>
              {matches[rule.id] && (
                <ul className="mt-3 border-t pt-3 text-sm">
                  {matches[rule.id].length ? (
                    matches[rule.id].map((item) => (
                      <li key={item.id}>
                        {item.title} · {t(item.status)}
                      </li>
                    ))
                  ) : (
                    <li className="text-slate-500">{t('noMatches')}</li>
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
