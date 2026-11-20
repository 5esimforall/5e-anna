/* Anna's tour on the real corpus: levels opened beside each other, folding and widening, keyboard travel, history
   and narrow screens. */
const {test:base,expect}=require('@playwright/test');
const {bookAddress}=require('./locations.cjs');
const port=Number(process.env.ANNA_E2E_PORT||18380);
const anna=`http://127.0.0.1:${port}`;
const test=base.extend({context:async({context},use)=>{
  const errors=[];context.on('page',page=>page.on('pageerror',error=>errors.push(error.message)));
  await use(context);expect(errors,'tour JavaScript errors').toEqual([]);
}});
const level=(page,depth)=>page.locator(`.tour-column[data-depth="${depth}"]`);
// A row by its name: the whole title, or the title's Chinese part before its English name.
const named=name=>new RegExp('^'+name.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')+'(\\s|$)');
const row=(page,depth,name)=>level(page,depth).locator('.tour-item').filter({has:page.locator('.tour-item-title',{hasText:named(name)})}).first();
async function ready(page,depth){
  await expect(level(page,depth)).toHaveAttribute('data-state','ready');
  await expect(page.locator('.tour-column')).toHaveCount(depth+1);
}
const at=page=>new URL(page.url()).searchParams.get('at');
const tourHref=at=>'/tour?'+new URLSearchParams({at});
const firstStep='create/'+bookAddress('n924'), secondStep='create/'+bookAddress('n925');

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
  await expect(level(page,1).locator('.tour-go').first()).toHaveAttribute('href',/^\/read\?root=5echm%3A\d+$/);
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
  await page.goto(anna+tourHref(firstStep));await ready(page,2);
  await row(page,2,'职业简介Class Overview').click();await ready(page,3);
  await expect(level(page,0)).toHaveAttribute('data-place','folded');
  await expect(level(page,0).locator('.tour-column-body')).toBeHidden();
  const spine=level(page,0).locator('.tour-spine');
  await expect(spine).toHaveAccessibleName('展开「导览」');
  const width=depth=>level(page,depth).evaluate(e=>e.getBoundingClientRect().width);
  // Only the deepest level widens; the open levels above it keep the index width.
  await expect.poll(()=>width(0)).toBeLessThan(60);
  await expect.poll(async()=>await width(1)-await width(2)).toBe(0);
  await expect.poll(async()=>await width(3)-1.5*await width(2)).toBeGreaterThan(0);
  await expect(page.locator('#tour-columns')).not.toHaveAttribute('data-rest',/./);
  // From the shallowest open level, ← unfolds the level above, closing just enough levels to show it again.
  await row(page,1,'第一步：选择职业').focus();
  await page.keyboard.press('ArrowLeft');await ready(page,2);
  expect(at(page)).toBe(firstStep);
  await expect(level(page,0)).toHaveAttribute('data-place','open');
  await expect(row(page,0,'创建角色')).toBeFocused();
  await page.goBack();await ready(page,3);
  // A narrower window keeps two levels open.
  await page.setViewportSize({width:1000,height:800});
  await expect(level(page,1)).toHaveAttribute('data-place','folded');
  await expect(level(page,2)).toHaveAttribute('data-place','open');
  await level(page,1).locator('.tour-spine').click();await ready(page,2);
  expect(at(page)).toBe(firstStep);
  await expect(level(page,1)).toHaveAttribute('data-place','open');
  await expect(level(page,0)).toHaveAttribute('data-place','folded');
});

test('a wide detail sets where it leads below its words, across the width',async({page})=>{
  await page.setViewportSize({width:1440,height:900});
  await page.goto(anna+'/tour?at=create');await ready(page,1);
  const column=level(page,1), onward=column.locator('.tour-onward');
  await expect(column.locator('.tour-body')).toBeVisible();
  await expect(column.locator('.tour-column-body')).toHaveCSS('display','block');
  await expect(column.locator('.tour-actions .tour-go')).toHaveCount(1);
  await expect(onward.locator('.tour-group.ordered .tour-item')).toHaveCount(5);
  await expect(column.locator('.tour-onward-title')).toHaveText('由此继续');
  // Below the words, ending where their measure ends rather than at the column's edge.
  const box=await onward.boundingBox(), text=await column.locator('.tour-main').boundingBox();
  const prose=await column.evaluate(c=>Math.max(...[...c.querySelectorAll('.tour-body p')].map(p=>p.getBoundingClientRect().right)));
  const edge=await column.evaluate(c=>c.getBoundingClientRect().right);
  expect(box.y).toBeGreaterThanOrEqual(text.y+text.height);
  expect(Math.abs(box.x+box.width-prose)).toBeLessThan(2);
  expect(box.x+box.width).toBeLessThan(edge-100);
  // While the rows lie below what the column shows, a link above the words leads to them and to the focus on them.
  const jump=column.locator('.tour-jump');
  await page.setViewportSize({width:1440,height:480});
  await expect(jump).toBeVisible();await expect(jump).toHaveText('由此继续 · 5 项 ↓');
  await jump.click();
  await expect(row(page,1,'第一步：选择职业')).toBeFocused();
  await expect(jump).toBeHidden();
  await page.setViewportSize({width:1440,height:900});
  // Narrowed beside a deeper level, the same column keeps the same order.
  await row(page,1,'第一步：选择职业').click();await ready(page,2);
  await expect(column.locator('.tour-body')).toBeHidden();
  // The index column keeps its width.
  await expect.poll(()=>level(page,2).evaluate(e=>e.getBoundingClientRect().width)).toBe(360);
});

// Every frame: each column's width, the width its text is set at, and whether it is moving or loading.
async function watchColumns(page){
  await page.evaluate(()=>{window.tourFrames=[];const tick=()=>{window.tourFrames.push([...document.querySelectorAll('.tour-column')].map(c=>({
    depth:c.dataset.depth,column:Math.round(c.getBoundingClientRect().width),sheet:Math.round(c.querySelector('.tour-sheet').getBoundingClientRect().width),
    moving:'moving' in c.dataset,busy:c.getAttribute('aria-busy')==='true',body:!!c.querySelector('.tour-body')})));requestAnimationFrame(tick);};requestAnimationFrame(tick);});
}
const framesOf=(page,depth)=>page.evaluate(depth=>window.tourFrames.splice(0).flatMap(frame=>frame.filter(c=>c.depth===String(depth))),String(depth));
const settledColumns=page=>expect(page.locator('.tour-column[data-moving]')).toHaveCount(0);

test('columns widen and narrow around text already set at the width they settle to',async({page})=>{
  await page.emulateMedia({reducedMotion:'no-preference'});
  await page.setViewportSize({width:1440,height:900});
  await page.goto(anna+'/tour');await ready(page,0);
  await watchColumns(page);
  // A detail opens once, with its own words, and unrolls from the level that opened it.
  await row(page,0,'创建角色').click();await ready(page,1);await settledColumns(page);
  const opening=await framesOf(page,1), wide=opening.at(-1).sheet;
  expect(wide).toBeGreaterThan(880);
  expect(opening.every(c=>!c.busy&&c.body&&c.sheet===wide)).toBe(true);
  expect(new Set(opening.filter(c=>c.moving).map(c=>c.column)).size).toBeGreaterThan(2);
  expect(opening[0].column).toBeLessThan(wide/2);
  // Narrowing beside the step it opened, it is set at its narrow width from the first frame.
  await row(page,1,'第一步：选择职业').click();await ready(page,2);await settledColumns(page);
  const narrowing=(await framesOf(page,1)).filter(c=>c.column<wide), narrow=narrowing.at(-1).sheet;
  expect(narrow).toBeLessThan(360);
  expect(narrowing.some(c=>c.moving&&c.column>400)).toBe(true);
  expect(narrowing.every(c=>c.sheet===narrow)).toBe(true);
  // Reduced motion settles the columns at once.
  await page.emulateMedia({reducedMotion:'reduce'});
  await page.goBack();await ready(page,1);
  expect((await framesOf(page,1)).some(c=>c.moving)).toBe(false);
});

test('a slow level keeps the levels it replaces until it shows that it is loading',async({page})=>{
  await page.setViewportSize({width:1440,height:900});
  await page.goto(anna+tourHref(firstStep));await ready(page,2);
  let release;const held=new Promise(resolve=>{release=resolve;});
  await page.route('**/api/tour?**',async route=>{
    if(new URL(route.request().url()).searchParams.get('at')===bookAddress('n925'))await held;
    await route.continue();
  });
  await row(page,1,'第二步：确定起源').click();
  // The step it replaces stays at first, then gives way to a loading level.
  await expect(row(page,1,'第二步：确定起源')).toHaveAttribute('aria-current','true');
  await expect(level(page,2).locator('h2')).toHaveText('第一步：选择职业');
  await expect(level(page,2)).toHaveAttribute('aria-busy','true');
  release();await ready(page,2);
  await expect(level(page,2).locator('h2')).toHaveText('第二步：确定起源');
});

test('the keys move through the levels like a file manager’s columns: the focused row is the open one',async({page})=>{
  await page.goto(anna+'/tour');await ready(page,0);
  const first=level(page,0).locator('.tour-item').first();
  await expect(first).toHaveAttribute('tabindex','0');
  await first.focus();
  // Entering a detail lands on its text; → moves on to its rows and opens the first one.
  await page.keyboard.press('ArrowRight');await ready(page,1);
  expect(at(page)).toBe('create');
  await expect(level(page,1)).toBeFocused();
  await page.keyboard.press('ArrowRight');await ready(page,2);
  await expect(level(page,1).locator('.tour-item').first()).toBeFocused();
  expect(at(page)).toBe(firstStep);
  // ↑ from the first row returns to the text, closing the level beside so the detail widens again.
  await page.keyboard.press('ArrowUp');await ready(page,1);
  await expect(level(page,1)).toBeFocused();
  await expect(level(page,1).locator('.tour-body')).toBeVisible();
  // Moving onto a row opens it.
  await page.keyboard.press('ArrowRight');await ready(page,2);
  await page.keyboard.press('ArrowDown');
  await expect(level(page,1).locator('.tour-item').nth(1)).toBeFocused();
  await expect(level(page,2).locator('h2')).toHaveText('第二步：确定起源');
  // Enter, like →, enters an index on its first row, which opens in turn; the first level folds.
  await page.keyboard.press('Enter');await ready(page,3);
  const origin=level(page,2).locator('.tour-item').first();
  await expect(origin).toBeFocused();
  await expect(origin).toHaveAttribute('aria-current','true');
  await expect(level(page,0)).toHaveAttribute('data-place','folded');
  // ← climbs onto the row that opened the level, and a folded level unfolds to take the focus.
  await page.keyboard.press('ArrowLeft');
  await expect(row(page,1,'第二步：确定起源')).toBeFocused();
  await page.keyboard.press('ArrowLeft');await ready(page,2);
  expect(at(page)).toBe(secondStep);
  await expect(first).toBeFocused();
  // Only one row per level waits in the Tab order.
  await expect(level(page,1).locator('.tour-item[tabindex="0"]')).toHaveCount(1);
  // Going back through history keeps the focus in the tour when the level that held it closes.
  await page.goto(anna+tourHref(firstStep));await ready(page,2);
  await row(page,2,'职业简介Class Overview').focus();
  await page.keyboard.press('ArrowRight');await ready(page,3);
  await expect(level(page,3)).toBeFocused();
  await page.goBack();await ready(page,2);
  await expect(row(page,2,'职业简介Class Overview')).toBeFocused();
});

test('moving onto a row opens it beside, a held key opens where it rests, Space reads on and Escape closes',async({page})=>{
  await page.goto(anna+'/tour?at=classes');await ready(page,1);
  const barbarian=row(page,1,'野蛮人'), bard=row(page,1,'吟游诗人');
  await barbarian.focus();
  const steps=await page.evaluate(()=>history.length);
  // A level opened past the deepest one is a step in history; moving on replaces it instead of adding one per row.
  await page.keyboard.press('ArrowDown');await ready(page,2);
  await expect(level(page,2).locator('h2')).toHaveText('吟游诗人');
  await expect(bard).toHaveAttribute('aria-current','true');
  await expect(bard).toBeFocused();
  await page.keyboard.press('ArrowUp');
  await expect(level(page,2).locator('h2')).toHaveText('野蛮人');
  await expect(barbarian).toHaveAttribute('aria-current','true');
  await page.keyboard.press('ArrowDown');
  await expect(level(page,2).locator('h2')).toHaveText('吟游诗人');
  expect(await page.evaluate(()=>history.length)).toBe(steps+1);
  // Holding ↓ passes over rows without opening them, and opens the row it rests on once released.
  const asked=[];
  page.on('request',request=>{const url=new URL(request.url());if(url.pathname==='/api/tour')asked.push(url.searchParams.get('at'));});
  const passed=await row(page,1,'德鲁伊').getAttribute('data-segment');
  for(let i=0;i<3;i++)await page.keyboard.down('ArrowDown');
  await expect(row(page,1,'战士')).toBeFocused();
  await page.keyboard.up('ArrowDown');
  await expect(level(page,2).locator('h2')).toHaveText('战士');
  await expect(row(page,1,'战士')).toHaveAttribute('aria-current','true');
  expect(asked).not.toContain(passed);
  expect(await page.evaluate(()=>history.length)).toBe(steps+1);
  const scrolled=()=>level(page,2).evaluate(e=>e.scrollTop);
  await page.keyboard.press('Space');
  await expect.poll(scrolled).toBeGreaterThan(0);
  await expect(row(page,1,'战士')).toBeFocused();
  await page.keyboard.press('Shift+Space');
  await expect.poll(scrolled).toBe(0);
  await page.keyboard.press('Escape');await ready(page,1);
  expect(at(page)).toBe('classes');
  await expect(row(page,1,'战士')).toBeFocused();
  await expect(row(page,1,'战士')).not.toHaveAttribute('aria-current','true');
});

test('h j k l move as the arrow keys do, and j k scroll a detail’s text',async({page})=>{
  await page.goto(anna+'/tour?at=classes');await ready(page,1);
  const barbarian=row(page,1,'野蛮人');
  await barbarian.focus();
  await page.keyboard.press('j');await ready(page,2);
  await expect(level(page,2).locator('h2')).toHaveText('吟游诗人');
  await page.keyboard.press('k');
  await expect(level(page,2).locator('h2')).toHaveText('野蛮人');
  await expect(barbarian).toBeFocused();
  await page.keyboard.press('l');await ready(page,3);
  await expect(level(page,2).locator('.tour-item').first()).toBeFocused();
  await page.keyboard.press('h');
  await expect(barbarian).toBeFocused();
  // Shifted letters and keys pressed while an input method is composing move nothing.
  await page.keyboard.press('Shift+J');
  await expect(barbarian).toBeFocused();
  await barbarian.evaluate(e=>e.dispatchEvent(new KeyboardEvent('keydown',{key:'j',isComposing:true,bubbles:true,cancelable:true})));
  await page.waitForTimeout(300);
  await expect(barbarian).toBeFocused();
  await expect(level(page,2).locator('h2')).toHaveText('野蛮人');
  // In a detail's text, j and k scroll it; l moves on to its rows.
  await page.setViewportSize({width:1000,height:600});
  await page.goto(anna+'/tour');await ready(page,0);
  await level(page,0).locator('.tour-item').first().focus();
  await page.keyboard.press('l');await ready(page,1);
  await expect(level(page,1)).toBeFocused();
  const scrolled=()=>level(page,1).evaluate(e=>e.scrollTop);
  for(let i=0;i<3;i++)await page.keyboard.press('j');
  await expect.poll(scrolled).toBe(120);
  await page.keyboard.press('k');
  await expect.poll(scrolled).toBe(80);
  await page.keyboard.press('l');await ready(page,2);
  await expect(level(page,1).locator('.tour-item').first()).toBeFocused();
});

test('moving onto a row that leads to Anna closes the levels beside it',async({page})=>{
  await page.goto(anna+tourHref(bookAddress('ver_46fd8d38c8840ad0d29b58ab')));await ready(page,1);
  const subclasses=level(page,1).locator('[data-group=subclasses] .tour-item');
  await subclasses.first().focus();
  await page.keyboard.press('ArrowDown');await ready(page,2);
  await expect(subclasses.nth(1)).toHaveAttribute('aria-current','true');
  // The spell list hands over to Anna's table, so nothing stays open beside it.
  const spells=level(page,1).locator('[data-group=spells] .tour-item');
  await page.keyboard.press('End');await ready(page,1);
  await expect(spells).toBeFocused();
  expect(at(page)).toBe(bookAddress('ver_46fd8d38c8840ad0d29b58ab'));
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

test('subclasses drill down by class directly in the tour without jumping to query',async({page})=>{
  await page.setViewportSize({width:1440,height:900});
  await page.goto(anna+'/tour');await ready(page,0);
  await row(page,0,'子职').click();await ready(page,1);
  expect(at(page)).toBe('subclasses');
  await expect(level(page,1).locator('.tour-go')).toHaveAttribute('href','/?type=subclass');
  const wizard=row(page,1,'法师');
  await expect(wizard).toHaveAttribute('data-at','');
  await wizard.click();await ready(page,2);
  expect(new URL(page.url()).pathname).toBe('/tour');
  expect(at(page)).toBe('subclasses/subclasses.wizard');
  await expect(level(page,2).locator('h2')).toHaveText('法师');
  await expect(level(page,2).locator('.tour-group h3').first()).toHaveText('官方');
  // Column-level filter searches across all origin groups by Chinese and English names.
  const filter=level(page,2).locator('.tour-filter-input');
  await expect(filter).toBeVisible();
  await filter.fill('Evoker');
  await expect(row(page,2,'塑能师')).toBeVisible();
  await expect(level(page,2).locator('.tour-group:not([hidden])')).toHaveCount(1);
  await filter.press('Escape');
  await expect(filter).toHaveValue('');
  // Clicking a subclass opens it in the tour, and clicking a feature expands its full detail.
  await row(page,2,'塑能师').click();await ready(page,3);
  await expect(level(page,3).locator('h2')).toContainText('塑能师');
  await expect(level(page,3).locator('[data-group=class] .tour-item-title')).toContainText(['法师']);
  await row(page,3,'6级：法术塑形').click();await ready(page,4);
  await expect(level(page,4)).toHaveAttribute('data-shape','detail');
  await expect(level(page,4).locator('.tour-body')).toContainText('塑能学派法术');
  await page.reload();await ready(page,4);
  await expect(level(page,4).locator('h2')).toContainText('法术塑形');
});

test('a level missing from this snapshot returns to the level above it',async({page})=>{
  await page.goto(anna+tourHref('create/'+bookAddress('n99999999')));await ready(page,1);
  expect(at(page)).toBe('create');
  await expect(page.locator('#tour-notice')).toHaveText('这一层已不在当前原文快照中，已回到上一层。');
});

test('Anna’s two pages each return to where the reader left them',async({page})=>{
  await page.goto(anna+tourHref(secondStep));await ready(page,2);
  await page.goto(anna+'/?type=spell');
  await expect(page.locator('#results')).toHaveAttribute('aria-busy','false');
  const tour=page.locator('.page-switch a[data-page=tour]');
  await expect(tour).toHaveAttribute('href',tourHref(secondStep));
  await expect(page.locator('.page-switch a[data-page=query]')).toHaveAttribute('aria-current','page');
  await tour.click();await ready(page,2);
  await expect(page.locator('.page-switch a[data-page=query]')).toHaveAttribute('href','/?type=spell');
});

test('a phone shows one level with the way back, in either theme',async({page})=>{
  await page.setViewportSize({width:375,height:812});
  await page.goto(anna+tourHref(firstStep));await ready(page,2);
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
  // With one level showing, ← climbs back to the level above and lands on the row that opened this one.
  await page.goto(anna+tourHref(firstStep));await ready(page,2);
  await level(page,2).locator('.tour-item').first().focus();
  await page.keyboard.press('ArrowLeft');await ready(page,1);
  expect(at(page)).toBe('create');
  await expect(row(page,1,'第一步：选择职业')).toBeFocused();
  // Moving opens nothing when it would replace the level in view; Enter goes into the row's level.
  await page.keyboard.press('ArrowDown');
  await page.waitForTimeout(400);
  await expect(page.locator('.tour-column')).toHaveCount(2);
  await page.keyboard.press('Enter');await ready(page,2);
  expect(at(page)).toBe(secondStep);
  await expect(level(page,2).locator('.tour-item').first()).toBeFocused();
  await expect(page.locator('.tour-column')).toHaveCount(3);
});
