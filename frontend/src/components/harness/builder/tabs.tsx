'use client';

/**
 * The builder's tab panels, one per manifest section. Each edits its slice of the
 * draft through `onChange` and shows the errors the backend put on that section.
 */

import { ArrowDown, ArrowUp, Plus, Trash, Warning } from '@phosphor-icons/react';

import type { CatalogModel, CatalogTool, Manifest } from '@/lib/api/types/manifest.types';
import { cn } from '@/lib/utils';

type Edit = (next: Manifest) => void;

export const input =
  'w-full rounded-xl border-2 border-slate-200 bg-slate-50/50 px-3 py-2 text-sm text-slate-900 focus:border-primary-500 focus:bg-white focus:outline-none focus:ring-4 focus:ring-primary-500/10'; export function Field({ label, hint, htmlFor, children,
}: { label: string; hint?: React.ReactNode; htmlFor?: string; children: React.ReactNode;
}): JSX.Element { return ( <div> <label htmlFor={htmlFor} className="block text-sm font-medium text-ink-2"> {label} </label> <div className="mt-1.5">{children}</div> {hint ? <p className="mt-1 text-xs text-ink-2">{hint}</p> : null} </div> );
} export function Errors({ messages }: { messages?: string[] }): JSX.Element | null { if (!messages?.length) return null; return ( <div role="alert" className="mb-5 rounded-xl border border-line bg-canvas px-4 py-3 text-sm text-bad"> {messages.map((m) => ( <p key={m}>{m}</p> ))} </div> );
} export function PromptTab({ m, onChange }: { m: Manifest; onChange: Edit }): JSX.Element { const sections = m.prompt.sections; const setSections = (next: Manifest['prompt']['sections']) => onChange({ ...m, prompt: { ...m.prompt, sections: next } });
  const move = (i: number, by: number) => {
    const next = [...sections];
    const [s] = next.splice(i, 1);
    next.splice(i + by, 0, s);
    setSections(next);
  };
  const freshId = () => {
    let n = sections.length + 1;
    while (sections.some((s) => s.id === `section-${n}`)) n += 1;
    return `section-${n}`;
  };
  return (
    <div className="space-y-5">
      <Field label="Persona" htmlFor="persona" hint="A short name for the role, e.g. coder or reviewer.">
        <input
          id="persona"
          className={input}
          value={m.prompt.persona}
          onChange={(e) => onChange({ ...m, prompt: { ...m.prompt, persona: e.target.value } })}
        />
      </Field>
      <div className="space-y-4">
        <p className="text-sm font-medium text-slate-700">
          System prompt sections <span className="font-normal text-slate-500">— sent in this order; {'{cwd}'} becomes the working directory</span>
        </p>
        {sections.map((s, i) => (
          <div key={i} className="rounded-xl border border-slate-200 bg-white p-4">
            <div className="flex items-center gap-2">
              <input
                aria-label={`Section ${i + 1} id`}
                className={cn(input, 'max-w-[14rem] font-mono text-xs')}
                value={s.id}
                onChange={(e) => setSections(sections.map((x, j) => (j === i ? { ...x, id: e.target.value } : x)))}
              />
              <div className="ml-auto flex items-center gap-1">
                <IconButton label="Move up" disabled={i === 0} onClick={() => move(i, -1)}>
                  <ArrowUp size={14} />
                </IconButton>
                <IconButton label="Move down" disabled={i === sections.length - 1} onClick={() => move(i, 1)}>
                  <ArrowDown size={14} />
                </IconButton>
                <IconButton label="Remove section" disabled={sections.length === 1} onClick={() => setSections(sections.filter((_, j) => j !== i))}>
                  <Trash size={14} />
                </IconButton>
              </div>
            </div>
            <textarea
              aria-label={`Section ${i + 1} text`}
              rows={4}
              className={cn(input, 'mt-3 resize-y leading-relaxed')}
              value={s.text}
              onChange={(e) => setSections(sections.map((x, j) => (j === i ? { ...x, text: e.target.value } : x)))}
            />
          </div>
        ))}
        <button
          type="button"
          onClick={() => setSections([...sections, { id: freshId(), text: ''}])} className="inline-flex items-center gap-1.5 text-sm font-medium text-accent-600 hover:text-accent-700" > <Plus size={14} weight="bold" /> Add section </button> </div> </div> );
} function IconButton({ label, disabled, onClick, children,
}: { label: string; disabled?: boolean; onClick: () => void; children: React.ReactNode;
}): JSX.Element { return ( <button type="button" aria-label={label} title={label} disabled={disabled} onClick={onClick} className="rounded-lg p-1.5 text-ink-2 hover:bg-line-soft disabled:opacity-30" > {children} </button> );
} const CLASS_HINT: Record<string, string> = { pure:'reads only',
  idempotent: 'safe to repeat',
  transactional: 'all or nothing',
  non_atomic: 'can change anything it touches',
};

export function ToolsTab({ m, onChange, tools }: { m: Manifest; onChange: Edit; tools: CatalogTool[] | undefined }): JSX.Element {
  const has = (name: string) => m.tools.includes(name);
  const needsApproval = (name: string) => m.policy.require_approval.includes(name);
  const toggle = (name: string) => {
    const next = has(name) ? m.tools.filter((t) => t !== name) : [...m.tools, name];
    onChange({
      ...m,
      tools: next,
      policy: { ...m.policy, require_approval: m.policy.require_approval.filter((t) => next.includes(t)) },
    });
  };
  const toggleApproval = (name: string) =>
    onChange({
      ...m,
      policy: {
        ...m.policy,
        require_approval: needsApproval(name)
          ? m.policy.require_approval.filter((t) => t !== name)
          : [...m.policy.require_approval, name],
      },
    });
  if (!tools) return <p className="text-sm text-slate-500">Loading the supervisor&apos;s tools…</p>;
  return (
    <div className="space-y-3">
      <p className="text-sm text-slate-500">
        The tools the supervisor can run. Custom HTTP tools can&apos;t be added to a harness yet.
      </p>
      <ul className="divide-y divide-slate-100 rounded-xl border border-slate-200 bg-white">
        {tools.map((t) => (
          <li key={t.name} className="flex items-start gap-3 px-4 py-3">
            <input
              id={`tool-${t.name}`}
              type="checkbox"
              className="mt-1 rounded border-slate-300"
              checked={has(t.name)}
              onChange={() => toggle(t.name)}
            />
            <label htmlFor={`tool-${t.name}`} className="min-w-0 flex-1 cursor-pointer">
              <span className="font-mono text-sm font-medium text-slate-900">{t.name}</span>
              <span className="ml-2 rounded-full bg-slate-100 px-2 py-0.5 text-[11px] text-slate-600">
                {CLASS_HINT[t.tool_class] ?? t.tool_class}
              </span>
              <span className="mt-0.5 block text-xs text-slate-500">{t.description}</span>
            </label>
            <label className={cn('flex items-center gap-1.5 text-xs', has(t.name) ? 'text-slate-600' : 'text-slate-300')}>
              <input
                type="checkbox"
                aria-label={`${t.name} needs approval`}
                className="rounded border-slate-300"
                disabled={!has(t.name)}
                checked={needsApproval(t.name)}
                onChange={() => toggleApproval(t.name)}
              />
              Ask first
            </label>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function ModelTab({ m, onChange, models }: { m: Manifest; onChange: Edit; models: CatalogModel[] | undefined }): JSX.Element {
  if (!models) return <p className="text-sm text-slate-500">Loading the models the gateway serves…</p>;
  const listed = models.some((x) => x.id === m.model);
  return (
    <div className="space-y-2">
      {!listed ? (
        <p className="flex items-center gap-2 rounded-xl bg-amber-50 px-4 py-3 text-sm text-amber-800">
          <Warning size={16} /> {m.model} is not served right now; pick one below.
        </p>
      ) : null}
      {models.map((x) => (
        <label
          key={x.id}
          className={cn(
            'flex cursor-pointer items-center gap-3 rounded-xl border-2 px-4 py-3',
            m.model === x.id ? 'border-primary-500 bg-primary-50/50' : 'border-slate-200 bg-white hover:border-slate-300'
          )}
        >
          <input type="radio" name="model" className="sr-only" checked={m.model === x.id} onChange={() => onChange({ ...m, model: x.id })} />
          <div className="min-w-0 flex-1">
            <p className="text-sm font-semibold text-slate-900">{x.label || x.name || x.id}</p>
            <p className="font-mono text-xs text-slate-500">{x.id}</p>
          </div>
          <div className="text-right text-xs text-slate-500">
            {x.context_window ? <p>{Math.round(x.context_window / 1000)}k context</p> : null}
            {x.cost?.input != null ? <p>${(x.cost.input * 1e6).toFixed(2)} / M in</p> : null}
          </div>
        </label>
      ))}
    </div>
  );
}

export function MemoryTab({ m, onChange }: { m: Manifest; onChange: Edit }): JSX.Element {
  return (
    <div className="space-y-4">
      <label className="flex items-start gap-3 rounded-xl border border-slate-200 bg-white px-4 py-3">
        <input
          type="checkbox"
          className="mt-1 rounded border-slate-300"
          checked={m.memory.compaction}
          onChange={(e) => onChange({ ...m, memory: { compaction: e.target.checked } })}
        />
        <span>
          <span className="block text-sm font-semibold text-slate-900">Compact long conversations</span>
          <span className="block text-xs text-slate-500">
            Summarize older turns when the context window fills, instead of stopping.
          </span>
        </span>
      </label>
      <p className="text-sm text-slate-500">
        Long-term memory is per user: every session reads and writes its user&apos;s notebooks for this harness.
      </p>
    </div>
  );
}

function NumberField({
  id,
  label,
  hint,
  value,
  step,
  onChange,
}: {
  id: string;
  label: string;
  hint?: string;
  value: number;
  step?: number;
  onChange: (v: number) => void;
}): JSX.Element {
  return (
    <Field label={label} htmlFor={id} hint={hint}>
      <input id={id} type="number" step={step ?? 1} className={input} value={Number.isFinite(value) ? value : ''} onChange={(e) => onChange(e.target.valueAsNumber)} />
    </Field>
  );
}

export function PolicyTab({ m, onChange }: { m: Manifest; onChange: Edit }): JSX.Element {
  const p = m.policy;
  const set = (patch: Partial<Manifest['policy']>) => onChange({ ...m, policy: { ...p, ...patch } });
  const guard = (key: 'repetition' | 'oscillation', label: string) => (
    <Field label={label} htmlFor={key}>
      <select id={key} className={input} value={p[key]} onChange={(e) => set({ [key]: e.target.value } as Partial<Manifest['policy']>)}>
        <option value="abort">Stop the run</option>
        <option value="warn">Warn and continue</option>
        <option value="off">Off</option>
      </select>
    </Field>
  );
  return (
    <div className="grid gap-5 sm:grid-cols-2">
      <NumberField id="max_steps" label="Model steps per run" hint="It is warned near the end and must answer on the last." value={p.max_steps} onChange={(v) => set({ max_steps: v })} />
      <NumberField id="max_cost" label="Cost cap per session ($)" step={0.5} value={p.max_cost_per_session} onChange={(v) => set({ max_cost_per_session: v })} />
      <NumberField id="max_tokens" label="Tokens per turn" value={p.max_tokens_per_turn} onChange={(v) => set({ max_tokens_per_turn: v })} />
      <NumberField id="stall" label="Stop after this many calls that get nowhere" value={p.stall_iterations} onChange={(v) => set({ stall_iterations: v })} />
      {guard('repetition', 'Same call repeated')}
      {guard('oscillation', 'Calls going back and forth')}
      <NumberField id="approval_timeout" label="Wait for an approval (seconds)" value={p.approval_timeout_s} onChange={(v) => set({ approval_timeout_s: v })} />
      <Field label="If nobody answers" htmlFor="on_timeout">
        <select id="on_timeout" className={input} value={p.on_approval_timeout} onChange={(e) => set({ on_approval_timeout: e.target.value as 'deny' | 'allow'})}> <option value="deny">Deny the call</option> <option value="allow">Allow the call</option> </select> </Field> <p className="text-xs text-ink-2 sm:col-span-2"> Tools that ask first: {p.require_approval.length ? p.require_approval.join(', ') : 'none'} (set on the Tools tab).
      </p>
    </div>
  );
}
