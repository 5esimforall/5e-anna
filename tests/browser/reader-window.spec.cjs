/* Controlled document heights isolate scheduling and reclamation from source-book layout. */
const {test:base,expect}=require('@playwright/test');
const anna=`http://127.0.0.1:${process.env.ANNA_E2E_PORT||18380}`;
const path=index=>`topics/window-${index}.htm`;
const test=base.extend({page:async({page},use)=>{
  const errors=[];page.on('pageerror',error=>errors.push(error.message));
  await use(page);expect(errors).toEqual([]);
}});
async function fixture(page,request,{count=12,height=2400}={}){
  const meta=await (await request.get(anna+'/api/reader')).json();
  const nodes=Array.from({length:count},(_,i)=>({id:`page-${i}`,parent_id:i?'page-0':'',path:path(i),fragment:'',label:`Page ${i+1}`,status:'present'}));
  nodes.push({id:'outside',parent_id:'',path:path('outside'),fragment:'',label:'Outside',status:'present'});
  const reads=[],heights=new Map();
  await page.route('**/api/reader',route=>route.fulfill({json:{...meta,nodes,default_page:path(0)}}));
  await page.route('**/api/reader-page?**',route=>{
    const source=new URL(route.request().url()).searchParams.get('path');
    return route.fulfill({json:{snapshot:meta.snapshot,path:source,title:source,fragment:'',definitions:[]}});
  });
  await page.route('**/book/topics/window-*.htm',route=>{
    const source=new URL(route.request().url()).pathname.slice('/book/'.length);reads.push(source);
    return route.fulfill({contentType:'text/html',body:`<!doctype html><html><head><meta charset="utf-8"><style>html,body{margin:0;padding:0}.content{height:${heights.get(source)||height}px;box-sizing:border-box;padding:24px}</style></head><body><div class="content"><h1>${source}</h1><p>Stable reading text</p><a id="target" href="#target">Target</a></div><script type="application/json" id="anna-bindings">${JSON.stringify({reader:{snapshot:meta.snapshot,presentation_resources:[]}})}</script><script src="/book/anna-assets/topic.js"></script></body></html>`});
  });
  await page.addInitScript(()=>{
    window.maxMountedDocuments=0;
    new MutationObserver(()=>window.maxMountedDocuments=Math.max(window.maxMountedDocuments,document.querySelectorAll('.book-document').length))
      .observe(document,{subtree:true,childList:true});
  });
  return {reads,heights};
}
async function ready(page){await expect(page.locator('#book-main')).toHaveAttribute('aria-busy','false');await expect(page.locator('#book-error')).toBeHidden();}
async function visit(page){await page.goto(anna+'/read?root=page-0');await ready(page);}
const section=(page,index)=>page.locator(`.book-page[data-path="${path(index)}"]`);
async function mounted(page,index){await expect(section(page,index).locator('iframe')).toHaveCSS('visibility','visible');}
async function scrollTo(page,index,y){await section(page,index).evaluate((section,y)=>{
  const scroll=document.querySelector('#book-scroll'),surface=section.querySelector('iframe,.book-page-placeholder');
  scroll.scrollTop+=surface.getBoundingClientRect().top-scroll.getBoundingClientRect().top+y;
},y);}
// Jumps within the scope through the reading bar's path, as a reader would: the scope name, or an item of a level's menu.
async function jump(page,label){
  const scope=page.locator('#book-scope-title');
  if(await scope.isVisible()&&(await scope.textContent())===label){await scope.click();return;}
  const controls=page.locator('#book-path button[aria-haspopup]:visible');
  for(let i=0;i<await controls.count();i++){
    await controls.nth(i).click();
    const item=page.locator('#book-path-menu a[role=menuitem]').filter({has:page.getByText(label,{exact:true})});
    if(await item.count()){await item.click();return;}
    await page.keyboard.press('Escape');
  }
  throw new Error('no path entry for '+label);
}
async function saved(page){return page.evaluate(()=>JSON.parse(localStorage.getItem('anna.bookPosition')));}
async function position(page,index,y){await expect.poll(async()=>{const value=await saved(page);return value.page===path(index)?value.position.y:-1;}).toBeCloseTo(y,0);}
async function advance(page,last){
  for(let i=0;i<last;i++){
    await scrollTo(page,i,1900);await mounted(page,i+1);
    await scrollTo(page,i+1,200);await position(page,i+1,200);
  }
}

test('near-bottom loading is sequential, scope-bound, and does not alter the current offset or history',async({page,request})=>{
  const {reads}=await fixture(page,request);await visit(page);
  await page.waitForTimeout(400);expect(reads).toEqual([path(0)]);
  const history=await page.evaluate(()=>window.history.length);
  await scrollTo(page,0,1700);await mounted(page,1);await position(page,0,1700);
  await page.waitForTimeout(400);expect(reads).toEqual([path(0),path(1)]);
  expect(await page.evaluate(()=>window.history.length)).toBe(history);
  expect((await saved(page)).root).toBe('page-0');
  await expect(page.locator('#book-page-count')).toHaveText('1 / 12');
  await jump(page,'Page 12');await ready(page);
  await scrollTo(page,11,1900);await page.waitForTimeout(400);
  await expect(page.locator('.book-scope-end')).toHaveText('已到本章节末尾');
  expect(reads).not.toContain(path('outside'));
});

test('long reading releases distant frames, keeps measured space, and rehydrates on reverse scroll with a five-frame ceiling',async({page,request})=>{
  const {reads}=await fixture(page,request);await visit(page);
  await page.frameLocator('#content').locator('#target').focus();await advance(page,8);
  await expect(page.locator('#book-scroll')).toBeFocused();
  await expect(section(page,0).locator('iframe')).toHaveCount(0);
  await expect(section(page,0).locator('.book-page-placeholder')).toHaveCSS('height','2400px');
  await position(page,8,200);
  expect(await page.locator('.book-document').count()).toBeLessThanOrEqual(5);
  await scrollTo(page,0,700);await mounted(page,0);await position(page,0,700);
  expect(reads.filter(p=>p===path(0))).toHaveLength(2);
  const count=reads.length;await page.waitForTimeout(600);expect(reads.length).toBe(count);
  expect(await page.evaluate(()=>window.maxMountedDocuments)).toBeLessThanOrEqual(5);
  expect((await saved(page)).root).toBe('page-0');
});

test('far pagination and history restore reclaimed pages without fetching skipped documents',async({page,request})=>{
  const {reads}=await fixture(page,request);await visit(page);await scrollTo(page,0,600);await position(page,0,600);
  await jump(page,'Page 10');await ready(page);
  await expect(section(page,0).locator('.book-page-placeholder')).toBeAttached();
  expect(reads).toEqual([path(0),path(9)]);
  await page.goBack();await ready(page);await position(page,0,600);
  await expect(page.locator('#book-page-count')).toHaveText('1 / 12');
  await page.reload();await ready(page);await position(page,0,600);
  expect((await saved(page)).root).toBe('page-0');
});

test('automatic failures do not retry in a loop; explicit retry retains existing content',async({page,request})=>{
  await fixture(page,request);let attempts=0,fail=true;
  await page.route('**/api/reader-page?**',route=>{
    if(new URL(route.request().url()).searchParams.get('path')!==path(1))return route.fallback();
    attempts++;return fail?route.fulfill({status:503,json:{code:'network'}}):route.fallback();
  });
  await visit(page);await scrollTo(page,0,1700);
  await expect(section(page,1).getByRole('alert')).toContainText('无法读取');
  await scrollTo(page,0,1750);await page.waitForTimeout(500);expect(attempts).toBe(1);
  await position(page,0,1750);await expect(page.locator('#book-error')).toBeHidden();
  fail=false;await section(page,1).getByRole('button',{name:'重试',exact:true}).press('Enter');
  await mounted(page,1);expect(attempts).toBe(2);
  await mounted(page,0);
});

test('a pending automatic request is single-flight and cannot return after a scope change',async({page,request})=>{
  await fixture(page,request);let release,attempts=0;
  const held=new Promise(resolve=>release=resolve);
  await page.route('**/api/reader-page?**',async route=>{
    if(new URL(route.request().url()).searchParams.get('path')===path(1)){attempts++;await held;}
    await route.fallback().catch(()=>{});
  });
  try{
    await visit(page);await scrollTo(page,0,1700);await expect.poll(()=>attempts).toBe(1);
    await scrollTo(page,0,1750);await page.waitForTimeout(300);expect(attempts).toBe(1);
    await page.locator('#book-tree [data-node="outside"] a').click();await ready(page);release();
    await expect(page.locator('#book-scope-title')).toHaveText('Outside');
    await page.waitForTimeout(300);await expect(page.locator('.book-document')).toHaveCount(1);
    expect((await saved(page)).root).toBe('outside');
  }finally{release();}
});

test('rehydrating a taller preceding document preserves the visible page and local offset',async({page,request})=>{
  const {heights}=await fixture(page,request);await visit(page);await advance(page,5);
  heights.set(path(0),3100);
  await jump(page,'Page 2');await ready(page);
  await scrollTo(page,1,150);await mounted(page,0);
  await expect(section(page,0).locator('iframe')).toHaveCSS('height','3100px');
  await position(page,1,150);
});

test('very short pages never trigger an unbounded fetch or mounting cascade',async({page,request})=>{
  const {reads}=await fixture(page,request,{count:60,height:180});await visit(page);
  await mounted(page,2);await page.waitForTimeout(600);
  expect(reads.length).toBeLessThanOrEqual(5);
  expect(await page.evaluate(()=>window.maxMountedDocuments)).toBeLessThanOrEqual(5);
  await expect(page.locator('.book-page-gap')).toContainText('尚未加载');
});

test('a far same-scope jump cancels an automatic load and ignores its eventual response',async({page,request})=>{
  await fixture(page,request);let release,requested=false;
  const held=new Promise(resolve=>release=resolve);
  await page.route('**/api/reader-page?**',async route=>{
    if(new URL(route.request().url()).searchParams.get('path')===path(1)){requested=true;await held;}
    await route.fallback().catch(()=>{});
  });
  try{
    await visit(page);await scrollTo(page,0,1700);await expect.poll(()=>requested).toBe(true);
    await jump(page,'Page 10');await ready(page);release();
    await page.waitForTimeout(300);await expect(page.locator('#book-page-count')).toHaveText('10 / 12');
    await expect(section(page,1).locator('iframe')).toHaveCount(0);
    await expect(section(page,1).locator('.book-page-placeholder')).toBeAttached();
    expect((await saved(page)).page).toBe(path(9));
  }finally{release();}
});

test('a failed rehydration keeps its measured slot and last successfully displayed position',async({page,request})=>{
  await fixture(page,request);await visit(page);await advance(page,5);let fail=true,attempts=0;
  await page.route('**/api/reader-page?**',route=>{
    if(new URL(route.request().url()).searchParams.get('path')!==path(0))return route.fallback();
    attempts++;return fail?route.fulfill({status:503,json:{code:'network'}}):route.fallback();
  });
  const height=await section(page,0).evaluate(e=>e.offsetHeight);
  await scrollTo(page,0,500);await expect(section(page,0).getByRole('alert')).toContainText('无法读取');
  const alert=await section(page,0).getByRole('alert').boundingBox(),viewport=await page.locator('#book-scroll').boundingBox();
  expect(alert.y).toBeGreaterThanOrEqual(viewport.y);expect(alert.y+alert.height).toBeLessThanOrEqual(viewport.y+viewport.height);
  expect(await section(page,0).evaluate(e=>e.offsetHeight)).toBe(height);
  await page.waitForTimeout(400);expect(attempts).toBe(1);expect((await saved(page)).page).toBe(path(5));
  fail=false;await section(page,0).getByRole('button',{name:'重试',exact:true}).press('Enter');
  await mounted(page,0);expect(attempts).toBe(2);
});

test('background-state fixture defers automatic requests until visibility resumes',async({page,request})=>{
  const {reads}=await fixture(page,request);await visit(page);
  await page.evaluate(()=>Object.defineProperty(document,'hidden',{configurable:true,value:true}));
  await scrollTo(page,0,1700);await page.waitForTimeout(400);expect(reads).toEqual([path(0)]);
  await page.evaluate(()=>{delete document.hidden;document.dispatchEvent(new Event('visibilitychange'));});
  await mounted(page,1);expect(reads).toEqual([path(0),path(1)]);
});

test('manual loading cannot reclaim an unfinished foreground pagination target',async({page,request})=>{
  await fixture(page,request);let release,requested=false;
  const held=new Promise(resolve=>release=resolve);
  await page.route('**/book/topics/window-9.htm',async route=>{requested=true;await held;await route.fallback().catch(()=>{});});
  try{
    await visit(page);await jump(page,'Page 10');await expect.poll(()=>requested).toBe(true);
    await page.getByRole('button',{name:'加载下一页：Page 2',exact:true}).press('Enter');await mounted(page,1);
    release();await ready(page);await expect(page.locator('#book-page-count')).toHaveText('10 / 12');
    await mounted(page,9);expect(await page.evaluate(()=>window.maxMountedDocuments)).toBeLessThanOrEqual(5);
  }finally{release();}
});
