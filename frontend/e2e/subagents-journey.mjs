// Browser journey against ./dev.sh: subagents, as a person meets them. The agent starts one
// that runs a command: the conversation shows its task, its approval (asked by the subagent,
// answered from the session) and its answer; the rail lists it; its page shows its own
// conversation. Then a continuable one: the agent is told when it finishes, the rail shows it
// waiting, and a person gives it a second task from its page.
//
//   npm i --no-save playwright && node e2e/subagents-journey.mjs
//   (CHROME=/path/to/chrome; SHOTS=dir to keep screenshots; BASE_URL, API_URL, DEMO_* to override)
import { chromium } from 'playwright';
import { mkdirSync } from 'node:fs';
import { API, BASE, EMAIL, PASSWORD, apiHeaders, check, deleteHarness } from './lib.mjs';

const dir = process.env.SHOTS;
if (dir) mkdirSync(dir, { recursive: true });
const shot = (p, n) => (dir ? p.screenshot({ path: `${dir}/${n}.png`, fullPage: true }) : Promise.resolve());
const b = await chromium.launch(process.env.CHROME ? { executablePath: process.env.CHROME } : {});
const p = await (await b.newContext({ viewport: { width: 1440, height: 1000 } })).newPage();

const A = await apiHeaders();
const name = 'subagents-' + Date.now().toString(36);
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

await p.goto(`${BASE}/harness/${hid}/test`, { waitUntil: 'domcontentloaded' });
await p.getByTestId('connection').filter({ hasText: 'Live' }).waitFor({ timeout: 90000 });
const box = p.getByPlaceholder(/Send a message|Send another/);

// 1. A subagent the agent waits on. Its task is a command (the dev model echoes, so a task
//    of "/tool bash …" makes the subagent run it), which asks a person first.
const task = '/tool bash {"command": "echo from-the-subagent"}';
await box.fill(`/tool subagent ${JSON.stringify({ prompt: task, description: 'checker' })}`);
await box.press('Enter');
const card = p.getByTestId('subagent-card').first();
await card.waitFor({ timeout: 30000 });
check((await card.getByTestId('subagent-task').innerText()).includes('echo from-the-subagent'), 'the card shows the task');
const asks = p.getByTestId('approval-banner').filter({ has: p.getByTestId('approval-from') });
await asks.waitFor({ timeout: 30000 });
check((await asks.getByTestId('approval-from').innerText()).includes('checker'), 'the approval says the subagent asks');
await shot(p, '1-subagent-asks');
await asks.getByTestId('approval-approve').click();
await p.getByTestId('subagent-card').filter({ has: p.locator('[data-testid=subagent-answer]') }).first().waitFor({ timeout: 60000 });
const answered = p.getByTestId('subagent-card').filter({ has: p.locator('[data-testid=subagent-answer]') }).first();
check((await answered.getAttribute('data-state')) === 'answered', 'the call came back answered');
check((await answered.getByTestId('subagent-answer').innerText()).includes('from-the-subagent'), 'the answer is what it ran');
const row = p.getByTestId('rail-subagent').first();
await row.waitFor({ timeout: 30000 });
await p.getByTestId('rail-subagent').filter({ hasText: 'Answered' }).first().waitFor({ timeout: 30000 });
await shot(p, '2-subagent-answered');
console.log('1 the subagent asked, was approved, ran the command and answered');

// 2. Its own page: the task, its conversation (the command and its output), no composer.
const link = answered.getByRole('link', { name: /Open its conversation/ });
const sessionUrl = `${BASE}${(await link.getAttribute('href')).split('/subagents/')[0]}`; // the test page starts a new session each visit
await link.click();
await p.getByTestId('subagent-bar').waitFor({ timeout: 30000 });
check((await p.getByTestId('subagent-page-task').innerText()).includes('echo from-the-subagent'), 'its page shows its task');
await p.getByTestId('tool-card').filter({ hasText: 'bash' }).first().waitFor({ timeout: 30000 });
await p.getByTestId('subagent-no-messages').waitFor({ timeout: 10000 });
check((await p.getByTestId('subagent-kind').innerText()).includes('waited'), 'it says the agent waited for it');
await shot(p, '3-subagent-page');
console.log('2 its page shows its task and its own conversation');
await p.goto(sessionUrl, { waitUntil: 'domcontentloaded' });
await p.getByTestId('connection').filter({ hasText: 'Live' }).waitFor({ timeout: 60000 });
check((await p.getByTestId('rail-subagent').count()) === 1, 'the session page lists the subagent too');

// 3. A continuable subagent: started, then the agent is told when it finishes its first task.
await p.getByPlaceholder(/Send a message|Send another/).fill(`/tool subagent_continuable ${JSON.stringify({ prompt: 'first task', description: 'helper' })}`);
await p.getByPlaceholder(/Send a message|Send another/).press('Enter');
const notice = p.getByTestId('subagent-notice').filter({ hasText: 'first task' });
await notice.waitFor({ timeout: 60000 });
check((await notice.innerText()).includes('helper finished'), 'the conversation says the helper finished');
const helper = p.getByTestId('rail-subagent').filter({ hasText: 'helper' });
await helper.filter({ hasText: 'Waiting for a message' }).waitFor({ timeout: 30000 });
await shot(p, '4-helper-waiting');
console.log('3 the helper finished its first task and waits for more');

// 4. A person gives the helper its second task from its page; the agent is told again.
await helper.getByRole('link').first().click();
await p.getByTestId('subagent-bar').waitFor({ timeout: 30000 });
const composer = p.getByRole('form', { name: 'Message the subagent' });
await composer.getByRole('textbox').fill('second task');
await composer.getByRole('button', { name: 'Send' }).click();
await p.getByText('echo:second task').first().waitFor({ timeout: 60000 });
await shot(p, '5-helper-second-task');
await p.goto(sessionUrl, { waitUntil: 'domcontentloaded' });
await p.getByTestId('subagent-notice').filter({ hasText: 'second task' }).waitFor({ timeout: 60000 });
await shot(p, '6-session-told-twice');
console.log('4 a person gave the helper a second task, and the agent was told');

await b.close();
await deleteHarness(hid);
console.log('subagents journey: ok');
