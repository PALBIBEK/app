/**
 * Honest empty state for product Memory UI until a durable-memory BFF ships.
 * Matches harness.memory attach 422: "Memory stores are not available yet".
 */

'use client';

import { motion } from 'framer-motion';
import { Brain } from '@phosphor-icons/react';
import { Header } from '@/components/layout/Header';
import { Sidebar } from '@/components/layout/Sidebar';

const MEMORY_UNAVAILABLE_MESSAGE = 'Memory stores are not available yet';

export { MEMORY_UNAVAILABLE_MESSAGE };

export function MemoryUnavailablePage(): JSX.Element {
  return (
    <div className="grid min-h-[100dvh] grid-cols-[auto_1fr] grid-rows-[auto_1fr] bg-gradient-subtle">
      <div className="col-span-2">
        <Header />
      </div>
      <Sidebar />

      <main className="overflow-auto">
        <motion.div
          className="mx-auto max-w-2xl px-8 py-8"
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.35 }}
        >
          <div className="flex items-start gap-4">
            <div className="flex h-14 w-14 items-center justify-center rounded-2xl bg-slate-100">
              <Brain size={26} weight="regular" className="text-slate-500" />
            </div>
            <div>
              <h1 className="text-2xl font-semibold tracking-tight text-slate-900">
                Memory
              </h1>
              <p className="mt-1 text-sm text-slate-500">
                Knowledge bases and durable stores for harnesses
              </p>
            </div>
          </div>

          <div className="mt-8 rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
            <div className="flex items-start gap-4">
              <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-xl bg-accent-50 text-accent-600">
                <Brain size={22} weight="fill" />
              </div>
              <div>
                <h2 className="text-sm font-semibold text-slate-900">
                  {MEMORY_UNAVAILABLE_MESSAGE}
                </h2>
                <p className="mt-1 text-sm text-slate-500">
                  The API rejects harness memory references until that model
                  ships. This page stays disabled — no mock stores are shown.
                </p>
              </div>
            </div>
          </div>
        </motion.div>
      </main>
    </div>
  );
}
