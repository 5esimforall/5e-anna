/* Workbench regressions use the same real, validated corpus as links.spec.cjs.
   Network failure/race tests explicitly inject transport faults, not fake rules. */
const {test:base,expect}=require('@playwright/test');
const test=base.extend({context:async({context},use)=>{
  const errors=[];context.on('page',page=>page.on('pageerror',e=>errors.push(e.message)));
  await use(context);expect(errors,'uncaught query-interface errors').toEqual([]);
}});
const port=Number(process.env.ANNA_E2E_PORT||18380);
const anna=`http://127.0.0.1:${port+1}`;
const spells='/?view='+encodeURIComponent('法术');
async function ready(page){await expect(page.locator('#result-count')).toContainText('找到');await expect(page.locator('#results')).toHaveAttribute('aria-busy','false');}
async function visit(page,query=spells){await page.goto(anna+query);await ready(page);}
async function entry(page,index=0){return page.locator('#results .entry-name').nth(index).getAttribute('data-entry');}
async function read(page,id){await expect(page.locator('#detail-content')).toHaveAttribute('aria-busy','false');await expect(page.locator('#detail .footnote')).toContainText(id);}
// Audit tabs belong to audit mode, a per-browser preference stored like the theme.
async function auditMode(page){await page.addInitScript(()=>{try{localStorage.setItem('anna.audit','on');}catch{}});}
const AUDIT_COLUMNS=['classification_status','legacy'];
async function shot(page,testInfo,name){await testInfo.attach(name,{body:await page.screenshot(),contentType:'image/png'});}
async function queryMatchesAPI(page){
  await ready(page);
  const data=await (await page.request.get(anna+'/api/entries'+new URL(page.url()).search)).json();
  expect(await page.locator('#results .entry-name').evaluateAll(els=>els.map(e=>e.dataset.entry))).toEqual(data.rows.map(r=>r.id));
  return data;
}

test('spell multi-select, mutually exclusive flags, unknowns and retained active filters',async({page},testInfo)=>{
  await page.setViewportSize({width:1440,height:900});await visit(page);
  await page.locator('#toggle-filters').click();
  await expect(page.locator('#domain-extra')).not.toHaveAttribute('open','');
  await page.getByRole('button',{name:'环阶清空',exact:true}).click();await ready(page);
  await expect(page.locator('#empty')).toBeVisible();
  await page.getByRole('checkbox',{name:'环阶：一环',exact:true}).check();await ready(page);
  await page.getByRole('checkbox',{name:'环阶：二环',exact:true}).check();
  let data=await queryMatchesAPI(page);expect(data.total).toBeGreaterThan(0);
  expect(data.rows.every(r=>r.quickref.spell_level.some(v=>['1','2'].includes(v)))).toBeTruthy();
  await page.locator('#domain-extra summary').click();
  await page.getByRole('button',{name:'专注：否',exact:true}).click();
  data=await queryMatchesAPI(page);expect(data.total).toBeGreaterThan(0);
  expect(data.rows.every(r=>r.quickref.concentration.includes('false')&&!r.quickref.concentration.includes('__conflict__'))).toBeTruthy();
  await expect(page.locator('[data-group=f_concentration] [aria-pressed=true]')).toHaveCount(1);
  await page.getByRole('button',{name:'专注：未提取',exact:true}).click();await queryMatchesAPI(page);
  await expect(page.getByRole('button',{name:'专注：否',exact:true})).toHaveAttribute('aria-pressed','false');
  await page.locator('#toggle-filters').click();
  await expect(page.locator('#filter-panel')).toBeHidden();
  await expect(page.locator('#active-filters')).toContainText('一环、二环');
  await expect(page.locator('#active-filters')).toContainText('专注');
  await shot(page,testInfo,'spell-active-filters.png');
  await page.locator('#reset').click();await ready(page);await expect(page.locator('#active-filters button')).toHaveCount(0);
});

test('filters start collapsed while active conditions stay visible and results keep the first screen',async({page})=>{
  for(const [width,height] of [[1280,800],[390,844]]){
    await page.setViewportSize({width,height});await visit(page,spells+'&f_spell_level=1');
    await expect(page.locator('#filter-panel')).toBeHidden();
    await expect(page.locator('#toggle-filters')).toHaveAttribute('aria-expanded','false');
    await expect(page.locator('#active-filters')).toContainText('环阶：一环');
    // Before collapsing by default, filters left 3 creature rows at 1280×800 and pushed the first
    // spell row to y=743 at 375×812; the first screen now belongs to results.
    const row=await page.locator('#results tr').first().boundingBox();
    expect(row.y+row.height).toBeLessThanOrEqual(height*.7);
    if(width>760){const table=await page.locator('.table-wrap').boundingBox();expect(table.height).toBeGreaterThanOrEqual(height*.5);}
  }
  await page.getByRole('button',{name:'移除 环阶：一环'}).click();await ready(page);
  await expect(page.locator('#active-filters button')).toHaveCount(0);expect(new URL(page.url()).searchParams.has('f_spell_level')).toBeFalsy();
  await page.locator('#toggle-filters').click();
  await expect(page.locator('#filter-panel')).toBeVisible();await expect(page.locator('#toggle-filters')).toHaveAttribute('aria-expanded','true');
  await page.reload();await ready(page);await expect(page.locator('#filter-panel')).toBeVisible();
  await visit(page);await expect(page.locator('#filter-panel')).toBeHidden();
});

test('switching categories keeps dimmed results and paging consistent until the new view renders',async({page})=>{
  await page.setViewportSize({width:1440,height:900});await visit(page);
  const range=await page.locator('#range').textContent(),pages=await page.locator('#page').textContent();
  const columns=await page.locator('#columns th').count(),rows=await page.locator('#results tr').count();
  let release;const gate=new Promise(resolve=>release=resolve);let started;const waiting=new Promise(resolve=>started=resolve);
  await page.route('**/api/entries?*',async route=>{
    if(new URL(route.request().url()).searchParams.get('view')==='生物数据卡'){started();await gate;}
    await route.continue();
  });
  await page.locator('#views [data-view="生物数据卡"]').click();await waiting;
  await expect(page.locator('#title')).toHaveText('生物数据卡');
  await expect(page.locator('#results')).toHaveAttribute('aria-busy','true');
  // The previous rows remain under their own header instead of disappearing or mixing schemas.
  await expect(page.locator('#results tr')).toHaveCount(rows);await expect(page.locator('#columns th')).toHaveCount(columns);
  expect(await page.locator('#results tr').first().locator('td').count()).toBe(columns);
  for(const [id,text] of [['#range',range],['#page',pages]]){
    await expect(page.locator(id)).toHaveText(text);await expect(page.locator(id)).toHaveCSS('opacity','0.45');
  }
  for(const id of ['#prev','#next','#bottom-prev','#bottom-next','#export'])await expect(page.locator(id)).toBeDisabled();
  release();await ready(page);await page.unrouteAll({behavior:'wait'});
  const facets=await (await page.request.get(anna+'/api/facets?view='+encodeURIComponent('生物数据卡'))).json();
  await expect(page.locator('#columns th')).toHaveCount(facets.schema.columns.length);
  expect(await page.locator('#results tr').first().locator('td').count()).toBe(facets.schema.columns.length);
  const data=await queryMatchesAPI(page);
  await expect(page.locator('#range')).toHaveText(`1–150 / ${data.total.toLocaleString('zh-CN')}`);
  await expect(page.locator('#page')).toHaveText(`1 / ${data.pages}`);await expect(page.locator('#range')).toHaveCSS('opacity','1');
  expect(await page.locator('.table-wrap').evaluate(e=>e.scrollTop)).toBe(0);
});

test('wide reader remains non-modal, consecutive entries and keyboard return preserve list position',async({page},testInfo)=>{
  await auditMode(page);await page.setViewportSize({width:1440,height:900});await visit(page);
  const first=await entry(page,8),second=await entry(page,9);
  await page.locator('#results .entry-name').nth(8).focus();
  const top=await page.locator('.table-wrap').evaluate(e=>e.scrollTop);
  await page.keyboard.press('Enter');await read(page,first);
  expect(await page.locator('#detail').evaluate(e=>e.matches(':modal'))).toBeFalsy();
  await expect(page.locator(`#results .entry-name[data-entry="${first}"]`)).toHaveAttribute('aria-current','true');
  await page.locator('#entry-next').click();await read(page,second);
  await expect(page.locator('#detail-position')).toHaveText('本页 10 / 150');
  await page.getByRole('tab',{name:/^证据/}).click();
  await expect(page.locator('#detail-panel .fact').first()).toBeVisible();
  await expect(page.locator('#detail-panel a').first()).toHaveAttribute('target','_blank');
  await shot(page,testInfo,'desktop-spell-evidence.png');
  await page.keyboard.press('F6');await expect(page.locator(`#results .entry-name[data-entry="${second}"]`)).toBeFocused();
  await page.keyboard.press('F6');await expect(page.locator('#close-detail')).toBeFocused();
  await page.keyboard.press('Escape');await expect(page.locator('#detail')).not.toBeVisible();
  await expect(page.locator(`#results .entry-name[data-entry="${second}"]`)).toBeFocused();
  expect(await page.locator('.table-wrap').evaluate(e=>e.scrollTop)).toBe(top);
  // Existing URL query, sort and size survive the entire reader session.
  expect(new URL(page.url()).searchParams.get('view')).toBe('法术');
  expect(new URL(page.url()).searchParams.has('entry')).toBeFalsy();
  await page.locator('#results .entry-name').nth(8).click();await read(page,first);
  // A non-modal reader never blocks a different result from being clicked.
  await page.locator('#results .entry-name').nth(9).click();await read(page,second);
});

test('history and reload restore page, sorting, disclosure, reading tab and scroll positions',async({page})=>{
  await auditMode(page);await page.setViewportSize({width:1440,height:900});await visit(page,spells+'&size=50&page=2&sort=name&dir=desc');
  await page.locator('#toggle-filters').click();
  await page.locator('#results .entry-name').nth(12).focus();
  const first=await entry(page,12),second=await entry(page,13);
  await page.keyboard.press('Enter');await read(page,first);
  await page.getByRole('tab',{name:/^证据/}).click();
  await page.locator('#detail-content').evaluate(e=>e.scrollTop=180);
  await page.locator('#entry-next').click();await read(page,second);
  await page.goBack();await read(page,first);
  await expect(page.getByRole('tab',{name:/^证据/})).toHaveAttribute('aria-selected','true');
  await expect(page.locator('#filter-panel')).toBeVisible();await expect(page.locator('#toggle-filters')).toHaveAttribute('aria-expanded','true');
  await expect.poll(()=>page.locator('#detail-content').evaluate(e=>e.scrollTop)).toBe(180);
  const listTop=await page.locator('.table-wrap').evaluate(e=>e.scrollTop);
  await expect(page.getByRole('tab',{name:/^证据/})).toBeFocused();
  await page.keyboard.press('F6');await expect(page.locator(`#results .entry-name[data-entry="${first}"]`)).toBeFocused();
  await page.keyboard.press('F6');
  await page.reload();await ready(page);await read(page,first);
  await expect(page.locator('#filter-panel')).toBeVisible();
  await expect(page.getByRole('tab',{name:/^证据/})).toHaveAttribute('aria-selected','true');
  await expect.poll(()=>page.locator('#detail-content').evaluate(e=>e.scrollTop)).toBe(180);
  expect(await page.locator('.table-wrap').evaluate(e=>e.scrollTop)).toBe(listTop);
  await expect(page.locator('#page')).toContainText('2 /');await expect(page.locator('#page-size')).toHaveValue('50');
  await expect(page.locator('#sort')).toHaveValue('name');await expect(page.locator('#direction')).toHaveText('降序 ↓');
  await page.goForward();await read(page,second);
});

test('narrow reader traps focus, tabs use arrow keys, resize keeps content and close restores focus',async({page},testInfo)=>{
  await page.setViewportSize({width:390,height:844});await visit(page);const id=await entry(page);
  await page.locator('#results .entry-name').first().click();await read(page,id);
  expect(await page.locator('#detail').evaluate(e=>e.matches(':modal'))).toBeTruthy();
  await expect(page.locator('#close-detail')).toBeFocused();
  for(let i=0;i<12;i++){await page.keyboard.press('Tab');expect(await page.locator('#detail').evaluate(e=>e.contains(document.activeElement))).toBeTruthy();}
  await page.getByRole('tab',{name:'正文',exact:true}).focus();await page.keyboard.press('ArrowRight');
  await expect(page.getByRole('tab',{name:'属性',exact:true})).toBeFocused();
  await expect(page.getByRole('tab',{name:'属性',exact:true})).toHaveAttribute('aria-selected','true');
  // Reading mode ends the tab list at relations; audit tabs are summarized below the panel.
  await page.keyboard.press('End');await expect(page.getByRole('tab',{name:/^关系/})).toBeFocused();
  await expect(page.getByRole('tab',{name:/^待核/})).toHaveCount(0);await expect(page.locator('[data-audit-summary]')).toContainText('待核');
  await page.keyboard.press('Home');await expect(page.getByRole('tab',{name:'正文',exact:true})).toBeFocused();
  expect(await page.locator('#detail-content').evaluate(e=>e.scrollWidth<=e.clientWidth)).toBeTruthy();
  await shot(page,testInfo,'mobile-spell-reader.png');
  await page.setViewportSize({width:1440,height:900});
  expect(await page.locator('#detail').evaluate(e=>e.matches(':modal'))).toBeFalsy();await read(page,id);
  await expect(page.getByRole('tab',{name:'正文',exact:true})).toBeFocused();
  await page.setViewportSize({width:390,height:844});await page.keyboard.press('Escape');
  await expect(page.locator('#detail')).not.toBeVisible();await expect(page.locator('#results .entry-name').first()).toBeFocused();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy();
});

test('changing filters or classification closes a stale reader without losing filter semantics',async({page})=>{
  await page.setViewportSize({width:1440,height:900});await visit(page);await page.locator('#toggle-filters').click();
  const id=await entry(page);await page.locator('#results .entry-name').first().click();await read(page,id);
  await page.getByRole('checkbox',{name:'环阶：戏法',exact:true}).uncheck();await ready(page);
  await expect(page.locator('#detail')).toBeHidden();expect(new URL(page.url()).searchParams.has('entry')).toBeFalsy();
  await page.locator('#results .entry-name').first().click();await read(page,await entry(page));
  await page.locator('#views [data-view="生物数据卡"]').click();await ready(page);
  await expect(page.locator('#detail')).toBeHidden();await expect(page.locator('#cr_min')).toBeVisible();
  await page.locator('#cr_min').selectOption('1/2');await page.locator('#cr_max').selectOption('2');
  const data=await queryMatchesAPI(page);expect(data.total).toBeGreaterThan(0);
  expect(new URL(page.url()).searchParams.has('f_spell_level')).toBeFalsy();
});

test('all category schemas share the workbench without losing their columns or filters',async({page},testInfo)=>{
  test.setTimeout(120000);
  const meta=await (await page.request.get(anna+'/api/meta')).json();
  await page.setViewportSize({width:1440,height:900});
  for(const view of [{name:''},...meta.views]){
    await visit(page,'/?view='+encodeURIComponent(view.name));
    const facets=await (await page.request.get(anna+'/api/facets?view='+encodeURIComponent(view.name))).json();
    await expect(page.locator('#columns th')).toHaveCount(facets.schema.columns.filter(k=>!AUDIT_COLUMNS.includes(k)).length);
    for(const group of facets.groups)await expect(page.locator(`[data-group="f_${group.key}"]`)).toHaveCount(1);
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy();
    const id=await entry(page);await page.locator('#results .entry-name').first().click();await read(page,id);
    await page.locator('#close-detail').click();
    await page.setViewportSize({width:390,height:844});
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy();
    await expect(page.locator('#title')).toHaveText(view.name||'全部条目');
    await page.setViewportSize({width:1440,height:900});
  }
  await visit(page,'/?view='+encodeURIComponent('魔法物品'));
  await page.locator('#toggle-filters').click();await page.locator('#domain-extra summary').click();
  await expect(page.getByRole('button',{name:'同调：需要',exact:true})).toBeVisible();
  await page.getByRole('button',{name:'同调：需要',exact:true}).click();await queryMatchesAPI(page);
  await shot(page,testInfo,'magic-items-filters.png');
});

test('category rail groups every view once and flattens into one strip on narrow screens',async({page})=>{
  const meta=await (await page.request.get(anna+'/api/meta')).json();
  await page.setViewportSize({width:1440,height:900});await visit(page);
  const names=await page.locator('#views [data-view]').evaluateAll(els=>els.map(e=>e.dataset.view));
  expect(names.slice(0,2)).toEqual(['','法术']);
  expect([...names].sort()).toEqual(['',...meta.views.map(v=>v.name)].sort());
  for(const [label,view] of [['怪物','生物数据卡'],['物品','魔法物品'],['角色选项','职业'],['规则','状态动作规则']])
    await expect(page.getByRole('group',{name:label}).locator(`[data-view="${view}"]`)).toHaveCount(1);
  await expect(page.locator('#views [data-view="法术"]')).toHaveAttribute('aria-pressed','true');
  await page.setViewportSize({width:390,height:844});
  await expect(page.locator('.view-group-label').first()).toBeHidden();
  expect((await page.locator('#views').boundingBox()).height).toBeLessThan(60);
  await page.locator('#views [data-view="生物数据卡"]').click();await ready(page);
  await expect(page.locator('#title')).toHaveText('生物数据卡');
});

test('spell components read as glyph strips while filters and export keep every component',async({page})=>{
  await page.setViewportSize({width:1440,height:900});
  await visit(page,spells+'&f_verbal=false&f_material=true');
  const data=await queryMatchesAPI(page);expect(data.total).toBeGreaterThan(0);
  const headers=await page.locator('#columns th').allTextContents();
  expect(headers.some(h=>h.includes('成分'))).toBeTruthy();expect(headers.some(h=>h.includes('言语'))).toBeFalsy();
  const cell=page.locator('#results tr').first().locator('td[data-label="成分"]');
  await expect(cell.locator('.mark.absent')).toContainText('言');
  await expect(cell.locator('.mark.absent .sr-only')).toHaveText('无言语');
  await expect(cell.locator('.mark.present, .mark.special').last()).toContainText('材');
  if(data.rows[0].fields.components)await expect(cell).toHaveAttribute('title',data.rows[0].fields.components);
  const csv=await (await page.request.get(anna+'/api/export'+new URL(page.url()).search)).text();
  for(const name of ['言语','姿势','材料','仪式','专注'])expect(csv.split('\n')[0]).toContain(name);
});

test('reading mode folds review apparatus and audit mode persists per browser',async({page})=>{
  await page.setViewportSize({width:1440,height:900});await visit(page,'/');
  expect((await page.locator('#columns th').allTextContents()).some(h=>h.includes('分类依据'))).toBeFalsy();
  await expect(page.locator('.topbar-actions a[href="/review"]')).toBeHidden();
  await page.locator('#toggle-filters').click();await expect(page.locator('#audit-details')).toBeHidden();
  const id=await entry(page);await page.locator('#results .entry-name').first().click();await read(page,id);
  await expect(page.getByRole('tab',{name:/^证据/})).toHaveCount(0);
  await page.locator('[data-audit-summary]').click();
  await expect(page.locator('#audit-mode')).toHaveAttribute('aria-pressed','true');
  await expect(page.getByRole('tab',{name:/^证据/})).toHaveAttribute('aria-selected','true');
  await expect(page.getByRole('tab',{name:/^证据/})).toBeFocused();
  await expect(page.locator('.topbar-actions a[href="/review"]')).toBeVisible();await expect(page.locator('#audit-details')).toBeVisible();
  expect((await page.locator('#columns th').allTextContents()).some(h=>h.includes('分类依据'))).toBeTruthy();
  // The mode belongs to this browser, not to the shared query URL.
  expect([...new URL(page.url()).searchParams.keys()].some(k=>k.includes('audit'))).toBeFalsy();
  await page.reload();await ready(page);await read(page,id);
  await expect(page.locator('#audit-mode')).toHaveAttribute('aria-pressed','true');
  await expect(page.getByRole('tab',{name:/^证据/})).toHaveAttribute('aria-selected','true');
  // Leaving audit mode returns an open audit tab to the text without closing the reader.
  await page.locator('#audit-mode').click();
  await expect(page.getByRole('tab',{name:'正文',exact:true})).toHaveAttribute('aria-selected','true');
  await expect(page.locator('#detail')).toBeVisible();
  expect(await page.evaluate(()=>localStorage.getItem('anna.audit'))).toBeNull();
  // An audit filter from a shared URL stays visible and removable in reading mode.
  await visit(page,'/?conflict=1');
  await expect(page.locator('#active-filters')).toContainText('字段冲突');
  await page.locator('#toggle-filters').click();await expect(page.locator('#audit-details')).toBeVisible();
  await page.getByRole('button',{name:/^移除 字段冲突/}).click();await ready(page);
  await expect(page.locator('#audit-details')).toBeHidden();
});

test('narrow results read as labelled lists without horizontal scrolling',async({page},testInfo)=>{
  await page.setViewportSize({width:390,height:844});await visit(page);
  await expect(page.locator('#columns')).toBeHidden();
  const row=page.locator('#results tr').first();
  await expect(row.locator('td[data-label="环阶"]')).toBeVisible();
  expect(await row.locator('td[data-label="环阶"]').evaluate(e=>getComputedStyle(e,'::before').content)).toBe('"环阶"');
  expect(await page.locator('.table-wrap').evaluate(e=>e.scrollWidth<=e.clientWidth)).toBeTruthy();
  const name=await row.locator('.name-cell').boundingBox(),wrap=await page.locator('.table-wrap').boundingBox();
  expect(name.width).toBeGreaterThan(wrap.width*.8);
  await shot(page,testInfo,'mobile-spell-list.png');
  await page.setViewportSize({width:1280,height:800});await expect(page.locator('#columns')).toBeVisible();
});

test('same-name versions stack into one row with pills, in-place differences and a reader comparison',async({page},testInfo)=>{
  await page.setViewportSize({width:1440,height:900});await visit(page,spells+'&q='+encodeURIComponent('以太化'));
  const data=await queryMatchesAPI(page);expect(data.versions).toBe('stacked');
  await expect(page.locator('#result-count')).toContainText(`${data.total} 行 · ${data.versions_total} 个版本`);
  const row=page.locator('#results tr').first(),[shown,other]=data.rows[0].versions;
  expect([shown.ruleset,other.ruleset]).toEqual(['5r','5e']);
  await expect(row.locator('.version-pill')).toHaveCount(2);
  await expect(row.locator('.version-pill.shown')).toHaveAttribute('data-entry',shown.id);
  const school=row.locator('td[data-label="学派"]');
  await expect(school).toHaveClass(/differs/);await expect(school.locator('.version-diff')).toContainText('变化');
  // A pill opens that version; its row stays current and reader stepping stays on this page.
  await row.locator(`.version-pill[data-entry="${other.id}"]`).click();await read(page,other.id);
  await expect(row).toHaveClass(/row-selected/);
  await expect(row.locator(`.version-pill[data-entry="${other.id}"]`)).toHaveAttribute('aria-current','true');
  await expect(page.locator('#detail-position')).toHaveText('本页 1 / 1');
  // The reader compares every version field by field and switches versions inside the same tab.
  await page.getByRole('tab',{name:/^版本对照/}).click();
  await expect(page.locator('.version-table tr.differs th').filter({hasText:'学派'})).toHaveCount(1);
  await expect(page.locator('.version-table tr.differs th').filter({hasText:'环阶'})).toHaveCount(0);
  await page.locator('.reader-versions').getByRole('button',{name:/PHB24/}).click();await read(page,shown.id);
  await expect(page.getByRole('tab',{name:/^版本对照/})).toHaveAttribute('aria-selected','true');
  await shot(page,testInfo,'stacked-versions-reader.png');
  // The edition preference changes the shown version without closing the reader.
  await page.locator('#prefer').selectOption('5e');await ready(page);
  await expect(row.locator('.version-pill.shown')).toHaveAttribute('data-entry',other.id);
  await expect(school.locator('.cell-value')).toHaveText('变化');await expect(page.locator('#detail')).toBeVisible();
  expect(new URL(page.url()).searchParams.get('prefer')).toBe('5e');
  // One row per version stays available, kept in the URL and in history.
  await page.locator('#stack-versions').uncheck();await ready(page);
  expect(new URL(page.url()).searchParams.get('versions')).toBe('each');
  await expect(page.locator('#results tr')).toHaveCount(2);await expect(page.locator('#prefer')).toBeDisabled();
  await expect(page.locator('#results .version-pill')).toHaveCount(0);await expect(page.locator('#result-count')).toContainText('2 条');
  await page.goBack();await ready(page);
  await expect(page.locator('#stack-versions')).toBeChecked();await expect(page.locator('#results tr')).toHaveCount(1);
});

test('stacking never merges class features from different classes',async({page})=>{
  await page.setViewportSize({width:1440,height:900});await visit(page,'/?view='+encodeURIComponent('职业能力')+'&q='+encodeURIComponent('属性值提升'));
  const data=await queryMatchesAPI(page);
  expect(data.total).toBe(data.versions_total);await expect(page.locator('#results .version-pill')).toHaveCount(0);
});

test('reader shows the original layout with field notes tied to their words and falls back to extracted text',async({page},testInfo)=>{
  await page.setViewportSize({width:1100,height:820});await visit(page,spells+'&q='+encodeURIComponent('以太化'));
  const data=await queryMatchesAPI(page),[shown,other]=data.rows[0].versions;
  await page.locator('#results .entry-name').first().click();await read(page,shown.id);
  const layout=page.locator('#detail-panel .layout-text');await expect(layout).toBeVisible();
  const mark=layout.locator('mark.note[data-notes~="school"]').first();await expect(mark).toHaveText('七环 咒法');
  const note=page.locator('.field-note[data-note="school"]');
  await expect(note).toContainText('学派');await expect(note).toContainText('咒法');
  // A wide reader sets the notes in the margin beside the text.
  const n=await note.boundingBox(),box=await layout.boundingBox();expect(n.x).toBeGreaterThanOrEqual(box.x+box.width);
  await note.hover();await expect(mark).toHaveClass(/lit/);
  await page.mouse.move(0,0);await expect(mark).not.toHaveClass(/lit/);
  await note.focus();await expect(mark).toHaveClass(/lit/);
  await expect(page.locator('.field-note.unlocated').first()).toBeVisible();
  await shot(page,testInfo,'layout-reader-notes.png');
  // An unavailable or changed source never yields a partial layout; extracted text stands in with the reason.
  await page.route('**/api/entry-layout?*',route=>route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({status:'stale',path:'x'})}));
  await page.locator('.reader-versions').getByRole('button',{name:/PHB 5e/}).click();await read(page,other.id);
  await expect(page.locator('#detail-panel .layout-notice')).toContainText('原文已更新');
  await expect(page.locator('#detail-panel .body-text')).toContainText('以太');
  await expect(page.locator('#detail-panel .layout-text')).toHaveCount(0);
});

test('stat blocks keep their tables in the reader without widening the page',async({page})=>{
  for(const [width,height] of [[1440,900],[390,844]]){
    await page.setViewportSize({width,height});
    await visit(page,'/?view='+encodeURIComponent('生物数据卡')+'&q='+encodeURIComponent('成年红龙'));
    await page.locator('#results .entry-name').first().click();
    const tables=page.locator('#detail-panel .layout-text table');await expect(tables.first()).toBeVisible();
    expect(await tables.count()).toBeGreaterThanOrEqual(2);
    await expect(page.locator('#detail-panel .layout-text td.short').first()).toHaveCSS('white-space','nowrap');
    expect(await page.locator('#detail-content').evaluate(e=>e.scrollWidth<=e.clientWidth)).toBeTruthy();
    await page.locator('#close-detail').click();
  }
});

test('detail failure is recoverable, missing IDs are explained, late responses cannot replace newer entries',async({page})=>{
  await page.setViewportSize({width:1440,height:900});await visit(page);
  const first=await entry(page),second=await entry(page,1);let fail=true;
  await page.route('**/api/entry?*',async route=>{if(fail){fail=false;await route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({error:'Injected transport failure'})});}else await route.continue();});
  await page.locator('#results .entry-name').first().click();
  await expect(page.locator('#detail [role=alert]')).toContainText('Injected transport failure');
  await page.getByRole('button',{name:'重新读取',exact:true}).click();await read(page,first);
  await page.unroute('**/api/entry?*');
  await page.locator('#close-detail').click();
  let release;const gate=new Promise(resolve=>release=resolve);let started;const waiting=new Promise(resolve=>started=resolve);
  await page.route('**/api/entry?id='+first,async route=>{started();await gate;await route.continue();});
  await page.locator('#results .entry-name').first().click();await waiting;
  await page.locator('#results .entry-name').nth(1).click();await read(page,second);release();
  await page.unrouteAll({behavior:'wait'});await read(page,second);
  await page.goto(anna+'/?entry=missing-visual-test');
  await expect(page.locator('#detail [role=alert]')).toContainText('无法读取条目');
  await expect(page.locator('#entry-next')).toBeDisabled();await page.locator('#close-detail').click();
  await expect(page.locator('#q')).toBeFocused();
});

test('Chinese IME composition never submits intermediate query text',async({page})=>{
  await visit(page);const requests=[];
  page.on('request',r=>{if(r.url().includes('/api/entries?'))requests.push(new URL(r.url()).searchParams.get('q'));});
  await page.locator('#q').dispatchEvent('compositionstart');
  await page.locator('#q').fill('huo');await page.locator('#q').press('Enter');
  await page.waitForTimeout(220);expect(requests).toEqual([]);
  await page.locator('#q').fill('火');await page.locator('#q').dispatchEvent('compositionend');
  await expect.poll(()=>requests).toEqual(['火']);await ready(page);
});

test('query failures clear stale rows and recover through the existing controls',async({page})=>{
  await visit(page);let fail=true;
  await page.route('**/api/entries?*',async route=>{if(fail){fail=false;await route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({error:'Injected query failure'})});}else await route.continue();});
  await page.locator('#q').fill('Shield');
  await expect(page.locator('#error')).toContainText('Injected query failure');
  await expect(page.locator('#results tr')).toHaveCount(0);await expect(page.locator('#export')).toBeDisabled();
  await expect(page.locator('#range')).toHaveText('');await expect(page.locator('#page')).toHaveText('');
  await expect(page.locator('#results')).toHaveAttribute('aria-busy','false');
  await page.locator('#reset').click();await ready(page);await expect(page.locator('#error')).toBeHidden();
  await expect(page.locator('#results tr').first()).toBeVisible();
});


test('responsive boundaries preserve readable content and reduced-motion preferences',async({page},testInfo)=>{
  await visit(page);
  const id=await entry(page);await page.locator('#results .entry-name').first().click();await read(page,id);
  for(const width of [320,760,761,1199,1200,1280,1920]){
    await page.setViewportSize({width,height:800});
    await expect.poll(()=>page.locator('#detail').evaluate(e=>e.matches(':modal'))).toBe(width<1200);
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy();
    expect(await page.locator('#detail-content').evaluate(e=>e.scrollWidth<=e.clientWidth)).toBeTruthy();
    const bounds=await page.locator('#detail').boundingBox();
    expect(bounds.x).toBeGreaterThanOrEqual(0);expect(bounds.x+bounds.width).toBeLessThanOrEqual(width);
  }
  await expect(page.locator('#entry-next')).toHaveCSS('transition-duration','0s');
  await page.setViewportSize({width:320,height:640});
  await shot(page,testInfo,'small-mobile-reader.png');
  await page.locator('#close-detail').click();await page.locator('#toggle-filters').click();
  await expect(page.locator('#filter-panel')).toBeVisible();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy();
});
