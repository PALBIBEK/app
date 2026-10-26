// The new pages against ./dev.sh: a run waits on an approval, which the Approvals page lists
// and answers; the session's sandbox shows on Sandboxes; its calls show on Usage; a secret
// and a runner are added and removed; the composer's checks run without publishing.
//
//   npm i --no-save playwright && node e2e/operate-journey.mjs
import { chromium } from 'playwright';
import { API, BASE, EMAIL, PASSWORD, apiHeaders, check, deleteHarness } from './lib.mjs';

const b = await chromium.launch(process.env.CHROME ? { executablePath: process.env.CHROME } : {});
const p = await (await b.newContext({ viewport: { width: 1440, height: 900 } })).newPage();
p.on('dialog', (d) => d.accept());
const A = await apiHeaders();

const name = 'ops-' + Date.now().toString(36);
const hid = (await (await fetch(`${API}/harnesses`, { method: 'POST', headers: A, body: JSON.stringify({ name, model: 'gateway/echo', mode: 'sticky' }) })).json()).id;
const m = (await (await fetch(`${API}/harnesses/${hid}/manifest`, { headers: A })).json()).manifest;
m.sandbox.provider = 'local_process'; m.sandbox.file_effects = 'danger_full_access';
await fetch(`${API}/harnesses/${hid}/manifest`, { method: 'PUT', headers: A, body: JSON.stringify(m) });
check((await fetch(`${API}/harnesses/${hid}/publish`, { method: 'POST', headers: A })).ok, 'publish');

await p.goto(`${BASE}/login`, { waitUntil: 'networkidle', timeout: 180000 });
await p.fill('input[type=email]', EMAIL);
await p.fill('input[type=password]', PASSWORD);
await p.click('button[type=submit]');
await p.waitForURL(`${BASE}/`);

// A run that runs a command (so the sandbox starts), then one that waits on an approval.
await p.goto(`${BASE}/harness/${hid}/test`);
await p.getByTestId('connection').filter({ hasText: 'Live' }).waitFor({ timeout: 90000 });
const box = p.getByPlaceholder(/Send a message|Send another/);
await box.fill('/tool bash {"command": "echo first"}');
await box.press('Enter');
await p.getByTestId('approval-approve').first().click({ timeout: 30000 });
await p.getByTestId('tool-output').filter({ hasText: 'first' }).first().waitFor({ timeout: 30000 });
await box.fill('/tool bash {"command": "echo second"}');
await box.press('Enter');
await p.getByTestId('approval-banner').waitFor({ timeout: 30000 });
await p.getByTestId('rail-sandbox').first().waitFor({ timeout: 30000 });
console.log('1 rail sandbox:', await p.getByTestId('rail-sandbox').first().getAttribute('data-state'));

// Approvals: listed with the session, answered from the page.
await p.goto(`${BASE}/approvals`);
const item = p.getByTestId('inbox-item').filter({ hasText: name });
await item.waitFor({ timeout: 30000 });
await item.getByRole('button', { name: /^(allow|approve|yes)$/i }).click();
await item.waitFor({ state: 'detached', timeout: 30000 });
console.log('2 approval answered from the inbox');

// Sandboxes: the session's sandbox is listed.
await p.goto(`${BASE}/sandboxes`);
await p.getByTestId('sandbox-row').first().waitFor({ timeout: 30000 });
check(await p.getByText(`${name} session`).count() >= 1, 'the sandbox names its session');
console.log('3 sandbox listed');

// Usage: the agent's calls are counted.
await p.goto(`${BASE}/usage`);
await p.getByRole('cell', { name, exact: true }).waitFor({ timeout: 30000 });
console.log('4 usage by agent shows it');

// Secrets: add, see the name only, delete.
const secret = 'E2E_' + Date.now().toString(36).toUpperCase();
await p.goto(`${BASE}/policies`);
await p.getByLabel('Name').fill(secret);
await p.getByLabel('Value').fill('s3cret-value');
await p.getByRole('button', { name: 'Save secret' }).click();
await p.getByTestId('secret-names').getByText(secret).waitFor({ timeout: 15000 });
check(await p.getByText('s3cret-value').count() === 0, 'the value is never shown');
await p.getByRole('button', { name: `Delete ${secret}` }).click();
await p.getByTestId('secret-names').getByText(secret).waitFor({ state: 'detached', timeout: 15000 }).catch(() => {});
check(await p.getByTestId('secret-names').getByText(secret).count() === 0, 'the secret is gone');
console.log('5 secret added and deleted');

// Runners: register (token shown once), then remove.
const runner = 'e2e-' + Date.now().toString(36);
await p.goto(`${BASE}/runners`);
await p.getByLabel('Name').fill(runner);
await p.getByRole('button', { name: 'Register' }).click();
await p.getByText(`Token for ${runner}`).waitFor({ timeout: 15000 });
await p.getByRole('button', { name: 'I have saved it' }).click();
await p.getByRole('button', { name: `Remove ${runner}` }).click();
await p.getByText(runner, { exact: true }).waitFor({ state: 'detached', timeout: 15000 });
console.log('6 runner registered and removed');

// Composer: the checks run and publish nothing.
const before = (await (await fetch(`${API}/harnesses/${hid}/releases`, { headers: A })).json()).length;
await p.goto(`${BASE}/harness/${hid}`);
await p.getByRole('button', { name: 'Check' }).click();
await p.getByTestId('publish-checks').getByText(/All pass|to fix/).waitFor({ timeout: 30000 });
const after = (await (await fetch(`${API}/harnesses/${hid}/releases`, { headers: A })).json()).length;
check(before === after, 'checking publishes nothing');
console.log('7 checks:', (await p.getByTestId('publish-checks').innerText()).split('\n').slice(0, 2).join(' | '));

// Every new page opens without an error.
for (const path of ['overview', 'files', 'artifacts', 'fleet']) {
  await p.goto(`${BASE}/${path}`);
  await p.getByRole('heading', { level: 1 }).waitFor({ timeout: 30000 });
}
console.log('8 overview, files, artifacts and fleet open');

await deleteHarness(hid);
await b.close();
console.log('operate journey passed');
