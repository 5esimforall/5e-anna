/* A small directory fixture over real source documents makes subtree boundaries explicit. */
const {test:base,expect}=require('@playwright/test');
const anna=`http://127.0.0.1:${process.env.ANNA_E2E_PORT||18380}`;
const paths=['topics/玩家手册2024/角色职业/野蛮人/野蛮人.htm','topics/玩家手册2024/角色职业/野蛮人/狂战士道途.htm',
  'topics/玩家手册2024/角色职业/野蛮人/兽心道途.htm','topics/玩家手册2024/角色职业/野蛮人/世界树道途.htm'];
const node=(id,parent_id,path='',fragment='')=>({id,parent_id,path,fragment,label:id,status:path?'present':'placeholder'});
const rows=[node('root','',paths[0]),node('child','root',paths[1]),node('group','root'),node('grandchild','group',paths[2]),
  node('alias','group',paths[2]),node('outside','',paths[3]),node('other-copy','outside',paths[2]),node('empty','')];
const test=base.extend({page:async({page},use)=>{
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/api/reader',async route=>{const response=await route.fetch();await route.fulfill({response,json:{...await response.json(),nodes:rows,default_page:paths[0]}});});
  await use(page);expect(errors).toEqual([]);
}});
async function ready(page){await expect(page.locator('#book-main')).toHaveAttribute('aria-busy','false');await expect(page.locator('#book-error')).toBeHidden();}
async function visit(page,params={root:'root'}){await page.goto(anna+'/read?'+new URLSearchParams(params));await ready(page);}
async function offset(page,y){await page.locator('#book-scroll').evaluate((e,y)=>{const f=document.querySelector('#content');e.scrollTop=f.getBoundingClientRect().top-e.getBoundingClientRect().top+e.scrollTop+y;},y);}
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
const key='anna.bookPosition';
async function saved(page){return page.evaluate(key=>JSON.parse(localStorage.getItem(key)),key);}

test('only the selected subtree is sequenced; manual append keeps the original frame and uses one scroll surface',async({page})=>{
  const loaded=[];page.on('request',r=>{if(r.isNavigationRequest()&&r.url().includes('/book/topics/'))loaded.push(decodeURI(r.url().split('/book/')[1]));});
  await visit(page);
  await expect(page.locator('#book-page-count')).toHaveText('1 / 3');
  expect(loaded).toEqual([paths[0]]);
  const first=page.frames().find(f=>decodeURI(f.url()).endsWith(paths[0]));
  await first.evaluate(()=>window.frameIdentity='retained');
  await page.getByRole('button',{name:'加载下一页：child',exact:true}).press('Enter');
  await expect(page.locator('.book-page[aria-busy=false]')).toHaveCount(2);
  expect(await first.evaluate(()=>window.frameIdentity)).toBe('retained');
  expect(loaded).toEqual(paths.slice(0,2));
  await expect(page.locator('.book-document')).toHaveCount(2);
  await expect(page.locator('#book-scope-title')).toHaveText('root');
  await jump(page,'child');await ready(page);
  await offset(page,50);await expect.poll(async()=>(await saved(page)).position.y).toBeCloseTo(50,0);
  expect(await first.evaluate(()=>scrollY)).toBe(0);
  expect((await saved(page)).root).toBe('root');
  await jump(page,'group');await ready(page);
  await expect(page.locator('.book-page[aria-busy=false]')).toHaveCount(3);
  await expect(page.locator('#book-next')).toHaveAttribute('aria-disabled','true');
  await expect(page.locator('.book-scope-end')).toHaveText('已到本章节末尾');
  expect(loaded).toEqual(paths.slice(0,3));
});

test('page boundaries sit on the source paper with a folio, the page name for screen readers, in both themes and widths',async({page})=>{
  await page.setViewportSize({width:1600,height:900});
  await visit(page);
  await page.getByRole('button',{name:'加载下一页：child',exact:true}).click();
  await expect(page.locator('.book-page[aria-busy=false]')).toHaveCount(2);
  await expect(page.locator('.book-page').first().locator('.book-page-divider')).toBeHidden();
  const divider=page.locator('.book-page').nth(1).locator('.book-page-divider');
  await expect(divider.locator('.book-page-mark')).toHaveText('2 / 3');
  await expect(divider.locator('.book-page-mark svg')).toHaveAttribute('aria-hidden','true');
  await expect(divider.locator('.sr-only')).toHaveText(/\S/);
  await expect(divider).toHaveCSS('border-top-width','0px');
  // The boundary shares the next page's column, paper and side margin, so the leaf continues across it.
  const paper=()=>divider.evaluate(d=>{
    const frame=d.closest('.book-page').querySelector('.book-document'),body=frame.contentDocument.body,view=frame.contentWindow;
    const a=d.getBoundingClientRect(),b=body.getBoundingClientRect(),f=frame.getBoundingClientRect();
    return {left:Math.abs(a.left-(f.left+b.left)),width:Math.abs(a.width-b.width),height:a.height,
      background:[getComputedStyle(d).backgroundColor,view.getComputedStyle(body).backgroundColor],
      padding:[getComputedStyle(d).paddingLeft,view.getComputedStyle(body).paddingLeft]};
  });
  for(const scheme of ['light','dark']){
    await page.emulateMedia({colorScheme:scheme});
    await expect.poll(async()=>{const p=await paper();return p.background[0]===p.background[1];},scheme).toBe(true);
    const p=await paper();
    expect(p.left).toBeLessThan(0.5);expect(p.width).toBeLessThan(0.5);expect(p.height).toBe(28);
    expect(p.padding[0]).toBe(p.padding[1]);
  }
  await page.setViewportSize({width:390,height:844});
  await expect.poll(async()=>(await paper()).padding).toEqual(['16px','16px']);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
});

const labels=page=>page.locator('#book-path-menu [role=menuitem] .book-path-label');
const visibleLevels=page=>page.locator('#book-path > li').evaluateAll(items=>items.filter(li=>li.getClientRects().length).map(li=>li.textContent));

test('the reading bar path lists siblings and children inside the scope and never changes it',async({page})=>{
  await visit(page);
  await expect(page.locator('#book-scope-title')).toHaveAttribute('aria-current','page');
  const down=page.getByRole('button',{name:'下一层：root',exact:true});
  await down.click();
  await expect(down).toHaveAttribute('aria-expanded','true');
  await expect(labels(page)).toHaveText(['child','group']);
  await expect(page.locator('#book-path-menu [role=menuitem]').nth(1).locator('.book-path-count')).toHaveText('2');
  await page.keyboard.press('Escape');
  await expect(page.locator('#book-path-menu')).toBeHidden();await expect(down).toBeFocused();
  // A grouping level opens its first readable descendant; the scope and its page count stay.
  await down.click();await page.locator('#book-path-menu').getByRole('menuitem',{name:'group',exact:true}).click();await ready(page);
  await expect(page.locator('#book-page-count')).toHaveText('3 / 3');
  expect(Object.fromEntries(new URL(page.url()).searchParams)).toMatchObject({root:'root',page:paths[2],node:'grandchild'});
  expect(await visibleLevels(page)).toEqual(['root','group','grandchild']);
  await expect(page.locator('#book-breadcrumb')).toHaveAttribute('title','root / group / grandchild');
  await expect(page.getByRole('button',{name:/^下一层/})).toHaveCount(0);
  await page.locator('.book-path-segment',{hasText:'group'}).click();
  await expect(labels(page)).toHaveText(['child','group']);
  await expect(page.locator('#book-path-menu [aria-current=location]')).toHaveText('group2');
  await page.keyboard.press('Escape');
  await page.locator('.book-path-segment',{hasText:'grandchild'}).click();
  await expect(labels(page)).toHaveText(['grandchild','alias']);
  await expect(page.locator('#book-path-menu [aria-current=page]')).toHaveText('grandchild');
  await page.keyboard.press('Escape');
  await page.goBack();await ready(page);
  await expect(page.locator('#book-page-count')).toHaveText('1 / 3');
  expect(new URL(page.url()).searchParams.get('root')).toBe('root');
});

test('path menus work from the keyboard: arrows, adjacent levels, type-ahead, Enter and Escape',async({page})=>{
  await visit(page);await jump(page,'group');await ready(page);
  const group=page.locator('.book-path-segment',{hasText:'group'});
  await group.focus();await page.keyboard.press('ArrowDown');
  await expect(page.locator('#book-path-menu')).toBeVisible();
  await expect(page.locator('#book-path-menu [role=menuitem]:focus')).toHaveText('group2');
  await page.keyboard.press('ArrowDown');await expect(page.locator('#book-path-menu [role=menuitem]:focus')).toHaveText('child');
  await page.keyboard.press('End');await expect(page.locator('#book-path-menu [role=menuitem]:focus')).toHaveText('group2');
  await page.keyboard.press('ArrowRight');
  await expect(page.locator('.book-path-segment',{hasText:'grandchild'})).toHaveAttribute('aria-expanded','true');
  await expect(group).toHaveAttribute('aria-expanded','false');
  await expect(page.locator('#book-path-menu [role=menuitem]:focus')).toHaveText('grandchild');
  await page.keyboard.press('a');await expect(page.locator('#book-path-menu [role=menuitem]:focus')).toHaveText('alias');
  // The alias shares the document; choosing it moves the directory entry, and focus returns to the path.
  await page.keyboard.press('Enter');await ready(page);
  await expect(page.locator('#book-path-menu')).toBeHidden();
  expect(new URL(page.url()).searchParams.get('node')).toBe('alias');
  await expect(page.locator('#book-path [aria-current=page]')).toHaveText('alias');
  await expect(page.locator('#book-path button:focus')).toHaveCount(1);
  await page.locator('#book-path [aria-current=page]').focus();await page.keyboard.press('ArrowDown');
  await page.keyboard.press('Escape');
  await expect(page.locator('#book-path-menu')).toBeHidden();await expect(page.locator('#book-path [aria-current=page]')).toBeFocused();
});

test('a narrow bar folds outside ancestors, then inner levels and the scope name, and keeps the current level',async({page})=>{
  const long=['An ancestor shelf of several books','The scope book with a long title','A chapter about adventuring far',
    'A section about long journeys','The current page with a long name'];
  const nested=long.map((label,i)=>({id:'n'+i,parent_id:i?'n'+(i-1):'',path:i?paths[i-1]:'',fragment:'',label,status:i?'present':'placeholder'}));
  await page.route('**/api/reader',async route=>{const response=await route.fetch();await route.fulfill({response,json:{...await response.json(),nodes:nested,default_page:paths[0]}});});
  await page.setViewportSize({width:1900,height:900});
  await visit(page,{root:'n1',page:paths[3],node:'n4'});
  expect(await visibleLevels(page)).toEqual(long);
  await expect(page.locator('.book-path-more')).toBeHidden();
  await page.setViewportSize({width:390,height:844});
  await expect.poll(()=>visibleLevels(page)).toEqual(['…',long[4]]);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  await page.getByRole('button',{name:'更多层级',exact:true}).click();
  await expect(labels(page)).toHaveText(long.slice(1,4));
  const depths=await page.locator('#book-path-menu [role=menuitem]').evaluateAll(items=>items.map(item=>item.style.getPropertyValue('--depth')));
  expect(depths).toEqual(['0','1','2']);
  await page.locator('#book-path-menu').getByRole('menuitem',{name:long[2],exact:true}).click();await ready(page);
  await expect(page.locator('#book-page-count')).toHaveText('2 / 4');
  expect(new URL(page.url()).searchParams.get('root')).toBe('n1');
});

test('pagination can jump past unloaded pages without fetching them; duplicates do not mount twice',async({page})=>{
  await visit(page);await jump(page,'group');await ready(page);
  await page.waitForTimeout(350); // Background loading must not fill skipped pages behind the destination.
  await expect(page.locator('.book-document')).toHaveCount(2);
  await expect(page.locator('.book-page-gap')).toContainText('第 2–2 页尚未加载');
  await expect(page.locator('#book-page-count')).toHaveText('3 / 3');
  expect(new URL(page.url()).searchParams.get('root')).toBe('root');
  await page.locator('#book-previous').click();await ready(page);
  await expect(page.locator('.book-document')).toHaveCount(3);
  await expect(page.locator('#book-page-count')).toHaveText('2 / 3');
  await expect(page.locator('.book-page-gap')).toHaveCount(0);
  await page.locator('#book-next').click();await ready(page);
  await expect(page.locator('.book-document')).toHaveCount(3);
});

test('a failed append keeps successful text and position; retry loads just that document',async({page})=>{
  await visit(page);
  await page.route('**/api/reader-page?**',async route=>{
    if(new URL(route.request().url()).searchParams.get('path')===paths[1])return route.fulfill({status:503,json:{code:'network'}});
    await route.continue();
  });
  const first=page.frames().find(f=>decodeURI(f.url()).endsWith(paths[0]));
  const text=await first.locator('body').innerText();
  await page.getByRole('button',{name:'加载下一页：child',exact:true}).press('Enter');
  await expect(page.locator('.book-page-state[role=alert]')).toContainText('无法读取');
  expect(await first.locator('body').innerText()).toBe(text);
  await expect(page.locator('#book-error')).toBeHidden();
  expect((await saved(page)).page).toBe(paths[0]);
  await page.unroute('**/api/reader-page?**');
  await page.locator('.book-page-state').getByRole('button',{name:'重试',exact:true}).click();
  await expect(page.locator('.book-page[aria-busy=false]')).toHaveCount(2);
  expect(await first.locator('body').innerText()).toBe(text);
});

test('scope, page and local offset survive reload, return navigation and a fresh visit',async({page})=>{
  await visit(page);await jump(page,'group');await ready(page);await offset(page,350);
  await expect.poll(async()=>(await saved(page)).position.y).toBeCloseTo(350,0);
  await page.reload();await ready(page);
  await expect(page.locator('#book-page-count')).toHaveText('3 / 3');
  expect((await saved(page)).root).toBe('root');
  await expect.poll(async()=>(await saved(page)).position.y).toBeCloseTo(350,0);
  await page.locator('#book-tree [data-node="outside"] > .toc-row a').click();await ready(page);
  await expect(page.locator('#book-scope-title')).toHaveText('outside');
  await page.goBack();await ready(page);
  await expect(page.locator('#book-scope-title')).toHaveText('root');
  await expect.poll(async()=>(await saved(page)).position.y).toBeCloseTo(350,0);
  await page.goto(anna);await visit(page,{});
  await expect(page.locator('#book-page-count')).toHaveText('3 / 3');
  await expect.poll(async()=>(await saved(page)).position.y).toBeCloseTo(350,0);
});

test('grouping nodes and leaves never expand to siblings; outside or invalid scope is rejected',async({page})=>{
  await visit(page);
  await expect(page.locator('.book-path-outside')).toHaveCount(0);
  await expect(page.locator('#book-scope-title')).toHaveText('root');
  await visit(page,{root:'group'});
  await expect(page.locator('#book-page-count')).toHaveText('1 / 1');
  // The path leads to the scope through links outside it, then to the page through levels inside it.
  await expect(page.locator('.book-path-outside a')).toHaveText(['root']);
  await expect(page.locator('#book-scope-title')).toHaveText('group');
  await expect(page.locator('#book-breadcrumb')).toHaveAttribute('title','root / group / grandchild');
  await expect(page.locator('#book-path [aria-current=page]')).toHaveText('grandchild');
  await visit(page,{root:'child'});
  await expect(page.locator('#book-page-count')).toHaveText('1 / 1');
  await expect(page.locator('#book-next')).toHaveAttribute('aria-disabled','true');
  for(const params of [{root:'child',page:paths[2]},{root:'missing'},{root:'empty'},{root:'root',page:paths[2],node:'other-copy'}]){
    await page.goto(anna+'/read?'+new URLSearchParams(params));
    await expect(page.locator('#book-error')).toBeVisible();
    await expect(page.locator('.book-document')).toHaveCount(0);
    // The error covers the text, not the reading bar: the contents stay one click away.
    const expanded=await page.locator('#book-menu').getAttribute('aria-expanded');
    await page.locator('#book-menu').click();
    await expect(page.locator('#book-menu')).toHaveAttribute('aria-expanded',String(expanded!=='true'));
  }
});

test('scrolling changes the current page without changing scope or adding browser history',async({page})=>{
  await visit(page);await page.getByRole('button',{name:'加载下一页：child',exact:true}).press('Enter');
  await expect(page.locator('.book-page[aria-busy=false]')).toHaveCount(2);
  const length=await page.evaluate(()=>history.length);
  await page.locator('.book-document').nth(1).evaluate(frame=>{
    const scroll=document.querySelector('#book-scroll');scroll.scrollTop=frame.getBoundingClientRect().top-scroll.getBoundingClientRect().top+scroll.scrollTop+150;
  });
  await expect.poll(async()=>(await saved(page)).page).toBe(paths[1]);
  expect((await saved(page)).root).toBe('root');
  expect(await page.evaluate(()=>history.length)).toBe(length);
  await expect(page.locator('#book-page-count')).toHaveText('2 / 3');
  await expect(page.locator('#book-outline-title')).not.toHaveText('野蛮人');
});

test('wheel and keyboard scroll the outer reading surface; earlier document growth preserves local position',async({page})=>{
  await visit(page);
  await page.locator('#book-menu').press('F6');
  await page.keyboard.press('PageDown');
  await expect.poll(async()=>(await saved(page)).position.y).toBeGreaterThan(100);
  const before=(await saved(page)).position.y;
  const rect=await page.locator('#book-scroll').boundingBox();
  await page.mouse.move(rect.x+rect.width/2,rect.y+100);await page.mouse.wheel(0,200);
  await expect.poll(async()=>(await saved(page)).position.y).toBeGreaterThan(before);
  await jump(page,'child');await ready(page);await offset(page,50);
  await expect.poll(async()=>(await saved(page)).position.y).toBeCloseTo(50,0);
  const first=page.frames().find(f=>decodeURI(f.url()).endsWith(paths[0]));
  const height=await page.locator('.book-document').first().evaluate(e=>e.offsetHeight);
  await first.locator('body').evaluate(body=>{const late=document.createElement('div');late.style.height='300px';body.prepend(late);});
  await expect.poll(()=>page.locator('.book-document').first().evaluate(e=>e.offsetHeight)).toBeGreaterThan(height+250);
  await expect.poll(async()=>(await saved(page)).position.y).toBeCloseTo(50,0);
  expect((await saved(page)).page).toBe(paths[1]);
});

test('a source definition link within the selected subtree preserves its reading root',async({page,request})=>{
  const cases=await (await request.get(anna+'/__cases')).json(),mention=cases.mention;
  await page.route('**/api/reader',async route=>{
    const response=await route.fetch();await route.fulfill({response,json:{...await response.json(),nodes:[
      node('reference-root','',mention.path),node('definition','reference-root',mention.target.path)]}});
  });
  await visit(page,{root:'reference-root'});
  const frame=page.frameLocator('#content');
  const link=frame.getByRole('link',{name:'查看定义: '+mention.text,exact:true}).first();
  await link.click();
  await expect.poll(()=>new URL(page.url()).searchParams.get('entry')).toBe(mention.target_id);
  await expect.poll(()=>new URL(page.url()).searchParams.get('root')).toBe('reference-root');
  await ready(page);
  await expect(page.locator('#book-page-count')).toHaveText('2 / 2');
  await expect(page.locator('.book-document')).toHaveCount(2);
  await page.goBack();await ready(page);
  await expect(page.locator('#book-page-count')).toHaveText('1 / 2');
  await expect(page.locator('.book-document')).toHaveCount(2);
});

test('late manual loads and forged messages cannot resurrect an abandoned subtree',async({page})=>{
  await visit(page);
  let release,requested=false;
  const held=new Promise(resolve=>{release=resolve;});
  await page.route('**/api/reader-page?**',async route=>{
    if(new URL(route.request().url()).searchParams.get('path')===paths[1]){requested=true;await held;}
    await route.continue().catch(()=>{});
  });
  try {
    await page.getByRole('button',{name:'加载下一页：child',exact:true}).press('Enter');
    await expect.poll(()=>requested).toBe(true);
    await page.locator('#book-tree [data-node="outside"] > .toc-row a').click();await ready(page);
    release();
    await page.evaluate(()=>window.postMessage({channel:'anna-reader',version:2,type:'navigate',route:{page:'topics/写在前面.html'}},location.origin));
    await expect(page.locator('#book-scope-title')).toHaveText('outside');
    await expect(page.locator('.book-document')).toHaveCount(1);
    expect(new URL(page.url()).searchParams.get('root')).toBe('outside');
  } finally {release();}
});

for(const width of [390,800,1280])test(`chapter pagination remains keyboard accessible at ${width}px`,async({page},testInfo)=>{
  await page.setViewportSize({width,height:844});await visit(page);
  await page.locator('#book-next').focus();await page.locator('#book-next').press('Enter');
  await ready(page);
  await expect(page.locator('#book-page-count')).toHaveText('2 / 3');
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  await expect(page.locator('#book-next')).toBeFocused();
  await page.setViewportSize({width:width===1280?390:1280,height:844});await ready(page);
  await expect(page.locator('#book-page-count')).toHaveText('2 / 3');
  // A short current page may automatically bring in its next page after resizing.
  for (const path of paths.slice(0,2)) await expect(page.locator(`.book-page[data-path="${path}"] iframe`)).toHaveCSS('visibility','visible');
  expect(await page.locator('.book-document').count()).toBeLessThanOrEqual(3);
  await page.setViewportSize({width,height:844});
  await testInfo.attach('chapter-pagination.png',{body:await page.screenshot(),contentType:'image/png'});
});
