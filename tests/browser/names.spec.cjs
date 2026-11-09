const {test, expect} = require('@playwright/test');
const port = Number(process.env.ANNA_E2E_PORT || 18380);
const anna = `http://127.0.0.1:${port}`;
const book = `http://127.0.0.1:${port}/book`;

test('wrapped translations display cleanly while source names and identities survive', async ({page, request}) => {
  const response = await request.get(`${anna}/api/entries?versions=each&q=${encodeURIComponent('冥河之剑')}`);
  expect(response.ok()).toBeTruthy();
  const result = await response.json();
  const entries = result.rows.filter(e => e.name_zh === '冥河之剑');
  expect(entries).toHaveLength(2);
  expect(new Set(entries.map(e=>e.id)).size).toBe(2);
  // Stacked rows list both identities as separate versions instead of choosing one.
  const stacked = await (await request.get(`${anna}/api/entries?q=${encodeURIComponent('冥河之剑')}`)).json();
  const row = stacked.rows.find(e => e.name_zh === '冥河之剑');
  expect(row.versions.map(v=>v.id).sort()).toEqual(entries.map(e=>e.id).sort());
  const entry = entries.find(e=>e.name_parse?.rule === 'wrapped_translation');
  expect(entry).toBeTruthy();
  expect(entry.name_parse.raw).toBe('冥河之剑 (Acheron blade)');
  await page.goto(`${anna}/?entry=${encodeURIComponent(entry.id)}`);
  await expect(page.locator('#entry-title')).toHaveText('冥河之剑');
  await expect(page.locator('#detail-content .english')).toHaveText('Acheron blade');
  const source = await request.get(`${book}/definition/${encodeURIComponent(entry.id)}`);
  expect(source.ok()).toBeTruthy();
  expect(decodeURIComponent(source.url())).toContain(entry.page_path);
});
