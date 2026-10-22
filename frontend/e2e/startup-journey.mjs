// Browser journey against ./dev.sh: a session starts in the background. With the agent worker
// stopped, the test page says it is waiting for a worker (instead of hanging on "Creating
// session…"); started again, the same page carries on by itself and the session answers.
//
//   npm i --no-save playwright && node e2e/startup-journey.mjs   (stops and restarts the worker)
import { chromium } from 'playwright';
import { execSync } from 'node:child_process';
import { API, BASE, EMAIL, PASSWORD, apiHeaders, check } from './lib.mjs';

const ROOT = new URL('../..', import.meta.url).pathname;
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
check(sh('./dev.sh status | grep worker') === 'worker: down', 'dev.sh status reports the stopped worker as down');
console.log('1 worker stopped; dev.sh status says so');

await p.goto(`${BASE}/harness/${demo.id}/test`, { waitUntil: 'domcontentloaded' });
await p.getByTestId('session-starting').filter({ hasText: /Waiting for an agent worker/ }).waitFor({ timeout: 60000 });
console.log('2 the page says:', await p.getByTestId('session-starting').innerText());

sh('./dev.sh >/dev/null 2>&1');
await p.getByTestId('connection').filter({ hasText: 'Live' }).waitFor({ timeout: 120000 });
const box = p.getByPlaceholder(/Send a message|Send another/);
await box.fill('hello after the wait');
await box.press('Enter');
await p.getByText('echo:hello after the wait').waitFor({ timeout: 30000 });
console.log('3 worker back: the same page went live and the session answers');
await b.close();
console.log('startup journey passed');
