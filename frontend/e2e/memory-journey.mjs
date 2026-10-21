// Browser journey against ./dev.sh: the memory page: create a notebook, add, edit and forget facts, restore an earlier revision, search.
//
//   npm i --no-save playwright && node e2e/memory-journey.mjs
//   (CHROME=/path/to/chrome for an installed browser; BASE_URL, API_URL, DEMO_EMAIL, DEMO_PASSWORD to override)
import { chromium } from 'playwright';
import { BASE, EMAIL, PASSWORD } from './lib.mjs';
const b = await chromium.launch(process.env.CHROME ? { executablePath: process.env.CHROME } : {});
const p = await (await b.newContext({ viewport: { width: 1400, height: 1000 } })).newPage();
p.on('dialog', (d) => d.accept());
await p.goto(`${BASE}/login`, { waitUntil: 'networkidle', timeout: 180000 });
await p.fill('input[type=email]', EMAIL);
await p.fill('input[type=password]', PASSWORD);
await p.click('button[type=submit]');
await p.waitForURL(`${BASE}/`);
await p.getByRole('link', { name: 'Memory' }).click();
await p.waitForURL(/\/memory/);
const nbName = 'prefs-' + Date.now().toString(36);
await p.getByLabel('New notebook').fill(nbName);
await p.getByLabel('New notebook').press('Enter');
await p.getByTestId('notebook').getByText(nbName).waitFor({ timeout: 20000 });
console.log('1 notebook created and opened');
await p.getByLabel('New fact').fill('I prefer tabs over spaces');
await p.getByLabel('New fact').press('Enter');
await p.getByLabel('New fact').fill('I live in Pune');
await p.getByLabel('New fact').press('Enter');
await p.getByTestId('facts').getByText('I live in Pune').waitFor();
console.log('2 facts:', (await p.getByTestId('facts').innerText()).split('\n').filter(Boolean));
await p.getByLabel('Edit I prefer tabs over spaces').click();
await p.getByLabel('Edit fact').fill('I prefer spaces now');
await p.getByLabel('Edit fact').press('Enter');
await p.getByTestId('facts').getByText('I prefer spaces now').waitFor();
await p.getByLabel('Forget I live in Pune').click();
await p.getByTestId('facts').getByText('I live in Pune').waitFor({ state: 'detached' });
console.log('3 after edit + forget:', (await p.getByTestId('facts').innerText()).split('\n').filter(Boolean));
await p.getByRole('button', { name: 'History' }).click();
await p.getByTestId('history').waitFor();
// Restore the state right after both facts were added (the oldest revision with an insert of Pune).
const restoreButtons = p.getByTestId('history').getByRole('button', { name: 'Restore' });
await restoreButtons.first().waitFor({ timeout: 15000 });
console.log('4 restorable states:', await restoreButtons.count());
await p.getByTestId('history').locator('div', { hasText: /^Revision 2/ }).getByRole('button', { name: 'Restore' }).first().click();
await p.getByTestId('facts').getByText('I live in Pune').waitFor({ timeout: 15000 });
console.log('5 restored:', (await p.getByTestId('facts').innerText()).split('\n').filter(Boolean));
await p.getByLabel('Search memory').fill('Pune');
await p.getByLabel('Search memory').press('Enter');
await p.getByTestId('search-results').waitFor();
console.log('6 search:', await p.getByTestId('search-results').innerText());
await b.close();
console.log('memory journey passed');
