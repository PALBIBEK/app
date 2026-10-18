/**
 * D11-E — Team settings product honesty: no mock invite theater.
 */
import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

const root = join(__dirname, '..');

describe('D11 Team settings honesty', () => {
  it('Team page shows not-wired banner — no mockTeamMembers theater', () => {
    const page = readFileSync(join(root, 'app/settings/team/page.tsx'), 'utf8');
    expect(page).not.toMatch(/mockTeamMembers/);
    expect(page).not.toMatch(/Invite Member/);
    expect(page).not.toMatch(/Change Role/);
    expect(page).toMatch(
      /not wired|team-settings-unavailable|scope:admin|workspace:read|product harness/i
    );
  });

  it('Team page distinguishes product roles from agent scope grants', () => {
    const page = readFileSync(join(root, 'app/settings/team/page.tsx'), 'utf8');
    expect(page).toMatch(/not agent workspace scope grants|harness RBAC/i);
    expect(page).toMatch(/sandbox:exec|scope:admin/);
  });
});
