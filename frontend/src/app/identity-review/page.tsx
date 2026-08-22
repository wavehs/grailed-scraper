'use client';

import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Check, History, Search, X } from 'lucide-react';
import { Badge, statusVariant } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { PageHeader } from '@/components/ui/page-header';
import { EmptyState, ErrorState, LoadingState, Notice } from '@/components/states';
import { api, getApi } from '@/lib/api';
import { useI18n } from '@/lib/i18n';
import type {
  IdentityCandidate,
  IdentityCandidateList,
  IdentityHistory,
  IdentityListing,
} from '@/lib/types';

export default function IdentityReviewPage() {
  const { t } = useI18n();
  const client = useQueryClient();
  const [level, setLevel] = useState<'model' | 'physical'>('physical');
  const [listingId, setListingId] = useState('');
  const [historyId, setHistoryId] = useState<number>();
  const [notice, setNotice] = useState('');
  const candidates = useQuery({
    queryKey: ['identity-candidates', level],
    queryFn: ({ signal }) =>
      getApi<IdentityCandidateList>(`/identity/candidates?status=pending&level=${level}`, signal),
  });
  const history = useQuery({
    queryKey: ['identity-history', historyId],
    queryFn: ({ signal }) => getApi<IdentityHistory>(`/identity/listings/${historyId}`, signal),
    enabled: historyId !== undefined,
  });
  const decide = useMutation({
    mutationFn: ({ id, decision }: { id: number; decision: 'confirmed' | 'rejected' }) =>
      api<IdentityCandidate>(`/identity/candidates/${id}`, 'PATCH', { decision }),
    onSuccess: () => {
      setNotice(t('success'));
      client.invalidateQueries({ queryKey: ['identity-candidates'] });
    },
  });
  const error = candidates.error ?? history.error ?? decide.error;
  return (
    <section className="space-y-6" aria-labelledby="identity-heading">
      <PageHeader title={t('identityReview')} description={t('identityReviewIntro')} />
      <Notice>{notice}</Notice>
      {error && <ErrorState error={error} retry={() => candidates.refetch()} />}

      {/* Controls */}
      <Card className="p-4">
        <div className="flex flex-wrap items-center gap-4">
          <label className="flex items-center gap-2 text-sm text-[var(--text-secondary)]">
            {t('identityType')}
            <select
              className="rounded-lg"
              value={level}
              onChange={(event) => setLevel(event.target.value as 'model' | 'physical')}
            >
              <option value="physical">{t('relist')}</option>
              <option value="model">{t('modelRules')}</option>
            </select>
          </label>
          <form
            className="flex items-center gap-2"
            onSubmit={(event) => {
              event.preventDefault();
              const parsed = Number(listingId);
              if (Number.isInteger(parsed) && parsed > 0) setHistoryId(parsed);
            }}
          >
            <label className="relative">
              <Search
                size={14}
                className="absolute left-3 top-1/2 -translate-y-1/2 text-[var(--text-muted)]"
              />
              <input
                className="w-32 rounded-lg pl-8"
                inputMode="numeric"
                placeholder={t('listingId')}
                value={listingId}
                onChange={(event) => setListingId(event.target.value)}
              />
            </label>
            <Button variant="secondary" size="sm" icon={<History size={14} />}>
              {t('identityHistory')}
            </Button>
          </form>
        </div>
      </Card>

      {/* History result */}
      {history.isFetching && <LoadingState />}
      {history.data && (
        <Card className="p-5 animate-slide-up">
          <h2 className="flex items-center gap-2 text-sm font-semibold uppercase tracking-wider text-[var(--text-muted)]">
            <History size={16} /> {t('identityHistory')}
          </h2>
          <p className="mt-2 text-[var(--text-primary)]">{history.data.listing.title}</p>
          <p className="text-xs text-[var(--text-muted)]">
            {history.data.model_group?.name ?? '—'} · #{history.data.physical_item_id ?? '—'}
          </p>
          <ul className="mt-3 space-y-1 text-sm">
            {history.data.members.map((item) => (
              <li key={item.id} className="flex items-center gap-2 text-[var(--text-secondary)]">
                <span className="h-1 w-1 rounded-full bg-[#818cf8]" />
                #{item.grailed_id} · {item.title} ·{' '}
                <Badge variant={statusVariant(item.status)}>{item.status}</Badge>
              </li>
            ))}
          </ul>
        </Card>
      )}

      {/* Candidates */}
      {candidates.isLoading ? (
        <LoadingState />
      ) : !candidates.data?.data.length ? (
        <EmptyState message={t('noCandidates')} />
      ) : (
        <div className="space-y-5">
          {candidates.data.data.map((candidate) => (
            <Card className="p-5 animate-slide-up" key={candidate.id}>
              <div className="grid gap-4 md:grid-cols-2">
                <ListingCard listing={candidate.left} />
                <ListingCard listing={candidate.right} />
              </div>
              <div className="mt-4 border-t border-[var(--border-subtle)] pt-4">
                <div className="flex flex-wrap items-center gap-3 text-sm">
                  <span className="text-[var(--text-muted)]">{t('confidence')}:</span>
                  <Badge variant="default">{candidate.confidence}</Badge>
                  <span className="text-[var(--text-muted)]">{t('evidence')}:</span>
                  <span className="text-xs text-[var(--text-secondary)]">
                    {Object.entries(candidate.evidence)
                      .map(([key, value]) => `${key}=${String(value)}`)
                      .join(' · ')}
                  </span>
                </div>
                <div className="mt-3 flex gap-2">
                  <Button
                    variant="success"
                    icon={<Check size={16} />}
                    disabled={decide.isPending}
                    onClick={() => decide.mutate({ id: candidate.id, decision: 'confirmed' })}
                  >
                    {t('sameItem')}
                  </Button>
                  <Button
                    variant="secondary"
                    icon={<X size={16} />}
                    disabled={decide.isPending}
                    onClick={() => decide.mutate({ id: candidate.id, decision: 'rejected' })}
                  >
                    {t('differentItems')}
                  </Button>
                </div>
              </div>
            </Card>
          ))}
        </div>
      )}
    </section>
  );
}

function ListingCard({ listing }: { listing: IdentityListing }) {
  return (
    <article className="rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-surface)] p-4 transition-all hover:border-[var(--border-default)]">
      {listing.cover_photo_url && (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          alt=""
          className="mb-3 aspect-square w-full rounded-lg object-cover"
          loading="lazy"
          referrerPolicy="no-referrer"
          src={listing.cover_photo_url}
        />
      )}
      <h3 className="font-medium text-[var(--text-primary)]">{listing.title}</h3>
      <p className="mt-1 text-xs text-[var(--text-muted)]">
        {listing.brand} · {listing.category ?? '—'} · {listing.size ?? '—'} · {listing.color ?? '—'}
      </p>
      <p className="mt-1 text-xs text-[var(--text-secondary)]">
        #{listing.grailed_id} · ${(listing.price / 100).toFixed(2)} ·{' '}
        <Badge variant={statusVariant(listing.status)}>
          {listing.status}
        </Badge>
      </p>
    </article>
  );
}
