// Browser journey against ./dev.sh: an environment (a shared sandbox setup) is made from a
// preset, a harness picks it in the builder and publishes, its session's sandbox is built as
// the run begins (before any tool asks), and changing the environment shows on the harness.
//
//   npm i --no-save playwright && node e2e/environment-journey.mjs
import { chromium } from 'playwright';
import { API, BASE, EMAIL, PASSWORD, apiHeaders, check, deleteHarness } from './lib.mjs';

const b = await chromium.launch(process.env.CHROME ? { executablePath: process.env.CHROME } : {});
const p = await (await b.newContext({ viewport: { width: 1400, height: 1000 } })).newPage();
p.on('dialog', (d) => d.accept());
const A = await apiHeaders();
const envName = 'Host fast ' + Date.now().toString(36);
const hid = (await (await fetch(`${API}/harnesses`, { method: 'POST', headers: A, body: JSON.stringify({ name: 'env-' + Date.now().toString(36), model: 'gateway/echo', mode: 'sticky' }) })).json()).id;

await p.goto(`${BASE}/login`, { waitUntil: 'networkidle', timeout: 180000 });
await p.fill('input[type=email]', EMAIL);
await p.fill('input[type=password]', PASSWORD);
await p.click('button[type=submit]');
await p.waitForURL(`${BASE}/`);

// 1. Make an environment from a preset.
await p.getByRole('link', { name: 'Environments' }).click();
await p.waitForURL(/\/environments$/);
await p.getByTestId('environment-new').click();
await p.getByRole('button', { name: 'Host process (development)' }).click();
await p.fill('#env-name', envName);
await p.getByTestId('environment-save').click();
const card = p.getByTestId('environment-card').filter({ hasText: envName });
await card.waitFor({ timeout: 15000 });
const settings = await card.getByTestId('environment-settings').innerText();
check(settings.includes('starts with each run') && settings.includes('15 min idle'), `settings shown (${settings})`);
console.log('1 environment made:', settings);

// 2. A harness picks it in the builder: its sandbox fields show the environment's, read only.
await p.goto(`${BASE}/harness/${hid}`, { waitUntil: 'domcontentloaded' });
await p.getByTestId('manifest-status').waitFor({ timeout: 60000 });
await p.getByRole('tab', { name: 'Sandbox' }).click();
await p.getByTestId('environment-picker').selectOption({ label: envName });
await p.getByTestId('environment-summary').filter({ hasText: envName }).waitFor();
check(await p.locator('#file_effects').isDisabled(), 'the environment sets the sandbox (fields read only)');
await p.getByRole('button', { name: 'Publish' }).click();
await p.getByText('Version 1 published', { exact: false }).first().waitFor({ timeout: 30000 });
console.log('2 harness uses it and published');

// 3. Its session's sandbox starts with the run: ready after a reply that ran no tool.
await p.goto(`${BASE}/harness/${hid}/test`, { waitUntil: 'domcontentloaded' });
await p.getByTestId('connection').filter({ hasText: 'Live' }).waitFor({ timeout: 90000 });
const box = p.getByPlaceholder(/Send a message|Send another/);
await box.fill('hello');
await box.press('Enter');
await p.getByText('echo:hello').waitFor({ timeout: 30000 });
await p.getByTestId('sandbox-status').filter({ hasText: 'started with the run' }).waitFor({ timeout: 30000 });
console.log('3 session bar:', await p.getByTestId('sandbox-status').innerText());

// 4. The environments page lists the harness; a change asks for a republish; in use, no delete.
await p.goto(`${BASE}/environments`, { waitUntil: 'domcontentloaded' });
await card.getByTestId('environment-users').getByText('published').waitFor({ timeout: 15000 });
await card.getByRole('button', { name: `Edit ${envName}` }).click();
await p.getByTestId('environment-form').locator('#env-idle_stop_s').selectOption('0');
await p.getByTestId('environment-save').click();
await card.getByText('never stops when idle').waitFor({ timeout: 15000 });
await card.getByText('republish to apply').waitFor({ timeout: 15000 });
console.log('4 changed: the harness asks for a republish');
await card.getByRole('button', { name: `Delete ${envName}` }).click();
await p.getByText(/harness\(es\) use this environment/).first().waitFor({ timeout: 15000 });
console.log('5 delete refused while in use');

await deleteHarness(hid);
const envs = await (await fetch(`${API}/environments`, { headers: A })).json();
const mine = envs.find((e) => e.name === envName);
const gone = await fetch(`${API}/environments/${mine.id}`, { method: 'DELETE', headers: A });
check(gone.status === 204, `deleted once unused (${gone.status})`);
await b.close();
console.log('environment journey passed');
