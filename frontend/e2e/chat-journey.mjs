// Browser journey against ./dev.sh: chat with a published harness: reply, approval countdown, a queued message, a denied call explained, a backend crash survived without duplicates, fork, end.
//
//   npm i --no-save playwright && node e2e/chat-journey.mjs
//   (CHROME=/path/to/chrome for an installed browser; BASE_URL, API_URL, DEMO_EMAIL, DEMO_PASSWORD to override)
import { chromium } from 'playwright';
import { execSync } from 'node:child_process';
import { API, BASE, EMAIL, PASSWORD, apiHeaders, check, deleteHarness } from './lib.mjs';
const shot = () => Promise.resolve();
const b = await chromium.launch(process.env.CHROME ? { executablePath: process.env.CHROME } : {});
const ctx = await b.newContext({ viewport: { width: 1400, height: 1100 } });
const p = await ctx.newPage();
p.on('dialog', (d) => d.accept());

// A published harness to chat with (host sandbox: bash needs full access here).
const A = await apiHeaders();
const hid = (await (await fetch(`${API}/harnesses`, { method: 'POST', headers: A, body: JSON.stringify({ name: 'chat-' + Date.now().toString(36), model: 'gateway/echo', mode: 'sticky' }) })).json()).id;
const m = (await (await fetch(`${API}/harnesses/${hid}/manifest`, { headers: A })).json()).manifest;
m.sandbox.provider = 'local_process'; m.sandbox.file_effects = 'danger_full_access'; m.policy.max_steps = 9;
await fetch(`${API}/harnesses/${hid}/manifest`, { method: 'PUT', headers: A, body: JSON.stringify(m) });
const published = await fetch(`${API}/harnesses/${hid}/publish`, { method: 'POST', headers: A });
check(published.ok, `publish (${published.status})`);

await p.goto(`${BASE}/login`, { waitUntil: 'networkidle', timeout: 180000 });
await p.fill('input[type=email]', EMAIL);
await p.fill('input[type=password]', PASSWORD);
await p.click('button[type=submit]');
await p.waitForURL(`${BASE}/`);
await p.goto(`${BASE}/harness/${hid}/test`, { waitUntil: 'domcontentloaded' });
await p.getByTestId('connection').filter({ hasText: 'Live' }).waitFor({ timeout: 60000 });
const box = p.getByPlaceholder(/Send a message|Send another/);

await box.fill('hello there');
await box.press('Enter');
await p.getByText('echo:hello there').waitFor({ timeout: 30000 });
console.log('1 reply shown; bar:', (await p.getByTestId('session-bar').innerText()).replace(/\n/g, ' | '));

await box.fill('/tool bash {"command": "echo from-chat > chat.txt && cat chat.txt"}');
await box.press('Enter');
await p.getByTestId('approval-banner').waitFor({ timeout: 30000 });
console.log('2 approval countdown:', await p.getByTestId('approval-countdown').innerText());
await p.getByPlaceholder(/Send another/).fill('and after that, this');
await p.getByPlaceholder(/Send another/).press('Enter');
await p.getByTestId('queued').waitFor({ timeout: 10000 });
console.log('3 queued badge:', await p.getByTestId('queued').innerText());
await shot(p, 'c1-approval');
await p.getByTestId('approval-deny').click();
await p.getByTestId('tool-explanation').first().waitFor({ timeout: 30000 });
console.log('4 refusal explained:', await p.getByTestId('tool-explanation').first().innerText());
await p.getByText('echo:and after that, this').waitFor({ timeout: 30000 });
check(await p.getByTestId('queued').count() === 0, 'no queued badge left once it ran');
console.log('5 queued message ran');

// Drop the connection and come back: no duplicates, nothing lost.
const before = await p.locator('text=echo:hello there').count();
// Kill the backend (as a crash would) and start it again with ./dev.sh: the stream
// really drops, and must come back on its own. Needs lsof and this repo's dev.sh.
execSync(`kill -KILL $(lsof -t -iTCP:8000 -sTCP:LISTEN); sleep 1; rm -f ${new URL('../../.dev/run/backend.pid', import.meta.url).pathname}`, { shell: '/bin/bash' });
await p.getByTestId('connection').filter({ hasText: 'Reconnecting' }).waitFor({ timeout: 30000 });
console.log('6 shows Reconnecting while the backend is down');
execSync(`cd ${new URL('../..', import.meta.url).pathname} && ./dev.sh >/dev/null 2>&1`, { timeout: 300000, shell: '/bin/bash', env: process.env });
await p.getByTestId('connection').filter({ hasText: 'Live' }).waitFor({ timeout: 60000 });
await p.waitForTimeout(1500);  // let a replay land, if it were going to duplicate
const dupes = (await p.locator('text=echo:hello there').count()) - before;
check(dupes === 0, `no duplicated messages after reconnect (got ${dupes})`);
console.log('7 live again; no duplicates');
await shot(p, 'c2-after');

await p.getByRole('button', { name: 'Fork', exact: true }).click();
await p.waitForURL(/\/sessions\/[0-9a-f-]+$/, { timeout: 30000 });
await p.getByText('echo:hello there').waitFor({ timeout: 30000 });
console.log('8 fork opened with the conversation:', p.url().split('/').pop().slice(0, 8));
await p.getByRole('button', { name: 'End', exact: true }).click();
await p.getByTestId('connection').filter({ hasText: 'Ended' }).waitFor({ timeout: 10000 });
check(await p.locator('textarea').isDisabled(), 'composer disabled after End');
console.log('9 ended; composer disabled');
await shot(p, 'c3-forked-ended');
await b.close();
await deleteHarness(hid);
console.log('chat journey passed');
