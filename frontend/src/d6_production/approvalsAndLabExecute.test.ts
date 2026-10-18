/**
 * D6 FULL-PRODUCTION FE gates — Lab execute honesty + approvals BFF paths.
 *
 * Intentional reds OK until production closes. Do not weaken to green MVP.
 */
import { readFileSync, existsSync, readdirSync } from 'node:fs';
import { resolve, join } from 'node:path';
import { describe, expect, it } from 'vitest';

const frontendSrc = resolve(__dirname, '..');
const toolsApi = resolve(frontendSrc, 'lib/api/endpoints/tools.ts');
const sessionsApi = resolve(frontendSrc, 'lib/api/endpoints/sessions.ts');
const backendToolsRoute = resolve(
  frontendSrc,
  '../../backend/prime_agent/web/routes/tools.py'
);

function read(path: string): string {
  expect(existsSync(path), `missing ${path}`).toBe(true);
  return readFileSync(path, 'utf8');
}

function walkTsFiles(dir: string, acc: string[] = []): string[] {
  if (!existsSync(dir)) return acc;
  for (const name of readdirSync(dir, { withFileTypes: true })) {
    const p = join(dir, name.name);
    if (name.isDirectory()) {
      if (name.name === 'node_modules' || name.name === '.next') continue;
      walkTsFiles(p, acc);
    } else if (/\.(ts|tsx)$/.test(name.name) && !name.name.endsWith('.test.ts')) {
      acc.push(p);
    }
  }
  return acc;
}

describe('D6 FULL-PROD Lab execute honesty', () => {
  it('FE toolsAPI.execute only if BE has POST /tools/{id}/execute', () => {
    const fe = read(toolsApi);
    const feClaims =
      /\bexecute\s*:/.test(fe) && /\/tools\/\$\{.*\}\/execute|\/tools\/.*\/execute/.test(fe);

    if (!feClaims) {
      // Honest: no execute claim — optional Lab path deferred.
      expect(feClaims).toBe(false);
      return;
    }

    expect(existsSync(backendToolsRoute), 'BE tools route missing').toBe(true);
    const be = read(backendToolsRoute);
    const beHas =
      /\/execute/.test(be) ||
      /async def execute/.test(be) ||
      /@router\.post\([^\)]*execute/.test(be);
    expect(
      beHas,
      'D6 FULL-PROD: FE claims toolsAPI.execute but BE has no POST …/execute ' +
        '(ship Lab execute with SSRF-at-call OR remove FE execute claim)'
    ).toBe(true);
  });
});

describe('D6 FULL-PROD approvals BFF path from FE', () => {
  it('FE can call BFF approvals list/respond (sessionsAPI or dedicated client)', () => {
    const sessions = existsSync(sessionsApi) ? read(sessionsApi) : '';
    const endpointDir = resolve(frontendSrc, 'lib/api/endpoints');
    const allEndpoints = walkTsFiles(endpointDir)
      .map((p) => readFileSync(p, 'utf8'))
      .join('\n');
    const hooksDir = resolve(frontendSrc, 'lib/api/hooks');
    const allHooks = walkTsFiles(hooksDir)
      .map((p) => readFileSync(p, 'utf8'))
      .join('\n');
    const combined = sessions + '\n' + allEndpoints + '\n' + allHooks;

    const hasList =
      /approvals/.test(combined) &&
      (/listApprovals|list_approvals|getApprovals|pendingApprovals|\/approvals/.test(
        combined
      ) ||
        /sessions\/\$\{.*\}\/approvals|sessions\/:.*\/approvals/.test(combined));

    const hasRespond =
      /respondApproval|resolveApproval|postApproval|\/approvals\/\$\{/.test(combined) ||
      /approvals\/\$\{.*\}/.test(combined);

    expect(
      hasList,
      'D6 FULL-PROD: FE has no BFF approvals list path (sessionsAPI or endpoints)'
    ).toBe(true);
    expect(
      hasRespond,
      'D6 FULL-PROD: FE has no BFF approvals respond path'
    ).toBe(true);

    // Never browser → supervisor approvals.
    expect(combined).not.toMatch(/localhost:8080.*approvals|\/v1\/approvals/);
  });
});
