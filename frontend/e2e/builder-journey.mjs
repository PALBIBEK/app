// Browser journey against ./dev.sh: build a harness, see an error on its tab, publish two
// versions, then chat on the test page, approve bash and see its output.
//
//   npm i --no-save playwright && node e2e/builder-journey.mjs
//   (CHROME=/path/to/chrome to use an installed browser; SHOTS=dir to keep screenshots)
import { chromium } from 'playwright';
import { BASE, EMAIL, PASSWORD, check, deleteHarness } from './lib.mjs';
const dir = process.env.SHOTS;
const shot = (p, n) => (dir ? p.screenshot({ path: `${dir}/${n}.png`, fullPage: true }) : Promise.resolve());
const b = await chromium.launch(process.env.CHROME ? { executablePath: process.env.CHROME } : {});
const p = await b.newPage({ viewport: { width: 1400, height: 1000 } });
p.on('dialog', (d) => d.accept());
const name = 'journey-' + Math.random().toString(36).slice(2, 7);

await p.goto(`${BASE}/login`, { waitUntil: 'networkidle', timeout: 180000 });
await p.fill('input[type=email]', EMAIL);
await p.fill('input[type=password]', PASSWORD);
await p.click('button[type=submit]');
await p.waitForURL(`${BASE}/`, { timeout: 60000 });

await p.goto(`${BASE}/harness/new`, { waitUntil: 'networkidle' });
await p.getByPlaceholder('e.g., coding-assistant-v1').fill(name);
await p.getByRole('button', { name: /Create and configure/ }).click();
await p.waitForURL(/\/harness\/[0-9a-f-]{36}$/, { timeout: 30000 });
const hid = p.url().split('/').pop();
await p.getByTestId('manifest-status').waitFor();
console.log('1 status:', await p.getByTestId('manifest-status').innerText());

// Prompt: add a section.
await p.getByRole('button', { name: 'Add section' }).click();
await p.getByLabel('Section 3 text').fill('Always answer in one sentence.');
// Tools: drop grep; edit asks first.
await p.getByRole('tab', { name: 'Tools' }).click();
await p.locator('#tool-grep').uncheck();
await p.getByLabel('edit needs approval').check();
// Sandbox: host process needs full access for bash (no Docker here).
await p.getByRole('tab', { name: 'Sandbox' }).click();
await p.getByLabel('Host process').check().catch(async () => { await p.getByText('Host process').click(); });
await p.selectOption('#file_effects', 'workspace_write');  // ./dev.sh starts new harnesses with full access
check(await p.getByText(/only runs with “Everything the sandbox can reach”/).isVisible(), 'bash warning on the Sandbox tab');
console.log('2 bash warning shown');
await p.selectOption('#file_effects', 'danger_full_access');
// Policy: an invalid value is shown on its tab.
await p.getByRole('tab', { name: 'Policy' }).click();
await p.fill('#max_steps', '0');
await p.getByRole('button', { name: 'Save draft' }).click();
await p.getByText(/max_steps: Input should be greater/).waitFor({ timeout: 10000 });
console.log('3 error on the Policy tab:', await p.getByText(/max_steps: Input should be greater/).isVisible(), '| tab badge:', await p.getByRole('tab', { name: /Policy/ }).innerText());
await shot(p, 'b1-error');
await p.fill('#max_steps', '12');

await p.getByRole('button', { name: 'Publish' }).click();
await p.getByText('Version 1 published', { exact: false }).first().waitFor({ timeout: 30000 });
console.log('4 status:', await p.getByTestId('manifest-status').innerText());
console.log('  releases:', (await p.getByTestId('releases').innerText()).split('\n').slice(0, 2).join(' | '));
// A second change shows up as changes since version 1.
await p.getByRole('tab', { name: 'Memory' }).click();
await p.getByText('Compact long conversations').click();
console.log('5 changes since v1:', await p.getByTestId('changes').innerText());
await p.getByRole('button', { name: 'Publish' }).click();
await p.getByText('Version 2 published', { exact: false }).first().waitFor({ timeout: 30000 });
const versions = (await p.getByTestId('releases').innerText()).match(/Version \d/g) ?? [];
check(versions.length === 2, `two versions listed (got ${versions})`);
console.log('  versions now:', versions);
await shot(p, 'b2-published');

// Test: run bash through the agent, approve it, see the output.
await p.getByRole('link', { name: 'Test' }).click();
await p.waitForURL(/\/test$/);
await p.getByTestId('connection').filter({ hasText: 'Live' }).waitFor({ timeout: 60000 });
const box = p.getByPlaceholder(/Send a message/);
await box.fill('/tool bash {"command": "echo built-in-the-browser"}');
await box.press('Enter');
await p.getByTestId('approval-approve').first().click({ timeout: 30000 });
await p.getByTestId('tool-output').filter({ hasText: 'built-in-the-browser' }).first().waitFor({ timeout: 30000 });
console.log('6 bash ran: its output is on the tool card');
check(await p.getByText('/tool bash {"command": "echo built-in-the-browser"}', { exact: true }).count() === 1, 'one user bubble for the command');
check(await p.getByTestId('approval-banner').count() === 0, 'approval banner gone after approving');
await shot(p, 'b3-test');
await b.close();
await deleteHarness(hid);
console.log('journey passed');
