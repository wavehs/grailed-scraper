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
      <div className="flex h-16 w-16 items-center justify-center rounded-2xl bg-[rgba(244,63,94,0.1)] border border-[rgba(244,63,94,0.2)]">
        <AlertCircle size={28} className="text-[#fb7185]" />
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
