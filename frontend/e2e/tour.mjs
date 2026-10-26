// The screenshots in docs/tour/ (the README's tour), taken against ./dev.sh with a tidy
// "code-reviewer" harness: two published versions and a draft change.
//
//   npm i --no-save playwright && node e2e/tour.mjs
//   (CHROME=/path/to/chrome for an installed browser; SHOTS=dir to write elsewhere)
// Best on a fresh stack (./dev.sh reset && ./dev.sh), so the harness list holds only the demo ones.
import { chromium } from 'playwright';
import { mkdirSync } from 'node:fs';
import { API, BASE, EMAIL, PASSWORD, apiHeaders } from './lib.mjs';
const dir = process.env.SHOTS ?? new URL('../../docs/tour', import.meta.url).pathname;
mkdirSync(dir, { recursive: true });
const shot = (p, n, full = false) => p.screenshot({ path: `${dir}/${n}.png`, fullPage: full });
const b = await chromium.launch(process.env.CHROME ? { executablePath: process.env.CHROME } : {});
const p = await (await b.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 })).newPage();
p.on('dialog', (d) => d.accept());
const A = await apiHeaders();

// A tidy harness to show.
const name = 'code-reviewer';
const list = (await (await fetch(`${API}/harnesses?page_size=100`, { headers: A })).json()).data;
for (const h of list.filter((h) => h.name === name)) await fetch(`${API}/harnesses/${h.id}`, { method: 'DELETE', headers: A });
const hid = (await (await fetch(`${API}/harnesses`, { method: 'POST', headers: A, body: JSON.stringify({ name, description: 'Reviews a change and runs the tests before answering', model: 'gateway/echo', mode: 'sticky' }) })).json()).id;
const m = (await (await fetch(`${API}/harnesses/${hid}/manifest`, { headers: A })).json()).manifest;
m.prompt.persona = 'reviewer';
m.prompt.sections = [
  { id: 'role', text: 'You review code changes in {cwd}. Be specific and kind.' },
  { id: 'method', text: 'Read the diff, run the tests, then list problems by severity.' },
  { id: 'style', text: 'Answer in short bullet points.' },
];
m.sandbox.provider = 'local_process'; m.sandbox.file_effects = 'danger_full_access';
m.policy.max_steps = 20;
await fetch(`${API}/harnesses/${hid}/manifest`, { method: 'PUT', headers: A, body: JSON.stringify(m) });
await fetch(`${API}/harnesses/${hid}/publish`, { method: 'POST', headers: A });
m.policy.max_steps = 25; m.tools = m.tools.filter((t) => t !== 'grep');
await fetch(`${API}/harnesses/${hid}/manifest`, { method: 'PUT', headers: A, body: JSON.stringify(m) });
await fetch(`${API}/harnesses/${hid}/publish`, { method: 'POST', headers: A });
m.prompt.sections[2].text = 'Answer in short bullet points, most serious first.';
await fetch(`${API}/harnesses/${hid}/manifest`, { method: 'PUT', headers: A, body: JSON.stringify(m) });

// 1 Login
await p.goto(`${BASE}/login`, { waitUntil: 'networkidle', timeout: 180000 });
await p.fill('input[type=email]', EMAIL);
await p.fill('input[type=password]', PASSWORD);
await shot(p, '01-login');
await p.click('button[type=submit]');
await p.waitForURL(`${BASE}/`);

// Builder tabs
await p.goto(`${BASE}/harness/${hid}`, { waitUntil: 'domcontentloaded' });
await p.getByTestId('manifest-status').waitFor();
await p.waitForTimeout(1500);
await shot(p, '03-builder-prompt', true);
await p.getByRole('tab', { name: 'Tools' }).click(); await p.waitForTimeout(800);
await shot(p, '04-builder-tools', true);
await p.getByRole('tab', { name: 'Model' }).click(); await p.waitForTimeout(800);
await shot(p, '05-builder-model', true);
await p.getByRole('tab', { name: 'Sandbox' }).click(); await p.waitForTimeout(500);
await shot(p, '06-builder-sandbox', true);
await p.getByRole('tab', { name: 'Policy' }).click(); await p.fill('#max_steps', '0');
await p.getByRole('button', { name: 'Save draft' }).click();
await p.getByText(/max_steps: Input should be greater/).waitFor();
await shot(p, '07-builder-policy-error', true);
await p.fill('#max_steps', '25');

// New harness page
await p.goto(`${BASE}/harness/new`, { waitUntil: 'networkidle' });
await shot(p, '08-new-harness');

// Test chat: reply, a gated tool waiting for approval with a queued message
await p.goto(`${BASE}/harness/${hid}/test`, { waitUntil: 'domcontentloaded' });
await p.getByTestId('connection').filter({ hasText: 'Live' }).waitFor({ timeout: 60000 });
let box = p.getByPlaceholder(/Send a message|Send another/);
await box.fill('Please review the change in calc.py'); await box.press('Enter');
await p.getByText('echo:Please review the change in calc.py').waitFor();
await box.fill('/tool bash {"command": "printf \'def add(a, b):\\n    return a + b\\n\' > calc.py && python3 -c \\"import calc; print(calc.add(2, 3))\\""}');
await box.press('Enter');
await p.getByTestId('approval-banner').waitFor();
await p.getByPlaceholder(/Send another/).fill('Then summarize what you found');
await p.getByPlaceholder(/Send another/).press('Enter');
await p.getByTestId('queued').waitFor();
await shot(p, '09-chat-approval');
await p.getByTestId('approval-approve').click();
await p.getByText('echo:Then summarize what you found').waitFor({ timeout: 30000 });
await p.waitForTimeout(2500);
await p.getByTestId('tool-card').first().getByRole('button').first().click();
await p.waitForTimeout(500);
await shot(p, '10-chat-tool-output');

// A refused call explained
await p.getByPlaceholder(/Send a message/).fill('/tool bash {"command": "rm -rf build"}'); await p.getByPlaceholder(/Send a message/).press('Enter');
await p.getByTestId('approval-deny').click({ timeout: 30000 });
await p.getByTestId('tool-explanation').last().waitFor();
await p.waitForTimeout(2000);
await shot(p, '11-chat-denied-explained');

// Sessions list and a session page
await p.goto(`${BASE}/sessions`, { waitUntil: 'domcontentloaded' }); await p.waitForTimeout(3000);
await shot(p, '12-sessions');
const sid = (await (await fetch(`${API}/sessions?page_size=1`, { headers: A })).json()).data[0].id;
await p.goto(`${BASE}/sessions/${sid}`, { waitUntil: 'domcontentloaded' });
await p.getByTestId('connection').filter({ hasText: 'Live' }).waitFor({ timeout: 60000 }); await p.waitForTimeout(2000);
await shot(p, '13-session-page');

// Builder again: versions and changes
await p.goto(`${BASE}/harness/${hid}`, { waitUntil: 'domcontentloaded' });
await p.getByTestId('releases').waitFor(); await p.waitForTimeout(1000);
await shot(p, '14-builder-versions');

// Memory
await p.goto(`${BASE}/memory?harness=${hid}`, { waitUntil: 'domcontentloaded' });
await p.getByLabel('New notebook').waitFor({ timeout: 30000 });
await p.getByLabel('New notebook').fill('preferences'); await p.getByLabel('New notebook').press('Enter');
await p.getByTestId('notebook').getByText('preferences').waitFor();
for (const f of ['I use Python 3.12', 'I prefer pytest over unittest', 'Our CI runs on GitHub Actions', 'Line length limit is 100']) {
  await p.getByLabel('New fact').fill(f); await p.getByLabel('New fact').press('Enter');
  await p.getByTestId('facts').getByText(f).waitFor();
}
await p.getByLabel('Edit Line length limit is 100').click();
await p.getByLabel('Edit fact').fill('Line length limit is 120'); await p.getByLabel('Edit fact').press('Enter');
await p.getByTestId('facts').getByText('Line length limit is 120').waitFor();
await p.getByRole('button', { name: 'History' }).click();
await p.getByTestId('history').getByRole('button', { name: 'Restore' }).first().waitFor();
await p.getByLabel('Search memory').fill('pytest'); await p.getByLabel('Search memory').press('Enter');
await p.getByTestId('search-results').waitFor();
await p.waitForTimeout(4500);  // let the toasts fade
await shot(p, '15-memory', true);

// Subagents and traces
await p.goto(`${BASE}/subagents`, { waitUntil: 'domcontentloaded' }); await p.waitForTimeout(3000);
await shot(p, '16-subagents');
await p.goto(`${BASE}/traces`, { waitUntil: 'domcontentloaded' }); await p.waitForTimeout(3000);
await shot(p, '17-traces');

// The operating pages, with one run left waiting on an approval.
await p.goto(`${BASE}/harness/${hid}/test`, { waitUntil: 'domcontentloaded' });
await p.getByTestId('connection').filter({ hasText: 'Live' }).waitFor({ timeout: 60000 });
box = p.getByPlaceholder(/Send a message|Send another/);
await box.fill('/tool bash {"command": "python3 -m pytest -q"}'); await box.press('Enter');
await p.getByTestId('approval-banner').waitFor({ timeout: 30000 });
await p.waitForTimeout(1500);
await shot(p, '18-live-session');
for (const [path, name] of [['overview', '19-overview'], ['approvals', '20-approvals'], ['sandboxes', '21-sandboxes'], ['usage', '22-usage'], ['policies', '23-policies']]) {
  await p.goto(`${BASE}/${path}`, { waitUntil: 'domcontentloaded' }); await p.waitForTimeout(3000);
  await shot(p, name);
}
await p.goto(`${BASE}/harness/${hid}`, { waitUntil: 'domcontentloaded' });
await p.getByRole('button', { name: 'Check' }).click();
await p.getByTestId('publish-checks').getByText(/All pass|to fix/).waitFor({ timeout: 30000 });
await shot(p, '24-publish-checks');

// The agent list last, so its counts include the sessions above.
await p.goto(`${BASE}/`, { waitUntil: 'domcontentloaded' }); await p.waitForTimeout(3000);
await shot(p, '02-harnesses');
await b.close();
console.log('tour done');
