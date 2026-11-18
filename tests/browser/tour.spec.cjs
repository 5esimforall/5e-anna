/* Anna's tour on the real corpus: levels opened beside each other, folding and widening, keyboard travel, history
   and narrow screens. */
const {test:base,expect}=require('@playwright/test');
const port=Number(process.env.ANNA_E2E_PORT||18380);
const anna=`http://127.0.0.1:${port}`;
const test=base.extend({context:async({context},use)=>{
  const errors=[];context.on('page',page=>page.on('pageerror',error=>errors.push(error.message)));
  await use(context);expect(errors,'tour JavaScript errors').toEqual([]);
}});
const level=(page,depth)=>page.locator(`.tour-column[data-depth="${depth}"]`);
const row=(page,depth,name)=>level(page,depth).locator('.tour-item').filter({has:page.locator('.tour-item-title',{hasText:name})}).first();
async function ready(page,depth){
  await expect(level(page,depth)).toHaveAttribute('data-state','ready');
  await expect(page.locator('.tour-column')).toHaveCount(depth+1);
}
const at=page=>new URL(page.url()).searchParams.get('at');

test('a newcomer walks from a purpose to the steps, a step’s checklist and the table it asks for',async({page})=>{
  await page.goto(anna+'/tour');await ready(page,0);
  await expect(page.locator('.page-switch a[aria-current=page]')).toHaveText('导览');
  await expect(level(page,0).locator('h3').first()).toHaveText('开始游戏');
  await row(page,0,'创建角色').click();await ready(page,1);
  expect(at(page)).toBe('create');
  await expect(page).toHaveTitle('创建角色 · 5e Anna');
  // The book's own steps, numbered, each with its first sentence.
  const steps=level(page,1).locator('.tour-group.ordered .tour-item');
  await expect(steps).toHaveCount(5);
  await expect(steps.locator('.tour-step')).toHaveText(['1','2','3','4','5']);
  await expect(steps.first().locator('.tour-item-hint')).toHaveText('选择一个职业，把它写在你的角色卡上。');
  // The deepest level reads as a detail: its own words as the book sets them, in place of the quoted lead.
  await expect(level(page,1).locator('.tour-body')).toContainText('下面是创建角色的步骤');
  await expect(level(page,1).locator('.tour-lead')).toBeHidden();
  await expect(level(page,1).locator('.tour-go').first()).toHaveAttribute('href',/^\/read\?root=\d+$/);
  await steps.first().click();await ready(page,2);
  // Once a step opens, the detail narrows back to its lead and the step, an index, stays narrow beside it.
  await expect(level(page,1).locator('.tour-body')).toBeHidden();
  await expect(level(page,1).locator('.tour-lead')).toContainText('下面是创建角色的步骤');
  await expect(level(page,2)).toHaveAttribute('data-shape','index');
  await expect(page.locator('#tour-columns')).toHaveAttribute('data-rest','选择一项，它会在这里展开');
  await expect(row(page,0,'创建角色')).toHaveAttribute('aria-current','true');
  await expect(steps.first()).toHaveAttribute('aria-current','true');
  await expect(level(page,2).locator('[data-group=choices] .tour-item-title')).toHaveText(['职业']);
  const table=row(page,2,'职业简介Class Overview');
  await expect(table.locator('.tour-tag')).toHaveText('表');
  await table.click();await ready(page,3);
  // A leaf entry is shown as the book sets it, and becomes the topic Stella turns to.
  await expect(level(page,3).locator('.tour-body table')).toBeVisible();
  await expect(level(page,3).locator('.tour-body')).toContainText('野蛮人');
  await expect(page.locator('.companion-go')).toHaveAccessibleName(/^5e Stella：翻到 职业简介/);
  // The next step replaces the step column without climbing back up.
  await page.goBack();await ready(page,2);
  await level(page,2).locator('.tour-sequence a[rel=next]').click();await ready(page,2);
  await expect(level(page,2).locator('h2')).toHaveText('第二步：确定起源');
  await expect(row(page,1,'第二步：确定起源')).toHaveAttribute('aria-current','true');
  await expect(level(page,2).locator('[data-group=choices] .tour-item-title')).toHaveText(['背景','种族','专长']);
  await page.goBack();await ready(page,2);
  await expect(level(page,2).locator('h2')).toHaveText('第一步：选择职业');
  await page.reload();await ready(page,2);
  await expect(level(page,2).locator('h2')).toHaveText('第一步：选择职业');
});

test('three levels stay open; the ones above fold into spines that bring them back',async({page})=>{
  await page.setViewportSize({width:1440,height:900});
  await page.goto(anna+'/tour?at=create.n924');await ready(page,2);
  await row(page,2,'职业简介Class Overview').click();await ready(page,3);
  await expect(level(page,0)).toHaveAttribute('data-place','folded');
  await expect(level(page,0).locator('.tour-column-body')).toBeHidden();
  const spine=level(page,0).locator('.tour-spine');
  await expect(spine).toHaveAccessibleName('展开「导览」');
  const width=depth=>level(page,depth).evaluate(e=>e.getBoundingClientRect().width);
  // Only the deepest level widens; the open levels above it keep the index width.
  await expect.poll(()=>width(0)).toBeLessThan(60);
  await expect.poll(async()=>await width(1)-await width(2)).toBe(0);
  await expect.poll(async()=>await width(3)-2*await width(2)).toBeGreaterThan(0);
  await expect(page.locator('#tour-columns')).not.toHaveAttribute('data-rest',/./);
  // From the shallowest open level, ← reaches the spine; choosing it closes just enough levels to show it again.
  await row(page,1,'第一步：选择职业').focus();
  await page.keyboard.press('ArrowLeft');
  await expect(spine).toBeFocused();
  await page.keyboard.press('Enter');await ready(page,2);
  expect(at(page)).toBe('create.n924');
  await expect(level(page,0)).toHaveAttribute('data-place','open');
  await expect(row(page,0,'创建角色')).toBeFocused();
  await page.goBack();await ready(page,3);
  // A narrower window keeps two levels open.
  await page.setViewportSize({width:1000,height:800});
  await expect(level(page,1)).toHaveAttribute('data-place','folded');
  await expect(level(page,2)).toHaveAttribute('data-place','open');
  await level(page,1).locator('.tour-spine').click();await ready(page,2);
  expect(at(page)).toBe('create.n924');
  await expect(level(page,1)).toHaveAttribute('data-place','open');
  await expect(level(page,0)).toHaveAttribute('data-place','folded');
});

test('arrow keys move within a level, open the next and return to the row that opened it',async({page})=>{
  await page.goto(anna+'/tour');await ready(page,0);
  const first=level(page,0).locator('.tour-item').first();
  await expect(first).toHaveAttribute('tabindex','0');
  await first.focus();
  await page.keyboard.press('ArrowRight');await ready(page,1);
  expect(at(page)).toBe('create');
  await expect(level(page,1).locator('.tour-item').first()).toBeFocused();
  await page.keyboard.press('ArrowDown');
  await expect(level(page,1).locator('.tour-item').nth(1)).toBeFocused();
  await page.keyboard.press('ArrowRight');await ready(page,2);
  await expect(level(page,2).locator('h2')).toHaveText('第二步：确定起源');
  await page.keyboard.press('ArrowLeft');
  await expect(row(page,1,'第二步：确定起源')).toBeFocused();
  await page.keyboard.press('ArrowLeft');
  await expect(first).toBeFocused();
  await page.keyboard.press('End');
  await expect(level(page,0).locator('.tour-item').last()).toBeFocused();
  // Only one row per level waits in the Tab order.
  await expect(level(page,1).locator('.tour-item[tabindex="0"]')).toHaveCount(1);
});

test('a class opens onto its subclasses and its spell list in Anna',async({page})=>{
  await page.goto(anna+'/tour?at=classes');await ready(page,1);
  await expect(level(page,1).locator('.tour-go').nth(1)).toHaveAttribute('href','/?type=class');
  await row(page,1,'法师').click();await ready(page,2);
  await row(page,2,'法师职业特性').click();await ready(page,3);
  await expect(row(page,3,'1级：施法')).toBeVisible();
  await page.goto(anna+'/tour?at=spells');await ready(page,1);
  const cantrips=row(page,1,'戏法');
  await expect(cantrips).toHaveAttribute('href','/?type=spell&f_spell_level=0');
  // Collections hand over to Anna's table rather than opening another level.
  expect(await cantrips.getAttribute('data-at')).toBeNull();
});

test('a level missing from this snapshot returns to the level above it',async({page})=>{
  await page.goto(anna+'/tour?at=create.n99999999');await ready(page,1);
  expect(at(page)).toBe('create');
  await expect(page.locator('#tour-notice')).toHaveText('这一层已不在当前原文快照中，已回到上一层。');
});

test('Anna’s two pages each return to where the reader left them',async({page})=>{
  await page.goto(anna+'/tour?at=create.n925');await ready(page,2);
  await page.goto(anna+'/?type=spell');
  await expect(page.locator('#results')).toHaveAttribute('aria-busy','false');
  const tour=page.locator('.page-switch a[data-page=tour]');
  await expect(tour).toHaveAttribute('href','/tour?at=create.n925');
  await expect(page.locator('.page-switch a[data-page=query]')).toHaveAttribute('aria-current','page');
  await tour.click();await ready(page,2);
  await expect(page.locator('.page-switch a[data-page=query]')).toHaveAttribute('href','/?type=spell');
});

test('a phone shows one level with the way back, in either theme',async({page})=>{
  await page.setViewportSize({width:375,height:812});
  await page.goto(anna+'/tour?at=create.n924');await ready(page,2);
  await expect(level(page,2)).toBeVisible();
  await expect(level(page,1)).toBeHidden();
  const back=level(page,2).locator('.tour-back');
  await expect(back).toHaveText('‹ 创建角色');
  const width=await page.evaluate(()=>document.documentElement.scrollWidth);
  expect(width).toBeLessThanOrEqual(375);
  await back.click();await ready(page,1);
  expect(at(page)).toBe('create');
  const paper=()=>level(page,1).evaluate(e=>getComputedStyle(e).backgroundColor);
  const light=await paper();
  await page.emulateMedia({colorScheme:'dark'});
  await expect.poll(paper).not.toBe(light);
});
