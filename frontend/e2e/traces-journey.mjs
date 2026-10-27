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

// A judge for the agent, set up from the Judges view (the dev model echoes, so its verdicts are "unclear").
await p.goto(`${BASE}/traces/judges`, { waitUntil: 'domcontentloaded' });
await p.getByRole('combobox', { name: 'Agent' }).selectOption(hid);
await p.getByRole('button', { name: 'Add a judge' }).click();
const form = p.getByRole('form', { name: 'New judge' });
await form.getByLabel('Name').fill('answers');
await form.getByLabel('What a good run looks like').fill('Answers the question that was asked, and says what it changed.');
await form.getByRole('button', { name: 'Add judge' }).click();
await p.getByTestId('judge').filter({ hasText: 'answers' }).waitFor({ timeout: 15000 });
console.log('0 judge added');

// Two runs: one writes a file, one runs a command that needs approval and exits 3.
await p.goto(`${BASE}/harness/${hid}/test`, { waitUntil: 'domcontentloaded' });
await p.getByTestId('connection').filter({ hasText: 'Live' }).waitFor({ timeout: 60000 });
const box = p.getByPlaceholder(/Send a message|Send another/);
await box.fill('/tool write {"file_path": "notes.txt", "content": "first line\\nsecond line\\n"}');
await box.press('Enter');
await p.getByText(/echo:.*path: notes\.txt/).first().waitFor({ timeout: 30000 }); // the model reads results as text
await box.fill('/tool bash {"command": "echo checking && exit 3"}');
await box.press('Enter');
await p.getByTestId('approval-banner').waitFor({ timeout: 30000 });
await p.waitForTimeout(1500); // some time waiting on a person, so the split shows it
await p.getByTestId('approval-approve').click();
await p.getByText(/exit code 3: the command failed/).first().waitFor({ timeout: 30000 });
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
const checks = await p.getByTestId('check').evaluateAll((els) => els.map((e) => `${e.dataset.name}:${e.dataset.label}`));
check(checks.includes('finished:pass') && checks.includes('approvals:pass'), `the run's checks (${checks})`);
check(checks.includes('answers:unclear'), `the judge's verdict is on the run (${checks})`);
check((await p.getByTestId('run-summary').innerText()) === 'Ran 1 command (1 failed); answered.', 'the run is summed up in a sentence');
await shot(p, 't2-transcript');
console.log('3 transcript ok; checks', checks.join(' '));

// Say it went wrong, with why: the list finds it, the agent's version shows it.
const verdict = p.getByTestId('feedback-run');
await verdict.getByRole('button', { name: 'Wrong answer' }).click();
await verdict.getByLabel('What went wrong? (optional)').fill('should not exit 3');
await verdict.getByRole('button', { name: 'Mark wrong' }).click();
await verdict.getByText('“should not exit 3”').waitFor({ timeout: 15000 });
check((await verdict.getByRole('button', { name: 'Wrong answer' }).getAttribute('aria-pressed')) === 'true', 'the verdict shows as chosen');
console.log('3b marked wrong');

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

// The runs list finds runs marked wrong; the agent's version history counts them.
await p.goto(`${BASE}/traces`, { waitUntil: 'domcontentloaded' });
await p.getByRole('combobox', { name: 'Agent' }).selectOption(hid);
await p.getByRole('button', { name: 'Marked wrong' }).click();
await p.waitForTimeout(1500);
const wrong = await p.getByTestId('trace-row-link').allInnerTexts();
check(wrong.length === 1 && wrong[0].includes('/tool bash'), `marked wrong filter (${wrong})`);
await p.goto(`${BASE}/`, { waitUntil: 'domcontentloaded' });
await p.getByText(name, { exact: true }).first().click();
const quality = await p.getByTestId('version-quality').first().innerText({ timeout: 30000 });
check(quality.includes('2 of 2 runs answered') && quality.includes('0 good, 1 wrong'), `version quality (${quality})`);
console.log('7 filters and version quality:', quality);

// Issues: the run marked wrong is an issue, with the note and a link to it.
await p.goto(`${BASE}/traces/issues`, { waitUntil: 'domcontentloaded' });
await p.getByRole('combobox', { name: 'Agent' }).selectOption(hid);
const marked = p.getByTestId('issue').filter({ hasText: 'Marked wrong by people' });
await marked.waitFor({ timeout: 15000 });
check((await marked.innerText()).includes('should not exit 3'), 'the issue shows why it was marked wrong');
await shot(p, 't5-issues');
console.log('8 issues ok');

// Save both runs to a dataset, replay it on v1, publish v2, replay on v2, compare.
async function saveRun(index, datasetChoice, expected) {
  await p.goto(`${BASE}/traces`, { waitUntil: 'domcontentloaded' });
  await p.getByRole('combobox', { name: 'Agent' }).selectOption(hid);
  await p.getByTestId('trace-row-link').nth(index).waitFor({ timeout: 15000 });
  await p.getByTestId('trace-row-link').nth(index).click();
  await p.getByTestId('save-to-dataset').click();
  const save = p.getByRole('form', { name: 'Save this run to a dataset' });
  await save.getByLabel('Dataset').waitFor();
  await p.waitForTimeout(800); // the agent's datasets load into the picker
  if (datasetChoice === 'new') {
    await save.getByLabel('Dataset').selectOption({ label: 'A new dataset…' });
    await save.getByLabel('Name').fill('regressions');
  } else {
    await save.getByLabel('Dataset').selectOption({ label: datasetChoice });
  }
  if (expected) await save.getByLabel('What a good answer does (optional)').fill(expected);
  await save.getByRole('button', { name: 'Save' }).click();
  await p.getByText('Saved to the dataset').waitFor({ timeout: 15000 });
}
await saveRun(0, 'new', 'the command exits 0');
await saveRun(1, 'regressions (1)', null);
console.log('9 two runs saved to a dataset');

await p.goto(`${BASE}/traces/datasets`, { waitUntil: 'domcontentloaded' });
await p.getByRole('combobox', { name: 'Agent' }).selectOption(hid);
await p.getByTestId('dataset-link').filter({ hasText: 'regressions' }).click();
await p.getByTestId('dataset-items').waitFor({ timeout: 15000 });
check((await p.getByTestId('dataset-items').locator('li').count()) === 2, 'the dataset holds the two runs');
async function replay(versionLabel) {
  await p.getByRole('combobox', { name: 'Version' }).selectOption({ label: versionLabel });
  await p.getByTestId('run-experiment').click();
  await p.locator('[data-testid=experiment][data-status=done]').filter({ hasText: versionLabel === 'The live version' ? 'v1' : versionLabel }).first()
    .waitFor({ timeout: 240000 });
}
await replay('The live version');
console.log('10 replayed on v1:', await p.getByTestId('experiment-stats').first().innerText());

// A second version: same agent, a changed prompt, published.
const A2 = await apiHeaders();
const m2 = (await (await fetch(`${API}/harnesses/${hid}/manifest`, { headers: A2 })).json()).manifest;
m2.prompt.persona = 'careful';
await fetch(`${API}/harnesses/${hid}/manifest`, { method: 'PUT', headers: A2, body: JSON.stringify(m2) });
check((await fetch(`${API}/harnesses/${hid}/publish`, { method: 'POST', headers: A2 })).ok, 'publish v2');
await p.reload({ waitUntil: 'domcontentloaded' });
await p.getByTestId('experiments').waitFor({ timeout: 15000 });
await p.waitForTimeout(1000); // the releases load into the version picker
await replay('v2');
console.log('11 replayed on v2');

await p.getByRole('checkbox', { name: 'Pick v1 to compare' }).check();
await p.getByRole('checkbox', { name: 'Pick v2 to compare' }).check();
await p.getByRole('link', { name: 'Compare the two' }).click();
await p.getByTestId('comparison-rows').waitFor({ timeout: 30000 });
const statuses = await p.getByTestId('result').evaluateAll((els) => els.map((e) => e.dataset.status));
check((await p.getByTestId('comparison-row').count()) === 2, 'both saved runs are compared');
check(statuses.includes('needs_approval') && statuses.includes('done'), `the bash replay stopped for its approval, the write one ran (${statuses})`);
await shot(p, 't6-compare');
console.log('12 compared:', await p.getByTestId('comparison-summary').innerText());

// Learn a change from the traces, measured on the dataset. The dev model only echoes, so what
// it proposes is not a real fix: the journey checks the loop ends in a decision that says why.
await p.goto(`${BASE}/traces/improve`, { waitUntil: 'domcontentloaded' });
await p.getByRole('combobox', { name: 'Agent' }).selectOption(hid);
await p.getByRole('combobox', { name: 'Measured on' }).selectOption({ label: 'regressions (2)' });
await p.getByLabel('What should it fix? (optional)').fill('the command should exit 0');
await p.getByTestId('propose').click();
const proposal = p.getByTestId('proposal').first();
await proposal.waitFor({ timeout: 15000 });
const ended = ['awaiting_approval', 'refused', 'no_change', 'failed'];
for (let i = 0; i < 240 && !ended.includes(await proposal.getAttribute('data-status')); i++) await p.waitForTimeout(2500);
const outcome = await proposal.getAttribute('data-status');
check(ended.includes(outcome), `the change reached a decision (${outcome})`);
await proposal.getByRole('link').click();
await p.getByTestId('proposal-status').waitFor({ timeout: 15000 });
const said = await p.getByTestId('proposal-status').innerText();
check(said.length > 20, `the change says where it stands (${said})`);
if (outcome === 'awaiting_approval' || outcome === 'refused') await p.getByTestId('gate').waitFor({ timeout: 15000 });
const live = (await (await fetch(`${API}/harnesses/${hid}/releases`, { headers: await apiHeaders() })).json()).map((r) => r.version);
check(live.length === 2, `nothing was published without an approval (${live})`);
await shot(p, 't7-change');
console.log('13 a change was learned and decided:', outcome, '·', said.replace(/\n/g, ' '));

await b.close();
await deleteHarness(hid);
console.log('traces journey passed');
