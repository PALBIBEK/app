/**
 * D6 / LOCAL_DEMO #3 — harness tool attach UI honesty.
 * Product must PUT/POST harness.tools: [uuid…] (not informational chrome).
 */
import { readFileSync, existsSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

const srcRoot = resolve(__dirname, '..');

function read(rel: string): string {
  const path = resolve(srcRoot, rel);
  expect(existsSync(path), `missing ${path}`).toBe(true);
  return readFileSync(path, 'utf8');
}

describe('D6 harness tool attach (LOCAL_DEMO #3)', () => {
  it('harness detail attaches tool UUIDs via updateHarness tools: […]', () => {
    const detail = read('app/harness/[id]/page.tsx');
    expect(detail).toMatch(/useUpdateHarness|updateHarness/);
    expect(detail).toMatch(/tools:\s*selectedToolIds|tools:\s*\[/);
    expect(detail).toMatch(/harness-tools-attach|Attached tools/);
    expect(detail).toMatch(/useTools/);
    // Must not claim tools are informational-only theater.
    expect(detail).not.toMatch(/Tools\/memory\/workspace chrome stays informational/);
  });

  it('harness create can include tools: selectedToolIds (not hard-coded [])', () => {
    const create = read('app/harness/new/page.tsx');
    expect(create).toMatch(/tools:\s*selectedToolIds/);
    expect(create).toMatch(/useTools/);
  });

  it('/tools/new copy points at harness Attached tools UI', () => {
    const neu = read('app/tools/new/page.tsx');
    expect(neu).toMatch(/Attached tools|harness detail/i);
    expect(neu).not.toMatch(/TODO:\s*wire.*attach/i);
  });

  it('Test page can invoke via sessionsAPI.invokeTool (BFF, not supervisor)', () => {
    const test = read('app/harness/[id]/test/page.tsx');
    expect(test).toMatch(/sessionsAPI\.invokeTool/);
    expect(test).not.toMatch(/localhost:8080/);
    expect(test).not.toMatch(/\/v1\/sessions\/.*\/tools\/invoke/);
  });
});
