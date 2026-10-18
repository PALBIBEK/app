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
import {
  ArrowLeft,
  CaretRight,
  Wrench,
  TextAa,
  Brain,
  Cube,
  Lightning,
  CheckCircle,
  Plus,
  Info,
  Sparkle,
} from '@phosphor-icons/react';
import { Header } from '@/components/layout/Header';
import { Sidebar } from '@/components/layout/Sidebar';
import { AuthGuard } from '@/components/auth/AuthGuard';
import { useCreateHarness } from '@/lib/api/hooks/useHarnesses';
import { useTools } from '@/lib/api/hooks/useTools';
import type { Harness } from '@/lib/api/types/harness.types';
import { cn } from '@/lib/utils';

const models = [
  { id: 'claude-sonnet-4', name: 'Claude Sonnet 4', description: 'Balanced performance and speed', recommended: true },
  { id: 'gpt-4o', name: 'GPT-4o', description: 'OpenAI flagship' },
  { id: 'gpt-4o-mini', name: 'GPT-4o mini', description: 'Fast and cost-efficient' },
];

const modes = [
  { id: 'agent', name: 'Agent', description: 'Autonomous tool use and multi-step work', icon: Cube },
  { id: 'chat', name: 'Chat', description: 'Interactive conversation', icon: Lightning },
  { id: 'completion', name: 'Completion', description: 'Single-shot text completion', icon: TextAa },
];

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

const scaleOnHover = {
  scale: 1.02,
  transition: { duration: 0.2, ease: 'easeOut' }
};

export default function CreateHarnessPage(): JSX.Element {
  const router = useRouter();
  const createHarness = useCreateHarness();
  const { data: toolsList } = useTools({ page_size: 100 });
  const ownedTools = toolsList?.data ?? [];
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [systemPrompt, setSystemPrompt] = useState('');
  const [selectedModel, setSelectedModel] = useState('claude-sonnet-4');
  const [selectedMode, setSelectedMode] = useState('agent');
  const [selectedToolIds, setSelectedToolIds] = useState<string[]>([]);

  function toggleToolId(toolId: string): void {
    setSelectedToolIds((prev) =>
      prev.includes(toolId) ? prev.filter((id) => id !== toolId) : [...prev, toolId]
    );
  }

  async function handleCreate(): Promise<void> {
    const trimmed = name.trim();
    if (!trimmed) {
      return;
    }
    try {
      const created = await createHarness.mutateAsync({
        name: trimmed,
        description: description.trim() || undefined,
        model: selectedModel as Harness['model'],
        mode: selectedMode as Harness['mode'],
        tools: selectedToolIds,
        memory: [],
        system_prompt: systemPrompt.trim() || undefined,
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
                  Configure your agent with tools, prompts, and memory
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
                description="Name and model selection"
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

                  {/* Model selection */}
                  <div>
                    <label className="block text-sm font-medium text-slate-700 mb-3">
                      Model
                    </label>
                    <div className="space-y-2">
                      {models.map((model) => (
                        <motion.label
                          key={model.id}
                          whileHover={scaleOnHover}
                          whileTap={{ scale: 0.98 }}
                          className={cn(
                            'relative flex cursor-pointer items-center gap-4 rounded-xl p-4',
                            'border-2 transition-all duration-200',
                            selectedModel === model.id
                              ? 'border-primary-500 bg-primary-50/50 shadow-md shadow-primary-500/10'
                              : 'border-slate-200 bg-white hover:border-slate-300 hover:shadow-sm'
                          )}
                        >
                          <input
                            type="radio"
                            name="model"
                            value={model.id}
                            checked={selectedModel === model.id}
                            onChange={() => setSelectedModel(model.id)}
                            className="sr-only"
                          />
                          <div
                            className={cn(
                              'flex h-5 w-5 items-center justify-center rounded-full border-2 transition-all',
                              selectedModel === model.id
                                ? 'border-primary-500 bg-primary-500'
                                : 'border-slate-300'
                            )}
                          >
                            {selectedModel === model.id && (
                              <motion.div
                                initial={{ scale: 0 }}
                                animate={{ scale: 1 }}
                                className="h-2 w-2 rounded-full bg-white"
                              />
                            )}
                          </div>
                          <div className="flex-1">
                            <div className="flex items-center gap-2">
                              <p className="text-sm font-semibold text-slate-900">{model.name}</p>
                              {model.recommended && (
                                <span className="rounded-full bg-secondary-100 px-2 py-0.5 text-[10px] font-semibold text-secondary-700">
                                  Recommended
                                </span>
                              )}
                            </div>
                            <p className="mt-0.5 text-xs text-slate-500">{model.description}</p>
                          </div>
                          {selectedModel === model.id && (
                            <motion.div
                              initial={{ scale: 0 }}
                              animate={{ scale: 1 }}
                              transition={{ type: 'spring', stiffness: 500, damping: 30 }}
                            >
                              <CheckCircle size={20} weight="fill" className="text-primary-500" />
                            </motion.div>
                          )}
                        </motion.label>
                      ))}
                    </div>
                  </div>
                </div>
              </FormSection>
            </motion.div>

            {/* Mode Selection */}
            <motion.div variants={itemVariants}>
              <FormSection
                icon={Cube}
                title="Execution Mode"
                description="How the agent manages state"
                number={2}
              >
                <div className="grid grid-cols-2 gap-4">
                  {modes.map((mode) => {
                    const Icon = mode.icon;
                    return (
                      <motion.label
                        key={mode.id}
                        whileHover={scaleOnHover}
                        whileTap={{ scale: 0.98 }}
                        className={cn(
                          'relative flex cursor-pointer flex-col gap-3 rounded-xl p-5',
                          'border-2 transition-all duration-200',
                          selectedMode === mode.id
                            ? 'border-primary-500 bg-primary-50/50 shadow-md shadow-primary-500/10'
                            : 'border-slate-200 bg-white hover:border-slate-300 hover:shadow-sm'
                        )}
                      >
                        <input
                          type="radio"
                          name="mode"
                          value={mode.id}
                          checked={selectedMode === mode.id}
                          onChange={() => setSelectedMode(mode.id)}
                          className="sr-only"
                        />
                        <div className="flex items-center justify-between">
                          <div
                            className={cn(
                              'flex h-10 w-10 items-center justify-center rounded-xl transition-colors',
                              selectedMode === mode.id
                                ? 'bg-primary-100'
                                : 'bg-slate-100'
                            )}
                          >
                            <Icon
                              size={20}
                              weight="regular"
                              className={selectedMode === mode.id ? 'text-primary-600' : 'text-slate-500'}
                            />
                          </div>
                          {selectedMode === mode.id && (
                            <motion.div
                              initial={{ scale: 0, rotate: -180 }}
                              animate={{ scale: 1, rotate: 0 }}
                              transition={{ type: 'spring', stiffness: 500, damping: 30 }}
                            >
                              <CheckCircle size={20} weight="fill" className="text-primary-500" />
                            </motion.div>
                          )}
                        </div>
                        <div>
                          <p className="text-sm font-semibold text-slate-900">{mode.name}</p>
                          <p className="mt-1 text-xs text-slate-500 leading-relaxed">{mode.description}</p>
                        </div>
                      </motion.label>
                    );
                  })}
                </div>
              </FormSection>
            </motion.div>

            {/* Tools */}
            <motion.div variants={itemVariants}>
              <FormSection
                icon={Wrench}
                title="Tools"
                description="Attach owned tool UUIDs (editable later on harness detail)"
                number={3}
                action={
                  <Link
                    href="/tools/new"
                    className="inline-flex items-center gap-1.5 text-sm font-medium text-primary-600 hover:text-primary-700"
                  >
                    <Plus size={14} weight="bold" />
                    Create Tool
                  </Link>
                }
              >
                {ownedTools.length === 0 ? (
                  <motion.div
                    whileHover={{ borderColor: 'rgb(148 163 184)' }}
                    className="rounded-xl border-2 border-dashed border-slate-300 bg-slate-50/50 p-10"
                  >
                    <div className="flex flex-col items-center justify-center text-center">
                      <motion.div
                        whileHover={{ scale: 1.1, rotate: 5 }}
                        className="flex h-14 w-14 items-center justify-center rounded-2xl bg-slate-100"
                      >
                        <Wrench size={24} weight="regular" className="text-slate-400" />
                      </motion.div>
                      <p className="mt-4 text-sm font-semibold text-slate-700">No tools yet</p>
                      <p className="mt-1 max-w-xs text-sm text-slate-500">
                        Create a tool, then attach its UUID here or on harness detail
                      </p>
                      <Link
                        href="/tools/new"
                        className={cn(
                          'mt-5 inline-flex items-center gap-2 rounded-xl px-5 py-3',
                          'text-sm font-medium text-white',
                          'bg-gradient-to-br from-primary-500 to-primary-600',
                          'shadow-lg shadow-primary-500/25'
                        )}
                      >
                        <Plus size={16} weight="bold" />
                        Browse Tools
                      </Link>
                    </div>
                  </motion.div>
                ) : (
                  <div className="max-h-56 space-y-2 overflow-y-auto rounded-xl border-2 border-slate-200 bg-white p-3">
                    {ownedTools.map((tool) => {
                      const checked = selectedToolIds.includes(tool.id);
                      return (
                        <label
                          key={tool.id}
                          className={cn(
                            'flex cursor-pointer items-start gap-3 rounded-lg px-2 py-2',
                            checked ? 'bg-primary-50 ring-1 ring-primary-200' : 'hover:bg-slate-50'
                          )}
                        >
                          <input
                            type="checkbox"
                            checked={checked}
                            onChange={() => toggleToolId(tool.id)}
                            className="mt-1 rounded border-slate-300"
                          />
                          <span className="min-w-0">
                            <span className="block text-sm font-medium text-slate-800">
                              {tool.name}
                            </span>
                            <span className="block font-mono text-[11px] text-slate-400">
                              {tool.id}
                            </span>
                          </span>
                        </label>
                      );
                    })}
                  </div>
                )}
              </FormSection>
            </motion.div>

            {/* System Prompt */}
            <motion.div variants={itemVariants}>
              <FormSection
                icon={TextAa}
                title="System Prompt"
                description="Instructions that define agent behavior"
                number={4}
                action={
                  <motion.button
                    whileHover={{ scale: 1.05 }}
                    whileTap={{ scale: 0.95 }}
                    className="inline-flex items-center gap-1.5 text-sm font-medium text-primary-600 hover:text-primary-700"
                  >
                    Select from Library
                  </motion.button>
                }
              >
                <motion.textarea
                  rows={6}
                  value={systemPrompt}
                  onChange={(e) => setSystemPrompt(e.target.value)}
                  placeholder="You are a helpful assistant that..."
                  whileFocus={{ scale: 1.005 }}
                  className={cn(
                    'w-full rounded-xl px-4 py-4',
                    'text-[15px] text-slate-900 placeholder:text-slate-400',
                    'bg-slate-50/50 border-2 border-slate-200',
                    'shadow-sm transition-all duration-200',
                    'hover:border-slate-300 hover:bg-white',
                    'focus:border-primary-500 focus:bg-white focus:outline-none focus:ring-4 focus:ring-primary-500/10',
                    'resize-none leading-relaxed'
                  )}
                />
                <div className="mt-2 flex items-center gap-4 text-xs text-slate-500">
                  <span>{systemPrompt.length} characters</span>
                  <span className="h-1 w-1 rounded-full bg-slate-300" />
                  <span>Supports markdown</span>
                </div>
              </FormSection>
            </motion.div>

            {/* Memory */}
            <motion.div variants={itemVariants}>
              <FormSection
                icon={Brain}
                title="Memory Configuration"
                description="How the agent remembers context"
                number={5}
              >
                <motion.div
                  whileHover={{ scale: 1.01 }}
                  className="flex items-start gap-4 rounded-xl border-2 border-slate-200 bg-white p-5 transition-all hover:border-slate-300 hover:shadow-sm"
                >
                  <div className="flex h-11 w-11 items-center justify-center rounded-xl bg-accent-50">
                    <Info size={20} weight="regular" className="text-accent-600" />
                  </div>
                  <div className="flex-1">
                    <p className="text-sm font-semibold text-slate-900">Default Memory</p>
                    <p className="mt-1 text-sm text-slate-500 leading-relaxed">
                      Uses conversation history within the session. Configure advanced memory providers after creation.
                    </p>
                  </div>
                </motion.div>
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
                whileHover={{ scale: 1.02 }}
                whileTap={{ scale: 0.98 }}
                className={cn(
                  'inline-flex items-center gap-2 rounded-xl px-5 py-3',
                  'text-sm font-medium text-slate-700',
                  'bg-white border-2 border-slate-200',
                  'shadow-sm transition-all hover:border-slate-300 hover:shadow'
                )}
              >
                Save as Draft
              </motion.button>
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
                {createHarness.isPending ? 'Creating…' : 'Create Harness'}
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
