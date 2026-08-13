'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { Menu } from 'lucide-react';
import { useState } from 'react';
import { useI18n } from '@/lib/i18n';

const links = [
  ['dashboard', '/dashboard'],
  ['brands', '/brands'],
  ['parserRuns', '/parser-runs'],
  ['modelRules', '/model-rules'],
  ['settings', '/settings'],
] as const;

export function AppSidebar() {
  const pathname = usePathname();
  const { locale, setLocale, t } = useI18n();
  const [open, setOpen] = useState(false);
  return (
    <>
      <button
        className="fixed left-3 top-3 z-30 rounded bg-slate-900 p-2 text-white md:hidden"
        aria-label={t('menu')}
        aria-expanded={open}
        onClick={() => setOpen(!open)}
      >
        <Menu size={20} />
      </button>
      <aside
        className={`${open ? 'translate-x-0' : '-translate-x-full'} fixed inset-y-0 z-20 w-64 border-r bg-white p-5 transition-transform md:sticky md:top-0 md:h-screen md:translate-x-0`}
      >
        <Link
          className="mb-8 block text-lg font-semibold"
          href="/dashboard"
          onClick={() => setOpen(false)}
        >
          Grailed Liquidity
        </Link>
        <nav aria-label={t('mainNavigation')} className="space-y-1">
          {links.map(([label, href]) => {
            const active = pathname === href || pathname.startsWith(`${href}/`);
            return (
              <Link
                aria-current={active ? 'page' : undefined}
                className={`block rounded px-3 py-2 text-sm focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 ${active ? 'bg-slate-900 text-white' : 'hover:bg-slate-100'}`}
                href={href}
                key={href}
                onClick={() => setOpen(false)}
              >
                {t(label)}
              </Link>
            );
          })}
        </nav>
        <label className="mt-8 block text-sm font-medium" htmlFor="locale">
          {t('language')}
        </label>
        <select
          id="locale"
          className="mt-1 w-full rounded border px-2 py-2 text-sm"
          value={locale}
          onChange={(event) => setLocale(event.target.value as 'en' | 'ru')}
        >
          <option value="en">{t('english')}</option>
          <option value="ru">{t('russian')}</option>
        </select>
      </aside>
      {open && (
        <button
          aria-label={t('closeMenu')}
          className="fixed inset-0 z-10 bg-black/30 md:hidden"
          onClick={() => setOpen(false)}
        />
      )}
    </>
  );
}
