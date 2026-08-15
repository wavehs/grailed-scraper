'use client';

import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
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
    <section className="space-y-5" aria-labelledby="identity-heading">
      <div>
        <h1 id="identity-heading" className="text-2xl font-semibold">
          {t('identityReview')}
        </h1>
        <p className="text-slate-600">{t('identityReviewIntro')}</p>
      </div>
      <Notice>{notice}</Notice>
      {error && <ErrorState error={error} retry={() => candidates.refetch()} />}
      <Card className="flex flex-wrap gap-3 p-4">
        <label className="text-sm">
          {t('identityType')}
          <select
            className="ml-2 rounded border px-2 py-1"
            value={level}
            onChange={(event) => setLevel(event.target.value as 'model' | 'physical')}
          >
            <option value="physical">{t('relist')}</option>
            <option value="model">{t('modelRules')}</option>
          </select>
        </label>
        <form
          className="flex gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            const parsed = Number(listingId);
            if (Number.isInteger(parsed) && parsed > 0) setHistoryId(parsed);
          }}
        >
          <label className="text-sm">
            {t('listingId')}
            <input
              className="ml-2 w-28 rounded border px-2 py-1"
              inputMode="numeric"
              value={listingId}
              onChange={(event) => setListingId(event.target.value)}
            />
          </label>
          <Button>{t('identityHistory')}</Button>
        </form>
      </Card>
      {history.isFetching && <LoadingState />}
      {history.data && (
        <Card className="p-4">
          <h2 className="font-semibold">{t('identityHistory')}</h2>
          <p className="mt-1">{history.data.listing.title}</p>
          <p className="text-sm text-slate-600">
            {history.data.model_group?.name ?? '—'} · #{history.data.physical_item_id ?? '—'}
          </p>
          <ul className="mt-3 space-y-1 text-sm">
            {history.data.members.map((item) => (
              <li key={item.id}>
                #{item.grailed_id} · {item.title} · {item.status}
              </li>
            ))}
          </ul>
        </Card>
      )}
      {candidates.isLoading ? (
        <LoadingState />
      ) : !candidates.data?.data.length ? (
        <EmptyState message={t('noCandidates')} />
      ) : (
        <div className="space-y-4">
          {candidates.data.data.map((candidate) => (
            <Card className="p-4" key={candidate.id}>
              <div className="grid gap-4 md:grid-cols-2">
                <ListingCard listing={candidate.left} />
                <ListingCard listing={candidate.right} />
              </div>
              <div className="mt-4 border-t pt-3 text-sm">
                <p>
                  {t('confidence')}: {candidate.confidence} · {t('evidence')}:{' '}
                  {Object.entries(candidate.evidence)
                    .map(([key, value]) => `${key}=${String(value)}`)
                    .join(' · ')}
                </p>
                <div className="mt-3 flex gap-2">
                  <Button
                    disabled={decide.isPending}
                    onClick={() => decide.mutate({ id: candidate.id, decision: 'confirmed' })}
                  >
                    {t('sameItem')}
                  </Button>
                  <Button
                    disabled={decide.isPending}
                    className="bg-slate-600 hover:bg-slate-500"
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
    <article className="rounded border p-3">
      {listing.cover_photo_url && (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          alt=""
          className="mb-3 aspect-square w-full rounded object-cover"
          loading="lazy"
          referrerPolicy="no-referrer"
          src={listing.cover_photo_url}
        />
      )}
      <h3 className="font-medium">{listing.title}</h3>
      <p className="text-sm text-slate-600">
        {listing.brand} · {listing.category ?? '—'} · {listing.size ?? '—'} · {listing.color ?? '—'}
      </p>
      <p className="text-sm">
        #{listing.grailed_id} · ${(listing.price / 100).toFixed(2)} · {listing.status}
      </p>
    </article>
  );
}
