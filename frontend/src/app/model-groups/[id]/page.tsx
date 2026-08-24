import { Suspense } from 'react';
import ModelDetailClient from './model-detail-client';
import { LoadingState } from '@/components/states';

export function generateStaticParams() {
  return [{ id: '1' }];
}

export default function ModelDetailPage() {
  return (
    <Suspense fallback={<LoadingState />}>
      <ModelDetailClient />
    </Suspense>
  );
}
