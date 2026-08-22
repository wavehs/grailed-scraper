import { Loader2 } from 'lucide-react';

export default function Loading() {
  return (
    <div className="flex min-h-[60vh] flex-col items-center justify-center gap-3 animate-fade-in">
      <Loader2 size={28} className="animate-spin text-[var(--accent)]" />
      <p className="text-sm text-[var(--text-secondary)]">Loading…</p>
    </div>
  );
}
