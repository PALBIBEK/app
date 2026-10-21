/**
 * D9 — Test harness live agent /workspace upload (G6) + naming honesty.
 */
import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

const root = join(__dirname, '..');

function read(rel: string): string {
  return readFileSync(join(root, rel), 'utf8');
}

describe('D9 Test agent /workspace upload (live BFF)', () => {
  it('Test page creates the scope and shows its workspace panel (BFF)', () => {
    const page = read('app/harness/[id]/test/page.tsx');
    expect(page).toMatch(/workspaceAPI\.ensureScope/);
    expect(page).toMatch(/scope_id/);
    expect(page).toMatch(/<WorkspacePanel/);
    const panel = read('components/session/WorkspacePanel.tsx');
    for (const call of ['uploadFile', 'listFiles', 'getFile', 'deleteFile']) expect(panel).toMatch(new RegExp(`workspaceAPI\\.${call}`));
    expect(panel).toMatch(/agent-workspace-upload/);
    for (const src of [page, panel]) {
      expect(src).not.toMatch(/localhost:8080.*scopes|\/v1\/scopes/);
      expect(src).not.toMatch(/mockWorkspace|fakeUpload|not shipped/i);
    }
  });

  it('a change to the files reloads the live session after the durable write', () => {
    const panel = read('components/session/WorkspacePanel.tsx');
    const run = panel.slice(panel.indexOf('async function run('));
    expect(run.indexOf('await action()')).toBeGreaterThan(-1);
    expect(run.indexOf('workspaceAPI.rehydrateSession')).toBeGreaterThan(run.indexOf('await action()'));
    // Uploads go straight to storage (start → PUT → complete), the API path only as a fallback.
    expect(panel).toMatch(/uploadToWorkspace\(/);
    for (const call of ['startUpload', 'completeUpload', 'uploadFile']) expect(panel).toMatch(new RegExp(`workspaceAPI\\.${call}`));
    expect(panel).toMatch(/`Uploaded \$\{file\.name\}`,\s*true/);
  });

  it('sessionsAPI create forwards scope_id', () => {
    const api = read('lib/api/endpoints/sessions.ts');
    expect(api).toMatch(/scope_id/);
  });

  it('workspaceAPI talks only to product BFF /workspace', () => {
    const api = read('lib/api/endpoints/workspace.ts');
    expect(api).toMatch(/\/workspace\/scopes\//);
    expect(api).toMatch(/ensureScope/);
    expect(api).toMatch(/uploadFile/);
    expect(api).not.toMatch(/localhost:8080|http:\/\/.*supervisor/);
  });

  it('Settings org chrome ≠ agent /workspace labels', () => {
    const settings = read('app/settings/page.tsx');
    expect(settings).toMatch(/Org defaults|Org runtime|org chrome|not the agent/i);
    expect(settings).toMatch(/\/workspace/);
    const sidebar = read('components/layout/Sidebar.tsx');
    expect(sidebar).not.toMatch(/>\s*Workspace\s*</);
  });
});
