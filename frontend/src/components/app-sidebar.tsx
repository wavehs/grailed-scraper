'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import {
  BadgePercent,
  Database,
  Menu,
  RadioTower,
  Settings,
  Star,
  Tags,
  TrendingUp,
  X,
  type LucideIcon,
} from 'lucide-react';
import { useEffect, useState } from 'react';
import { useI18n } from '@/lib/i18n';

type NavItem = { key: string; href: string; icon: LucideIcon };

const sections: { label: string; items: NavItem[] }[] = [
  {
    label: 'analytics',
    items: [
      { key: 'trends', href: '/trends', icon: TrendingUp },
      { key: 'watchlist', href: '/watchlist', icon: Star },
      { key: 'deals', href: '/deals', icon: BadgePercent },
      { key: 'catalog', href: '/listings', icon: Database },
    ],
  },
  {
    label: 'management',
    items: [
      { key: 'brands', href: '/brands', icon: Tags },
      { key: 'collect', href: '/collect', icon: RadioTower },
    ],
  },
];

const settingsItem: NavItem = { key: 'settings', href: '/settings', icon: Settings };

function NavLink({ item, active, onNavigate }: { item: NavItem; active: boolean; onNavigate: () => void }) {
  const { t } = useI18n();
  const Icon = item.icon;
  return (
    <Link
      aria-current={active ? 'page' : undefined}
      className={`flex h-7 items-center gap-2.5 rounded-[5px] px-2 text-[13px] transition-colors duration-75 ${
        active
          ? 'bg-[var(--bg-selected)] text-[var(--text-primary)]'
          : 'text-[var(--text-body)] hover:bg-[var(--bg-surface-hover)] hover:text-[var(--text-primary)]'
      }`}
      href={item.href}
      onClick={onNavigate}
    >
      <Icon
        size={15}
        strokeWidth={1.75}
        className={active ? 'text-[var(--text-primary)]' : 'text-[var(--text-muted)]'}
      />
      <span className="truncate">{t(item.key)}</span>
    </Link>
  );
}

/** Linear's sidebar: workspace row, grouped navigation, settings pinned to the bottom. */
export function AppSidebar() {
  const pathname = usePathname() ?? '';
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const close = () => setOpen(false);
  const isActive = (href: string) => pathname === href || pathname.startsWith(`${href}/`);

  useEffect(() => {
    if (!open) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false);
    };
    window.addEventListener('keydown', closeOnEscape);
    return () => window.removeEventListener('keydown', closeOnEscape);
  }, [open]);

  return (
    <>
      {/* The phone top bar holds the menu button; the sidebar becomes a drawer below md. */}
      <div className="fixed inset-x-0 top-0 z-30 flex h-11 items-center gap-2 border-b border-[var(--border-subtle)] bg-[var(--bg-sidebar)] px-3 md:hidden">
        <button
          className="inline-flex h-7 w-7 items-center justify-center rounded-[5px] text-[var(--text-body)] hover:bg-[var(--bg-surface-hover)]"
          aria-label={t(open ? 'closeMenu' : 'menu')}
          aria-expanded={open}
          onClick={() => setOpen(!open)}
        >
          {open ? <X size={16} /> : <Menu size={16} />}
        </button>
        <span className="text-[13px] font-medium text-[var(--text-primary)]">Grailed Intelligence</span>
      </div>

      <aside
        className={`${open ? 'translate-x-0' : '-translate-x-full'} fixed inset-y-0 left-0 z-40 flex w-[var(--sidebar-width)] shrink-0 flex-col border-r border-[var(--border-subtle)] bg-[var(--bg-sidebar)] transition-transform duration-150 md:sticky md:top-0 md:z-0 md:h-dvh md:translate-x-0`}
      >
        <Link className="flex h-11 items-center gap-2 px-4" href="/trends" onClick={close}>
          <span
            aria-hidden="true"
            className="flex h-5 w-5 items-center justify-center rounded-[5px] bg-[var(--accent)] text-[11px] font-semibold text-white"
          >
            G
          </span>
          <span className="truncate text-[13px] font-medium text-[var(--text-primary)]">
            Grailed Intelligence
          </span>
        </Link>

        <nav
          aria-label={t('mainNavigation')}
          className="flex flex-1 flex-col gap-5 overflow-y-auto px-2.5 pt-2"
        >
          {sections.map((section) => (
            <div key={section.label}>
              <p className="mb-1 px-2 text-xs text-[var(--text-muted)]">{t(section.label)}</p>
              <div className="flex flex-col gap-px">
                {section.items.map((item) => (
                  <NavLink key={item.href} item={item} active={isActive(item.href)} onNavigate={close} />
                ))}
              </div>
            </div>
          ))}
          <div className="mt-auto border-t border-[var(--border-subtle)] py-2.5">
            <NavLink item={settingsItem} active={isActive(settingsItem.href)} onNavigate={close} />
          </div>
        </nav>
      </aside>

      {open && (
        <button
          aria-label={t('closeMenu')}
          className="fixed inset-0 z-30 cursor-default bg-black/50 md:hidden"
          onClick={close}
        />
      )}
    </>
  );
}
