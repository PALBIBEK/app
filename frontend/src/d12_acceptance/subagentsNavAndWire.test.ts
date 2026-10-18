/**
 * D12 §5 B / LOCAL_DEMO #4 — discoverability + wire chips + no browser→supervisor.
 *
 * Prefer live path for local demo: applyWireEvent must handle subagent_start / subagent_end.
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
    if (
      name === 'node_modules' ||
      name === '.next' ||
      name === 'test' ||
      /_acceptance$/.test(name) ||
      /_production$/.test(name)
    ) {
      continue;
    }
    const full = join(dir, name);
    let st;
    try {
      st = statSync(full);
    } catch {
      continue;
    }
    if (st.isDirectory()) {
      collectProductTsFiles(full, out);
    } else if (
      /\.(ts|tsx)$/.test(name) &&
      !name.endsWith('.d.ts') &&
      !/\.(test|spec)\.(ts|tsx)$/.test(name)
    ) {
      out.push(full);
    }
  }
  return out;
}

describe('D12 Subagents discoverability + wire + BFF-only', () => {
  it('Sidebar/nav OR Test page links to Subagents or spawn', () => {
    const sidebar = read('components/layout/Sidebar.tsx');
    const testPage = read('app/harness/[id]/test/page.tsx');
    const headerExists = existsSync(join(root, 'components/layout/Header.tsx'));
    const header = headerExists ? read('components/layout/Header.tsx') : '';

    const navDiscovers =
      /href=["']\/subagents|href=["']\/subagents\/spawn|label:\s*['"]Subagents['"]|Subagents/.test(
        sidebar
      ) ||
      /href=["']\/subagents|\/subagents\/spawn|Spawn subagent|Subagents/.test(header);

    const testDiscovers =
      /\/subagents|spawnSubagent|Spawn subagent|subagents\/spawn/i.test(testPage);

    expect(
      navDiscovers || testDiscovers,
      'Sidebar/Header must link Subagents|/subagents|/subagents/spawn, or Test page must surface spawn/subagents'
    ).toBe(true);
  });

  it('applyWireEvent handles subagent_start and subagent_end (live local-demo chips)', () => {
    const reducer = read('lib/session/wireEventReducer.ts');
    expect(reducer).toMatch(/subagent_start/);
    expect(reducer).toMatch(/subagent_end/);
    // Case arms (not only a comment) — require switch cases.
    expect(reducer).toMatch(/case\s+['"]subagent_start['"]/);
    expect(reducer).toMatch(/case\s+['"]subagent_end['"]/);
  });

  it('sessionsAPI exposes BFF children/spawn when live path is chosen', () => {
    const api = read('lib/api/endpoints/sessions.ts');
    const subagentsApiPath = join(root, 'lib/api/endpoints/subagents.ts');
    const subagentsApi = existsSync(subagentsApiPath)
      ? readFileSync(subagentsApiPath, 'utf8')
      : '';
    const pages = ['page.tsx', '[id]/page.tsx', 'spawn/page.tsx']
      .map((rel) => {
        const p = join(root, 'app/subagents', rel);
        return existsSync(p) ? readFileSync(p, 'utf8') : '';
      })
      .join('\n');

    const unavailable =
      /subagents-unavailable|SubagentsUnavailable|not wired|not available yet|subagents are not wired/i.test(
        pages
      );
    const liveUi =
      /sessionsAPI\.(listChildren|spawnSubagent|spawn|listSubagents)|subagentsAPI|useSubagents|useSpawnSubagent/.test(
        pages
      ) ||
      /\/sessions\/\$\{[^}]+\}\/(children|subagents)/.test(pages);

    expect(
      liveUi || unavailable,
      'Subagents pages must choose live BFF or unavailable before BFF method checks apply'
    ).toBe(true);

    if (unavailable && !liveUi) {
      // Honest disabled path: BFF children/spawn optional until wired.
      return;
    }

    // Live UI requires BFF client surface (never supervisor host).
    const hasBff =
      /listChildren|spawnSubagent|listSubagents|\.spawn\b/.test(api) ||
      /listChildren|spawnSubagent|listSubagents|\.spawn\b/.test(subagentsApi) ||
      /\/sessions\/\$\{[^}]+\}\/(children|subagents)/.test(api + subagentsApi);

    expect(
      hasBff,
      'live Subagents UI requires sessionsAPI/subagentsAPI children+spawn via product BFF'
    ).toBe(true);
    expect(api + subagentsApi).not.toMatch(/localhost:8080/);
    expect(api + subagentsApi).not.toMatch(/\/v1\/sessions\/.*\/subagents/);
  });

  it('frontend product src never hits localhost:8080 or supervisor /v1/sessions/*/subagents', () => {
    const files = collectProductTsFiles(root);
    const offenders: string[] = [];
    for (const file of files) {
      const src = readFileSync(file, 'utf8');
      if (/localhost:8080/.test(src) || /\/v1\/sessions\/[^`'"]*\/subagents/.test(src)) {
        offenders.push(file.replace(root + '/', ''));
      }
    }
    expect(offenders, `browser→supervisor banned: ${offenders.join(', ')}`).toEqual(
      []
    );
  });
});
