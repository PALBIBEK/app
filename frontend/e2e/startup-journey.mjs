// Browser journey against ./dev.sh: a session starts in the background. With the agent worker
// stopped, the test page says it is waiting for a worker (instead of hanging on "Creating
// session…") and a banner says no worker is running; started again, the same page carries on
// by itself, the session answers and the banner clears.
//
//   npm i --no-save playwright && node e2e/startup-journey.mjs   (stops and restarts the worker)
import { chromium } from 'playwright';
import { execSync } from 'node:child_process';
import { API, BASE, EMAIL, PASSWORD, apiHeaders, check } from './lib.mjs';

const ROOT = new URL('../..', import.meta.url).pathname;
// The platform status (cached by the backend for a few seconds) until it matches.
async function statusUntil(ok, what, timeoutMs = 30000) {
  const end = Date.now() + timeoutMs;
  let body;
  while (Date.now() < end) {
    body = await (await fetch(`${API}/platform/status`, { headers: A })).json();
    if (ok(body)) return body;
    await new Promise((res) => setTimeout(res, 1000));
  }
  check(false, `${what} (last: ${JSON.stringify(body?.workers)})`);
}
const sh = (cmd, timeout = 300000) => execSync(cmd, { shell: '/bin/bash', cwd: ROOT, timeout, env: process.env }).toString().trim();
const b = await chromium.launch(process.env.CHROME ? { executablePath: process.env.CHROME } : {});
const p = await (await b.newContext({ viewport: { width: 1400, height: 1000 } })).newPage();
const A = await apiHeaders();
const demo = (await (await fetch(`${API}/harnesses?page_size=100`, { headers: A })).json()).data.find((h) => h.name === 'Demo coding agent');
check(demo, 'the seeded demo harness exists');

await p.goto(`${BASE}/login`, { waitUntil: 'networkidle', timeout: 180000 });
await p.fill('input[type=email]', EMAIL);
await p.fill('input[type=password]', PASSWORD);
await p.click('button[type=submit]');
await p.waitForURL(`${BASE}/`);

// Stop the worker as a crash would (its whole process group).
sh(`kill -TERM -- -$(cut -d' ' -f1 .dev/run/worker.pid)`);
// It shuts down gracefully (finishing its polls), which takes a few seconds.
sh(`for i in $(seq 1 60); do ./dev.sh status | grep -q 'worker: down' && exit 0; sleep 0.5; done; exit 1`, 60000);
check(sh("./dev.sh status | grep '^worker:'") === 'worker: down', 'dev.sh status reports the stopped worker as down');
// The fleet as the API reports it: the stopped worker's heartbeat left with it.
const fleet = await statusUntil((s) => s.workers?.status === 'down', 'platform status: workers down');
check(fleet.workers.alerts[0]?.code === 'no_workers', 'the alert says no worker is running');
check(fleet.run_allowed === true, 'no worker does not refuse runs: they wait');
console.log('1 worker stopped; dev.sh status and the platform status say so:', sh("./dev.sh status | grep '^workers:'"));
check(sh("./dev.sh status | grep '^workers:'").startsWith('workers: none serving'), 'dev.sh status: none serving');

await p.goto(`${BASE}/harness/${demo.id}/test`, { waitUntil: 'domcontentloaded' });
await p.getByTestId('session-starting').filter({ hasText: /Waiting for an agent worker/ }).waitFor({ timeout: 60000 });
console.log('2 the page says:', await p.getByTestId('session-starting').innerText());
await p.getByTestId('workers-banner').filter({ hasText: 'No agent worker is running' }).waitFor({ timeout: 30000 });
console.log('2b banner:', (await p.getByTestId('workers-banner').innerText()).replace(/\n/g, ' | '));

sh('./dev.sh >/dev/null 2>&1');
await p.getByTestId('connection').filter({ hasText: 'Live' }).waitFor({ timeout: 120000 });
const box = p.getByPlaceholder(/Send a message|Send another/);
await box.fill('hello after the wait');
await box.press('Enter');
await p.getByText('echo:hello after the wait').waitFor({ timeout: 30000 });
console.log('3 worker back: the same page went live and the session answers');
await p.getByTestId('workers-banner').waitFor({ state: 'detached', timeout: 60000 });
const after = await statusUntil((s) => s.workers?.status === 'ok' && s.workers.slots_max > 0, 'workers ok again');
console.log('4 banner gone; workers:', after.workers.polling, 'polling,', after.workers.slots_max, 'slots');
await b.close();
console.log('startup journey passed');
