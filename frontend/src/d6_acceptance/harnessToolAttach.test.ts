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
  it('Test page can invoke via sessionsAPI.invokeTool (BFF, not supervisor)', () => {
    const test = read('app/harness/[id]/test/page.tsx');
    expect(test).toMatch(/sessionsAPI\.invokeTool/);
    expect(test).not.toMatch(/localhost:8080/);
    expect(test).not.toMatch(/\/v1\/sessions\/.*\/tools\/invoke/);
  });
});
