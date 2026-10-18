/**
 * Team Settings Page — product honesty (D11-E).
 *
 * Outer harness Team roles are NOT agent scope grants. Scope membership admin
 * lives on the BFF / supervisor grant plane — not this Settings tab.
 */

'use client';

import Link from 'next/link';
import { motion } from 'framer-motion';
import { ArrowLeft, Users, Info } from '@phosphor-icons/react';
import { Header } from '@/components/layout/Header';
import { Sidebar } from '@/components/layout/Sidebar';
import { cn } from '@/lib/utils';

const pageVariants = {
  hidden: { opacity: 0 },
  visible: { opacity: 1, transition: { staggerChildren: 0.08 } },
};

const itemVariants = {
  hidden: { opacity: 0, y: 16 },
  visible: { opacity: 1, y: 0, transition: { duration: 0.4, ease: [0.25, 0.46, 0.45, 0.94] } },
};

export default function TeamSettingsPage(): JSX.Element {
  return (
    <div className="grid min-h-[100dvh] grid-cols-[auto_1fr] grid-rows-[auto_1fr] bg-gradient-subtle">
      <div className="col-span-2">
        <Header />
      </div>
      <Sidebar />

      <main className="overflow-auto">
        <motion.div
          className="mx-auto max-w-4xl px-8 py-8"
          initial="hidden"
          animate="visible"
          variants={pageVariants}
        >
          <motion.div variants={itemVariants}>
            <Link
              href="/settings"
              className="group inline-flex items-center gap-2 text-sm font-medium text-slate-500 transition-colors hover:text-slate-700"
            >
              <ArrowLeft size={16} className="transition-transform group-hover:-translate-x-1" />
              Back to Settings
            </Link>
          </motion.div>

          <motion.div variants={itemVariants} className="mt-6">
            <div className="flex items-start gap-4">
              <motion.div
                whileHover={{ scale: 1.05, rotate: -5 }}
                className="flex h-14 w-14 items-center justify-center rounded-2xl bg-gradient-to-br from-secondary-400 to-secondary-600 shadow-lg shadow-secondary-500/25"
              >
                <Users size={26} weight="fill" className="text-white" />
              </motion.div>
              <div>
                <h1 className="text-2xl font-semibold tracking-tight text-slate-900">
                  Team Management
                </h1>
                <p className="mt-1 text-sm text-slate-500">
                  Product harness roles — not agent workspace scope grants
                </p>
              </div>
            </div>
          </motion.div>

          <motion.div
            variants={itemVariants}
            className={cn(
              'mt-8 rounded-2xl bg-white p-6',
              'border-2 border-slate-200/80 shadow-sm'
            )}
            data-testid="team-settings-unavailable"
          >
            <h3 className="text-sm font-semibold text-slate-900 mb-2">
              Team invites not wired
            </h3>
            <p className="text-sm text-slate-600 leading-relaxed">
              Settings Team roles (Owner / Admin / Member) are product harness RBAC — they do
              not map to agent scope grants such as{' '}
              <code className="text-xs bg-slate-100 px-1 rounded">workspace:read</code>,{' '}
              <code className="text-xs bg-slate-100 px-1 rounded">sandbox:exec</code>, or{' '}
              <code className="text-xs bg-slate-100 px-1 rounded">scope:admin</code>.
              Shared agent scopes are managed via the BFF{' '}
              <code className="text-xs bg-slate-100 px-1 rounded">/api/v1/workspace/scopes/…/members</code>{' '}
              proxy (service JWT + durable Postgres grants).
            </p>
            <motion.div className="mt-4 flex items-start gap-3 rounded-xl border-2 border-slate-200 bg-slate-50 p-4">
              <Info size={18} className="text-slate-500 flex-shrink-0 mt-0.5" />
              <p className="text-xs text-slate-600 leading-relaxed">
                This tab is disabled intentionally (no mock invite theater). A future Scopes admin
                UI will surface agent grant membership separately from harness Team roles.
              </p>
            </motion.div>
          </motion.div>
        </motion.div>
      </main>
    </div>
  );
}
