'use client';

import { AlertCircle, RefreshCw } from 'lucide-react';
import { Button } from '@/components/ui/button';

export default function ErrorPage({
  error,
  reset,
}: {
  error: Error;
  reset: () => void;
}) {
  return (
    <div className="flex min-h-[60vh] flex-col items-center justify-center gap-4 animate-fade-in">
      <div className="flex h-14 w-14 items-center justify-center rounded-lg border border-[var(--danger-border)] bg-[var(--danger-bg)]">
        <AlertCircle size={26} className="text-[var(--danger)]" />
      </div>
      <h1 className="text-xl font-bold text-[var(--text-primary)]">Something went wrong</h1>
      <p className="max-w-md text-center text-sm text-[var(--text-secondary)]">
        {error.message}
      </p>
      <Button icon={<RefreshCw size={16} />} onClick={reset}>
        Try again
      </Button>
    </div>
  );
}
