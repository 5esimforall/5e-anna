/* Real-corpus reader checks. Faults below affect transport, never rule identity. */
const {test:base,expect} = require('@playwright/test');
const port=Number(process.env.ANNA_E2E_PORT||18380);
const anna=`http://127.0.0.1:${port}`, book=`http://127.0.0.1:${port}/book`, original=`http://127.0.0.1:${port}/__original`;
const test=base.extend({context:async({context},use)=>{
  const errors=[];context.on('page',page=>page.on('pageerror',error=>errors.push(error.message)));
  await context.route('**/*',route=>['127.0.0.1','localhost'].includes(new URL(route.request().url()).hostname)?route.continue():route.abort());
  await use(context);expect(errors,'reader JavaScript errors').toEqual([]);
}});
let cases,meta;
test.beforeAll(async({request})=>{
  cases=await (await request.get(anna+'/__cases')).json();
  meta=await (await request.get(anna+'/api/reader')).json();
});
async function ready(page){
  await expect(page.locator('#book-main')).toHaveAttribute('aria-busy','false');
  await expect(page.locator('#book-error')).toBeHidden();
  await expect(page.locator('#content')).toBeVisible();
  await expect(page.locator('#content')).toHaveCSS('visibility','visible');
}
async function visit(page,params={}){await page.goto(anna+'/read?'+new URLSearchParams(params));await ready(page);}
function content(page){return page.frames().find(f=>f.url().startsWith(book+'/topics/'));}
async function scrollToPosition(page,y){await page.locator('#book-scroll').evaluate((e,y)=>{
  const frame=document.querySelector('#content');e.scrollTop=frame.getBoundingClientRect().top-e.getBoundingClientRect().top+e.scrollTop+y;
},y);}
async function readingPosition(page){return page.locator('#book-scroll').evaluate(e=>Math.max(0,e.getBoundingClientRect().top-document.querySelector('#content').getBoundingClientRect().top));}

async function sidebar(page,pane){
  if(await page.locator('#book-sidebar').isHidden())await page.locator('#book-menu').click();
  if(pane)await page.locator('#tab-'+pane).click();
}
async function shot(page,testInfo,name){await testInfo.attach(name,{body:await page.screenshot(),contentType:'image/png'});}

test('titles name the open content and the topbar links carry the open entry both ways',async({page})=>{
  await page.goto(anna);
  await expect(page).toHaveTitle('5e anna');
  await expect(page.locator('#book-reader')).toHaveAttribute('href','/read');
  await page.locator('#results .entry-name').first().click();
  await expect(page.locator('#entry-title')).toBeVisible();
  const name=(await page.locator('#entry-title').textContent()).trim();
  const entry=new URL(page.url()).searchParams.get('entry');
  await expect(page).toHaveTitle(`${name} · 5e anna`);
  await expect(page.locator('#book-reader')).toHaveAttribute('href','/read?entry='+encodeURIComponent(entry));
  await page.locator('#close-detail').click();
  await expect(page).toHaveTitle('5e anna');
  await expect(page.locator('#book-reader')).toHaveAttribute('href','/read');
  await page.locator('#results .entry-name').first().click();
  await page.locator('#book-reader').click();await ready(page);
  expect(new URL(page.url()).searchParams.get('entry')).toBe(entry);
  await expect(page).toHaveTitle(/ · 5e stella$/);
  await expect(page.locator('.book-query')).toHaveText('5e anna');
  await expect(page.locator('.book-query')).toHaveAttribute('href','/?entry='+encodeURIComponent(entry));
  await page.locator('.book-query').click();
  await expect(page.locator('#entry-title')).toHaveText(name);
  expect(new URL(page.url()).searchParams.get('entry')).toBe(entry);
  await visit(page);
  await expect(page.locator('.book-query')).toHaveAttribute('href','/');
});

test('query and reader use the new app; upstream reading entries are gone',async({page,request})=>{
  await page.goto(anna);
  await expect(page.getByRole('link',{name:'5e stella',exact:true})).toBeVisible();
  await page.getByRole('link',{name:'5e stella',exact:true}).click();await ready(page);
  expect(new URL(page.url()).pathname).toBe('/read');
  await expect(page).toHaveTitle('写在前面 · 5e stella');
  await expect(page.locator('#book-main > h1, #book-title')).toHaveCount(0);
  await expect(page.locator('#book-print')).toHaveCount(0);
  await expect(page.locator('#content')).not.toHaveAttribute('sandbox',/allow-modals/);
  const source=content(page);
  await expect(source.locator('script[src*="webhelp-prepaint"],script[src*="webhelp-content-bridge"]')).toHaveCount(0);
  for(const path of ['/assets/webhelp-shell.js','/webhelp-content-bridge.js','/api/reader-context','/anna/entry/obsolete'])
    expect((await request.get(book+path)).status(),path).toBe(404);
  for(const path of ['/','/index.htm','/index.html','/indexh.htm','/webhelpframe.htm','/webhelpcontents.htm','/webhelpsearch.htm','/webhelpbookmark.htm'])
    expect((await request.get(book+path)).status(),path).toBe(404);
});

test('legacy WinCHM page no longer redirects or synchronizes a removed frame',async({page})=>{
  const path='topics/艾伯伦：从终末战争中崛起/背景-家族代理人.html';
  await visit(page,{page:path});
  await expect(content(page).locator('body')).toContainText('你宣誓效忠于其中一个龙纹家族');
  expect(await content(page).locator('script').allTextContents()).not.toEqual(expect.arrayContaining([expect.stringContaining('parent.nav.tree')]));
  await page.goto(book+'/'+path.split('/').map(encodeURIComponent).join('/'));
  await expect(page.locator('body')).toContainText('你宣誓效忠于其中一个龙纹家族');
  await expect(page).toHaveURL(book+'/'+path.split('/').map(encodeURIComponent).join('/'));
});

test('reader footer shows the printed source release and only the search shortcut',async({page})=>{
  await visit(page);
  expect(meta.source_release.path).toBe('topics/写在前面.html');
  expect(meta.source_release.version).toMatch(/^\d{8}(?:\.\d+)*$/);
  await expect(page.locator('.book-sidebar-foot')).toHaveText('不全书 v'+meta.source_release.version+'/ 搜索');
  await expect(page.locator('#book-snapshot')).toHaveCount(0);
  await page.route('**/api/reader',async route=>{
    const response=await route.fetch();const data=await response.json();data.source_release=null;
    await route.fulfill({response,json:data});
  });
  await page.reload();await ready(page);
  await expect(page.locator('#book-version')).toBeHidden();
  await expect(page.locator('.book-sidebar-foot')).toHaveText('/ 搜索');
});

test('duplicate directory entries retain their node; filtering restores expansion and keyboard navigation',async({page})=>{
  const path='topics/玩家手册2024/角色职业/野蛮人/野蛮人.htm';
  const repeated=meta.nodes.filter(n=>n.path===path && n.status==='present');expect(repeated.length).toBeGreaterThan(1);
  const selected=repeated.at(-1);
  await visit(page,{page:path,node:selected.id});
  await expect(page.locator('#book-tree a[aria-current=page]')).toHaveCount(1);
  await expect(page.locator(`#book-tree [data-node="${selected.id}"] > .toc-row a`)).toHaveAttribute('aria-current','page');
  const expanded=await page.locator('#book-tree [aria-expanded=true]').evaluateAll(nodes=>nodes.map(n=>n.dataset.node));
  await page.locator('#book-filter').fill('没有这个目录xyz');
  await expect(page.locator('#book-tree-empty')).toBeVisible();
  await page.locator('#book-filter').press('Escape');
  await expect(page.locator('#book-filter')).toHaveValue('');
  expect(await page.locator('#book-tree [aria-expanded=true]').evaluateAll(nodes=>nodes.map(n=>n.dataset.node))).toEqual(expanded);
  await page.reload();await ready(page);
  await expect(page.locator(`#book-tree [data-node="${selected.id}"] > .toc-row a`)).toHaveAttribute('aria-current','page');
  await page.locator('#book-collapse').click();
  await expect(page.locator('#book-tree [aria-expanded=true]')).toHaveCount(0);
  const first=page.locator('#book-tree [role=treeitem]').first();await first.focus();
  await first.press('ArrowDown');
  const second=page.locator('#book-tree [role=treeitem]').nth(1);await expect(second).toBeFocused();
  await second.press('ArrowRight');await expect(second).toHaveAttribute('aria-expanded','true');
  await second.press('ArrowLeft');await expect(second).toHaveAttribute('aria-expanded','false');
});

test('source reference navigation has one history entry and restores scroll on back, forward and reload',async({page})=>{
  const m=cases.mention;
  await visit(page,{page:m.path});
  const source=content(page);
  const link=source.getByRole('link',{name:'查看定义: '+m.text,exact:true}).first();
  await scrollToPosition(page,1000);
  await link.focus();await expect(link).toBeFocused();
  await expect.poll(()=>page.evaluate(()=>history.state?.book?.position.y)).toBeGreaterThan(0);
  const before=await page.evaluate(()=>history.state.book.position.y), url=page.url();
  await page.keyboard.press('Enter');
  await expect(page).toHaveURL(new RegExp('entry='+m.target_id));await ready(page);
  expect(content(page).url()).toContain('#'+m.target.fragment);
  await page.goBack();await expect(page).toHaveURL(url);await ready(page);
  await expect.poll(()=>readingPosition(page)).toBeCloseTo(before,0);
  await page.goForward();await ready(page);await expect(page).toHaveURL(new RegExp('entry='+m.target_id));
  await page.locator('#book-scroll').evaluate(e=>e.scrollBy(0,250));
  await expect.poll(()=>page.evaluate(()=>history.state.book.position.y)).toBeGreaterThan(200);
  const position=await page.evaluate(()=>history.state.book.position.y);
  await page.reload();await ready(page);
  await expect.poll(()=>readingPosition(page)).toBeCloseTo(position,0);
});

const positionKey='anna.bookPosition';
test('closing and reopening the reader resumes its page, directory entry and scroll',async({page,context})=>{
  const path='topics/玩家手册2024/角色职业/野蛮人/野蛮人.htm';
  const node=meta.nodes.filter(n=>n.path===path && n.status==='present').at(-1).id;
  await visit(page,{page:path,node});
  await scrollToPosition(page,950);
  await expect.poll(()=>page.evaluate(key=>JSON.parse(localStorage.getItem(key)).position.y,positionKey)).toBe(950);
  await page.close();
  const reopened=await context.newPage();await visit(reopened);
  expect(new URL(reopened.url()).searchParams.get('page')).toBe(path);
  expect(new URL(reopened.url()).searchParams.get('node')).toBe(node);
  await expect.poll(()=>readingPosition(reopened)).toBe(950);
  await expect(reopened.locator(`#book-tree [data-node="${node}"] > .toc-row a`)).toHaveAttribute('aria-current','page');
  await reopened.goto(anna);await reopened.getByRole('link',{name:'5e stella',exact:true}).click();await ready(reopened);
  await expect.poll(()=>readingPosition(reopened)).toBe(950);
});

test('explicit page and entry links take priority over the remembered reading position',async({page,context})=>{
  await visit(page,{page:'topics/玩家手册2024/法术详述/1环.htm'});
  await scrollToPosition(page,1100);
  await expect.poll(()=>page.evaluate(key=>JSON.parse(localStorage.getItem(key)).position.y,positionKey)).toBe(1100);
  const explicit=await context.newPage();await visit(explicit,{entry:cases.spell.id});
  expect(new URL(explicit.url()).searchParams.get('entry')).toBe(cases.spell.id);
  expect(content(explicit).url()).toContain('#anna-'+cases.spell.id);
  await visit(explicit,{page:'topics/玩家手册2024/法术详述/1环.htm'});
  await expect.poll(()=>readingPosition(explicit)).toBe(0);
  await page.reload();await ready(page);
  await expect.poll(()=>readingPosition(page)).toBe(1100);
});

test('invalid or outdated persistent positions fall back to the default page',async({page})=>{
  await page.goto(anna);
  const valid={version:1,snapshot:meta.snapshot,page:'topics/玩家手册2024/法术详述/1环.htm',node:'',position:{x:0,y:900}};
  for(const value of ['{',JSON.stringify({...valid,snapshot:'old'}),JSON.stringify({...valid,version:3}),
      JSON.stringify({...valid,position:{x:0,y:-1}}),JSON.stringify({...valid,position:{x:0,y:'900'}}),
      JSON.stringify({...valid,page:'topics/../index.htm'})]){
    await page.evaluate(({key,value})=>localStorage.setItem(key,value),{key:positionKey,value});
    await visit(page);
    expect(content(page).url()).toContain(encodeURI(meta.default_page));
    await expect.poll(()=>readingPosition(page)).toBe(0);
    await page.goto(anna);
  }
});

test('failed navigation does not replace the last successfully read position',async({page,context})=>{
  await visit(page,{page:'topics/玩家手册2024/法术详述/1环.htm'});
  await scrollToPosition(page,850);
  await expect.poll(()=>page.evaluate(key=>JSON.parse(localStorage.getItem(key)).position.y,positionKey)).toBe(850);
  const before=await page.evaluate(key=>localStorage.getItem(key),positionKey);
  await page.goto(anna+'/read?entry=missing-entry');
  await expect(page.locator('#book-error')).toBeVisible();
  expect(await page.evaluate(key=>localStorage.getItem(key),positionKey)).toBe(before);
  const reopened=await context.newPage();await visit(reopened);
  await expect.poll(()=>readingPosition(reopened)).toBe(850);
});

test('mobile drawer, focus, outline and resize keep the same document',async({page},testInfo)=>{
  await page.setViewportSize({width:390,height:844});await visit(page,{entry:cases.spell.id});
  await expect(page.locator('#book-sidebar')).not.toBeVisible();
  await sidebar(page,'outline');await expect(page.locator('#book-sidebar')).toHaveJSProperty('open',true);
  expect(await page.locator('#book-sidebar').evaluate(e=>e.matches(':modal'))).toBe(true);
  await page.locator('#book-close').focus();
  for(let i=0;i<12;i++)await page.keyboard.press('Tab');
  expect(await page.evaluate(()=>document.querySelector('#book-sidebar').contains(document.activeElement))).toBe(true);
  const target=page.locator('#book-outline li a').first();await target.click();await ready(page);
  await expect(page.locator('#book-sidebar')).not.toBeVisible();
  const url=page.url();
  for(const width of [375,768,800,801,1280,1440,390]){
    await page.setViewportSize({width,height:844});await ready(page);
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
    await expect(page).toHaveURL(url);
    expect(await page.locator('#book-sidebar').evaluate(e=>e.matches(':modal'))).toBe(false);
  }
  await shot(page,testInfo,'reader-mobile.png');
  await sidebar(page,'contents');await page.locator('#book-filter').fill('掩护');
  await page.locator('#book-filter').press('Escape');await expect(page.locator('#book-filter')).toHaveValue('');
  await expect(page.locator('#book-sidebar')).toBeVisible();
  await page.keyboard.press('Escape');await expect(page.locator('#book-sidebar')).not.toBeVisible();
  await expect(page.locator('#book-menu')).toBeFocused();
  await page.setViewportSize({width:1440,height:900});
  await shot(page,testInfo,'reader-desktop.png');
});

test('full-text results, paging and reading state survive reload; search failure is recoverable',async({page})=>{
  await visit(page);await sidebar(page,'search');
  await page.locator('#book-search').fill('护盾');await page.locator('#book-search-form button').click();
  await expect(page.locator('#book-search-status')).toContainText('找到');
  await expect(page.locator('#book-search-next')).toBeEnabled();await page.locator('#book-search-next').click();
  await expect(page.locator('#book-search-page')).toContainText('第 2 /');
  const name=await page.locator('#book-search-results a').first().innerText();
  await page.reload();await ready(page);
  await expect(page.locator('#tab-search')).toHaveAttribute('aria-selected','true');
  await expect(page.locator('#book-search-page')).toContainText('第 2 /');
  await expect(page.locator('#book-search-results a').first()).toHaveText(name);
  await page.locator('#book-search-results a').first().click();await ready(page);
  await expect(page).not.toHaveTitle('写在前面 · 5e stella');
  await page.route('**/api/book-search?**',route=>route.fulfill({status:503,json:{code:'network'}}));
  await page.locator('#book-search-form button').click();
  await expect(page.locator('#book-search-status')).toContainText('无法读取');
  await expect(page.locator('#book-search-results a')).toHaveCount(0);
  await page.unroute('**/api/book-search?**');await page.locator('#book-search-form button').click();
  await expect(page.locator('#book-search-status')).toContainText('找到');
});

test('missing and conflicting targets fail explicitly; navigation remains available',async({page})=>{
  await page.goto(anna+'/read?entry=missing');
  await expect(page.locator('#book-error')).toContainText('不会自动跳到同名条目');
  await expect(page.locator('#content')).not.toBeVisible();
  // A failed first target must still leave the complete directory usable.
  await page.locator('#book-error a').click();await ready(page);
  await page.goto(anna+'/read?entry=missing&page=topics/test.htm');
  await expect(page.locator('#book-error')).toContainText('阅读地址无效');
});

test('late responses and unrelated messages cannot replace the current page',async({page})=>{
  await visit(page,{entry:cases.spell.id});await sidebar(page,'outline');
  const links=page.locator('#book-outline li > a:first-child');
  const slow=await links.nth(0).getAttribute('href'), fast=await links.nth(1).getAttribute('href');
  const slowId=new URL(slow,anna).searchParams.get('entry');
  await page.route('**/api/reader-page?**',async route=>{
    if(new URL(route.request().url()).searchParams.get('entry')===slowId)await new Promise(resolve=>setTimeout(resolve,500));
    await route.continue();
  });
  await links.nth(0).click();await links.nth(1).click();
  await expect(page).toHaveURL(anna+fast);await ready(page);
  await page.evaluate(snapshot=>window.postMessage({channel:'anna-reader',version:2,type:'navigate',snapshot,route:{page:'topics/写在前面.html'}},location.origin),meta.snapshot);
  await page.waitForTimeout(650);await expect(page).toHaveURL(anna+fast);
});

test('disabled storage still permits navigation and shared shell theme; original text stays unchanged',async({page,context})=>{
  await context.addInitScript(()=>{Storage.prototype.getItem=()=>{throw new Error('blocked')};Storage.prototype.setItem=()=>{throw new Error('blocked')};Storage.prototype.removeItem=()=>{throw new Error('blocked')};});
  await visit(page,{entry:cases.spell.id});
  const before=await content(page).locator('body').innerText();
  await expect(page.locator('#theme')).not.toHaveAttribute('aria-keyshortcuts');
  for(const preference of ['light','dark','system']){
    await page.locator('#theme').click();
    await expect(page.locator('#theme')).toHaveAttribute('data-preference',preference);
    await expect(content(page).locator('html')).toHaveAttribute('data-webhelp-theme',preference==='system'?'light':preference);
    expect(await content(page).locator('body').innerText()).toBe(before);
  }
  await page.reload();await ready(page);
});

for(const [label,path] of [
  ['spells','topics/玩家手册2024/法术详述/1环.htm'],
  ['wide-table','topics/玩家手册2024/装备/武器.htm'],
  ['image','topics/玩家手册2024/附录A：多元宇宙.htm']
 ]) for(const width of [1280,390]) for(const theme of ['light','dark']) {
  test(`embedded original layout ${label} ${width}px ${theme}`,async({page,context},testInfo)=>{
    const {PNG}=require('pngjs'),pixelmatch=(await import('pixelmatch')).default;
    await page.emulateMedia({colorScheme:theme});
    await page.setViewportSize({width,height:900});await visit(page,{page:path});
    const f=content(page),frameRect=await page.locator('#content').boundingBox(),scrollRect=await page.locator('#book-scroll').boundingBox();
    const size={...frameRect,height:scrollRect.height};
    const source=await context.newPage();await source.setViewportSize({width:Math.round(size.width)+(width>800?290:0),height:Math.round(size.height)});
    // Use the actual upstream shell: it adds contrast repair and applies topic preferences.
    await source.addInitScript(theme=>localStorage.setItem('5echm.webhelp.theme',theme),theme);
    await source.goto(original+'/index.htm?'+new URLSearchParams({page:path}));
    const sourceRect=await source.locator('#content').boundingBox();
    await source.setViewportSize({width:Math.round(size.width+sourceRect.x),height:Math.round(size.height+sourceRect.y)});
    const originalFrame=source.frames().find(f=>f.url().includes('/topics/'));
    for(const frame of [f,originalFrame]) {
      await frame.evaluate(()=>document.fonts.ready);
      await expect(frame.locator('html')).toHaveAttribute('data-webhelp-theme',theme);
      await expect(frame.locator('html')).toHaveAttribute('data-webhelp-contrast-repair-count',/\d+/);
    }
    const shape=()=>({text:document.body.innerText,geometry:Array.from(document.body.querySelectorAll('h1,h2,h3,h4,p,table,tr,td,img')).map(e=>{
      const r=e.getBoundingClientRect();return [e.tagName,Math.round(r.x*100),Math.round((r.y+scrollY)*100),Math.round(r.width*100),Math.round(r.height*100)];
    })});
    expect(await f.evaluate(shape)).toEqual(await originalFrame.evaluate(shape));
    if(label==='image')expect(await f.locator('img').evaluateAll(images=>images.every(image=>image.complete && image.naturalWidth>0))).toBe(true);
    await page.mouse.move(0,0);await source.mouse.move(0,0);
    const actual=await page.screenshot({clip:{x:size.x,y:scrollRect.y,width:size.width,height:size.height}}),expected=await source.locator('#content').screenshot();
    const a=PNG.sync.read(actual),b=PNG.sync.read(expected);
    expect([a.width,a.height]).toEqual([b.width,b.height]);
    const diff=new PNG({width:a.width,height:a.height});
    const mismatches=pixelmatch(a.data,b.data,diff.data,a.width,a.height,{threshold:0,includeAA:true});
    if(mismatches)await testInfo.attach('embedded-diff.png',{body:PNG.sync.write(diff),contentType:'image/png'});
    expect(mismatches,'original source / embedded source pixels').toBe(0);
  });
}

test('reader labels have matching Chinese and English resources',async()=>{
  const vm=require('node:vm'),fs=require('node:fs');
  const context=vm.createContext({});
  for(const file of ['link-labels.js','reader-labels.js'])vm.runInContext(fs.readFileSync('web/'+file,'utf8'),context);
  const labels=JSON.parse(vm.runInContext('JSON.stringify(annaReaderLabels)',context));
  expect(Object.keys(labels.zh)).toEqual(Object.keys(labels.en));
  expect(Object.keys(labels.zh.errors)).toEqual(Object.keys(labels.en.errors));
});

for(const width of [390,1280,1920]) test(`text fills the reading area without duplicate headings ${width}px`,async({page})=>{
  await page.setViewportSize({width,height:900});await visit(page,{page:cases.spell.path});
  const rect=await page.locator('#content').boundingBox();
  const scroll=await page.locator('#book-scroll').boundingBox();
  expect(rect.y).toBe(scroll.y);
  expect(scroll.y).toBeLessThanOrEqual(100);
  expect(rect.height).toBeGreaterThanOrEqual(scroll.height);
  expect(await content(page).evaluate(()=>scrollY)).toBe(0);
  expect(Math.round(rect.x+rect.width)).toBe(width);
  expect(Math.round(scroll.y+scroll.height)).toBe(900);
  await expect(page.locator('#book-title, .book-page-heading, .book-footer, #book-wide')).toHaveCount(0);
});

test('parsed text is available before delayed images and late loads do not replace the next page',async({page},testInfo)=>{
  let release;
  const held=new Promise(resolve=>{release=resolve;});
  let imageRequested=false;
  await page.route('**/*',async route=>{
    if(route.request().resourceType()==='image' && route.request().url().startsWith(book+'/topics/')){
      imageRequested=true;await held;
    }
    await route.continue();
  });
  try {
    const started=Date.now();
    await page.goto(anna+'/read?'+new URLSearchParams({page:'topics/玩家手册2024/附录A：多元宇宙.htm'}),{waitUntil:'domcontentloaded'});
    await expect(page.locator('#book-main')).toHaveAttribute('aria-busy','false',{timeout:4000});
    await expect.poll(()=>imageRequested).toBe(true);
    await expect(page.locator('#book-error')).toBeHidden();
    const f=content(page);
    expect(await f.locator('img').evaluateAll(images=>images.some(image=>!image.complete))).toBe(true);
    await testInfo.attach('text-ready-before-image.json',{body:Buffer.from(JSON.stringify({textReadyMs:Date.now()-started,imageStillPending:true})),contentType:'application/json'});
    await sidebar(page,'contents');await page.locator('#book-filter').fill('写在前面');
    const nextLink=page.locator('#book-tree a').filter({hasText:'写在前面'});
    const next=await nextLink.getAttribute('href');
    await nextLink.click();await ready(page);
    release();
    await expect(page).toHaveURL(anna+next);
    await expect(page.locator('#book-main')).toHaveAttribute('aria-busy','false');
  } finally {release();}
});
