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
import { AuthGuard } from '@/components/auth/AuthGuard';
import { useCreateHarness } from '@/lib/api/hooks/useHarnesses';
import { cn } from '@/lib/utils';
import { AppShell } from '@/components/layout/AppShell';

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
    <AppShell>
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
              className="group inline-flex items-center gap-2 text-sm font-medium text-ink-2 transition-colors hover:text-ink-2"
            >
              <ArrowLeft size={16} className="transition-transform group-hover:-translate-x-1" />
              Back to Harnesses
            </Link>
          </motion.div>

          {/* Page header */}
          <motion.div className="mt-6" variants={itemVariants}>
            <div className="flex items-center gap-3">
              <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-accent-500">
                <Sparkle size={20} weight="fill" className="text-white" />
              </div>
              <div>
                <h1 className="text-2xl font-semibold tracking-tight text-ink">
                  Create New Harness
                </h1>
                <p className="mt-0.5 text-sm text-ink-2">
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
                    <label className="block text-sm font-medium text-ink-2">
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
                        'text-[15px] text-ink placeholder:text-ink-3',
                        'bg-canvas border border-line',
                        'transition-all duration-200',
                        'hover:border-line-strong hover:bg-surface',
                        'focus:border-accent-500 focus:bg-surface focus:outline-none focus:ring-4 focus:ring-accent-500/10'
                      )}
                    />
                    <p className="mt-2 text-xs text-ink-2">
                      Use lowercase letters, numbers, and hyphens
                    </p>
                  </div>

                  <div>
                    <label className="block text-sm font-medium text-ink-2">
                      Description
                    </label>
                    <input
                      type="text"
                      value={description}
                      onChange={(e) => setDescription(e.target.value)}
                      placeholder="Optional"
                      className={cn(
                        'mt-2 h-12 w-full rounded-xl px-4',
                        'text-[15px] text-ink placeholder:text-ink-3',
                        'bg-canvas border border-line',
                        'focus:border-accent-500 focus:bg-surface focus:outline-none focus:ring-4 focus:ring-accent-500/10'
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
            className="mt-10 flex items-center justify-between border-t border-line pt-6"
          >
            <Link
              href="/"
              className="text-sm font-medium text-ink-2 transition-colors hover:text-ink-2"
            >
              Cancel
            </Link>
            <div className="flex items-center gap-3">
              <motion.button
                type="button"
                disabled={createHarness.isPending || !name.trim()}
                onClick={() => void handleCreate()}
                className={cn(
                  'inline-flex items-center gap-2 rounded-xl px-6 py-3',
                  'text-sm font-medium text-white',
                  'bg-accent-500',
                  '',
                  'transition- hover:',
                  'disabled:opacity-60'
                )}
              >
                {createHarness.isPending ? 'Creating…' : 'Create and configure'}
                <CaretRight size={16} weight="bold" />
              </motion.button>
            </div>
          </motion.div>
        </motion.div>
      </AppShell>
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
      transition={{ duration: 0.2 }}
      className="rounded-card border border-line bg-surface p-6 transition-"
    >
      <div className="flex items-start justify-between">
        <div className="flex items-start gap-4">
          <div className="relative">
            <div className="flex h-11 w-11 items-center justify-center rounded-xl bg-gradient-to-br from-slate-100 to-slate-50">
              <Icon size={20} weight="regular" className="text-ink-2" />
            </div>
            <div className="absolute -right-1 -top-1 flex h-5 w-5 items-center justify-center rounded-full bg-accent-500 text-[10px] font-bold text-white">
              {number}
            </div>
          </div>
          <div>
            <h2 className="text-base font-semibold text-ink">{title}</h2>
            <p className="mt-0.5 text-sm text-ink-2">{description}</p>
          </div>
        </div>
        {action}
      </div>
      <div className="mt-6">{children}</div>
    </motion.div>
  );
}
