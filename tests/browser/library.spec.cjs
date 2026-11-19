/* The reader's library. The test server registers demo (a copy of the frozen fixture: 护盾术 PHB 2014 and
   回气 PHB24, with the same IDs as the book), reserved-demo, live our5etools when built, and reserved 5etools. */
const {test:base,expect}=require('@playwright/test');
const test=base.extend({context:async({context},use)=>{
  const errors=[];context.on('page',page=>page.on('pageerror',e=>errors.push(e.message)));
  await use(context);expect(errors,'uncaught library errors').toEqual([]);
}});
const anna=`http://127.0.0.1:${Number(process.env.ANNA_E2E_PORT||18380)}`;
const resultsReady=page=>expect(page.locator('#results')).toHaveAttribute('aria-busy','false');
const button=page=>page.locator('.topbar .library-button');
const dialog=page=>page.getByRole('dialog',{name:'资料库'});
const libraryBox=(page,id)=>dialog(page).locator(`input[data-library="${id}"]`);
async function openLibrary(page){
  await button(page).click();await expect(dialog(page)).toBeVisible();
  // The works list arrives with the dialog; the totals line says so.
  await expect(dialog(page).locator('.library-total')).toContainText('部作品');
}
async function setLibrary(page,settings){
  await page.goto(anna+'/anna-logo.png');
  await page.evaluate(s=>{s?localStorage.setItem('anna.library',JSON.stringify(s)):localStorage.removeItem('anna.library');},settings);
}
const shieldRow=page=>page.locator('#results tr').filter({has:page.locator('.entry-name',{hasText:'护盾术'})}).first();

test('every page opens one library from its topbar and lists what each collection offers',async({page})=>{
  await setLibrary(page,null);
  for(const path of ['/','/tour','/read']){
    await page.goto(anna+path);
    await expect(button(page)).toBeVisible();
    await expect(button(page).locator('.library-summary')).toHaveText('不全书');
    await expect(button(page)).not.toHaveClass(/changed/);
  }
  await page.goto(anna+'/?type=spell');await resultsReady(page);
  await openLibrary(page);
  // The book is read and is the last one, so it cannot be unticked; reserved collections stay visible but unusable.
  await expect(libraryBox(page,'5echm')).toBeChecked();await expect(libraryBox(page,'5echm')).toBeDisabled();
  await expect(libraryBox(page,'demo')).not.toBeChecked();await expect(libraryBox(page,'demo')).toBeEnabled();
  for(const id of ['reserved-demo','5etools']){
    await expect(libraryBox(page,id)).toBeDisabled();
    await expect(dialog(page).locator(`[data-library-row="${id}"]`)).toContainText('未接入');
  }
  await expect(dialog(page).getByRole('button',{name:'恢复默认'})).toBeDisabled();
  // The filter panel no longer carries the library's choices.
  await page.keyboard.press('Escape');await expect(dialog(page)).toBeHidden();await expect(button(page)).toBeFocused();
  await page.locator('#toggle-filters').click();
  await expect(page.locator('#filter-panel [data-group="ruleset"],#filter-panel [data-group="source"],#filter-panel [data-group="origin"]')).toHaveCount(0);
  await expect(page.locator('#display-panel #stack-versions,#display-panel #prefer')).toHaveCount(0);
  // On a phone the button keeps its icon and the dialog fits the screen.
  await page.setViewportSize({width:375,height:812});
  await expect(button(page).locator('.library-word')).toBeHidden();
  await openLibrary(page);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(375);
  const box=await dialog(page).boundingBox();expect(box.x).toBeGreaterThanOrEqual(0);expect(box.x+box.width).toBeLessThanOrEqual(375);
});

test('a second collection joins the table; same-name versions share a row in the reader\'s order',async({page})=>{
  await setLibrary(page,null);
  await page.goto(anna+'/?type=spell&q='+encodeURIComponent('护盾术'));await resultsReady(page);
  await expect(shieldRow(page).locator('.version-pill')).toHaveCount(2);
  await openLibrary(page);
  await libraryBox(page,'demo').check();
  await expect(button(page).locator('.library-summary')).toHaveText('不全书 + 测试资料库');
  await page.keyboard.press('Escape');await resultsReady(page);
  const row=shieldRow(page);
  await expect(row.locator('.version-pill')).toHaveCount(3);
  await expect(row.locator('.version-pill').filter({hasText:'测试资料库 · PHB 2014'})).toHaveCount(1);
  // 2024 leads by default whatever the order; with 2014 first, the collection order picks between equal versions.
  await expect(row.locator('.version-pill.shown')).toHaveText('PHB24 2024');
  await openLibrary(page);
  await dialog(page).locator('[data-prefer="5e"]').click();
  await dialog(page).getByRole('button',{name:'上移 测试资料库'}).click();
  await expect(dialog(page).locator('[data-library-row]').first()).toHaveAttribute('data-library-row','demo');
  await page.keyboard.press('Escape');await resultsReady(page);
  await expect(shieldRow(page).locator('.version-pill.shown')).toHaveText('PHB 2014');
  await expect(shieldRow(page).locator('.version-pill.shown')).toHaveAttribute('title',/^测试资料库/);
  // The demo version opens from its own collection, and its comparison spans both collections.
  await shieldRow(page).locator('.entry-name').click();
  await expect(page.locator('#entry-title')).toHaveText('护盾术');
  await expect(page.getByRole('tab',{name:/^版本对照 3/})).toBeVisible();
  await page.locator('#close-detail').click();
  // Without deduplication each version is a row; the choice outlives a reload.
  await openLibrary(page);await dialog(page).getByLabel('去重：同名条目合为一行').uncheck();
  await expect(dialog(page).locator('[data-prefer="5e"]')).toBeDisabled();
  await page.keyboard.press('Escape');await resultsReady(page);
  await expect(page.locator('#results .entry-name').filter({hasText:'护盾术'})).toHaveCount(3);
  await expect(page.locator('#result-count')).toContainText('3 条');
  await page.reload();await resultsReady(page);
  await expect(page.locator('#results .entry-name').filter({hasText:'护盾术'})).toHaveCount(3);
  await expect(button(page).locator('.library-summary')).toHaveText('测试资料库 + 不全书');
  await expect(button(page)).toHaveClass(/changed/);
  const [download]=await Promise.all([page.waitForEvent('download'),page.locator('#export').click()]);
  expect(download.suggestedFilename()).toBe('5e-anna-library.csv');
});

test('rules versions and kinds of content bound the table, and an empty table points back to them',async({page})=>{
  await setLibrary(page,null);
  await page.goto(anna+'/?type=spell&q='+encodeURIComponent('护盾术'));await resultsReady(page);
  await openLibrary(page);
  // The last rules version cannot be left out.
  await dialog(page).getByRole('checkbox',{name:/^2014 版/}).uncheck();
  await expect(dialog(page).getByRole('checkbox',{name:/^2024 版/})).not.toBeDisabled();
  await dialog(page).getByRole('checkbox',{name:/^待核/}).uncheck();
  await expect(dialog(page).getByRole('checkbox',{name:/^2024 版/})).toBeDisabled();
  await page.keyboard.press('Escape');await resultsReady(page);
  await expect(shieldRow(page).locator('.version-pill')).toHaveCount(0);
  await expect(shieldRow(page).locator('td[data-label="规则版本"]')).toHaveText('2024');
  // Leaving out official works leaves the book's spells out; the empty table says why and opens the library.
  await openLibrary(page);
  await dialog(page).locator('[data-kind="official"]').uncheck();
  await expect(dialog(page).locator('.library-hidden-count')).not.toBeEmpty();
  await page.keyboard.press('Escape');await resultsReady(page);
  await expect(page.locator('#empty-library')).toBeVisible();
  await page.locator('#empty-library button').click();await expect(dialog(page)).toBeVisible();
  // One work can come back alone: its kind turns on with the others of that kind left out.
  await dialog(page).locator('.library-works summary').click();
  await dialog(page).locator('.library-filter').fill('玩家手册2024');
  const work=dialog(page).locator('.library-works li:not([hidden]) input[data-work]').first();
  await work.check();
  await expect(dialog(page).locator('[data-kind="official"]')).toHaveJSProperty('indeterminate',true);
  await page.keyboard.press('Escape');await resultsReady(page);
  await expect(page.locator('#empty-library')).toBeHidden();
  await expect(shieldRow(page).locator('td[data-label="来源"]')).toHaveText('PHB24');
  await openLibrary(page);await dialog(page).getByRole('button',{name:'恢复默认'}).click();
  await expect(dialog(page).locator('[data-kind="official"]')).toBeChecked();
  await page.keyboard.press('Escape');await resultsReady(page);
  await expect(button(page)).not.toHaveClass(/changed/);
  await expect(shieldRow(page).locator('.version-pill')).toHaveCount(2);
});

test('a query link keeps its filters but cannot pin the reader to one collection',async({page})=>{
  await setLibrary(page,null);
  const query=new URLSearchParams({type:'spell',q:'护盾术'}).toString();
  await page.goto(anna+'/?collection=demo&'+query);await resultsReady(page);
  await expect(page).toHaveURL(anna+'/?'+query);
  await expect(shieldRow(page).locator('.version-pill')).toHaveCount(2);

  await openLibrary(page);
  await libraryBox(page,'demo').check();await libraryBox(page,'5echm').uncheck();
  await page.keyboard.press('Escape');
  await expect(button(page).locator('.library-summary')).toHaveText('测试资料库');
  await page.goto(anna+'/?collection=5echm&'+query);await resultsReady(page);
  await expect(page).toHaveURL(anna+'/?'+query);
  await expect(shieldRow(page).locator('td[data-label="规则版本"]')).toHaveText('2014');
  await page.reload();await resultsReady(page);
  await expect(button(page).locator('.library-summary')).toHaveText('测试资料库');
  await expect(shieldRow(page).locator('td[data-label="规则版本"]')).toHaveText('2014');
});

test('a query-only collection can lead the table without taking the tour and Stella away',async({page,request})=>{
  await setLibrary(page,{order:['demo','5echm'],enabled:['demo','5echm']});
  const {versions_total:count}=await (await request.get(anna+'/api/entries?libraries=demo,5echm&type=spell&limit=1')).json();
  await page.goto(anna+'/?type=spell');await resultsReady(page);
  await expect(button(page).locator('.library-summary')).toHaveText('测试资料库 + 不全书');
  await page.locator('.page-switch a[data-page=tour]').click();
  const root=page.locator('.tour-column[data-depth="0"]');
  await expect(root).toHaveAttribute('data-state','ready');
  // The tour still counts spells from both selected collections.
  await expect(root.locator('.tour-item').filter({hasText:'法术（按环阶）'}).locator('.tour-item-count')).toHaveText(count.toLocaleString('zh-CN'));
  await root.locator('[data-segment="create"]').click();
  const stella=page.locator('.tour-column[data-depth="1"] .tour-go[href^="/read?"]');
  await expect(stella).toHaveAttribute('href',/^\/read\?root=5echm%3A/);
  await stella.click();
  await expect(page.locator('#content')).toHaveCSS('visibility','visible');
  await expect(button(page).locator('.library-summary')).toHaveText('测试资料库 + 不全书');
});
