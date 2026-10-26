'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import {
  Brain,
  ChartLine,
  ClockCounterClockwise,
  Cube,
  Gear,
  GitBranch,
  Plus,
  Stack,
  TextAa,
  Wrench,
  X,
} from '@phosphor-icons/react';

import { cn } from '@/lib/utils';

interface NavItem {
  label: string;
  href: string;
  icon: React.ElementType;
}

interface NavGroup {
  label: string;
  items: NavItem[];
}

// Grouped by what the user is doing: running agents, building them, operating
// the platform. Every href must have a page (Sidebar.routes.test.ts checks it).
const groups: NavGroup[] = [
  {
    label: 'Work',
    items: [{ label: 'Sessions', href: '/sessions', icon: ClockCounterClockwise }],
  },
  {
    label: 'Build',
    items: [
      { label: 'Agents', href: '/', icon: Stack },
      { label: 'Prompts', href: '/prompts', icon: TextAa },
      { label: 'Tools', href: '/tools', icon: Wrench },
      { label: 'Subagents', href: '/subagents', icon: GitBranch },
      { label: 'Memory', href: '/memory', icon: Brain },
    ],
  },
  {
    label: 'Operate',
    items: [
      { label: 'Environments', href: '/environments', icon: Cube },
      { label: 'Traces', href: '/traces', icon: ChartLine },
    ],
  },
  {
    label: 'Admin',
    items: [{ label: 'Settings', href: '/settings', icon: Gear }],
  },
];

export const NAV_GROUPS = groups;

function isActive(pathname: string, href: string): boolean {
  if (href === '/') return pathname === '/' || pathname.startsWith('/harness');
  return pathname === href || pathname.startsWith(`${href}/`);
}

interface SidebarProps {
  /** On narrow screens the menu is a drawer; these open and close it. */
  open?: boolean;
  onClose?: () => void;
}

export function Sidebar({ open = false, onClose }: SidebarProps): JSX.Element {
  const pathname = usePathname() ?? '/';

  const nav = (
    <nav aria-label="Main" className="flex h-full flex-col">
      <div className="flex h-14 items-center gap-2.5 border-b border-line px-4">
        <span className="flex h-[26px] w-[26px] items-center justify-center rounded-[7px] bg-accent-500" aria-hidden="true">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#FFFFFF" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round">
            <path d="M4 17l6-6-6-6" />
            <path d="M12 19h8" />
          </svg>
        </span>
        <Link href="/" className="font-semibold tracking-[-0.01em] text-ink hover:text-ink">
          Prime Agent
        </Link>
        {onClose ? (
          <button
            type="button"
            aria-label="Close menu"
            onClick={onClose}
            className="ml-auto inline-flex h-9 w-9 items-center justify-center rounded-lg text-ink-2 hover:bg-line-soft md:hidden"
          >
            <X size={18} />
          </button>
        ) : null}
      </div>

      <div className="flex-1 overflow-y-auto px-3 py-3">
        {groups.map((group) => (
          <div key={group.label} className="mb-4">
            <p className="px-3 pb-1.5 text-2xs font-medium text-ink-3">{group.label}</p>
            <ul className="space-y-0.5">
              {group.items.map((item) => {
                const active = isActive(pathname, item.href);
                const Icon = item.icon;
                return (
                  <li key={item.href}>
                    <Link
                      href={item.href}
                      aria-current={active ? 'page' : undefined}
                      onClick={onClose}
                      className={cn(
                        'flex h-9 items-center gap-2.5 rounded-lg px-3 text-sm transition-colors',
                        active
                          ? 'bg-accent-50 font-medium text-accent-700 hover:text-accent-700'
                          : 'text-ink-2 hover:bg-line-soft hover:text-ink',
                      )}
                    >
                      <Icon size={17} weight={active ? 'fill' : 'regular'} aria-hidden="true" />
                      {item.label}
                    </Link>
                  </li>
                );
              })}
            </ul>
          </div>
        ))}
      </div>

      <div className="border-t border-line p-3">
        <Link
          href="/harness/new"
          onClick={onClose}
          className="flex h-10 items-center justify-center gap-2 rounded-lg bg-accent-500 text-sm font-medium text-white hover:bg-accent-600 hover:text-white"
        >
          <Plus size={16} weight="bold" aria-hidden="true" />
          New agent
        </Link>
      </div>
    </nav>
  );

  return (
    <>
      {/* Wide screens: a fixed column. */}
      <aside className="hidden w-[232px] shrink-0 border-r border-line bg-surface md:block">{nav}</aside>
      {/* Narrow screens: a drawer over the page. */}
      {open ? (
        <div className="fixed inset-0 z-40 md:hidden">
          <button type="button" aria-label="Close menu" className="absolute inset-0 bg-ink/30" onClick={onClose} />
          <aside className="absolute inset-y-0 left-0 w-[264px] bg-surface shadow-xl">{nav}</aside>
        </div>
      ) : null}
    </>
  );
}
