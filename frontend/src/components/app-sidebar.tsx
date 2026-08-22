'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import {
  BarChart3,
  BookOpen,
  Fingerprint,
  Globe,
  LayoutDashboard,
  Menu,
  Play,
  Settings,
  Tags,
  X,
} from 'lucide-react';
import { useState, type ReactNode } from 'react';
import { useI18n } from '@/lib/i18n';

type NavItem = {
  key: string;
  href: string;
  icon: ReactNode;
  group: 'analytics' | 'management' | 'system';
};

const navItems: NavItem[] = [
  { key: 'dashboard', href: '/dashboard', icon: <LayoutDashboard size={18} />, group: 'analytics' },
  { key: 'brands', href: '/brands', icon: <Tags size={18} />, group: 'management' },
  { key: 'parserRuns', href: '/parser-runs', icon: <Play size={18} />, group: 'management' },
  { key: 'modelRules', href: '/model-rules', icon: <BookOpen size={18} />, group: 'management' },
  { key: 'identityReview', href: '/identity-review', icon: <Fingerprint size={18} />, group: 'management' },
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

  const groups = ['analytics', 'management', 'system'] as const;

  return (
    <>
      {/* Mobile toggle */}
      <button
        className="fixed left-3 top-3 z-30 rounded-lg glass p-2 text-[var(--text-primary)] md:hidden cursor-pointer"
        aria-label={t('menu')}
        aria-expanded={open}
        onClick={() => setOpen(!open)}
      >
        {open ? <X size={20} /> : <Menu size={20} />}
      </button>

      {/* Sidebar */}
      <aside
        className={`${open ? 'translate-x-0' : '-translate-x-full'} fixed inset-y-0 z-20 w-[260px] flex flex-col border-r border-[var(--border-subtle)] bg-[rgba(10,10,18,0.95)] backdrop-blur-xl p-5 transition-transform duration-300 md:sticky md:top-0 md:h-screen md:translate-x-0`}
      >
        {/* Logo */}
        <Link
          className="mb-8 flex items-center gap-3 group"
          href="/dashboard"
          onClick={() => setOpen(false)}
        >
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-gradient-to-br from-[#6366f1] to-[#8b5cf6] shadow-lg shadow-[rgba(99,102,241,0.2)]">
            <BarChart3 size={16} className="text-white" />
          </div>
          <div>
            <span className="text-sm font-bold tracking-tight text-[var(--text-primary)]">
              Grailed Liquidity
            </span>
            <span className="block text-[10px] font-medium uppercase tracking-widest text-[var(--text-muted)]">
              Analyzer
            </span>
          </div>
        </Link>

        {/* Navigation */}
        <nav aria-label={t('mainNavigation')} className="flex-1 space-y-6">
          {groups.map((group) => {
            const items = navItems.filter((item) => item.group === group);
            if (!items.length) return null;
            return (
              <div key={group}>
                <p className="mb-2 px-3 text-[10px] font-semibold uppercase tracking-widest text-[var(--text-muted)]">
                  {groupLabels[group]}
                </p>
                <div className="space-y-0.5">
                  {items.map(({ key, href, icon }) => {
                    const active = pathname === href || pathname.startsWith(`${href}/`);
                    return (
                      <Link
                        aria-current={active ? 'page' : undefined}
                        className={`flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-all duration-200 ${
                          active
                            ? 'bg-gradient-to-r from-[rgba(99,102,241,0.15)] to-[rgba(139,92,246,0.08)] text-white border border-[rgba(99,102,241,0.2)] shadow-sm shadow-[rgba(99,102,241,0.1)]'
                            : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:bg-[rgba(255,255,255,0.04)]'
                        }`}
                        href={href}
                        key={href}
                        onClick={() => setOpen(false)}
                      >
                        <span className={active ? 'text-[#818cf8]' : ''}>{icon}</span>
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
        <div className="mt-auto border-t border-[var(--border-subtle)] pt-4">
          <button
            className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-sm text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:bg-[rgba(255,255,255,0.04)] transition-colors cursor-pointer"
            onClick={() => setLocale(locale === 'en' ? 'ru' : 'en')}
          >
            <Globe size={16} />
            <span>{locale === 'en' ? 'English' : 'Русский'}</span>
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
