'use client';

import { Suspense } from 'react';
import ModelDetailClient from './[id]/model-detail-client';
import { LoadingState } from '@/components/states';

export default function ModelGroupsPage() {
  return (
    <Suspense fallback={<LoadingState />}>
      <ModelDetailClient />
    </Suspense>
  );
}

