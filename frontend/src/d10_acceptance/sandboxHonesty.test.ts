/**
 * D10-F — sandbox product honesty: no fake sandbox chrome; readiness banner optional.
 */
import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

const root = join(__dirname, '..');

function read(rel: string): string {
  return readFileSync(join(root, rel), 'utf8');
}

describe('D10 sandbox honesty (no fake chrome)', () => {
  it('Test page does not claim container isolation without wiring', () => {
    const page = read('app/harness/[id]/test/page.tsx');
    expect(page).not.toMatch(/mockSandbox|fakeSandbox|sandboxProvider.*mock/i);
    expect(page).not.toMatch(/>\s*Docker\s*Container\s*</);
    expect(page).not.toMatch(/>\s*E2B\s*Sandbox\s*</);
  });

  it('Harness detail may show composition pins honestly (no fabricated provider UI)', () => {
    const detail = read('app/harness/[id]/page.tsx');
    expect(detail).not.toMatch(/mockSandbox|fake.*sandbox|not shipped.*sandbox profile/i);
  });

  it('platform status banner includes optional sandbox readiness reason', () => {
    const banner = read('components/platform/AiPlatformBanner.tsx');
    expect(banner).toMatch(/sandbox/);
    expect(banner).toMatch(/REASON_COPY/);
    expect(banner).not.toMatch(/mockSandbox|fakeReady/i);
  });

  it('frontend never talks to Docker/E2B directly (control plane via BFF)', () => {
    const apiDir = join(root, 'lib', 'api');
    const combined = ['endpoints', 'hooks']
      .flatMap((sub) => {
        try {
          const { readdirSync } = require('node:fs') as typeof import('node:fs');
          return readdirSync(join(apiDir, sub))
            .filter((f: string) => f.endsWith('.ts') || f.endsWith('.tsx'))
            .map((f: string) => read(`lib/api/${sub}/${f}`));
        } catch {
          return [];
        }
      })
      .join('\n');
    expect(combined).not.toMatch(/docker\.from_env|e2b\.|AsyncSandbox/);
    expect(combined).not.toMatch(/localhost:2375|docker\.sock/);
  });
});
