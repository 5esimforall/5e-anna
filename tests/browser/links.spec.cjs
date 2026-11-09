const {test:base,expect} = require('@playwright/test');
const fs = require('node:fs/promises');
const {PNG} = require('pngjs');
const port=Number(process.env.ANNA_E2E_PORT || 18380);
const original=`http://127.0.0.1:${port}`, anna=`http://127.0.0.1:${port+1}`, book=`http://127.0.0.1:${port+2}`;
const topic=(origin,path)=>origin+'/'+path.split('/').map(encodeURIComponent).join('/');
// Uncaught JS errors and failed bindings must fail the test, including popup/frame errors.
const test=base.extend({context:async({context},use)=>{
  const errors=[];
  context.on('page',page=>{
    page.on('pageerror',e=>errors.push(e.message));
    page.on('console',m=>{if(m.type()==='warning' && m.text().startsWith('anna:'))errors.push(m.text());});
  });
  await context.route('**/*',route=>{
    const url=new URL(route.request().url());
    return ['127.0.0.1','localhost'].includes(url.hostname) || ['data:','about:'].includes(url.protocol) ? route.continue() : route.abort();
  });
  await use(context);
  expect(errors,'browser errors / binding mismatches').toEqual([]);
}});
let cases;
test.beforeAll(async({request})=>{cases=await (await request.get(original+'/__cases')).json();});

async function ready(page,linked=true){
  await page.waitForLoadState('load');
  await page.evaluate(()=>document.fonts.ready);
  if(linked)await expect(page.locator('anna-links')).toHaveCount(1);
  await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
}
async function attach(testInfo,name,body,contentType){
  const path=testInfo.outputPath(name);await fs.writeFile(path,body);await testInfo.attach(name,{path,contentType});return path;
}
async function sameImage(before,after,testInfo,label){
  const pixelmatch=(await import('pixelmatch')).default;
  const a=PNG.sync.read(before),b=PNG.sync.read(after);
  await attach(testInfo,label+'-original.png',before,'image/png');
  await attach(testInfo,label+'-linked.png',after,'image/png');
  expect([b.width,b.height]).toEqual([a.width,a.height]);
  const diff=new PNG({width:a.width,height:a.height});
  const mismatches=pixelmatch(a.data,b.data,diff.data,a.width,a.height,{threshold:0,includeAA:true});
  if(mismatches)await attach(testInfo,label+'-diff.png',PNG.sync.write(diff),'image/png');
  expect(mismatches,label+' source / linked pixel differences').toBe(0);
}
async function pageShape(page){
  return page.evaluate(()=>({text:document.body.innerText,
    geometry:Array.from(document.body.querySelectorAll('h1,h2,h3,h4,p,table,tr,td,img')).map(e=>{
      const r=e.getBoundingClientRect();return [e.tagName,Math.round(r.x*100),Math.round((r.y+scrollY)*100),Math.round(r.width*100),Math.round(r.height*100)];
    })}));
}

const visuals=[
  ['spells','topics/玩家手册2024/法术详述/1环.htm'],
  ['weapons-table','topics/玩家手册2024/装备/武器.htm'],
  ['statblock','topics/怪物图鉴2025/附录A/狼.htm'],
  ['reference-table','topics/速查/合作万器大全.htm']
];
for(const [name,path] of visuals)for(const width of [1280,390]){
  test(`source parity ${name} ${width}px`,async({context,page},testInfo)=>{
    await page.setViewportSize({width,height:800});
    const source=await context.newPage();await source.setViewportSize({width,height:800});
    await source.goto(topic(original,path));await ready(source,false);
    await page.goto(topic(book,path));await ready(page);
    expect(await pageShape(page),'visible text and element geometry').toEqual(await pageShape(source));
    for(const [label,fraction] of [['top',0],['middle',.5]]){
      for(const p of [source,page]){
        await p.evaluate(f=>scrollTo(0,(document.documentElement.scrollHeight-innerHeight)*f),fraction);
        await p.mouse.move(width-1,1);await ready(p,p===page);
      }
      await sameImage(await source.screenshot({animations:'disabled'}),await page.screenshot({animations:'disabled'}),testInfo,label);
    }
  });
}

test('definition → new anna tab → reader → same definition; original pages retained',async({page,context},testInfo)=>{
  const s=cases.spell;
  await page.goto(topic(book,s.path)+'#'+s.anchor);await ready(page);
  const title=page.getByRole('link',{name:'查看 anna 条目: '+s.name,exact:true});
  const originalURL=page.url();
  const entryPromise=context.waitForEvent('page');await title.click();const entry=await entryPromise;
  await expect(entry).toHaveURL(new RegExp(`:${port+1}/\\?.*entry=${s.id}`));
  await expect(page).toHaveURL(originalURL);
  await expect(entry.getByRole('dialog').getByRole('heading',{name:'护盾术',exact:true})).toBeVisible();
  await attach(testInfo,'anna-definition.png',await entry.screenshot(),'image/png');
  const popupPromise=context.waitForEvent('page');
  await entry.getByRole('link',{name:'查看不全书定义 ↗',exact:true}).click();
  const reader=await popupPromise;await reader.waitForLoadState('load');
  const frame=reader.frameLocator('#content');
  await expect(frame.getByRole('link',{name:'查看 anna 条目: '+s.name,exact:true})).toBeVisible();
  await expect(reader.locator('#content')).toHaveAttribute('src',new RegExp('#anna-'+s.id+'$'));
  await expect.poll(async()=>frame.locator('#Shield').evaluate(e=>e.getBoundingClientRect().top)).toBeGreaterThanOrEqual(0);
  expect(await frame.locator('#Shield').evaluate(e=>e.getBoundingClientRect().top)).toBeLessThan(80);
  await expect.poll(async()=>new URL(await reader.url()).searchParams.get('page')).toContain('#anna-'+s.id);
  await reader.reload();
  await expect(frame.getByRole('link',{name:'查看 anna 条目: '+s.name,exact:true})).toBeVisible();
  expect(await frame.locator('#Shield').evaluate(e=>e.getBoundingClientRect().top)).toBeLessThan(80);
  await attach(testInfo,'reader-definition.png',await reader.screenshot(),'image/png');
  const readerURL=reader.url();const returnPromise=context.waitForEvent('page');
  await frame.getByRole('link',{name:'查看 anna 条目: '+s.name,exact:true}).click();
  const returned=await returnPromise;
  await expect(returned).toHaveURL(new RegExp(`:${port+1}/\\?.*entry=${s.id}`));
  await expect(returned.getByRole('dialog').getByRole('heading',{name:'护盾术',exact:true})).toBeVisible();
  await expect(reader).toHaveURL(readerURL);
});

test('existing reference reaches the exact target in the current page',async({page},testInfo)=>{
  const ref=cases.reference;
  await page.goto(topic(book,cases.reference_source));await ready(page);
  const link=page.getByRole('link',{name:'查看定义: '+ref.text,exact:true}).first();
  // A reference in a long table must be keyboard-reachable without a guessed coordinate.
  await expect(link).toHaveAttribute('target','_self');
  await link.focus();await link.click();
  await expect(page).toHaveURL(topic(book,ref.path)+'#'+ref.fragment);await ready(page);
  const anchor=page.locator(`anna-links a[href="/anna/entry/${ref.id}"]`).first();
  await expect(anchor).toBeVisible();
  const box=await anchor.boundingBox();expect(box.y).toBeGreaterThanOrEqual(0);expect(box.y).toBeLessThan(80);
  await attach(testInfo,'reference-target.png',await page.screenshot(),'image/png');
});

test('formatted mention navigates in the current page and exposes evidence in anna',async({page},testInfo)=>{
  const m=cases.mention;
  await page.goto(topic(book,m.path));await ready(page);
  const link=page.getByRole('link',{name:'查看定义: '+m.text,exact:true}).first();
  await expect(link).toHaveAttribute('target','_self');
  await link.focus();await expect(link).toBeFocused();await page.keyboard.press('Enter');
  await expect(page).toHaveURL(topic(book,m.target.path)+'#'+m.target.fragment);await ready(page);
  // Concept evidence is an audit tab; reading mode summarizes it below the entry.
  await page.goto(anna+'/?entry='+m.entry_id);
  await expect(page.getByRole('tab',{name:/概念线索/})).toHaveCount(0);
  await page.locator('[data-audit-summary]').click();
  await page.getByRole('tab',{name:/概念线索/}).click();
  const detail=await (await page.request.get(anna+'/api/entry?id='+m.entry_id)).json();
  const pending=detail.concept_observations.find(o=>o.status==='review' && o.candidate_entries.length);
  expect(pending).toBeTruthy();
  const candidateCard=page.locator('article.fact').filter({has:page.locator('strong').filter({hasText:pending.text})}).first();
  await candidateCard.getByText(`候选定义 (${pending.candidate_entries.length})`,{exact:true}).click();
  const candidate=pending.candidate_entries[0];
  await expect(candidateCard.getByRole('button',{name:candidate.name+' · '+candidate.ruleset,exact:true})).toBeVisible();
  const mention=page.locator('article.fact').filter({has:page.getByRole('button',{name:m.text,exact:true})}).first();
  await expect(mention).toContainText('格式标记');await expect(mention).toContainText('正文提及');
  await mention.getByText('发现依据',{exact:true}).click();
  await expect(mention.locator('pre')).toContainText('topics/style.css');
  await expect(mention.locator('pre')).toContainText(m.evidence.stylesheet_sha256);
  await page.setViewportSize({width:390,height:800});
  await attach(testInfo,'concept-mention-evidence.png',await page.screenshot(),'image/png');
  await mention.getByRole('button',{name:m.text,exact:true}).click();
  await expect(page).toHaveURL(new RegExp('entry='+m.target_id));
});

test('plain and marked core-rule mentions share the precise subdefinition in anna and the book',async({page})=>{
  await page.setViewportSize({width:390,height:800});
  const [plain,marked]=cases.core_rules;
  expect(plain.references.map(r=>[r.text,r.signal,r.can_link])).toEqual([
    ['半身掩护','plain',true],['四分之三掩护','plain',true]
  ]);
  expect(plain.references[1].evidence.syntax.rule).toBe('coordinated_rule_use');
  expect(marked.references.map(r=>[r.signal,r.target_id])).toEqual(
    plain.references.map(r=>['format',r.target_id])
  );
  const ref=plain.references[0];
  const subject=await (await page.request.get(anna+'/api/entry?id='+plain.entry_id)).json();
  expect(subject.effective_references.find(r=>r.id===ref.id)).toMatchObject({
    target_id:ref.target_id,can_link:true,review_status:'unreviewed'
  });
  const child=await (await page.request.get(anna+'/api/entry?id='+ref.target_id)).json();
  expect(child).toMatchObject({name_zh:'半身掩护',type:'rule',ruleset:'5r',boundary_rule:'rule_term_gloss_list'});
  expect(child.body).toContain('+2加值');
  expect(child.body).not.toContain('四分之三掩护');
  expect(child.relations).toEqual(expect.arrayContaining([expect.objectContaining({predicate:'section_of',target_label:'掩护 Cover'})]));
  await page.goto(topic(book,plain.path));await ready(page);
  const link=page.getByRole('link',{name:'查看定义: '+ref.text,exact:true}).first();
  await link.focus();await expect(link).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(page).toHaveURL(topic(book,ref.target.path)+'#'+ref.target.fragment);
  await ready(page);
  await expect(page.getByRole('link',{name:'查看 anna 条目: 半身掩护',exact:true})).toBeInViewport();
  const old=cases.classic_cover;
  expect(old.target_id).not.toBe(ref.target_id);
  expect(old.target.path).toBe(old.path);
  const oldChild=await (await page.request.get(anna+'/api/entry?id='+old.target_id)).json();
  expect(oldChild).toMatchObject({name_zh:'半身掩护',name_en:'half cover',ruleset:'5e',boundary_rule:'rule_parallel_lead'});
  await page.goto(topic(book,old.path));await ready(page);
  const oldLink=page.getByRole('link',{name:'查看定义: 半身掩护',exact:true});
  await oldLink.focus();await expect(oldLink).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(page).toHaveURL(topic(book,old.path)+'#'+old.target.fragment);
  await expect(page.getByRole('link',{name:'查看 anna 条目: '+oldChild.name,exact:true})).toBeInViewport();
});

test('fixture table links track moved nodes, focus, scrolling and narrow reflow',async({page},testInfo)=>{
  await page.goto(original+'/__fixture__/linked');await ready(page);
  const cell=page.getByRole('link',{name:'查看 anna 条目: 长剑 Longsword',exact:true});
  await expect(page.locator('#wrapper table')).toHaveCount(1);
  async function aligned(){
    await expect.poll(()=>page.evaluate(()=>{
      const a=document.querySelector('anna-links').shadowRoot.querySelector('a[href="/anna/entry/cell"]').getBoundingClientRect();
      const r=document.createRange();r.selectNodeContents(document.querySelector('#cell'));
      const b=r.getBoundingClientRect();return Math.max(Math.abs(a.x-b.x),Math.abs(a.y-b.y));
    })).toBeLessThan(1);
  }
  await aligned();await cell.focus();await expect(cell).toBeFocused();
  await page.mouse.wheel(0,100);await expect.poll(async()=>page.evaluate(()=>scrollY)).toBeGreaterThan(0);
  await aligned();await expect(cell).toBeFocused();
  await page.getByRole('button',{name:'重排表格'}).click();
  await page.setViewportSize({width:390,height:800});await ready(page);await aligned();
  const beta=page.getByRole('link',{name:'查看 anna 条目: 乙 Beta',exact:true});
  await beta.focus();await expect(beta).toBeFocused();
  await expect(beta).toBeInViewport();
  await expect(page.locator('#Beta')).toBeInViewport();
  await attach(testInfo,'keyboard-focus.png',await page.screenshot(),'image/png');
  await page.keyboard.press('Shift+Tab');await expect(cell).toBeFocused();await aligned();
});


test('reference links survive source-table pagination and filtering',async({page})=>{
  const ref=cases.reference;
  await page.goto(topic(book,cases.reference_source));await ready(page);
  const link=page.getByRole('link',{name:'查看定义: '+ref.text,exact:true}).first();
  await expect(link).toHaveAttribute('href','/definition/'+ref.id);
  await page.getByRole('button',{name:'下一页',exact:true}).click();
  await expect(link).toHaveCount(0);
  await page.getByRole('button',{name:'上一页',exact:true}).click();
  await expect(link).toHaveAttribute('href','/definition/'+ref.id);
  await page.getByRole('searchbox',{name:'搜索物品',exact:true}).fill('Abjurer');
  await expect(page.getByText(/找到 1 \/ \d+ 条/)).toBeVisible();
  await expect(link).toHaveAttribute('href','/definition/'+ref.id);
  const overlay=await link.boundingBox();
  const originalLink=page.getByRole('link',{name:ref.text,exact:true});
  const source=await originalLink.boundingBox();
  expect(Math.abs(overlay.x-source.x)).toBeLessThan(1);
  expect(Math.abs(overlay.y-source.y)).toBeLessThan(1);
});
