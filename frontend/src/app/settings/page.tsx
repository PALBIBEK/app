'use client';

import { useState } from 'react';
import { Cube, Gear, Key, Wallet } from '@phosphor-icons/react';
import { motion } from 'framer-motion';
import Link from 'next/link';

import { AuthGuard } from '@/components/auth/AuthGuard';
import { cn } from '@/lib/utils';
import { AppShell } from '@/components/layout/AppShell';

const tabs = [
  { id: 'general', label: 'General', icon: Gear },
  { id: 'api-keys', label: 'API keys', icon: Key },
  { id: 'budget', label: 'Budget', icon: Wallet },
  // Org chrome label — not the agent /workspace file tree (D9 G6).
  { id: 'workspace', label: 'Org runtime', icon: Cube },
] as const;

type TabId = (typeof tabs)[number]['id'];

export default function SettingsPage(): JSX.Element {
  const [activeTab, setActiveTab] = useState<TabId>('general');

  return (
    <AuthGuard>
      <AppShell>
          <div className="mx-auto max-w-4xl px-8 py-8">
            <h1 className="text-2xl font-semibold tracking-tight text-slate-900">Settings</h1>
            <p className="mt-2 text-sm text-slate-500">
              Product settings. Team chrome lives under{' '}
              <Link href="/settings/team" className="text-primary-600 hover:underline">
                Team
              </Link>
              .
            </p>

            <div className="mt-6 flex flex-wrap gap-2">
              {tabs.map((tab) => {
                const Icon = tab.icon;
                const active = activeTab === tab.id;
                return (
                  <button
                    key={tab.id}
                    type="button"
                    onClick={() => setActiveTab(tab.id)}
                    className={cn(
                      'inline-flex items-center gap-2 rounded-lg px-3 py-2 text-sm font-medium',
                      active
                        ? 'bg-slate-900 text-white'
                        : 'bg-white text-slate-600 border border-slate-200 hover:bg-slate-50'
                    )}
                  >
                    <Icon size={16} />
                    {tab.label}
                  </button>
                );
              })}
            </div>

            <div className="mt-6 rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
              {activeTab === 'general' && (
                <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }}>
                  <h3 className="text-sm font-semibold text-slate-900 mb-2">General</h3>
                  <p className="text-sm text-slate-500">
                    Profile and org defaults are managed via the authenticated user API.
                  </p>
                </motion.div>
              )}
              {activeTab === 'api-keys' && (
                <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }}>
                  <h3 className="text-sm font-semibold text-slate-900 mb-2">API keys</h3>
                  <p className="text-sm text-slate-500">
                    BYOK is not required for local demo. Model keys stay on the AI plane / gateway.
                  </p>
                </motion.div>
              )}
              {activeTab === 'budget' && (
                <motion.div
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  data-testid="budget-settings-unavailable"
                >
                  <h3 className="text-sm font-semibold text-slate-900 mb-2">Budget</h3>
                  <p className="text-sm text-slate-500">
                    Monthly budget controls are not wired here — disabled intentionally (D8). Session
                    spend surfaces as <code className="text-xs">total_cost</code> on live sessions;
                    BudgetSpec / VK caps stay on the AI plane.
                  </p>
                </motion.div>
              )}
              {activeTab === 'workspace' && (
                <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }}>
                  <h3 className="text-sm font-semibold text-slate-900 mb-4">Org defaults</h3>
                  <p className="text-sm text-slate-500">
                    This is org chrome (runtime defaults), not the agent{' '}
                    <code className="text-xs">/workspace</code> file tree. Upload durable agent files
                    from a live Test session scope.
                  </p>
                </motion.div>
              )}
            </div>
          </div>
        </AppShell>
    </AuthGuard>
  );
}
