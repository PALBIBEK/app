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

// Upload a file: straight to storage over a signed URL, then the agent reads it.
const marker = 'uploaded-' + Date.now().toString(36);  // new bytes, so a real upload (no dedupe)
const directPuts = [];
p.on('request', (r) => { if (r.method() === 'PUT' && r.url().includes('/uploads/')) directPuts.push(r.url()); });
await p.getByTestId('agent-workspace-upload').setInputFiles({ name: 'data.txt', mimeType: 'text/plain', buffer: Buffer.from(marker + '\n') });
await p.getByTestId('agent-workspace-file-list').getByText('data.txt', { exact: true }).waitFor({ timeout: 30000 });
check(directPuts.length === 1 && !directPuts[0].includes(':8000'), `the file went straight to storage (${directPuts})`);
await box.fill('/tool bash {"command": "cat data.txt"}');
await box.press('Enter');
await p.getByTestId('approval-approve').first().click({ timeout: 30000 });
await p.getByTestId('tool-output').filter({ hasText: marker }).first().waitFor({ timeout: 30000 });
console.log('1b uploaded straight to storage; the agent read it');

// A second upload of the same path is version 2; its history can restore version 1.
await p.getByTestId('agent-workspace-upload').setInputFiles({ name: 'data.txt', mimeType: 'text/plain', buffer: Buffer.from('replaced\n') });
await p.getByTestId('agent-workspace-file-list').getByText('v2 · ', { exact: false }).waitFor({ timeout: 30000 });
await p.getByRole('button', { name: 'History of data.txt' }).click();
await p.getByTestId('file-history').getByRole('button', { name: 'Restore version 1' }).click();
await p.getByTestId('file-history').getByText('restored from version 1').waitFor({ timeout: 30000 });
check((await p.getByTestId('file-history').innerText()).includes('Version 3'), 'restore made version 3');
await p.getByRole('button', { name: 'View data.txt' }).click();
await p.getByTestId('file-preview').filter({ hasText: marker }).waitFor({ timeout: 15000 });
console.log('1c version history: restored version 1 as version 3');

// Attach a file to the conversation (pinned, read-only for the agent), have the agent read it,
// write an output (collected as an artifact when the run ends), and save that to the workspace.
const conv = p.getByTestId('conversation-files');
await p.getByTestId('attach-upload').setInputFiles({ name: 'sales.csv', mimeType: 'text/csv', buffer: Buffer.from(`q4,${marker}\n`) });
await conv.getByTestId('attachments').getByText('sales.csv').waitFor({ timeout: 30000 });
await box.fill('/tool read {"file_path": ".attachments/sales.csv"}');
await box.press('Enter');
await p.getByText(`q4,${marker}`, { exact: false }).first().waitFor({ timeout: 30000 });
await box.fill(`/tool write {"file_path": ".outputs/report.md", "content": "report ${marker}"}`);
await box.press('Enter');
await conv.getByTestId('artifacts').getByText('report.md').waitFor({ timeout: 30000 });
await conv.getByRole('button', { name: 'Save report.md to the workspace' }).click();
await conv.getByLabel('Save as path').fill('Reports/report.md');
await conv.getByRole('button', { name: 'Save', exact: true }).click();
await conv.getByTestId('artifact-saved').waitFor({ timeout: 30000 });
check(!(await p.getByTestId('agent-workspace-file-list').innerText()).includes('.outputs'), 'session folders stay out of the workspace');
console.log('1d attached, read, output collected and saved to Reports/report.md');

// The agent's own edit to a workspace file is a version marked as the agent's.
await box.fill('/tool write {"file_path": "agent-note.txt", "content": "from the agent"}');
await box.press('Enter');
await p.getByTestId('agent-workspace-file-list').getByText('agent-note.txt', { exact: true }).waitFor({ timeout: 30000 });
await p.getByRole('button', { name: 'History of agent-note.txt' }).click();
await p.getByTestId('file-history').getByText('by the agent').waitFor({ timeout: 15000 });
console.log('1e the agent\'s edit is in the history as the agent\'s');

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
await p.getByTestId('queued').first().waitFor({ state: 'detached', timeout: 15000 }).catch(() => {});
check(await p.getByTestId('queued').count() === 0, 'no queued badge left once it ran');
console.log('5 queued message ran');

// The rail names what the session runs: the published version and each pinned part.
await p.getByTestId('pinned-card').waitFor({ timeout: 15000 });
const roles = await p.getByTestId('pinned-part').evaluateAll((els) => els.map((e) => e.dataset.role));
check(roles.includes('tools') && roles.includes('hand:default'), `runs-on lists its parts (${roles})`);
check((await p.getByTestId('pinned-card').innerText()).includes('version 1'), 'runs-on names the agent version');
await shot(p, 'c1b-runs-on');
console.log('5a runs on:', roles.join(', '));

// Runs by id: a queued message is cancelled before it runs; Stop cancels the run going now (by
// its id, safe to repeat); the runs panel shows each run's status.
await box.fill('/tool bash {"command": "echo never"}');
await box.press('Enter');
await p.getByTestId('approval-banner').waitFor({ timeout: 30000 });
await p.getByPlaceholder(/Send another/).fill('this one never runs');
await p.getByPlaceholder(/Send another/).press('Enter');
await p.getByTestId('queued-cancel').click({ timeout: 10000 });
await p.getByTestId('never-ran').waitFor({ timeout: 15000 });
await p.getByTestId('runs-toggle').click();
await p.locator('[data-testid=run-row][data-status=requires_action]').waitFor({ timeout: 15000 });
check(await p.locator('[data-testid=run-row][data-status=cancelled]').count() >= 1, 'the queued run shows as cancelled');
const going = await p.locator('[data-testid=run-row][data-status=requires_action]').getAttribute('data-run-id');
await p.getByTestId('stop').click();
await p.locator(`[data-testid=run-row][data-run-id="${going}"][data-status=cancelled]`).waitFor({ timeout: 30000 });
await p.getByTestId('approval-banner').waitFor({ state: 'detached', timeout: 30000 });
await p.waitForTimeout(2000);
check(await p.getByText('echo:this one never runs').count() === 0, 'the cancelled message never ran');
const A2 = await apiHeaders();
const sessions = await (await fetch(`${API}/sessions?harness_id=${hid}`, { headers: A2 })).json();
check(sessions.data.length === 1, `one session for the harness (${sessions.data.length})`);
const sid = sessions.data[0].id;
const fetched = await fetch(`${API}/sessions/${sid}/runs/${going}`, { headers: A2 });
const again = await fetch(`${API}/sessions/${sid}/runs/${going}/cancel`, { method: 'POST', headers: A2 });
check(fetched.status === 200 && (await fetched.json()).status === 'cancelled', `fetch by id: 200 once ended (${fetched.status})`);
check(again.status === 200 && (await again.json()).stop_reason === 'aborted', 'cancel again is a no-op');
console.log('5b runs:', (await p.getByTestId('run-history').innerText()).split('\n').slice(0, 6).join(' | '));
await p.getByTestId('runs-toggle').click();

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
