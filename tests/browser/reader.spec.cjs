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

test('the reader topbar mirrors anna with its own Stella avatar on desktop and mobile',async({page})=>{
  await visit(page);
  const brand=page.getByRole('link',{name:'5e Stella',exact:true});
  const logo=brand.locator('img');
  await expect(brand).toHaveAttribute('href','/read');
  await expect(logo).toHaveAttribute('src','/stella-logo.png');
  await expect.poll(()=>logo.evaluate(img=>img.naturalWidth)).toBe(128);
  await expect(page.locator('link[rel=icon]')).toHaveAttribute('href','/stella-favicon.ico');
  for(const viewport of [{width:1280,height:800},{width:390,height:844}]){
    await page.setViewportSize(viewport);
    await expect(logo).toBeInViewport();
    await expect(logo).toHaveCSS('width','36px');
    await expect(logo).toHaveCSS('height','36px');
    await expect(brand).toHaveText('5e Stella');
    await expect(brand).toHaveCSS('font-size','21px');
    await expect(page.locator('.book-topbar')).toHaveCSS('height',viewport.width>760?'56px':'52px');
    // The topbar carries exactly anna's parts; the contents toggle leads the reading bar above the text.
    await page.locator('.skip-link').focus();await page.keyboard.press('Tab');
    await expect(brand).toBeFocused();
    await page.locator('.companion-go').focus();await page.keyboard.press('Tab');
    await expect(page.locator('#theme')).toBeFocused();
    // With nothing more remembered, the companion is one link and the theme is the only visible button.
    await expect(page.locator('.book-topbar button:visible')).toHaveCount(1);
    await expect(page.locator('#book-bar #book-menu')).toHaveCSS('border-top-color','rgba(0, 0, 0, 0)');
    const [bar,menu,scope,pager,marks]=await Promise.all(['#book-bar','#book-menu','#book-scope-title','#book-paging','#book-link-marks'].map(s=>page.locator(s).boundingBox()));
    expect(menu.x).toBeLessThan(scope.x);expect(scope.x).toBeLessThan(pager.x);
    // Marking links is a view of the text, so it ends the bar as the theme ends the topbar.
    expect(pager.x+pager.width).toBeLessThanOrEqual(marks.x);
    expect(marks.x+marks.width).toBeLessThanOrEqual(bar.x+bar.width);
    // Ancestors outside the scope leave the bar first on narrow screens; the current level always stays.
    if(viewport.width<=800)for(const outside of await page.locator('.book-path-outside').all())await expect(outside).toBeHidden();
    await expect(page.locator('#book-path > li:not(.folded):not([hidden])').last()).toBeVisible();
    // The sidebar closes from the bar beside it; only the modal drawer, which covers the bar, has its own close.
    if(viewport.width>800)await expect(page.locator('#book-close')).toBeHidden();
  }
});

test('icon buttons center their icons in the topbar, reading bar and contents tools',async({page})=>{
  await visit(page);await sidebar(page,'contents');
  const offsets=await page.locator('.icon-button:visible').evaluateAll(buttons=>buttons.map(b=>{
    const r=b.getBoundingClientRect(),s=b.querySelector('svg').getBoundingClientRect();
    return {id:b.id,dx:Math.abs(s.x+s.width/2-r.x-r.width/2),dy:Math.abs(s.y+s.height/2-r.y-r.height/2)};
  }));
  expect(offsets.map(o=>o.id)).toEqual(expect.arrayContaining(['theme','book-menu','book-link-marks','book-filter-open','book-locate','book-expand','book-collapse']));
  for(const o of offsets){expect(o.dx,o.id).toBeLessThan(0.5);expect(o.dy,o.id).toBeLessThan(0.5);}
});

// What each named text highlight covers in the source page; the page's bindings list the same ranges.
function linkMarks(frame){
  return frame.evaluate(()=>{
    const covered=name=>[...(CSS.highlights.get(name)||[])].map(r=>{
      const range=document.createRange();range.setStart(r.startContainer,r.startOffset);range.setEnd(r.endContainer,r.endOffset);return range.toString();
    }).sort();
    return {references:covered('anna-reference'),definitions:covered('anna-definition')};
  });
}
function boundLinks(frame){
  return frame.evaluate(()=>{
    const data=JSON.parse(document.getElementById('anna-bindings').textContent);
    return {references:data.references.map(r=>r.range.text).sort(),definitions:data.definitions.filter(d=>d.title).map(d=>d.title.text).sort()};
  });
}
function markColors(frame){
  return frame.evaluate(()=>['anna-reference','anna-definition'].map(name=>{
    const range=[...CSS.highlights.get(name)][0];
    return getComputedStyle(range.startContainer.parentElement,`::highlight(${name})`).backgroundColor;
  }));
}

test('marking links tints every link anna recognized, by destination and theme, without changing the source',async({page},testInfo)=>{
  const {PNG}=require('pngjs'),pixelmatch=(await import('pixelmatch')).default;
  await visit(page,{page:cases.spell.path});
  const toggle=page.locator('#book-link-marks'),f=content(page);
  await expect(toggle).toBeVisible();
  await expect(toggle).toHaveAccessibleName('标出 Anna 识别的链接');
  await expect(toggle).toHaveAccessibleDescription('蓝底跳到 Stella 中的定义，红底在 Anna 打开条目');
  // Off by default: the source keeps its original look until the reader asks.
  await expect(toggle).toHaveAttribute('aria-pressed','false');
  expect(await linkMarks(f)).toEqual({references:[],definitions:[]});
  const bound=await boundLinks(f);
  expect(bound.references.length).toBeGreaterThan(0);expect(bound.definitions.length).toBeGreaterThan(0);
  const source=await f.evaluate(()=>document.body.innerHTML);
  const reference=f.locator('anna-links a[data-kind=reference]').first();
  await reference.scrollIntoViewIfNeeded();
  const clip=await reference.boundingBox(),url=page.url();
  const pixels=async()=>PNG.sync.read(await page.screenshot({clip}));
  const changed=(a,b)=>pixelmatch(a.data,b.data,null,a.width,a.height,{threshold:0});
  const unmarked=await pixels();

  await toggle.click();
  await expect(toggle).toHaveAttribute('aria-pressed','true');
  await expect.poll(()=>linkMarks(f)).toEqual(bound);
  await expect.poll(async()=>changed(await pixels(),unmarked),'the reference is painted').toBeGreaterThan(0);
  expect(await f.evaluate(()=>document.body.innerHTML),'marks add no source DOM').toBe(source);
  expect(page.url(),'marks are not part of the shared address').toBe(url);
  // Stella's blue jumps within the reader; Anna's wine opens her entry.
  expect(await markColors(f)).toEqual(['rgba(29, 95, 167, 0.16)','rgba(150, 44, 63, 0.12)']);
  await testInfo.attach('link-marks-light.png',{body:await page.screenshot(),contentType:'image/png'});

  await toggle.focus();await page.keyboard.press('Space');
  await expect(toggle).toHaveAttribute('aria-pressed','false');await expect(toggle).toBeFocused();
  await expect.poll(()=>linkMarks(f)).toEqual({references:[],definitions:[]});
  await expect.poll(async()=>changed(await pixels(),unmarked),'turning marks off restores the original pixels').toBe(0);
  expect(await f.evaluate(()=>document.body.innerHTML)).toBe(source);

  // Dark pages get their own tints. (The source's contrast repair restyles elements on a theme change, so the
  // DOM comparisons above stay in one theme.)
  await page.keyboard.press('Enter');await expect(toggle).toHaveAttribute('aria-pressed','true');
  await page.emulateMedia({colorScheme:'dark'});
  await expect(f.locator('html')).toHaveAttribute('data-webhelp-theme','dark');
  await expect.poll(()=>linkMarks(f)).toEqual(bound);
  expect(await markColors(f)).toEqual(['rgba(114, 167, 228, 0.26)','rgba(240, 157, 171, 0.22)']);
  await testInfo.attach('link-marks-dark.png',{body:await page.screenshot(),contentType:'image/png'});
});

test('link marks persist in this browser, reach later documents and other tabs; without text highlights there is no switch',async({page,context,browser})=>{
  const ref=cases.reference;
  await visit(page,{page:cases.reference_source});
  await page.locator('#book-link-marks').click();
  expect(await page.evaluate(()=>localStorage.getItem('anna.linkMarks'))).toBe('on');
  // A document mounted afterwards starts marked: follow a marked reference to its definition on another page.
  await page.frameLocator('#content').getByRole('link',{name:'查看定义: '+ref.text,exact:true}).first().click();
  await expect.poll(()=>new URL(page.url()).searchParams.get('entry')).toBe(ref.id);await ready(page);
  const target=()=>page.frames().find(f=>decodeURIComponent(f.url()).includes(ref.path));
  await expect.poll(async()=>target() && linkMarks(target())).toEqual(await boundLinks(target()));
  const other=await context.newPage();await visit(other,{page:cases.spell.path});
  await expect(other.locator('#book-link-marks')).toHaveAttribute('aria-pressed','true');
  await expect.poll(async()=>(await linkMarks(content(other))).references.length).toBeGreaterThan(0);
  await page.reload();await ready(page);
  await expect(page.locator('#book-link-marks')).toHaveAttribute('aria-pressed','true');
  await expect.poll(async()=>(await linkMarks(content(page))).references.length).toBeGreaterThan(0);
  // Turning them off in one tab clears the others; the default stores nothing.
  await page.locator('#book-link-marks').click();
  expect(await page.evaluate(()=>localStorage.getItem('anna.linkMarks'))).toBeNull();
  await expect(other.locator('#book-link-marks')).toHaveAttribute('aria-pressed','false');
  await expect.poll(()=>linkMarks(content(other))).toEqual({references:[],definitions:[]});
  const plain=await browser.newContext();
  await plain.addInitScript(()=>{if(window===top)Object.defineProperty(CSS,'highlights',{value:undefined,configurable:true});});
  const unsupported=await plain.newPage();await visit(unsupported,{page:cases.spell.path});
  await expect(unsupported.locator('#book-link-marks')).toBeHidden();
  await plain.close();
});

test('titles name the open content on both pages',async({page})=>{
  await page.goto(anna);
  await expect(page).toHaveTitle('5e Anna');
  await page.locator('#results .entry-name').first().click();
  await expect(page.locator('#entry-title')).toBeVisible();
  const name=(await page.locator('#entry-title').textContent()).trim();
  const entry=new URL(page.url()).searchParams.get('entry');
  await expect(page).toHaveTitle(`${name} · 5e Anna`);
  await page.locator('#close-detail').click();
  await expect(page).toHaveTitle('5e Anna');
  // The open entry is the topic the switcher turns to; companions.spec.cjs covers where each choice leads.
  await page.locator('#results .entry-name').first().click();await expect(page.locator('#entry-title')).toHaveText(name);
  await page.locator('.companion-go').click();await ready(page);
  expect(new URL(page.url()).searchParams.get('entry')).toBe(entry);
  await expect(page).toHaveTitle(/ · 5e Stella$/);
});

test('query and reader use the new app; upstream reading entries are gone',async({page,request})=>{
  await page.goto(anna);
  await expect(page.getByRole('link',{name:'5e Stella',exact:true})).toBeVisible();
  await page.getByRole('link',{name:'5e Stella',exact:true}).click();await ready(page);
  expect(new URL(page.url()).pathname).toBe('/read');
  await expect(page).toHaveTitle('写在前面 · 5e Stella');
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
  await page.locator('#book-filter-open').click();await page.locator('#book-filter').fill('没有这个目录xyz');
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
  const before=await readingPosition(page), url=page.url();
  expect(before).toBeGreaterThan(0);
  await expect.poll(()=>page.evaluate(()=>history.state?.book?.position.y)).toBeCloseTo(before,0);
  await page.keyboard.press('Enter');
  await expect(page).toHaveURL(new RegExp('entry='+m.target_id));await ready(page);
  expect(content(page).url()).toContain('#'+m.target.fragment);
  await page.goBack();await expect(page).toHaveURL(url);await ready(page);
  await expect.poll(()=>readingPosition(page)).toBeCloseTo(before,0);
  await page.goForward();await ready(page);await expect(page).toHaveURL(new RegExp('entry='+m.target_id));
  await page.locator('#book-scroll').evaluate(e=>e.scrollBy(0,250));
  const position=await readingPosition(page);expect(position).toBeGreaterThan(200);
  await page.reload();await ready(page);
  await expect.poll(()=>readingPosition(page)).toBeCloseTo(position,0);
});

// Stella's places live in the shared workspace, newest first.
const workspaceKey='anna.workspace', legacyKey='anna.bookPosition';
async function latestPlace(page){return page.evaluate(key=>JSON.parse(localStorage.getItem(key))?.book[0],workspaceKey);}
test('closing and reopening the reader resumes its page, directory entry and scroll',async({page,context})=>{
  const path='topics/玩家手册2024/角色职业/野蛮人/野蛮人.htm';
  const node=meta.nodes.filter(n=>n.path===path && n.status==='present').at(-1).id;
  await visit(page,{page:path,node});
  await scrollToPosition(page,950);
  await expect.poll(async()=>(await latestPlace(page))?.position.y).toBe(950);
  await page.close();
  const reopened=await context.newPage();await visit(reopened);
  expect(new URL(reopened.url()).searchParams.get('page')).toBe(path);
  expect(new URL(reopened.url()).searchParams.get('node')).toBe(node);
  await expect.poll(()=>readingPosition(reopened)).toBe(950);
  await expect(reopened.locator(`#book-tree [data-node="${node}"] > .toc-row a`)).toHaveAttribute('aria-current','page');
  // Turning to Stella from Anna's switcher resumes the same place.
  await reopened.goto(anna);await reopened.locator('.companion-go').click();await ready(reopened);
  await expect.poll(()=>readingPosition(reopened)).toBe(950);
});

test('explicit page and entry links take priority over the remembered reading position',async({page,context})=>{
  await visit(page,{page:'topics/玩家手册2024/法术详述/1环.htm'});
  await scrollToPosition(page,1100);
  await expect.poll(async()=>(await latestPlace(page))?.position.y).toBe(1100);
  const explicit=await context.newPage();await visit(explicit,{entry:cases.spell.id});
  expect(new URL(explicit.url()).searchParams.get('entry')).toBe(cases.spell.id);
  expect(content(explicit).url()).toContain('#anna-'+cases.spell.id);
  await visit(explicit,{page:'topics/玩家手册2024/法术详述/1环.htm'});
  await expect.poll(()=>readingPosition(explicit)).toBe(0);
  await page.reload();await ready(page);
  await expect.poll(()=>readingPosition(page)).toBe(1100);
});

test('invalid, outdated or foreign remembered places fall back to the default page',async({page})=>{
  await page.goto(anna);
  const valid={version:2,snapshot:meta.snapshot,root:'',page:'topics/玩家手册2024/法术详述/1环.htm',node:'',position:{x:0,y:900}};
  for(const book of [[{...valid,position:{x:0,y:-1}}],[{...valid,position:{x:0,y:'900'}}],[{...valid,page:'topics/../index.htm'}],
      [{...valid,root:'missing-node'}],'not a list']){
    for(const record of ['{',JSON.stringify({version:1,snapshot:meta.snapshot,book}),JSON.stringify({version:1,snapshot:'old',book:[valid]})]){
      await page.evaluate(({key,record})=>localStorage.setItem(key,record),{key:workspaceKey,record});
      await visit(page);
      expect(content(page).url()).toContain(encodeURI(meta.default_page));
      await expect.poll(()=>readingPosition(page)).toBe(0);
      await page.goto(anna);
    }
  }
});

test('the single saved position of earlier versions becomes the first remembered place once',async({page})=>{
  await page.goto(anna);
  const path='topics/玩家手册2024/法术详述/1环.htm';
  const legacy={version:1,snapshot:meta.snapshot,page:path,node:'',position:{x:0,y:900}};
  for(const value of ['{',JSON.stringify({...legacy,snapshot:'old'}),JSON.stringify({...legacy,version:3}),JSON.stringify({...legacy,position:{x:0,y:-1}})]){
    await page.evaluate(({key,value})=>{localStorage.clear();localStorage.setItem(key,value);},{key:legacyKey,value});
    await visit(page);
    expect(content(page).url()).toContain(encodeURI(meta.default_page));
    expect(await page.evaluate(key=>localStorage.getItem(key),legacyKey)).toBeNull();
    await page.goto(anna);
  }
  await page.evaluate(({key,value})=>{localStorage.clear();localStorage.setItem(key,value);},{key:legacyKey,value:JSON.stringify(legacy)});
  await visit(page);
  expect(new URL(page.url()).searchParams.get('page')).toBe(path);
  await expect.poll(()=>readingPosition(page)).toBe(900);
  expect(await page.evaluate(key=>localStorage.getItem(key),legacyKey)).toBeNull();
  expect((await latestPlace(page)).page).toBe(path);
});

test('failed navigation does not replace the last successfully read position',async({page,context})=>{
  await visit(page,{page:'topics/玩家手册2024/法术详述/1环.htm'});
  await scrollToPosition(page,850);
  await expect.poll(async()=>(await latestPlace(page))?.position.y).toBe(850);
  // Leaving the page saves the same place again; only where it is matters.
  const place=({root,page,node,position})=>({root,page,node,position});
  const before=place(await latestPlace(page));
  await page.goto(anna+'/read?entry=missing-entry');
  await expect(page.locator('#book-error')).toBeVisible();
  expect(place(await latestPlace(page))).toEqual(before);
  const reopened=await context.newPage();await visit(reopened);
  await expect.poll(()=>readingPosition(reopened)).toBe(850);
});

test('mobile drawer, focus, outline and resize keep the same document',async({page},testInfo)=>{
  await page.setViewportSize({width:390,height:844});await visit(page,{entry:cases.spell.id});
  await expect(page.locator('#book-sidebar')).not.toBeVisible();
  await sidebar(page,'outline');await expect(page.locator('#book-sidebar')).toHaveJSProperty('open',true);
  expect(await page.locator('#book-sidebar').evaluate(e=>e.matches(':modal'))).toBe(true);
  await expect(page.locator('#book-close')).toBeVisible();
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
  await sidebar(page,'contents');await page.locator('#book-filter-open').click();await page.locator('#book-filter').fill('掩护');
  await page.locator('#book-filter').press('Escape');await expect(page.locator('#book-filter')).toHaveValue('');
  await expect(page.locator('#book-sidebar')).toBeVisible();
  // Escape steps back one layer at a time: the text, the empty filter box, then the drawer.
  await page.keyboard.press('Escape');await expect(page.locator('#book-filter')).toBeHidden();await expect(page.locator('#book-filter-open')).toBeFocused();
  await page.keyboard.press('Escape');await expect(page.locator('#book-sidebar')).not.toBeVisible();
  await expect(page.locator('#book-menu')).toBeFocused();
  await page.setViewportSize({width:1440,height:900});
  await shot(page,testInfo,'reader-desktop.png');
});

test('full-text results, paging and reading state survive reload; search failure is recoverable',async({page})=>{
  await visit(page);await sidebar(page,'search');
  const readingURL=page.url();
  await page.locator('#book-search').fill('护盾');await page.locator('#book-search-form button').click();
  expect(page.url()).toBe(readingURL);
  await expect(page.locator('#book-search-status')).toContainText('找到');
  await expect(page.locator('#book-search-next')).toBeEnabled();await page.locator('#book-search-next').click();
  await expect(page.locator('#book-search-page')).toContainText('第 2 /');
  const name=await page.locator('#book-search-results a').first().innerText();
  await page.reload();await ready(page);
  await expect(page.locator('#tab-search')).toHaveAttribute('aria-selected','true');
  await expect(page.locator('#book-search-page')).toContainText('第 2 /');
  await expect(page.locator('#book-search-results a').first()).toHaveText(name);
  await page.locator('#book-search-results a').first().click();await ready(page);
  await expect(page).not.toHaveTitle('写在前面 · 5e Stella');
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

test('disabled storage still permits navigation, shared shell theme and link marks; original text stays unchanged',async({page,context})=>{
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
  await page.locator('#book-link-marks').click();
  await expect(page.locator('#book-link-marks')).toHaveAttribute('aria-pressed','true');
  await expect.poll(async()=>(await linkMarks(content(page))).references.length).toBeGreaterThan(0);
  expect(await content(page).locator('body').innerText()).toBe(before);
  await page.reload();await ready(page);
  await expect(page.locator('#book-link-marks')).toHaveAttribute('aria-pressed','false');
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
  expect([labels.zh.title,labels.en.title]).toEqual(['5e Stella','5e Stella']);
  vm.runInContext(fs.readFileSync('web/companions.js','utf8'),context);
  const companions=JSON.parse(vm.runInContext('JSON.stringify(annaCompanionLabels)',context));
  const shape=value=>value&&typeof value==='object'?Object.fromEntries(Object.entries(value).map(([k,v])=>[k,shape(v)])):typeof value;
  expect(shape(companions.zh)).toEqual(shape(companions.en));
  expect([companions.zh.names,companions.en.names]).toEqual([{query:'Anna',book:'Stella'},{query:'Anna',book:'Stella'}]);
  expect([labels.zh.entry,labels.en.entry]).toEqual(['在 Anna 查看条目','Open entry in Anna']);
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
    await sidebar(page,'contents');await page.locator('#book-filter-open').click();await page.locator('#book-filter').fill('写在前面');
    const nextLink=page.locator('#book-tree a').filter({hasText:'写在前面'});
    const next=await nextLink.getAttribute('href');
    await nextLink.click();await ready(page);
    release();
    await expect(page).toHaveURL(anna+next);
    await expect(page.locator('#book-main')).toHaveAttribute('aria-busy','false');
  } finally {release();}
});
