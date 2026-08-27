'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import {
  BarChart3,
  Bot,
  Database,
  LayoutDashboard,
  Menu,
  Moon,
  Play,
  Settings,
  Sun,
  Tags,
  X,
} from 'lucide-react';
import { useEffect, useState, type ReactNode } from 'react';
import { useI18n } from '@/lib/i18n';

type NavItem = {
  key: string;
  href: string;
  icon: ReactNode;
  group: 'analytics' | 'management' | 'system';
};

const navItems: NavItem[] = [
  { key: 'dashboard', href: '/dashboard', icon: <LayoutDashboard size={18} />, group: 'analytics' },
  { key: 'catalog', href: '/listings', icon: <Database size={18} />, group: 'analytics' },
  { key: 'brands', href: '/brands', icon: <Tags size={18} />, group: 'management' },
  { key: 'parserRuns', href: '/parser-runs', icon: <Play size={18} />, group: 'management' },
  { key: 'aiGrouping', href: '/ai-grouping', icon: <Bot size={18} />, group: 'management' },
  { key: 'settings', href: '/settings', icon: <Settings size={18} />, group: 'system' },
];

const groupLabels: Record<string, string> = {
  analytics: 'Analytics',
  management: 'Management',
  system: 'System',
};

export function AppSidebar() {
  const pathname = usePathname();
  const { locale, setLocale, t } = useI18n();
  const [open, setOpen] = useState(false);
  const [dark, setDark] = useState(false);

  useEffect(() => {
    const saved = window.localStorage.getItem('gla-theme');
    const next = saved
      ? saved === 'dark'
      : (window.matchMedia?.('(prefers-color-scheme: dark)').matches ?? false);
    setDark(next);
    document.documentElement.dataset.theme = next ? 'dark' : 'light';
  }, []);

  useEffect(() => {
    if (!open) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false);
    };
    window.addEventListener('keydown', closeOnEscape);
    return () => window.removeEventListener('keydown', closeOnEscape);
  }, [open]);

  const toggleTheme = () => {
    const next = !dark;
    setDark(next);
    document.documentElement.dataset.theme = next ? 'dark' : 'light';
    window.localStorage.setItem('gla-theme', next ? 'dark' : 'light');
  };

  const groups = ['analytics', 'management', 'system'] as const;

  return (
    <>
      {/* Mobile toggle */}
      <button
        className={`${open ? 'left-[188px]' : 'left-3'} fixed top-3 z-30 rounded-md border border-[var(--border-default)] bg-[var(--bg-surface-raised)] p-2 text-[var(--text-primary)] shadow-sm transition-[left] md:hidden`}
        aria-label={t(open ? 'closeMenu' : 'menu')}
        aria-expanded={open}
        onClick={() => setOpen(!open)}
      >
        {open ? <X size={20} /> : <Menu size={20} />}
      </button>

      {/* Sidebar */}
      <aside
        className={`${open ? 'translate-x-0' : '-translate-x-full'} fixed inset-y-0 z-20 flex w-[var(--sidebar-width)] flex-col border-r border-[var(--border-subtle)] bg-[var(--bg-sidebar)] p-3 transition-transform duration-200 md:sticky md:top-0 md:h-dvh md:translate-x-0`}
      >
        {/* Logo */}
        <Link
          className="mb-5 flex items-center gap-2.5 rounded-lg border border-[var(--border-default)] bg-[var(--bg-surface)] p-2.5"
          href="/dashboard"
          onClick={() => setOpen(false)}
        >
          <div className="flex h-8 w-8 items-center justify-center rounded-md bg-[var(--accent)]">
            <BarChart3 size={16} className="text-[var(--accent-contrast)]" />
          </div>
          <div>
            <span className="text-[13px] font-semibold tracking-tight text-[var(--text-primary)]">
              Grailed Intelligence
            </span>
            <span className="block text-[10px] text-[var(--text-muted)]">Market analyzer</span>
          </div>
        </Link>

        {/* Navigation */}
        <nav aria-label={t('mainNavigation')} className="flex-1 space-y-5 overflow-y-auto">
          {groups.map((group) => {
            const items = navItems.filter((item) => item.group === group);
            if (!items.length) return null;
            return (
              <div key={group}>
                <p className="mb-1.5 px-2 text-[10px] font-semibold uppercase tracking-[0.08em] text-[var(--text-secondary)]">
                  {t(groupLabels[group].toLowerCase())}
                </p>
                <div className="space-y-0.5">
                  {items.map(({ key, href, icon }) => {
                    const active = pathname === href || pathname.startsWith(`${href}/`);
                    return (
                      <Link
                        aria-current={active ? 'page' : undefined}
                        className={`relative flex items-center gap-2.5 rounded-md px-2.5 py-2 text-[13px] font-medium transition-colors ${
                          active
                            ? 'bg-[var(--bg-surface-raised)] text-[var(--text-primary)] shadow-sm before:absolute before:left-0 before:h-4 before:w-0.5 before:rounded-full before:bg-[var(--accent)]'
                            : 'text-[var(--text-secondary)] hover:bg-[var(--bg-surface-hover)] hover:text-[var(--text-primary)]'
                        }`}
                        href={href}
                        key={href}
                        onClick={() => setOpen(false)}
                      >
                        <span
                          className={active ? 'text-[var(--accent)]' : 'text-[var(--text-muted)]'}
                        >
                          {icon}
                        </span>
                        {t(key)}
                      </Link>
                    );
                  })}
                </div>
              </div>
            );
          })}
        </nav>

        {/* Language switcher */}
        <div className="mt-auto grid grid-cols-2 gap-1 border-t border-[var(--border-subtle)] pt-3">
          <label>
            <span className="sr-only">{t('language')}</span>
            <select
              id="app-language"
              name="language"
              className="h-8 min-h-8 w-full rounded-md border border-transparent bg-transparent px-2 py-1 text-xs text-[var(--text-secondary)] shadow-none hover:border-[var(--border-default)] hover:bg-[var(--bg-surface-hover)]"
              value={locale}
              onChange={(event) => setLocale(event.target.value as 'en' | 'ru')}
            >
              <option value="en">EN</option>
              <option value="ru">RU</option>
            </select>
          </label>
          <button
            className="flex items-center gap-2 rounded-md px-2.5 py-2 text-xs text-[var(--text-secondary)] transition-colors hover:bg-[var(--bg-surface-hover)] hover:text-[var(--text-primary)]"
            onClick={toggleTheme}
            aria-label={dark ? t('lightMode') : t('darkMode')}
          >
            {dark ? <Sun size={16} /> : <Moon size={16} />}
            <span>{dark ? t('light') : t('dark')}</span>
          </button>
        </div>
      </aside>

      {/* Mobile backdrop */}
      {open && (
        <button
          aria-label={t('closeMenu')}
          className="fixed inset-0 z-10 bg-black/50 backdrop-blur-sm md:hidden cursor-default"
          onClick={() => setOpen(false)}
        />
      )}
    </>
  );
}
