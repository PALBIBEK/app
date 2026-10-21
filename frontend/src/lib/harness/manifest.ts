/**
 * Manifest helpers the builder needs, kept free of React so they are easy to test:
 * where an error belongs, what changed between two versions, export and import.
 */

import { ManifestSchema, SECTIONS, type Manifest, type Section } from '@/lib/api/types/manifest.types';

export type SectionErrors = Partial<Record<Section | 'general', string[]>>;

const isSection = (value: string): value is Section => (SECTIONS as readonly string[]).includes(value);

function add(out: SectionErrors, key: Section | 'general', message: string): void {
  (out[key] ??= []).push(message);
}

/**
 * Sort an API error into the tabs it belongs to.
 *
 * - draft validation (422): `details[].field` is `body.<section>.<field>`; a cross-field
 *   check has field `body` and a message starting with `<section>.`;
 * - a publish refusal: `detail.section` and `detail.message`.
 */
export function errorsBySection(data: unknown): SectionErrors {
  const out: SectionErrors = {};
  if (!data || typeof data !== 'object') return out;
  const body = data as Record<string, unknown>;

  if (Array.isArray(body.details)) {
    for (const d of body.details as { field?: string; message?: string }[]) {
      const message = (d.message ?? 'invalid').replace(/^Value error, /, '');
      const parts = (d.field ?? '').split('.');
      const fromField = parts[0] === 'body' ? parts[1] : undefined;
      const fromMessage = message.split('.')[0];
      const section = fromField && isSection(fromField) ? fromField : isSection(fromMessage) ? fromMessage : undefined;
      const where = fromField && isSection(fromField) ? parts.slice(2).join('.') : '';
      add(out, section ?? 'general', where ? `${where}: ${message}` : message);
    }
    return out;
  }

  const detail = body.detail as Record<string, unknown> | string | undefined;
  if (detail && typeof detail === 'object' && typeof detail.message === 'string') {
    const section = typeof detail.section === 'string' && isSection(detail.section) ? detail.section : 'general';
    add(out, section, detail.message);
  } else if (typeof detail === 'string') {
    add(out, 'general', detail);
  }
  return out;
}

export interface Change {
  path: string;
  before: unknown;
  after: unknown;
}

/** Every leaf that differs, as `section.field` paths (arrays compared whole). */
export function diffManifests(before: Manifest, after: Manifest): Change[] {
  const changes: Change[] = [];
  const walk = (a: unknown, b: unknown, path: string): void => {
    const isObj = (v: unknown) => v !== null && typeof v === 'object' && !Array.isArray(v);
    if (isObj(a) && isObj(b)) {
      const keys = new Set([...Object.keys(a as object), ...Object.keys(b as object)]);
      for (const key of keys) {
        walk((a as Record<string, unknown>)[key], (b as Record<string, unknown>)[key], path ? `${path}.${key}` : key);
      }
      return;
    }
    if (JSON.stringify(a) !== JSON.stringify(b)) changes.push({ path, before: a, after: b });
  };
  walk(before, after, '');
  return changes;
}

const EXPORT_KIND = 'prime-agent/harness-manifest';

export interface ExportedHarness {
  kind: typeof EXPORT_KIND;
  name: string;
  description?: string | null;
  manifest: Manifest;
}

export function exportHarness(name: string, description: string | null | undefined, manifest: Manifest): string {
  const doc: ExportedHarness = { kind: EXPORT_KIND, name, description: description ?? null, manifest };
  return JSON.stringify(doc, null, 2);
}

/** Read an exported file (or a bare manifest); throws with a readable message when it is neither. */
export function parseImport(text: string): Manifest {
  let raw: unknown;
  try {
    raw = JSON.parse(text);
  } catch {
    throw new Error('Not a JSON file');
  }
  const candidate =
    raw && typeof raw === 'object' && 'manifest' in (raw as object) ? (raw as { manifest: unknown }).manifest : raw;
  const parsed = ManifestSchema.safeParse(candidate);
  if (!parsed.success) {
    const issue = parsed.error.issues[0];
    throw new Error(`Not a harness manifest: ${issue.path.join('.') || 'file'} ${issue.message}`);
  }
  return parsed.data;
}
