/* The companion switcher and the shared workspace behind it, on the real corpus. */
const {test:base,expect}=require('@playwright/test');
const port=Number(process.env.ANNA_E2E_PORT||18380);
const anna=`http://127.0.0.1:${port}`;
const test=base.extend({context:async({context},use)=>{
  const errors=[];context.on('page',page=>page.on('pageerror',error=>errors.push(error.message)));
  await use(context);expect(errors,'companion JavaScript errors').toEqual([]);
}});
let cases,meta;
test.beforeAll(async({request})=>{
  cases=await (await request.get(anna+'/__cases')).json();
  meta=await (await request.get(anna+'/api/reader')).json();
});
async function annaReady(page){await expect(page.locator('#results')).toHaveAttribute('aria-busy','false');await expect(page.locator('.companion')).toBeVisible();}
async function entryReady(page){await expect(page.locator('#detail-content')).toHaveAttribute('aria-busy','false');await expect(page.locator('#entry-title')).toBeVisible();}
async function stellaReady(page){
  await expect(page.locator('#book-main')).toHaveAttribute('aria-busy','false');
  await expect(page.locator('#content')).toBeVisible();await expect(page.locator('.companion')).toBeVisible();
}
const go=page=>page.locator('.companion-go'), more=page=>page.locator('.companion-more');
const workspace=page=>page.evaluate(()=>JSON.parse(localStorage.getItem('anna.workspace')));
async function scrollBook(page,y){await page.locator('#book-scroll').evaluate((e,y)=>{
  const frame=document.querySelector('#content');e.scrollTop=frame.getBoundingClientRect().top-e.getBoundingClientRect().top+e.scrollTop+y;
},y);}
async function bookOffset(page){return page.locator('#book-scroll').evaluate(e=>Math.max(0,e.getBoundingClientRect().top-document.querySelector('#content').getBoundingClientRect().top));}

test('with nothing remembered, each companion leads to the other home',async({page})=>{
  await page.goto(anna);await annaReady(page);
  await expect(go(page)).toHaveAccessibleName('5e Stella');
  await expect(go(page)).toHaveAttribute('href','/read');
  await expect(go(page).locator('img')).toHaveAttribute('src','/stella-logo.png');
  await expect(more(page)).toBeHidden();
  await page.goto(anna+'/read');await stellaReady(page);
  await expect(go(page).locator('img')).toHaveAttribute('src','/anna-logo.png');
  // Anna remembers the empty query she just showed.
  await expect(go(page)).toHaveAccessibleName('5e Anna：回到 全部条目');
  await expect(go(page)).toHaveAttribute('href','/');
});

test('an entry open in Anna becomes the topic Stella turns to, and Anna resumes where she was',async({page})=>{
  await page.goto(anna+'/?q='+encodeURIComponent('护盾术'));await annaReady(page);
  await page.locator(`#results .entry-name[data-entry="${cases.spell.id}"]`).first().click();await entryReady(page);
  const name=(await page.locator('#entry-title').textContent()).trim();
  await expect(go(page)).toHaveAccessibleName(`5e Stella：翻到 ${name}`);
  await expect(go(page)).toHaveAttribute('href','/read?entry='+encodeURIComponent(cases.spell.id));
  expect((await workspace(page)).topic).toMatchObject({entry:cases.spell.id,name,from:'query'});
  // Closing the entry ends the topic; reopening brings it back.
  await page.locator('#close-detail').click();
  await expect(go(page)).toHaveAccessibleName('5e Stella');
  expect((await workspace(page)).topic).toBeNull();
  await page.locator(`#results .entry-name[data-entry="${cases.spell.id}"]`).first().click();await entryReady(page);
  await go(page).click();await stellaReady(page);
  expect(new URL(page.url()).searchParams.get('entry')).toBe(cases.spell.id);
  // Stella answers about the same topic, so she offers Anna's place rather than a new topic.
  await expect(go(page)).toHaveAccessibleName(`5e Anna：回到 ${name}`);
  const back=new URL(await go(page).getAttribute('href'),anna);
  expect([back.searchParams.get('q'),back.searchParams.get('entry')]).toEqual(['护盾术',cases.spell.id]);
  // Reading on past the entry makes the definition in view the topic; Anna's place stays in the menu.
  await scrollBook(page,600);
  await expect.poll(async()=>(await workspace(page)).book[0]?.position.y).toBe(600);
  await expect(go(page)).toHaveAccessibleName(/^5e Anna：查 /);
  expect((await workspace(page)).topic.from).toBe('book');
  await more(page).click();
  await page.locator('#companion-menu').getByRole('group',{name:'Anna 记得'}).getByRole('menuitem',{name:name}).click();await entryReady(page);
  await expect(page.locator('#entry-title')).toHaveText(name);
  expect(new URL(page.url()).searchParams.getAll('q')).toEqual(['护盾术']);
  // Stella brought up the latest topic herself, so turning back resumes her reading position.
  await expect(go(page)).toHaveAccessibleName(/^5e Stella：接着读 /);
  await go(page).click();await stellaReady(page);
  await expect.poll(()=>bookOffset(page)).toBe(600);
});

test('the memory menu lists the topic and remembered places, and works from the keyboard',async({page})=>{
  const first='topics/玩家手册2024/法术详述/1环.htm';
  const node=meta.nodes.find(n=>n.path===first && n.status==='present').id;
  await page.goto(anna+'/read?'+new URLSearchParams({page:first,node}));await stellaReady(page);
  await scrollBook(page,400);
  await expect.poll(async()=>(await workspace(page)).book[0]?.position.y).toBe(400);
  const other=meta.nodes.find(n=>n.status==='present' && n.path.includes('/专长/')).id;
  await page.goto(anna+'/read?'+new URLSearchParams({root:other}));await stellaReady(page);
  await page.goto(anna+`/?entry=${encodeURIComponent(cases.spell.id)}`);await annaReady(page);await entryReady(page);
  await expect(more(page)).toBeVisible();
  await more(page).focus();await page.keyboard.press('ArrowDown');
  const menu=page.locator('#companion-menu');
  await expect(menu).toBeVisible();
  await expect(menu.getByRole('group',{name:'正在看'}).getByRole('menuitem')).toHaveCount(1);
  const places=menu.getByRole('group',{name:'Stella 记得'}).getByRole('menuitem');
  await expect(places).toHaveCount(2);
  await expect(menu.getByRole('menuitem').first()).toBeFocused();
  await page.keyboard.press('End');await expect(places.last()).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(menu).toBeHidden();await expect(more(page)).toBeFocused();
  await expect(more(page)).toHaveAttribute('aria-expanded','false');
  // Choosing the older place restores its scope and offset in this tab.
  await more(page).click();await places.last().click();await stellaReady(page);
  expect(new URL(page.url()).searchParams.get('page')).toBe(first);
  await expect.poll(()=>bookOffset(page)).toBe(400);
});

test('other tabs follow the shared workspace as it changes',async({context})=>{
  const stella=await context.newPage();await stella.goto(anna+'/read');await stellaReady(stella);
  const query=await context.newPage();await query.goto(anna+`/?entry=${encodeURIComponent(cases.spell.id)}`);await annaReady(query);await entryReady(query);
  const name=(await query.locator('#entry-title').textContent()).trim();
  await expect(go(stella)).toHaveAccessibleName(`5e Anna：回到 ${name}`);
  await expect(go(query)).toHaveAccessibleName(`5e Stella：翻到 ${name}`);
});

test('refused storage leaves both switchers leading home',async({context})=>{
  await context.addInitScript(()=>{
    for(const name of ['localStorage','sessionStorage'])Object.defineProperty(window,name,{get(){throw new DOMException('blocked','SecurityError');}});
  });
  const page=await context.newPage();
  await page.goto(anna+`/?entry=${encodeURIComponent(cases.spell.id)}`);await annaReady(page);await entryReady(page);
  await expect(go(page)).toHaveAccessibleName('5e Stella');
  await go(page).click();await stellaReady(page);
  await expect(go(page)).toHaveAccessibleName('5e Anna');await expect(go(page)).toHaveAttribute('href','/');
});

test('another build keeps the query conditions and drops places named by the old one',async({page})=>{
  // A static asset of the same origin writes the record without either page saving over it.
  await page.goto(anna+'/anna-logo.png');
  await page.evaluate(snapshot=>localStorage.setItem('anna.workspace',JSON.stringify({version:1,snapshot:'old',
    topic:{entry:'gone',name:'旧条目',from:'query',at:5},
    query:[{query:'q=%E6%8A%A4%E7%9B%BE%E6%9C%AF',entry:'gone',entryName:'旧条目',title:'护盾术',opened:true,ui:{loadedRows:900},at:5}],
    book:[{version:2,snapshot:'old',root:'',page:'topics/玩家手册2024/法术详述/1环.htm',node:'',position:{x:0,y:300},label:'一环',at:5}]})),meta.snapshot);
  await page.goto(anna+'/read?'+new URLSearchParams({page:meta.default_page}));await stellaReady(page);
  await expect(go(page)).toHaveAccessibleName('5e Anna：回到 护盾术');
  await expect(go(page)).toHaveAttribute('href','/?q=%E6%8A%A4%E7%9B%BE%E6%9C%AF');
  const state=await workspace(page);
  expect([state.snapshot,state.topic,state.book.length]).toEqual([meta.snapshot,null,1]);
  expect(state.book[0].page).toBe(meta.default_page);
});

test('narrow topbars keep both avatars on one line',async({page})=>{
  await page.setViewportSize({width:390,height:844});
  await page.goto(anna+`/?entry=${encodeURIComponent(cases.spell.id)}`);await annaReady(page);
  for(const target of [anna+'/read',anna+'/']){
    await expect(page.locator('.companion-label')).toBeHidden();
    await expect(page.locator('.companion-avatar')).toBeInViewport();
    const bar=await page.locator('.topbar').boundingBox(),theme=await page.locator('#theme').boundingBox();
    expect(bar.height).toBe(52);expect(theme.x+theme.width).toBeLessThanOrEqual(390);
    await page.goto(target);await expect(page.locator('.companion')).toBeVisible();
  }
});

test('reading on in Stella makes the definition in view the topic Anna looks up',async({page,request})=>{
  const path=cases.spell.path;
  const data=await (await request.get(anna+'/api/reader-page?'+new URLSearchParams({path}))).json();
  const target=data.definitions.filter(d=>d.locatable)[3];
  await page.goto(anna+'/read?'+new URLSearchParams({page:path}));await stellaReady(page);
  const frame=page.frames().find(f=>decodeURI(f.url()).includes(path));
  // definitionPosition leaves 20px above the title; scroll the title itself to the top of the view.
  const y=await frame.evaluate(id=>window.annaSourceLinks.definitionPosition(id).y+20,target.id);
  await scrollBook(page,y);
  await expect.poll(async()=>(await workspace(page)).topic?.entry).toBe(target.id);
  expect((await workspace(page)).topic).toMatchObject({name:target.name,from:'book'});
  await expect(go(page)).toHaveAccessibleName(`5e Anna：查 ${target.name}`);
  expect(new URL(await go(page).getAttribute('href'),anna).searchParams.get('entry')).toBe(target.id);
  await go(page).click();await entryReady(page);
  expect(new URL(page.url()).searchParams.get('entry')).toBe(target.id);
});
