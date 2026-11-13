/* Workbench regressions use the same real, validated corpus as links.spec.cjs.
   Network failure/race tests explicitly inject transport faults, not fake rules. */
const {test:base,expect}=require('@playwright/test');
const test=base.extend({context:async({context},use)=>{
  const errors=[];context.on('page',page=>page.on('pageerror',e=>errors.push(e.message)));
  await use(context);expect(errors,'uncaught query-interface errors').toEqual([]);
}});
const port=Number(process.env.ANNA_E2E_PORT||18380);
const anna=`http://127.0.0.1:${port}`;
const spells='/?type=spell';
async function ready(page){await expect(page.locator('#result-count')).toContainText('找到');await expect(page.locator('#results')).toHaveAttribute('aria-busy','false');}
async function visit(page,query=spells){await page.goto(anna+query);await ready(page);}
// Type is an ordinary field: choosing one type unchooses the others, then picks it.
async function chooseOnly(page,type){
  const panel=page.locator('#filter-panel');if(await panel.isHidden())await page.locator('#toggle-filters').click();
  const group=page.locator('[data-group="type"]');
  if(await group.locator('[data-action="clear"]').isEnabled()){await group.locator('[data-action="clear"]').click();await ready(page);}
  await group.locator(`input[value="${type}"]`).check({force:true});
}
async function entry(page,index=0){return page.locator('#results .entry-name').nth(index).getAttribute('data-entry');}
async function read(page,id){await expect(page.locator('#detail-content')).toHaveAttribute('aria-busy','false');await expect(page.locator('#detail .footnote')).toContainText(id);}
// Audit tabs belong to audit mode, a per-browser preference stored like the theme.
async function auditMode(page){await page.addInitScript(()=>{try{localStorage.setItem('anna.audit','on');}catch{}});}
const AUDIT_COLUMNS=['classification_status','legacy'];
const fmt=n=>n.toLocaleString('zh-CN');
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
  // Nothing chosen sets no condition; choosing values includes just those.
  await expect(page.getByRole('button',{name:'环阶清除',exact:true})).toBeDisabled();
  await expect(page.getByRole('checkbox',{name:'环阶：一环',exact:true})).not.toBeChecked();
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
  // Clearing removes every condition, the type included.
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
  await expect(page.locator('#active-filters .condition-text')).toHaveText(['类型：法术']);expect(new URL(page.url()).searchParams.has('f_spell_level')).toBeFalsy();
  await page.locator('#toggle-filters').click();
  await expect(page.locator('#filter-panel')).toBeVisible();await expect(page.locator('#toggle-filters')).toHaveAttribute('aria-expanded','true');
  await page.reload();await ready(page);await expect(page.locator('#filter-panel')).toBeVisible();
  // A new visit (a fresh tab, not a same-URL navigation that browsers treat as a reload) starts collapsed.
  const fresh=await page.context().newPage();await visit(fresh);await expect(fresh.locator('#filter-panel')).toBeHidden();
});

test('switching types keeps dimmed results and counts consistent until the new table renders',async({page})=>{
  await page.setViewportSize({width:1440,height:900});await visit(page);
  await page.locator('#toggle-filters').click();
  await page.getByRole('checkbox',{name:'类型：法术',exact:true}).uncheck({force:true});await ready(page);
  const range=await page.locator('#range').textContent();
  const columns=await page.locator('#columns th').count(),rows=await page.locator('#results tr').count();
  let release;const gate=new Promise(resolve=>release=resolve);let started;const waiting=new Promise(resolve=>started=resolve);
  await page.route('**/api/entries?*',async route=>{
    if(new URL(route.request().url()).searchParams.getAll('type').join()==='creature'){started();await gate;}
    await route.continue();
  });
  await chooseOnly(page,'creature');await waiting;
  await expect(page.locator('#title')).toHaveText('生物数据卡');
  await expect(page.locator('#results')).toHaveAttribute('aria-busy','true');
  // The previous rows remain under their own header instead of disappearing or mixing schemas.
  await expect(page.locator('#results tr')).toHaveCount(rows);await expect(page.locator('#columns th')).toHaveCount(columns);
  expect(await page.locator('#results tr').first().locator('td').count()).toBe(columns);
  await expect(page.locator('#range')).toHaveText(range);
  for(const id of ['#range','#load-more-row'])await expect(page.locator(id)).toHaveCSS('opacity','0.45');
  for(const id of ['#load-more','#export'])await expect(page.locator(id)).toBeDisabled();
  release();await ready(page);await page.unrouteAll({behavior:'wait'});
  const facets=await (await page.request.get(anna+'/api/facets?type=creature')).json();
  await expect(page.locator('#columns th')).toHaveCount(facets.schema.columns.length);
  expect(await page.locator('#results tr').first().locator('td').count()).toBe(facets.schema.columns.length);
  const data=await queryMatchesAPI(page);
  await expect(page.locator('#range')).toHaveText(`已显示 150 / ${fmt(data.total)} 行`);
  await expect(page.locator('#range')).toHaveCSS('opacity','1');await expect(page.locator('#load-more')).toBeEnabled();
  expect(await page.locator('.table-wrap').evaluate(e=>e.scrollTop)).toBe(0);
});

test('wide reader remains non-modal, consecutive entries and keyboard return preserve list position',async({page},testInfo)=>{
  await auditMode(page);await page.setViewportSize({width:1440,height:900});await visit(page);
  const first=await entry(page,8),second=await entry(page,9);
  await page.locator('#results .entry-name').nth(8).focus();
  await page.keyboard.press('Enter');await read(page,first);
  expect(await page.locator('#detail').evaluate(e=>e.matches(':modal'))).toBeFalsy();
  await expect(page.locator(`#results .entry-name[data-entry="${first}"]`)).toHaveAttribute('aria-current','true');
  await page.locator('#entry-next').click();await read(page,second);
  await expect(page.locator('#detail-position')).toHaveText(/^第 10 \/ [\d,]+ 行$/);
  // Stepping keeps the current row in sight below the pinned header; closing later leaves the list where it is.
  const current=await page.locator('#results tr.row-selected').boundingBox(),list=await page.locator('.table-wrap').boundingBox();
  expect(current.y).toBeGreaterThanOrEqual(list.y);expect(current.y+current.height).toBeLessThanOrEqual(list.y+list.height+1);
  const top=await page.locator('.table-wrap').evaluate(e=>e.scrollTop);
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
  expect(new URL(page.url()).searchParams.getAll('type')).toEqual(['spell']);
  expect(new URL(page.url()).searchParams.has('entry')).toBeFalsy();
  await page.locator('#results .entry-name').nth(8).click();await read(page,first);
  // A non-modal reader never blocks a different result from being clicked.
  await page.locator('#results .entry-name').nth(9).click();await read(page,second);
});

test('history and reload restore loaded rows, sorting, disclosure, reading tab and scroll positions',async({page})=>{
  await auditMode(page);await page.setViewportSize({width:1440,height:900});await visit(page,spells+'&size=50&page=2&sort=name&dir=desc');
  // Links from before batch loading keep their query; page and size fall away.
  const keys=[...new URL(page.url()).searchParams.keys()];expect(keys).not.toContain('page');expect(keys).not.toContain('size');
  await expect(page.locator('#results tr')).toHaveCount(150);
  await page.locator('#toggle-filters').click();
  await page.locator('#load-more').click();await expect(page.locator('#results tr')).toHaveCount(300);
  await page.locator('#results .entry-name').nth(160).focus();
  const first=await entry(page,160),second=await entry(page,161);
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
  await expect(page.locator('#results tr')).toHaveCount(300);await expect(page.locator('#range')).toContainText('已显示 300 /');
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
  await page.getByRole('checkbox',{name:'环阶：戏法',exact:true}).check();await ready(page);
  await expect(page.locator('#detail')).toBeHidden();expect(new URL(page.url()).searchParams.has('entry')).toBeFalsy();
  await page.locator('#results .entry-name').first().click();await read(page,await entry(page));
  // Choosing other types closes the reader and drops the conditions the new table cannot answer.
  await chooseOnly(page,'creature');await ready(page);
  await expect(page.locator('#detail')).toBeHidden();await expect(page.locator('#cr_min')).toBeVisible();
  await page.locator('#cr_min').selectOption('1/2');await page.locator('#cr_max').selectOption('2');
  const data=await queryMatchesAPI(page);expect(data.total).toBeGreaterThan(0);
  expect(new URL(page.url()).searchParams.has('f_spell_level')).toBeFalsy();
});

test('every type and preset shares the workbench without losing its columns or filters',async({page},testInfo)=>{
  test.setTimeout(150000);
  const meta=await (await page.request.get(anna+'/api/meta')).json();
  await page.setViewportSize({width:1440,height:900});
  const choices=[{search:'',title:'全部条目'},...meta.type_order.map(t=>({search:'type='+t,types:[t],title:meta.types[t]})),
    ...meta.presets.map(p=>({search:`type=${p.type}&f_${p.field}=${encodeURIComponent(p.value)}`,types:[p.type],title:p.name}))];
  for(const choice of choices){
    await visit(page,'/?'+choice.search);
    const facets=await (await page.request.get(anna+'/api/facets?'+new URLSearchParams((choice.types||[]).map(t=>['type',t])))).json();
    await expect(page.locator('#columns th')).toHaveCount(facets.schema.columns.filter(k=>!AUDIT_COLUMNS.includes(k)).length);
    for(const group of facets.groups)await expect(page.locator(`[data-group="${group.key}"]`)).toHaveCount(1);
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy();
    const id=await entry(page);await page.locator('#results .entry-name').first().click();await read(page,id);
    await page.locator('#close-detail').click();
    await page.setViewportSize({width:390,height:844});
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy();
    await expect(page.locator('#title')).toHaveText(choice.title);
    await page.setViewportSize({width:1440,height:900});
  }
  await visit(page,'/?type=magic_item');
  await page.locator('#toggle-filters').click();await page.locator('#domain-extra summary').click();
  await expect(page.getByRole('button',{name:'同调：需要',exact:true})).toBeVisible();
  await page.getByRole('button',{name:'同调：需要',exact:true}).click();await queryMatchesAPI(page);
  await shot(page,testInfo,'magic-items-filters.png');
});

test('type is an ordinary field: counts, include on click, invert, removable chip and clear all',async({page})=>{
  const meta=await (await page.request.get(anna+'/api/meta')).json();
  await page.setViewportSize({width:1440,height:900});await visit(page);
  await expect(page.locator('#title')).toHaveText('法术');
  await expect(page.locator('#title')).not.toHaveAttribute('role','button');
  await page.locator('#toggle-filters').click();
  const group=page.locator('[data-group="type"]');
  // Every type the data holds is an option, in reading order, under the same field layout as the others.
  expect(await group.locator('input').evaluateAll(els=>els.map(e=>e.value))).toEqual(meta.type_order);
  await expect(group.locator('legend')).toHaveText('类型');
  await expect(page.getByRole('checkbox',{name:'类型：法术',exact:true})).toBeChecked();
  // Adding a type keeps only the conditions every chosen type can answer, so the new type's entries appear.
  await visit(page,'/?type=creature&f_role='+encodeURIComponent('坐骑'));await page.locator('#toggle-filters').click();
  expect((await queryMatchesAPI(page)).versions_total).toBe(20);
  await page.getByRole('checkbox',{name:'类型：法术',exact:true}).check({force:true});await ready(page);
  expect(new URL(page.url()).searchParams.getAll('type')).toEqual(['spell','creature']);
  expect(new URL(page.url()).searchParams.has('f_role')).toBeFalsy();
  await expect(page.locator('#title')).toHaveText('法术、生物数据卡');
  await expect(page.getByRole('checkbox',{name:'类型：法术',exact:true})).toBeFocused();
  // Invert chooses every other type; the chip removes the field and returns to all entries.
  await page.getByRole('button',{name:'类型反选',exact:true}).click();await ready(page);
  expect(new URL(page.url()).searchParams.getAll('type')).not.toContain('spell');
  await page.locator('#active-filters [data-remove="type"]').click();await ready(page);
  await expect(page.locator('#title')).toHaveText('全部条目');expect(new URL(page.url()).search).toBe('');
  // Clear all removes every condition, types included, and keeps presentation choices.
  await visit(page,spells+'&q='+encodeURIComponent('火')+'&sort=name');
  await page.locator('#reset').click();await ready(page);
  expect(new URL(page.url()).search).toBe('?sort=name');
});

test('counts say what choosing each option would match, and an empty result offers conditions to drop',async({page})=>{
  await page.setViewportSize({width:1440,height:900});
  await visit(page,spells+'&q='+encodeURIComponent('火')+'&f_school='+encodeURIComponent('塑能'));
  const facets=await (await page.request.get(anna+'/api/facets'+new URL(page.url()).search)).json();
  const types=Object.fromEntries(facets.groups[0].options.map(o=>[o.value,o.count]));
  const others=Object.entries(types).filter(([t])=>t!=='spell').reduce((sum,[,n])=>sum+n,0);
  await expect(page.locator('#elsewhere')).toHaveText(`其他类型另有 ${fmt(others)} 条`);
  await page.locator('#elsewhere').click();await expect(page.locator('#filter-panel')).toBeVisible();
  await expect(page.locator('[data-group="type"] input').first()).toBeFocused();
  await expect(page.locator('[data-group="type"] input[value="magic_item"] + span .chip-count')).toHaveText(fmt(types.magic_item));
  const school=facets.groups.find(g=>g.key==='f_school').options.find(o=>o.value==='咒法');
  await expect(page.locator('[data-group="f_school"] input[value="咒法"] + span .chip-count')).toHaveText(fmt(school.count));
  // Counts follow every change without rebuilding the panel, so focus stays on the chosen option.
  await page.getByRole('checkbox',{name:'学派：咒法',exact:true}).check({force:true});await ready(page);
  await expect(page.getByRole('checkbox',{name:'学派：咒法',exact:true})).toBeFocused();
  // An empty result lists each condition whose removal brings results back.
  await visit(page,spells+'&q='+encodeURIComponent('成年红龙'));
  await expect(page.locator('#empty')).toBeVisible();
  await expect(page.locator('#empty-elsewhere [data-drop="type"]')).toContainText('去掉 类型：法术');
  await page.locator('#empty-elsewhere [data-drop="type"]').click();await ready(page);
  expect(new URL(page.url()).searchParams.has('type')).toBeFalsy();
  expect(new URL(page.url()).searchParams.get('q')).toBe('成年红龙');
  await expect(page.locator('#results .entry-name').first()).toHaveText('成年红龙');
  // Without a search, or across all types, there is nothing elsewhere to point at.
  await visit(page);await expect(page.locator('#elsewhere')).toBeHidden();
});

test('an empty name search offers unclassified passages under the same conditions',async({page})=>{
  await page.setViewportSize({width:1440,height:900});
  await visit(page,'/?q='+encodeURIComponent('盾矮人'));
  const data=await (await page.request.get(anna+'/api/entries'+new URL(page.url()).search)).json();
  expect(data.total).toBe(0);expect(data.section_matches).toBeGreaterThan(0);
  await expect(page.locator('#empty')).toBeVisible();
  await expect(page.locator('#empty-sections')).toBeVisible();
  await expect(page.locator('#show-sections')).toContainText(`原文中还有 ${fmt(data.section_matches)} 条未分类段落匹配`);
  await page.locator('#show-sections').click();await ready(page);
  expect(new URL(page.url()).searchParams.get('sections')).toBe('1');
  await expect(page.locator('#empty')).toBeHidden();
  await expect(page.locator('#results .entry-name')).toHaveCount(data.section_matches);
  // A typed result, a chosen type a section cannot answer, or a body-only search suppresses the offer.
  await visit(page,'/?q='+encodeURIComponent('幸运符'));
  await expect(page.locator('#empty-sections')).toBeHidden();
  await visit(page,'/?type=spell&q='+encodeURIComponent('盾矮人'));
  await expect(page.locator('#empty')).toBeVisible();
  await expect(page.locator('#empty-sections')).toBeHidden();
  await visit(page,'/?body='+encodeURIComponent('卓尔蘑菇扒'));
  await expect(page.locator('#empty')).toBeVisible();
  await expect(page.locator('#empty-sections')).toBeHidden();
});

test('environmental hazards read as their own query type and view',async({page})=>{
  await page.setViewportSize({width:1440,height:900});
  await visit(page,'/?type=hazard');
  const data=await queryMatchesAPI(page);
  expect(data.total).toBe(49);
  await expect(page.locator('#title')).toHaveText('环境危害');
  await page.locator('#toggle-filters').click();
  await expect(page.getByRole('checkbox',{name:'类型：环境危害',exact:true})).toBeChecked();
  await expect(page.locator('#results .entry-name').filter({hasText:'雪崩'})).toHaveCount(1);
  // The 2014 and 2024 guides both define thin ice; the two versions read as one stacked row.
  await expect(page.locator('#results .entry-name').filter({hasText:'薄冰'})).toHaveCount(1);
});

test('skills read by the ability they use and stack both rule generations',async({page})=>{
  await page.setViewportSize({width:1440,height:900});
  await visit(page,'/?type=skill');
  const data=await queryMatchesAPI(page);
  expect(data.total).toBe(18);
  await expect(page.locator('#title')).toHaveText('技能');
  const headers=await page.locator('#columns th').allTextContents();
  expect(headers.some(h=>h.includes('属性'))).toBeTruthy();
  // Ability order puts Strength's 运动 first; the 2014 and 2024 skill read as one stacked row.
  await expect(page.locator('#results .entry-name').first()).toContainText('运动');
  await expect(page.locator('#results tr').first().locator('td[data-label="属性"]')).toContainText('力量');
  await visit(page,'/?type=skill&f_ability='+encodeURIComponent('敏捷'));
  const dex=await queryMatchesAPI(page);
  expect(dex.rows.map(r=>r.name_zh).sort()).toEqual(['巧手','特技','隐匿'].sort());
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
  await expect(page.locator('#detail-position')).toHaveText('第 1 / 1 行');
  // The reader compares every version field by field and switches versions inside the same tab.
  await page.getByRole('tab',{name:/^版本对照/}).click();
  await expect(page.locator('.version-table tr.differs th').filter({hasText:'学派'})).toHaveCount(1);
  await expect(page.locator('.version-table tr.differs th').filter({hasText:'环阶'})).toHaveCount(0);
  await page.locator('.reader-versions').getByRole('button',{name:/PHB24/}).click();await read(page,shown.id);
  await expect(page.getByRole('tab',{name:/^版本对照/})).toHaveAttribute('aria-selected','true');
  await shot(page,testInfo,'stacked-versions-reader.png');
  // The edition preference changes the shown version without closing the reader; the menu marks a
  // presentation other than the default, and stays open while its choices change.
  await expect(page.locator('#display-toggle')).not.toHaveClass(/changed/);
  await page.locator('#display-toggle').click();await page.locator('#prefer').selectOption('5e');await ready(page);
  await expect(page.locator('#display-toggle')).toHaveClass(/changed/);await expect(page.locator('#display-panel')).toBeVisible();
  await expect(row.locator('.version-pill.shown')).toHaveAttribute('data-entry',other.id);
  await expect(school.locator('.cell-value')).toHaveText('变化');await expect(page.locator('#detail')).toBeVisible();
  expect(new URL(page.url()).searchParams.get('prefer')).toBe('5e');
  // One row per version stays available, kept in the URL and in history.
  await page.locator('#stack-versions').uncheck();await ready(page);
  expect(new URL(page.url()).searchParams.get('versions')).toBe('each');
  await expect(page.locator('#results tr')).toHaveCount(2);await expect(page.locator('#prefer')).toBeDisabled();
  await expect(page.locator('#results .version-pill')).toHaveCount(0);await expect(page.locator('#result-count')).toContainText('2 条');
  // Esc closes the menu before the reader.
  await page.keyboard.press('Escape');await expect(page.locator('#display-panel')).toBeHidden();
  await expect(page.locator('#display-toggle')).toBeFocused();await expect(page.locator('#detail')).toBeVisible();
  await page.goBack();await ready(page);
  await expect(page.locator('#stack-versions')).toBeChecked();await expect(page.locator('#results tr')).toHaveCount(1);
});

test('a field reads the same in the table, the reader attributes, the notes and the version comparison',async({page})=>{
  await page.setViewportSize({width:1440,height:900});await visit(page,spells+'&q='+encodeURIComponent('亡者丧钟'));
  const data=await queryMatchesAPI(page),head=data.rows[0];
  // The source text abbreviates warlock as 锁 on both versions; every place shows the normalized class.
  expect(head.versions.every(v=>v.fields.spell_classes==='牧 法 锁')).toBeTruthy();
  const row=page.locator('#results tr').first(),classes=row.locator('td[data-label="职业"]');
  await expect(classes.locator('.cell-value')).toHaveText('牧 法 契');await expect(classes).toHaveAttribute('title','牧师、法师、魔契师');
  // Rules versions read as rules generations; books read as catalog codes with the full name on hover.
  await expect(row.locator('td[data-label="规则版本"] .badge')).toHaveText('2024');
  await expect(row.locator('td[data-label="规则版本"] .badge')).toHaveAttribute('title','2024 版规则（5r）');
  await expect(row.locator('.source-cell abbr')).toHaveText('PHB24');await expect(row.locator('.source-cell abbr')).toHaveAttribute('title','玩家手册2024');
  await expect(row.locator('.version-pill')).toHaveText(['PHB24 2024','XGE 2014']);
  await row.locator('.entry-name').click();await read(page,head.id);
  await expect(page.locator('#detail .reader-type')).toHaveText('法术');
  await expect(page.locator('#detail .reader-ruleset')).toHaveText('2024 版规则（5r）');
  await expect(page.locator('#detail .reader-source')).toHaveText('玩家手册2024（PHB24）');
  await page.getByRole('tab',{name:'属性',exact:true}).click();
  const attributes=page.locator('.detail-fields');
  const names=await attributes.locator('dt').allTextContents(),values=await attributes.locator('dd').allTextContents();
  const at=name=>values[names.indexOf(name)];
  expect(at('职业')).toBe('牧师、法师、魔契师');expect(at('材料')).toBe('否');expect(at('规则版本')).toBe('2024 版规则（5r）');
  // The type's table columns lead, a detail follows its field, and fields naming the version come last.
  expect(names.slice(0,4)).toEqual(['环阶','学派','职业','施法时间']);
  expect(names[names.indexOf('施法时间')+1]).toBe('施法时间原文');
  expect(names.slice(-4)).toEqual(['类型','来源','来源缩写','规则版本']);
  await page.getByRole('tab',{name:/^版本对照/}).click();
  const classRow=page.locator('.version-table tr').filter({has:page.locator('th',{hasText:/^职业$/})});
  await expect(classRow.locator('td')).toHaveText(['牧师、法师、魔契师','牧师、法师、魔契师']);
});

test('compact rows join names and versions on one line, as a per-browser preference above a 13px floor',async({page})=>{
  await page.setViewportSize({width:1440,height:900});await visit(page,spells+'&q='+encodeURIComponent('亡者丧钟'));
  // Chinese interface text stays at 13px or more; 12px is left to codes, counts and English names.
  for(const selector of ['.result-actions label','#result-count','.bottom-bar'])
    expect(parseFloat(await page.locator(selector).first().evaluate(e=>getComputedStyle(e).fontSize))).toBeGreaterThanOrEqual(13);
  const row=page.locator('#results tr').first(),comfortable=(await row.boundingBox()).height;
  const url=page.url();await page.locator('#display-toggle').click();await page.locator('#compact-rows').check();
  await expect(page.locator('html')).toHaveAttribute('data-density','compact');
  const name=await row.locator('.entry-name').boundingBox(),english=await row.locator('.english').boundingBox();
  expect(english.y).toBeLessThan(name.y+name.height);expect(english.x).toBeGreaterThan(name.x+name.width);
  expect((await row.boundingBox()).height).toBeLessThan(comfortable*.7);expect(page.url()).toBe(url);
  await page.reload();await ready(page);
  await expect(page.locator('#compact-rows')).toBeChecked();await expect(page.locator('html')).toHaveAttribute('data-density','compact');
  await page.locator('#display-toggle').click();await page.locator('#compact-rows').uncheck();await expect(page.locator('html')).toHaveAttribute('data-density','comfortable');
  expect(await page.evaluate(()=>localStorage.getItem('anna.density'))).toBeNull();
});

test('the condition bar keeps each search as its own ∧-joined chip and turns typed values into conditions',async({page})=>{
  await page.setViewportSize({width:1440,height:900});await visit(page,spells);
  const q=page.locator('#q'),chips=page.locator('#active-filters');
  // The everyday path: typing filters live; Enter keeps the search and empties the field for the next one.
  // The text being typed is `typing`, not yet a kept search `q`.
  await q.fill('火');await expect(page).toHaveURL(/typing=%E7%81%AB/);await ready(page);
  await expect(page.getByRole('option').first()).toContainText('包含“火”');
  await q.press('Enter');await expect(q).toHaveValue('');await expect(q).toBeFocused();
  await q.fill('球');await expect.poll(()=>new URL(page.url()).searchParams.get('typing')).toBe('球');await ready(page);
  expect(new URL(page.url()).searchParams.getAll('q')).toEqual(['火']);
  await q.press('Enter');
  // Every condition is its own chip; chips always join with ∧.
  await expect(chips.locator('.condition-text')).toHaveText(['类型：法术','名称：火','名称：球']);
  await expect(chips.locator('.and')).toHaveText(['∧','∧']);
  const both=await queryMatchesAPI(page);
  expect(both.rows.every(r=>r.name.includes('火')&&r.name.includes('球'))).toBeTruthy();
  await expect(page.locator('#filter-count')).toHaveText('3');
  // A typed field value is offered after the name search; choosing it adds the value, not a name search.
  await q.fill('塑能');await expect(page.getByRole('option',{name:/学派\s*塑能/})).toBeVisible();
  await q.press('ArrowDown');await q.press('ArrowDown');await expect(q).toHaveAttribute('aria-activedescendant','suggestion-2');
  await q.press('Enter');await ready(page);
  expect(new URL(page.url()).searchParams.getAll('q')).toEqual(['火','球']);
  expect(new URL(page.url()).searchParams.getAll('f_school')).toEqual(['塑能']);
  await expect(chips).toContainText('学派：塑能');
  // The second suggestion searches the entry text instead of the name.
  await q.fill('专注');await expect(page.getByRole('option').nth(1)).toContainText('正文');
  await q.press('ArrowDown');await q.press('Enter');await ready(page);
  expect(new URL(page.url()).searchParams.getAll('body')).toEqual(['专注']);
  expect(new URL(page.url()).searchParams.has('typing')).toBeFalsy();
  await expect(chips.locator('[data-flip="body0"]')).toHaveText('正文：专注');
  await chips.locator('[data-remove="body0"]').click();await ready(page);
  expect(new URL(page.url()).searchParams.has('body')).toBeFalsy();
  // Types and presets are found the same way.
  await q.fill('坐骑');await page.getByRole('option',{name:/预设\s*坐骑/}).click();await ready(page);
  expect(new URL(page.url()).searchParams.getAll('type')).toEqual(['creature']);
  expect(new URL(page.url()).searchParams.getAll('f_role')).toEqual(['坐骑']);
  await expect(page.locator('#title')).toHaveText('坐骑');
  // Esc closes the suggestions first; Backspace in an empty field removes the last chip.
  await q.fill('x');await expect(page.locator('#suggestions')).toBeVisible();
  await q.press('Escape');await expect(page.locator('#suggestions')).toBeHidden();await expect(q).toBeFocused();
  await q.fill('');await ready(page);
  const before=await chips.locator('[data-remove]').count();
  await q.press('Backspace');await ready(page);
  await expect(chips.locator('[data-remove]')).toHaveCount(before-1);
  // A reload returns the text being typed to the field, not to a chip.
  await q.fill('冰');await expect(page).toHaveURL(/typing=/);await ready(page);
  await page.reload();await ready(page);
  await expect(q).toHaveValue('冰');await expect(chips.locator('[data-remove="typing"]')).toHaveCount(0);
  // A term's × removes only that term; a reload shows every kept search as a chip.
  await visit(page,spells+'&q='+encodeURIComponent('火')+'&q='+encodeURIComponent('球'));
  await expect(q).toHaveValue('');await expect(chips.locator('[data-remove^="q"]')).toHaveCount(2);
  await chips.locator('[data-remove="q0"]').click();await ready(page);
  expect(new URL(page.url()).searchParams.getAll('q')).toEqual(['球']);
});

test('any chip or field flips between is and is not; excluded values keep entries without them',async({page})=>{
  await page.setViewportSize({width:1440,height:900});
  await visit(page,spells+'&q='+encodeURIComponent('火')+'&f_school='+encodeURIComponent('塑能'));
  const chips=page.locator('#active-filters'),url=()=>new URL(page.url()).searchParams;
  const all=(await queryMatchesAPI(page)).versions_total;
  // The chip's text flips it; its × still removes it.
  await chips.locator('[data-flip="f_school"]').click();await ready(page);
  expect(url().getAll('not_f_school')).toEqual(['塑能']);expect(url().has('f_school')).toBeFalsy();
  await expect(chips.locator('.condition.negated .condition-text')).toHaveText(['学派不是：塑能']);
  const notSchool=await queryMatchesAPI(page);
  expect(notSchool.rows.every(r=>!r.quickref.school.includes('塑能'))).toBeTruthy();
  // Name and body searches flip one term at a time.
  await chips.locator('[data-flip="q0"]').click();await ready(page);
  expect(url().getAll('not_q')).toEqual(['火']);expect(url().has('q')).toBeFalsy();
  const notFire=await queryMatchesAPI(page);
  expect(notFire.rows.every(r=>!r.name.includes('火'))).toBeTruthy();
  await chips.locator('[data-flip="not_q0"]').click();await ready(page);
  expect(url().getAll('q')).toEqual(['火']);expect(url().has('not_q')).toBeFalsy();
  // The panel edits whichever list the field uses; 排除 flips the whole field.
  await page.locator('#toggle-filters').click();
  const school=page.locator('[data-group="f_school"]');
  await expect(school).toHaveClass(/excluding/);await expect(school.locator('[data-action="exclude"]')).toHaveAttribute('aria-pressed','true');
  await page.getByRole('checkbox',{name:'学派：咒法',exact:true}).check({force:true});await ready(page);
  expect(url().getAll('not_f_school')).toEqual(['塑能','咒法']);
  await school.locator('[data-action="exclude"]').click();await ready(page);
  expect(url().getAll('f_school')).toEqual(['塑能','咒法']);expect(url().has('not_f_school')).toBeFalsy();
  // A suggestion always includes; an excluded value it names leaves the excluded list.
  await visit(page,spells+'&not_f_school='+encodeURIComponent('塑能'));
  await page.locator('#q').fill('塑能');await page.getByRole('option',{name:/学派\s*塑能/}).click();await ready(page);
  expect(url().getAll('f_school')).toEqual(['塑能']);expect(url().has('not_f_school')).toBeFalsy();
  // Excluded types read in the heading; ranges and the conflict switch cannot flip.
  await visit(page,'/?not_type=spell&not_type=lore');
  await expect(page.locator('#title')).toHaveText('全部条目（不含法术、资料索引）');
  await visit(page,'/?type=creature&cr_min=1');await expect(chips.locator('[data-flip="cr_min"]')).toHaveCount(0);
  expect(all).toBeGreaterThan(0);
});

test('a search ranks closer names first until the reader picks a sort',async({page})=>{
  await page.setViewportSize({width:1440,height:900});await visit(page,spells);
  await page.locator('#q').fill('火球');await expect(page).toHaveURL(/typing=/);await ready(page);
  await queryMatchesAPI(page);
  await expect(page.locator('#results .entry-name')).toHaveText(['火球术','延迟爆裂火球']);
  await expect(page.locator('#sort')).toHaveValue('match');await expect(page.locator('#sort option').first()).toHaveText('匹配度');
  expect(new URL(page.url()).searchParams.has('sort')).toBeFalsy();
  // A chosen sort is recorded and kept while searching; clearing the search drops the match option.
  await page.locator('#sort').selectOption('name');await ready(page);
  expect(new URL(page.url()).searchParams.get('sort')).toBe('name');await queryMatchesAPI(page);
  // The draft search leaves with the text; the chosen sort stays.
  await page.locator('#q').fill('');await expect(page).not.toHaveURL(/typing=/);await ready(page);
  await expect(page.locator('#sort option[value="match"]')).toHaveCount(0);await expect(page.locator('#sort')).toHaveValue('name');
});

test('phones hide keyboard hints and keep the filter panel within the screen',async({browser})=>{
  const context=await browser.newContext({viewport:{width:390,height:844},hasTouch:true,isMobile:true});
  const page=await context.newPage();await visit(page,'/?type=feat');
  await page.locator('#toggle-filters').click();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy();
  await expect(page.getByRole('checkbox',{name:'类型：专长',exact:true})).toBeChecked();
  await page.locator('#toggle-filters').click();
  await expect(page.locator('.searchbox kbd')).toBeHidden();
  // Version pills are the one target inside a row, so on touch they keep a fingertip's height.
  expect((await page.locator('#results .version-pill').first().boundingBox()).height).toBeGreaterThanOrEqual(30);
  await page.locator('#results .entry-name').first().click();
  await expect(page.locator('#detail .reader-hint')).toHaveText('用上方箭头连续查阅结果');
  // The reader already fills the phone, so full-width reading is not offered there.
  await expect(page.locator('#reader-full')).toBeHidden();
  await context.close();
});

test('stacking never merges class features from different classes',async({page})=>{
  await page.setViewportSize({width:1440,height:900});await visit(page,'/?type=class_feature&q='+encodeURIComponent('属性值提升'));
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
  // At 1280px the side reader is too narrow for text and margin both, so the notes follow the text.
  await page.setViewportSize({width:1280,height:800});
  await expect.poll(async()=>{const n=await note.boundingBox(),box=await layout.boundingBox();return n.y>=box.y+box.height;}).toBeTruthy();
  await page.setViewportSize({width:1100,height:820});
  // An unavailable or changed source never yields a partial layout; extracted text stands in with the reason.
  await page.route('**/api/entry-layout?*',route=>route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({status:'stale',path:'x'})}));
  await page.locator('.reader-versions').getByRole('button',{name:/PHB 2014/}).click();await read(page,other.id);
  await expect(page.locator('#detail-panel .layout-notice')).toContainText('原文已更新');
  await expect(page.locator('#detail-panel .body-text')).toContainText('以太');
  await expect(page.locator('#detail-panel .layout-text')).toHaveCount(0);
});

test('reader sets label lines as a parameter card, starts after the title and groups audit tabs',async({page})=>{
  await auditMode(page);await page.setViewportSize({width:1440,height:900});await visit(page,spells+'&q='+encodeURIComponent('亡者丧钟'));
  await page.locator('#results .entry-name').first().click();
  const card=page.locator('#detail-panel .layout-text dl.parameters');
  await expect(card.locator('dt')).toHaveText(['施法时间','施法距离','法术成分','持续时间']);await expect(card.locator('dd').first()).toHaveText('动作');
  expect(await card.evaluate(e=>getComputedStyle(e).gridTemplateColumns.split(' ').length)).toBe(2);
  await expect(page.locator('#detail-panel .layout-text')).not.toContainText('Toll the Dead');
  // Reading tabs lead; audit tabs follow their group label, and a tab with nothing in it is faded.
  const tabs=page.locator('.detail-tabs');await expect(tabs.locator('.tab-group')).toHaveText('核对');
  expect(await tabs.locator('[role=tab]').evaluateAll(els=>els.map(e=>e.dataset.tab))).toEqual(['body','fields','versions','relations','facts','discoveries','references','reviews']);
  for(const tab of await tabs.locator('[role=tab]').all()){
    const count=(await tab.textContent()).match(/ (\d+)$/)?.[1];
    if(count!==undefined)await expect(tab).toHaveClass(count==='0'?/\bempty\b/:/^(?!.*\bempty\b)/);
  }
  // The extracted-text fallback starts after the title as well.
  await page.route('**/api/entry-layout?*',route=>route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({status:'missing',path:'x'})}));
  await page.locator('.reader-versions').getByRole('button',{name:/XGE/}).click();
  await expect(page.locator('#detail-panel .body-text')).toContainText('施法时间');
  await expect(page.locator('#detail-panel .body-text')).not.toContainText('亡者丧钟｜');
});

test('stat blocks keep their tables in the reader without widening the page',async({page})=>{
  for(const [width,height] of [[1440,900],[390,844]]){
    await page.setViewportSize({width,height});
    await visit(page,'/?type=creature&q='+encodeURIComponent('成年红龙'));
    await page.locator('#results .entry-name').first().click();
    const tables=page.locator('#detail-panel .layout-text table');await expect(tables.first()).toBeVisible();
    expect(await tables.count()).toBeGreaterThanOrEqual(2);
    await expect(page.locator('#detail-panel .layout-text td.short').first()).toHaveCSS('white-space','nowrap');
    expect(await page.locator('#detail-content').evaluate(e=>e.scrollWidth<=e.clientWidth)).toBeTruthy();
    await page.locator('#close-detail').click();
  }
});

test('opening, stepping and closing an entry leave the heading and every row where they were',async({page})=>{
  await page.setViewportSize({width:1440,height:900});await visit(page,spells+'&f_spell_level=1&f_spell_level=2&sort=name');
  const row=n=>page.locator('#results tr').nth(n);
  const place=async n=>{const b=await row(n).locator('.name-cell').boundingBox();return [b.x,b.y,b.width,b.height].map(Math.round);};
  const places=async()=>[await place(3),await place(4)];
  const lines=()=>page.locator('.heading-row,.toolbar,#active-filters,.results-bar').evaluateAll(els=>els.map(e=>Math.round(e.getBoundingClientRect().height)));
  const before=await places(),chrome=await lines();
  // Before: the rail folded to 44px and the results turned into a list, moving a clicked row from y=587 to y=854.
  const id=await entry(page,3);await row(3).locator('td[data-label="学派"]').click();await read(page,id);
  expect(await places()).toEqual(before);expect(await lines()).toEqual(chrome);
  await expect(page.locator('#columns')).toBeVisible();await expect(page.locator('#title')).toBeVisible();
  // The reader takes its share of the workspace.
  expect(Math.round((await page.locator('#detail').boundingBox()).width)).toBe(Math.round(1440*.6));
  // Another row opens in place of the first; closing leaves every row and line where it was.
  const next=await entry(page,4);await row(4).locator('.english').click();await read(page,next);
  expect(await places()).toEqual(before);
  await page.keyboard.press('Escape');await expect(page.locator('#detail')).toBeHidden();
  expect(await places()).toEqual(before);expect(await lines()).toEqual(chrome);
});

test('the reader starts its text high, keeps its tabs pinned and steps without a loading flash',async({page},testInfo)=>{
  await page.setViewportSize({width:1440,height:900});await visit(page,'/?type=class&q='+encodeURIComponent('法师'));
  const id=await entry(page);await page.locator('#results .entry-name').first().click();await read(page,id);
  const reader=await page.locator('#detail').boundingBox();
  const panel=await page.locator('#detail-panel').boundingBox();expect(panel.y-reader.y).toBeLessThan(240);
  // The text and its source layout arrive together, so the body appears once, already in the book's layout.
  await expect(page.locator('#detail-panel .layout-text')).toBeVisible();await expect(page.locator('#detail-panel .body-text')).toHaveCount(0);
  // Scrolling the text keeps the tabs at the top of the reader, and the toolbar takes over the name.
  await expect(page.locator('#reader-name')).toBeHidden();
  await page.locator('#detail-content').evaluate(e=>e.scrollTop=1200);
  const content=await page.locator('#detail-content').boundingBox();
  await expect.poll(async()=>Math.round((await page.locator('.detail-tabs').boundingBox()).y)).toBe(Math.round(content.y));
  await expect(page.locator('#reader-name')).toBeVisible();await expect(page.locator('#reader-name')).toHaveText('法师');
  await expect(page.locator('#reader-title')).toBeHidden();
  await shot(page,testInfo,'reader-pinned-tabs.png');
  // Stepping keeps the current entry until the next one is ready: no loading line, no extracted-text stand-in.
  await visit(page,spells+'&sort=name');await page.locator('#results .entry-name').first().click();await read(page,await entry(page));
  await page.locator('#detail-content').evaluate(e=>e.scrollTop=200);
  await page.locator('#detail-content').evaluate(e=>{window.readerInserts=[];new MutationObserver(ms=>{for(const m of ms)for(const n of m.addedNodes)if(n.nodeType===1)window.readerInserts.push(n.className);}).observe(e,{childList:true,subtree:true});});
  const second=await entry(page,1);await page.locator('#entry-next').click();await read(page,second);
  const inserted=await page.evaluate(()=>window.readerInserts);
  expect(inserted).not.toContain('reader-message');expect(inserted.some(c=>String(c).includes('body-text'))).toBeFalsy();
  expect(await page.locator('#detail-content').evaluate(e=>e.scrollTop)).toBe(0);
});

test('the reader width is a per-browser preference set by dragging or the separator keys',async({page})=>{
  await page.setViewportSize({width:1440,height:900});await visit(page);
  const id=await entry(page);await page.locator('#results .entry-name').first().click();await read(page,id);
  const separator=page.getByRole('separator',{name:'调整阅读栏宽度'}),width=async()=>Math.round((await page.locator('#detail').boundingBox()).width);
  // Shares are of the whole 1440px workspace.
  await expect(separator).toHaveAttribute('aria-valuenow','60');expect(await width()).toBe(Math.round(1440*.6));
  await separator.focus();await page.keyboard.press('ArrowLeft');
  await expect(separator).toHaveAttribute('aria-valuenow','62');expect(await width()).toBe(Math.round(1440*.62));
  // Home and End stop at the bounds: the reader keeps 400px and the results keep 360px.
  await page.keyboard.press('Home');expect(await width()).toBe(400);
  await page.keyboard.press('End');expect(await width()).toBe(1440-360);
  await expect(separator).toHaveAttribute('aria-valuetext','阅读栏占 75%');
  // Dragging the separator follows the pointer; the share survives a reload but never enters the URL.
  const box=await separator.boundingBox();
  await page.mouse.move(box.x+2,box.y+300);await page.mouse.down();await page.mouse.move(720,400,{steps:4});await page.mouse.up();
  expect(await width()).toBe(720);
  expect(await page.evaluate(()=>localStorage.getItem('anna.readerShare'))).toBe('0.500');
  const url=page.url();await page.reload();await ready(page);await read(page,id);
  expect(await width()).toBe(720);expect(page.url()).toBe(url);
  // Double-clicking restores the default and forgets the preference.
  await separator.dblclick();expect(await width()).toBe(Math.round(1440*.6));
  expect(await page.evaluate(()=>localStorage.getItem('anna.readerShare'))).toBeNull();
  // The modal reader has no separator.
  await page.setViewportSize({width:1100,height:820});await expect(separator).toBeHidden();
});

test('full-width reading shows a wide table whole and F6 gives the results their place back',async({page},testInfo)=>{
  await page.setViewportSize({width:1280,height:800});await visit(page,'/?type=class&q='+encodeURIComponent('法师'));
  const id=await entry(page);await page.locator('#results .entry-name').first().click();await read(page,id);
  // The class table is wider than the side reader: it scrolls inside its own frame and offers full width.
  const wide=page.locator('#detail-panel .table-frame.overflowing');await expect(wide).toHaveCount(1);
  await expect(wide.locator('.table-wide-hint')).toBeVisible();
  expect(await page.locator('#detail-content').evaluate(e=>e.scrollWidth<=e.clientWidth)).toBeTruthy();
  await wide.locator('.table-wide-hint').click();
  await expect(page.locator('#reader-full')).toHaveAttribute('aria-pressed','true');await expect(page.locator('#reader-full')).toBeFocused();
  const reader=await page.locator('#detail').boundingBox();expect(reader.x).toBe(0);expect(reader.width).toBe(1280);
  await expect(page.locator('#query-main')).toHaveCSS('visibility','hidden');
  await expect(page.locator('#detail-panel .table-frame.overflowing')).toHaveCount(0);
  await expect(page.locator('#detail-panel .table-wide-hint:visible')).toHaveCount(0);
  await shot(page,testInfo,'full-width-class-table.png');
  // Full-width reading belongs to this reading: the tab's history restores it on reload, and the URL stays the same.
  const url=page.url();await page.reload();await ready(page);await read(page,id);
  await expect(page.locator('#reader-full')).toHaveAttribute('aria-pressed','true');expect(page.url()).toBe(url);
  // F6 heads for the results, so they come back first.
  await page.locator('#reader-full').focus();await page.keyboard.press('F6');
  await expect(page.locator(`#results .entry-name[data-entry="${id}"]`)).toBeFocused();
  await expect(page.locator('#reader-full')).toHaveAttribute('aria-pressed','false');
  await expect(page.locator('#query-main')).toHaveCSS('visibility','visible');
  // Below 1200px the modal reader widens to the whole window.
  await page.setViewportSize({width:1100,height:820});await page.locator('#reader-full').click();
  const modal=await page.locator('#detail').boundingBox();expect(modal.x).toBe(0);expect(modal.width).toBe(1100);
  await page.locator('#reader-full').click();expect((await page.locator('#detail').boundingBox()).width).toBe(920);
});

test('a whole result row opens its entry, and a double click reads it at full width',async({page})=>{
  await page.setViewportSize({width:1440,height:900});await visit(page,spells+'&sort=name');
  const row=n=>page.locator('#results tr').nth(n),cell=n=>row(n).locator('td[data-label="学派"]');
  // A click on a plain cell reads the row's entry beside the results.
  const second=await entry(page,1);await cell(1).click();await read(page,second);
  await expect(page.locator('#reader-full')).toHaveAttribute('aria-pressed','false');
  await page.keyboard.press('Escape');await expect(page.locator('#detail')).toBeHidden();
  // Selecting text in a row reads nothing, so values stay copyable.
  const english=row(1).locator('.english'),box=await english.boundingBox();
  await page.mouse.move(box.x+1,box.y+box.height/2);await page.mouse.down();
  await page.mouse.move(box.x+box.width-1,box.y+box.height/2,{steps:5});await page.mouse.up();
  expect(await page.evaluate(()=>getSelection().toString().length)).toBeGreaterThan(0);
  await expect(page.locator('#detail')).toBeHidden();
  // The first click of a double click opens the entry; the second lands on the same row, which has not moved,
  // and only asks for full width: it neither reopens the row nor selects its text.
  const third=await entry(page,2),visits=await page.evaluate(()=>history.length);
  await cell(2).dblclick();await read(page,third);
  expect(await page.evaluate(()=>history.length)).toBe(visits+1);
  await expect(page.locator('#reader-full')).toHaveAttribute('aria-pressed','true');
  await expect(page.locator('#query-main')).toHaveCSS('visibility','hidden');
  expect(new URL(page.url()).searchParams.get('entry')).toBe(third);
  expect(await page.evaluate(()=>getSelection().toString())).toBe('');
  // Full-width reading lasts one reading: stepping keeps it, closing ends it.
  await page.locator('#entry-next').click();await read(page,await entry(page,3));
  await expect(page.locator('#reader-full')).toHaveAttribute('aria-pressed','true');
  await page.keyboard.press('Escape');await expect(page.locator('#detail')).toBeHidden();
  await row(0).locator('.entry-name').click();await read(page,await entry(page,0));
  await expect(page.locator('#reader-full')).toHaveAttribute('aria-pressed','false');
  // Below 1200px a double click opens the modal reader across the whole window.
  await page.keyboard.press('Escape');await page.setViewportSize({width:1100,height:820});
  await cell(1).dblclick();await read(page,second);
  const modal=await page.locator('#detail').boundingBox();expect(modal.x).toBe(0);expect(modal.width).toBe(1100);
});

test('scrolling near the end appends exactly the next batch and keyboard focus continues into it',async({page},testInfo)=>{
  await page.setViewportSize({width:1440,height:900});await visit(page,spells+'&sort=name');
  const data=await queryMatchesAPI(page);
  await expect(page.locator('#range')).toHaveText(`已显示 150 / ${fmt(data.total)} 行`);
  const offsets=[];page.on('request',r=>{if(r.url().includes('/api/entries?'))offsets.push(new URL(r.url()).searchParams.get('offset'));});
  await page.locator('.table-wrap').evaluate(e=>e.scrollTop=e.scrollHeight);
  await expect(page.locator('#results tr')).toHaveCount(300);
  await page.waitForTimeout(500);expect(offsets).toEqual(['150']);
  // Appended rows continue the API order without gaps or repeats, still dim-free.
  const next=await (await page.request.get(anna+'/api/entries'+new URL(page.url()).search+'&offset=150')).json();
  const ids=await page.locator('#results .entry-name').evaluateAll(els=>els.map(e=>e.dataset.entry));
  expect(ids.slice(150)).toEqual(next.rows.map(r=>r.id));expect(new Set(ids).size).toBe(300);
  await expect(page.locator('#range')).toHaveText(`已显示 300 / ${fmt(data.total)} 行`);
  await expect(page.locator('#results')).toHaveAttribute('aria-busy','false');
  await shot(page,testInfo,'batch-loading.png');
  await page.locator('#results .entry-name').nth(299).focus();await page.keyboard.press('ArrowDown');
  await expect(page.locator('#results .entry-name').nth(300)).toBeFocused();
  expect(new URL(page.url()).searchParams.has('offset')).toBeFalsy();
  // The last batch hides the button and states that everything is shown.
  for(let i=0;i<10&&await page.locator('#load-more-row').isVisible();i++){await page.locator('#load-more').click();await expect(page.locator('#load-more')).not.toHaveAttribute('aria-busy','true');}
  await expect(page.locator('#results tr')).toHaveCount(data.total);
  await expect(page.locator('#range')).toHaveText(`已全部显示 ${fmt(data.total)} 行`);await expect(page.locator('#load-more-row')).toBeHidden();
});

test('the reader steps past the loaded rows and waits for the batch already in flight',async({page})=>{
  await page.setViewportSize({width:1440,height:900});await visit(page,spells+'&sort=name');
  const data=await queryMatchesAPI(page);
  let release;const gate=new Promise(resolve=>release=resolve);
  await page.route('**/api/entries?*',async route=>{if(new URL(route.request().url()).searchParams.get('offset')==='150')await gate;await route.continue();});
  const last=await entry(page,149);await page.locator('#results .entry-name').nth(149).click();await read(page,last);
  await expect(page.locator('#detail-position')).toHaveText(`第 150 / ${fmt(data.total)} 行`);
  await expect(page.locator('#entry-next')).toBeEnabled();
  await page.locator('#entry-next').click();
  await expect(page.locator('#load-more')).toHaveText('正在加载…');
  release();
  const following=await (await page.request.get(anna+'/api/entries'+new URL(page.url()).search.replace(/&?entry=[^&]*/,'')+'&offset=150&limit=1')).json();
  await read(page,following.rows[0].id);
  await expect(page.locator('#detail-position')).toHaveText(`第 151 / ${fmt(data.total)} 行`);
  await expect(page.locator('#results tr')).toHaveCount(300);
});

test('a failed batch keeps the loaded rows and retries from the same place',async({page})=>{
  await page.setViewportSize({width:1440,height:900});await visit(page,spells+'&sort=name');
  let fail=true;
  await page.route('**/api/entries?*',async route=>{
    if(fail&&new URL(route.request().url()).searchParams.get('offset')==='150'){fail=false;await route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({error:'Injected batch failure'})});}
    else await route.continue();
  });
  await page.locator('.table-wrap').evaluate(e=>e.scrollTop=e.scrollHeight);
  await expect(page.locator('#load-error')).toContainText('Injected batch failure');
  await expect(page.locator('#results tr')).toHaveCount(150);await expect(page.locator('#load-more')).toHaveText('重新加载');
  await expect(page.locator('#error')).toBeHidden();await expect(page.locator('#results')).toHaveAttribute('aria-busy','false');
  await page.locator('#load-more').click();
  await expect(page.locator('#results tr')).toHaveCount(300);await expect(page.locator('#load-error')).toBeHidden();
  await expect(page.locator('#load-more')).toHaveText('继续加载');
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

test('IME commits in quick succession never keep a search without Enter',async({page})=>{
  await visit(page,spells);const q=page.locator('#q');await q.focus();
  // One commit as Chromium reports it: a still-composing input event inside the composition events.
  const commit=text=>q.evaluate((el,text)=>{el.dispatchEvent(new CompositionEvent('compositionstart'));el.value+=text;
    el.dispatchEvent(new InputEvent('input',{isComposing:true,data:text}));el.dispatchEvent(new CompositionEvent('compositionend',{data:text}));},text);
  // Before: the second commit landed inside the 160ms debounce and “火” became a chip (q=火&q=火球术).
  await commit('火');await expect(page).toHaveURL(/typing=%E7%81%AB$/);await ready(page);
  await commit('球');await page.waitForTimeout(60);await commit('术');
  await expect.poll(()=>new URL(page.url()).searchParams.get('typing')).toBe('火球术');await ready(page);
  expect(new URL(page.url()).searchParams.has('q')).toBeFalsy();
  await expect(page.locator('#active-filters [data-remove^="q"]')).toHaveCount(0);
  // Only Enter keeps it; the next commits stay a draft beside it.
  await q.press('Enter');await expect(page.locator('#active-filters [data-flip="q0"]')).toHaveText('名称：火球术');
  await commit('塑');await page.waitForTimeout(30);await commit('能');
  await expect.poll(()=>new URL(page.url()).searchParams.get('typing')).toBe('塑能');await ready(page);
  expect(new URL(page.url()).searchParams.getAll('q')).toEqual(['火球术']);
  await expect(page.locator('#active-filters [data-remove^="q"]')).toHaveCount(1);
  // Safari sends the key that ends a composition afterwards, as keyCode 229; it keeps nothing.
  await q.evaluate(el=>el.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',keyCode:229,bubbles:true})));
  await expect(page.locator('#active-filters [data-remove^="q"]')).toHaveCount(1);await expect(q).toHaveValue('塑能');
});

test('Chinese IME composition never submits intermediate query text',async({page})=>{
  await visit(page);const requests=[];
  page.on('request',r=>{if(r.url().includes('/api/entries?'))requests.push(new URL(r.url()).searchParams.get('typing'));});
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
  await expect(page.locator('#range')).toHaveText('');await expect(page.locator('#load-more-row')).toBeHidden();
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
