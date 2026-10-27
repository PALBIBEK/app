// Browser journey against ./dev.sh: runs become readable traces. A run that writes a file and
// one that runs an approved command show in the runs list; a run reads as a transcript (the
// approval, the command's exit code, the change as a diff), as a tree with span detail, and
// as its raw journal events; the session replays run by run.
//
//   npm i --no-save playwright && node e2e/traces-journey.mjs
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
const name = 'traces-' + Date.now().toString(36);
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

// Two runs: one writes a file, one runs a command that needs approval and exits 3.
await p.goto(`${BASE}/harness/${hid}/test`, { waitUntil: 'domcontentloaded' });
await p.getByTestId('connection').filter({ hasText: 'Live' }).waitFor({ timeout: 60000 });
const box = p.getByPlaceholder(/Send a message|Send another/);
await box.fill('/tool write {"file_path": "notes.txt", "content": "first line\\nsecond line\\n"}');
await box.press('Enter');
await p.getByText(/echo:.*"ok": true/).first().waitFor({ timeout: 30000 });
await box.fill('/tool bash {"command": "echo checking && exit 3"}');
await box.press('Enter');
await p.getByTestId('approval-banner').waitFor({ timeout: 30000 });
await p.waitForTimeout(1500); // some time waiting on a person, so the split shows it
await p.getByTestId('approval-approve').click();
await p.getByText(/exit_code.: 3/).first().waitFor({ timeout: 30000 });
console.log('1 two runs done');

// The runs list: both runs, newest first, with the agent and its version.
await p.goto(`${BASE}/traces`, { waitUntil: 'domcontentloaded' });
await p.getByTestId('trace-rows').waitFor({ timeout: 30000 });
await p.getByRole('combobox', { name: 'Agent' }).selectOption(hid);
await p.getByTestId('trace-row-link').first().waitFor({ timeout: 30000 });
const links = await p.getByTestId('trace-row-link').allInnerTexts();
check(links.length === 2 && links[0].includes('/tool bash') && links[1].includes('/tool write'), `runs listed newest first (${links})`);
check((await p.getByTestId('trace-rows').innerText()).includes(`${name}`), 'the agent is named');
await shot(p, 't1-runs');
console.log('2 runs list:', links.join(' | '));

// The command run as a transcript: the approval, exit 3, the output.
await p.getByTestId('trace-row-link').first().click();
await p.getByTestId('run-header').waitFor({ timeout: 30000 });
const header = await p.getByTestId('run-header').innerText();
check(header.includes('Answered') && header.includes('Waiting on you'), `header has status and time split (${header.replace(/\n/g, ' | ')})`);
const bash = p.getByTestId('transcript-tool').filter({ hasText: 'echo checking' });
check((await bash.innerText()).includes('exit 3'), 'the command card shows its exit code');
check((await bash.getByTestId('transcript-approval').innerText()).startsWith('allowed by you'), 'the approval is on the card');
check((await bash.innerText()).includes('checking'), "the command's output is inline");
check(await p.getByTestId('transcript-answer').count() === 1, 'the answer closes the transcript');
await shot(p, 't2-transcript');
console.log('3 transcript ok');

// Its tree, with the command's span opened.
await bash.getByRole('button', { name: 'Details' }).click();
await p.getByTestId('span-tree').waitFor();
const kinds = await p.getByTestId('span-row').evaluateAll((els) => els.map((e) => e.dataset.kind));
check(['agent', 'step', 'llm', 'tool', 'approval'].every((k) => kinds.includes(k)), `tree has every kind (${kinds})`);
const detail = p.getByTestId('span-detail');
check((await detail.getAttribute('data-kind')) === 'tool', 'the tool span is open');
await detail.getByRole('tab', { name: 'Result' }).click();
await detail.locator('pre').filter({ hasText: 'checking' }).waitFor({ timeout: 15000 }); // the result, loaded
await shot(p, 't3-tree');
console.log('4 tree ok:', kinds.join(','));

// Raw events: the journal as recorded.
await p.getByRole('tab', { name: 'Raw events' }).click();
await p.getByTestId('raw-events').getByText('RunStart').waitFor({ timeout: 15000 });
console.log('5 raw events ok');

// The session replayed, and the write run's change as a diff.
await p.getByRole('navigation', { name: 'Breadcrumb' }).getByRole('link').nth(1).click();
await p.getByTestId('turn-card').first().waitFor({ timeout: 15000 });
check(await p.getByTestId('turn-card').count() === 2, 'the session replays its two runs');
await shot(p, 't4-session');
await p.getByTestId('turn-card').first().click();
await p.getByTestId('transcript').waitFor();
const write = p.getByTestId('transcript-tool').filter({ hasText: 'notes.txt' });
await write.getByText('second line').waitFor({ timeout: 15000 });
console.log('6 session replay and diff ok');

await b.close();
await deleteHarness(hid);
console.log('traces journey passed');
