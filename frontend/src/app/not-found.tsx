import Link from 'next/link';
import { Home, Search } from 'lucide-react';

export default function NotFound() {
  return (
    <div className="flex min-h-[60vh] flex-col items-center justify-center gap-4 animate-fade-in">
      <div className="flex h-14 w-14 items-center justify-center rounded-lg border border-[var(--border-default)] bg-[var(--bg-surface)]">
        <Search size={26} className="text-[var(--accent)]" />
      </div>
      <h1 className="text-4xl font-semibold text-[var(--text-primary)]">404</h1>
      <p className="text-sm text-[var(--text-secondary)]">Page not found</p>
      <Link
        href="/dashboard"
        className="inline-flex items-center gap-2 rounded-md border border-[var(--accent)] bg-[var(--accent)] px-3 py-2 text-sm font-medium text-[var(--accent-contrast)] transition-colors hover:bg-[var(--accent-hover)]"
      >
        <Home size={16} />
        Go to Dashboard
      </Link>
    </div>
  );
}
