'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { Brain, ChartLine, ClockCounterClockwise, Cube, Gear, GitBranch, Lightning, Plus, Stack, TextAa, Wrench } from '@phosphor-icons/react';

import { cn } from '@/lib/utils';

interface NavItem {
  label: string;
  href: string;
  icon: React.ElementType;
  badge?: string;
}

// Memory nav stays hidden — not available (D7 honesty) until durable stores ship product-ready.
const navItems: NavItem[] = [
  { label: 'Harnesses', href: '/', icon: Stack },
  { label: 'Tools', href: '/tools', icon: Wrench },
  { label: 'Environments', href: '/environments', icon: Cube },
  { label: 'Prompts', href: '/prompts', icon: TextAa },
  { label: 'Subagents', href: '/subagents', icon: GitBranch },
  { label: 'Sessions', href: '/sessions', icon: ClockCounterClockwise },
  { label: 'Memory', href: '/memory', icon: Brain },
  { label: 'Traces', href: '/traces', icon: ChartLine, badge: 'New' },
];

const secondaryItems: NavItem[] = [
  { label: 'Settings', href: '/settings', icon: Gear },
];

export function Sidebar(): JSX.Element {
  const pathname = usePathname();

  return (
    <aside className="flex w-60 flex-col border-r border-slate-200 bg-white">
      <div className="flex-1 overflow-y-auto px-3 py-4">
        <p className="mb-2 px-3 text-[11px] font-semibold uppercase tracking-wider text-slate-400">
          Product
        </p>
        <nav className="space-y-1">
          {navItems.map((item) => {
            const active =
              item.href === '/'
                ? pathname === '/'
                : pathname === item.href || pathname.startsWith(`${item.href}/`);
            const Icon = item.icon;
            return (
              <Link
                key={item.href}
                href={item.href}
                className={cn(
                  'flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm font-medium transition-colors',
                  active
                    ? 'bg-primary-50 text-primary-600'
                    : 'text-slate-600 hover:bg-slate-50 hover:text-slate-900'
                )}
              >
                <Icon size={18} weight={active ? 'fill' : 'regular'} />
                <span className="flex-1">{item.label}</span>
                {item.badge ? (
                  <span className="rounded-full bg-amber-100 px-1.5 py-0.5 text-[10px] font-semibold text-amber-700">
                    {item.badge}
                  </span>
                ) : null}
              </Link>
            );
          })}
        </nav>

        <div className="mt-6">
          <Link
            href="/harness/new"
            className={cn(
              'flex items-center justify-center gap-2 rounded-lg px-3 py-2.5 text-sm font-medium',
              'bg-slate-900 text-white hover:bg-slate-800'
            )}
          >
            <Plus size={16} weight="bold" />
            New harness
          </Link>
        </div>
      </div>

      <div className="border-t border-slate-200 px-3 py-3">
        {secondaryItems.map((item) => {
          const Icon = item.icon;
          const active = pathname.startsWith(item.href);
          return (
            <Link
              key={item.href}
              href={item.href}
              className={cn(
                'flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm font-medium',
                active ? 'bg-slate-100 text-slate-900' : 'text-slate-600 hover:bg-slate-50'
              )}
            >
              <Icon size={18} />
              {item.label}
            </Link>
          );
        })}
        <p className="mt-2 flex items-center gap-1 px-3 text-[11px] text-slate-400">
          <Lightning size={12} /> Local demo shell
        </p>
      </div>
    </aside>
  );
}
