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
  it('Test page wires workspaceAPI ensure/list/upload (BFF)', () => {
    const page = read('app/harness/[id]/test/page.tsx');
    expect(page).toMatch(/workspaceAPI/);
    expect(page).toMatch(/ensureScope/);
    expect(page).toMatch(/uploadFile/);
    expect(page).toMatch(/listFiles/);
    expect(page).toMatch(/agent-workspace-upload/);
    expect(page).toMatch(/Upload to \/workspace/);
    expect(page).toMatch(/scope_id/);
    expect(page).not.toMatch(/localhost:8080.*scopes|\/v1\/scopes/);
    expect(page).not.toMatch(/mockWorkspace|fakeUpload|not shipped/i);
  });

  it('upload handler rehydrates live session after durable put', () => {
    const page = read('app/harness/[id]/test/page.tsx');
    expect(page).toMatch(/handleWorkspaceUpload/);
    expect(page).toMatch(/workspaceAPI\.uploadFile/);
    expect(page).toMatch(/workspaceAPI\.rehydrateSession/);
    const uploadBlock = page.slice(
      page.indexOf('handleWorkspaceUpload'),
      page.indexOf('[scopeId, session?.id, refreshWorkspaceFiles]') + 60
    );
    const uploadIdx = uploadBlock.indexOf('workspaceAPI.uploadFile');
    const rehydrateIdx = uploadBlock.indexOf('workspaceAPI.rehydrateSession');
    expect(uploadIdx).toBeGreaterThan(-1);
    expect(rehydrateIdx).toBeGreaterThan(uploadIdx);
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
