import Link from 'next/link';
import { Home, Search } from 'lucide-react';

export default function NotFound() {
  return (
    <div className="flex min-h-[60vh] flex-col items-center justify-center gap-4 animate-fade-in">
      <div className="flex h-16 w-16 items-center justify-center rounded-2xl bg-[rgba(99,102,241,0.1)] border border-[rgba(99,102,241,0.2)]">
        <Search size={28} className="text-[#818cf8]" />
      </div>
      <h1 className="text-4xl font-bold gradient-text">404</h1>
      <p className="text-sm text-[var(--text-secondary)]">Page not found</p>
      <Link
        href="/dashboard"
        className="inline-flex items-center gap-2 rounded-lg bg-gradient-to-r from-[#6366f1] to-[#8b5cf6] px-4 py-2 text-sm font-medium text-white shadow-lg shadow-[rgba(99,102,241,0.2)] hover:brightness-110 transition-all"
      >
        <Home size={16} />
        Go to Dashboard
      </Link>
    </div>
  );
}
