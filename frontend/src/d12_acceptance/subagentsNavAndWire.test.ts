/**
 * D12 §5 B — a session's subagents, as people see them, through the BFF only.
 *
 * Subagents are sessions the agent starts with its own tool calls; the product never spawns
 * one. A person finds them where they work: in the conversation (the call that started one,
 * with its task and answer, and what it told the agent later) and in the session's rail (every
 * subagent below the session), and reads one's own conversation on its page.
 */
import { describe, expect, it } from 'vitest';
import { existsSync, readFileSync, readdirSync, statSync } from 'node:fs';
import { join } from 'node:path';

const root = join(__dirname, '..');

function read(rel: string): string {
  return readFileSync(join(root, rel), 'utf8');
}

function collectProductTsFiles(dir: string, out: string[] = []): string[] {
  if (!existsSync(dir)) return out;
  for (const name of readdirSync(dir)) {
    // Acceptance/unit tests may mention banned strings in negative asserts; skip them.
    if (name === 'node_modules' || name === '.next' || name === 'test' || /_acceptance$/.test(name) || /_production$/.test(name)) {
      continue;
    }
    const full = join(dir, name);
    const st = statSync(full);
    if (st.isDirectory()) collectProductTsFiles(full, out);
    else if (/\.(ts|tsx)$/.test(name) && !name.endsWith('.d.ts') && !/\.(test|spec)\.(ts|tsx)$/.test(name)) out.push(full);
  }
  return out;
}

const SUBAGENT_UI = [
  'components/session/subagents.tsx',
  'components/session/SessionChat.tsx',
  'app/sessions/[id]/subagents/[subagentId]/page.tsx',
  'lib/session/subagents.ts',
];

describe('D12 subagents: where people find them, and BFF-only', () => {
  it('the session shows its subagents in the conversation and the rail, and each has a page', () => {
    const chat = read('components/session/SessionChat.tsx');
    expect(chat).toMatch(/SubagentCallCard/);
    expect(chat).toMatch(/SubagentNoticeLine/);
    expect(chat).toMatch(/SubagentsSection/);
    expect(existsSync(join(root, 'app/sessions/[id]/subagents/[subagentId]/page.tsx'))).toBe(true);
  });

  it('the reducer follows the calls that start subagents and what they tell the agent', () => {
    const reducer = read('lib/session/wireEventReducer.ts');
    expect(reducer).toMatch(/case\s+['"]subagent_start['"]/);
    expect(reducer).toMatch(/case\s+['"]subagent_notice['"]/);
  });

  it('sessionsAPI reads, messages and stops subagents through the BFF, and spawns none', () => {
    const api = read('lib/api/endpoints/sessions.ts');
    expect(api).toMatch(/\/sessions\/\$\{[^}]+\}\/subagents`/);
    expect(api).toMatch(/\/subagents\/\$\{[^}]+\}\/messages/);
    expect(api).toMatch(/\/subagents\/\$\{[^}]+\}\/abort/);
    expect(api).toMatch(/\/subagents\/\$\{[^}]+\}\/events/);
    expect(api).not.toMatch(/spawn|\/children/);
  });

  it('nothing in the product spawns a subagent or shows the retired echo stub', () => {
    const offenders: string[] = [];
    for (const file of collectProductTsFiles(root)) {
      const src = readFileSync(file, 'utf8');
      if (/spawnSubagent|subagents\/spawn|lab_echo|listChildren/.test(src)) offenders.push(file.replace(root + '/', ''));
    }
    expect(offenders).toEqual([]);
  });

  it('the subagent UI shows real data: no mock theater, no claims about memory', () => {
    for (const rel of SUBAGENT_UI) {
      const src = read(rel);
      expect(src, rel).not.toMatch(/\bmockSubagents?\b|\bmockParentSessions\b/);
      expect(src, rel).not.toMatch(/long[- ]term memory|\bLTM\b/i);
    }
  });

  it('frontend product src never hits localhost:8080 or supervisor /v1/sessions/*/subagents', () => {
    const offenders: string[] = [];
    for (const file of collectProductTsFiles(root)) {
      const src = readFileSync(file, 'utf8');
      if (/localhost:8080/.test(src) || /\/v1\/sessions\/[^`'"]*\/subagents/.test(src)) offenders.push(file.replace(root + '/', ''));
    }
    expect(offenders, `browser→supervisor banned: ${offenders.join(', ')}`).toEqual([]);
  });
});
