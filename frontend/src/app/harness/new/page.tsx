/**
 * Create Harness Page - Premium Edition
 *
 * Design: Multi-step form with Framer Motion animations
 * Quality: Senior frontend developer level
 */

'use client';

import { useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { motion } from 'framer-motion';
import { ArrowLeft, CaretRight, Lightning, Sparkle } from '@phosphor-icons/react';
import { Header } from '@/components/layout/Header';
import { Sidebar } from '@/components/layout/Sidebar';
import { AuthGuard } from '@/components/auth/AuthGuard';
import { useCreateHarness } from '@/lib/api/hooks/useHarnesses';
import { cn } from '@/lib/utils';

// Animation variants
const containerVariants = {
  hidden: { opacity: 0 },
  visible: {
    opacity: 1,
    transition: { staggerChildren: 0.1, delayChildren: 0.1 }
  }
};

const itemVariants = {
  hidden: { opacity: 0, y: 20 },
  visible: { opacity: 1, y: 0, transition: { duration: 0.4, ease: [0.25, 0.46, 0.45, 0.94] } }
};

export default function CreateHarnessPage(): JSX.Element {
  const router = useRouter();
  const createHarness = useCreateHarness();
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');

  async function handleCreate(): Promise<void> {
    const trimmed = name.trim();
    if (!trimmed) {
      return;
    }
    try {
      // The builder (next page) starts it as the shipped coding agent; everything is set there.
      const created = await createHarness.mutateAsync({
        name: trimmed,
        description: description.trim() || undefined,
        model: 'gateway/echo',
        mode: 'sticky',
        tools: [],
        memory: [],
      });
      router.push(`/harness/${created.id}`);
    } catch {
      // Toast is handled by useCreateHarness
    }
  }

  return (
    <AuthGuard>
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
          variants={containerVariants}
        >
          {/* Back link */}
          <motion.div variants={itemVariants}>
            <Link
              href="/"
              className="group inline-flex items-center gap-2 text-sm font-medium text-slate-500 transition-colors hover:text-slate-700"
            >
              <ArrowLeft size={16} className="transition-transform group-hover:-translate-x-1" />
              Back to Harnesses
            </Link>
          </motion.div>

          {/* Page header */}
          <motion.div className="mt-6" variants={itemVariants}>
            <div className="flex items-center gap-3">
              <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-gradient-to-br from-primary-500 to-primary-600 shadow-lg shadow-primary-500/25">
                <Sparkle size={20} weight="fill" className="text-white" />
              </div>
              <div>
                <h1 className="text-2xl font-semibold tracking-tight text-slate-900">
                  Create New Harness
                </h1>
                <p className="mt-0.5 text-sm text-slate-500">
                  Name it, then set its prompt, tools, model, sandbox, memory and policy
                </p>
              </div>
            </div>
          </motion.div>

          {/* Form sections */}
          <div className="mt-8 space-y-6">
            {/* Basic Info */}
            <motion.div variants={itemVariants}>
              <FormSection
                icon={Lightning}
                title="Basic Information"
                description="What the harness is called"
                number={1}
              >
                <div className="space-y-6">
                  {/* Name input */}
                  <div>
                    <label className="block text-sm font-medium text-slate-700">
                      Harness Name
                    </label>
                    <motion.input
                      type="text"
                      value={name}
                      onChange={(e) => setName(e.target.value)}
                      placeholder="e.g., coding-assistant-v1"
                      whileFocus={{ scale: 1.01 }}
                      className={cn(
                        'mt-2 h-12 w-full rounded-xl px-4',
                        'text-[15px] text-slate-900 placeholder:text-slate-400',
                        'bg-slate-50/50 border-2 border-slate-200',
                        'shadow-sm transition-all duration-200',
                        'hover:border-slate-300 hover:bg-white',
                        'focus:border-primary-500 focus:bg-white focus:outline-none focus:ring-4 focus:ring-primary-500/10'
                      )}
                    />
                    <p className="mt-2 text-xs text-slate-500">
                      Use lowercase letters, numbers, and hyphens
                    </p>
                  </div>

                  <div>
                    <label className="block text-sm font-medium text-slate-700">
                      Description
                    </label>
                    <input
                      type="text"
                      value={description}
                      onChange={(e) => setDescription(e.target.value)}
                      placeholder="Optional"
                      className={cn(
                        'mt-2 h-12 w-full rounded-xl px-4',
                        'text-[15px] text-slate-900 placeholder:text-slate-400',
                        'bg-slate-50/50 border-2 border-slate-200',
                        'focus:border-primary-500 focus:bg-white focus:outline-none focus:ring-4 focus:ring-primary-500/10'
                      )}
                    />
                  </div>

                </div>
              </FormSection>
            </motion.div>

          </div>

          {/* Actions */}
          <motion.div
            variants={itemVariants}
            className="mt-10 flex items-center justify-between border-t border-slate-200 pt-6"
          >
            <Link
              href="/"
              className="text-sm font-medium text-slate-500 transition-colors hover:text-slate-700"
            >
              Cancel
            </Link>
            <div className="flex items-center gap-3">
              <motion.button
                type="button"
                disabled={createHarness.isPending || !name.trim()}
                onClick={() => void handleCreate()}
                whileHover={{ scale: 1.02, x: 2 }}
                whileTap={{ scale: 0.98 }}
                className={cn(
                  'inline-flex items-center gap-2 rounded-xl px-6 py-3',
                  'text-sm font-medium text-white',
                  'bg-gradient-to-br from-primary-500 to-primary-600',
                  'shadow-lg shadow-primary-500/25',
                  'transition-shadow hover:shadow-xl hover:shadow-primary-500/30',
                  'disabled:opacity-60'
                )}
              >
                {createHarness.isPending ? 'Creating…' : 'Create and configure'}
                <CaretRight size={16} weight="bold" />
              </motion.button>
            </div>
          </motion.div>
        </motion.div>
      </main>
    </div>
    </AuthGuard>
  );
}

interface FormSectionProps {
  icon: React.ElementType;
  title: string;
  description: string;
  number: number;
  action?: React.ReactNode;
  children: React.ReactNode;
}

function FormSection({ icon: Icon, title, description, number, action, children }: FormSectionProps): JSX.Element {
  return (
    <motion.div
      whileHover={{ y: -2 }}
      transition={{ duration: 0.2 }}
      className="rounded-2xl border border-slate-200/80 bg-white p-6 shadow-sm transition-shadow hover:shadow-md"
    >
      <div className="flex items-start justify-between">
        <div className="flex items-start gap-4">
          <div className="relative">
            <div className="flex h-11 w-11 items-center justify-center rounded-xl bg-gradient-to-br from-slate-100 to-slate-50">
              <Icon size={20} weight="regular" className="text-slate-600" />
            </div>
            <div className="absolute -right-1 -top-1 flex h-5 w-5 items-center justify-center rounded-full bg-primary-500 text-[10px] font-bold text-white shadow-sm">
              {number}
            </div>
          </div>
          <div>
            <h2 className="text-base font-semibold text-slate-900">{title}</h2>
            <p className="mt-0.5 text-sm text-slate-500">{description}</p>
          </div>
        </div>
        {action}
      </div>
      <div className="mt-6">{children}</div>
    </motion.div>
  );
}
