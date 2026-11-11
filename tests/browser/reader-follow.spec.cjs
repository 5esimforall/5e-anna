/* Directory fixture over real documents: reading roots and the current visible node stay distinct. */
const {test:base,expect}=require('@playwright/test');
const anna=`http://127.0.0.1:${process.env.ANNA_E2E_PORT||18380}`;
const paths=['topics/玩家手册2024/角色职业/野蛮人/野蛮人.htm','topics/玩家手册2024/角色职业/野蛮人/狂战士道途.htm','topics/玩家手册2024/角色职业/野蛮人/兽心道途.htm'];
const node=(id,parent_id='',path='',label=id)=>({id,parent_id,path,label,fragment:'',status:path?'present':'placeholder'});
const rows=[...Array.from({length:30},(_,i)=>node('before-'+i)),node('root','',paths[0]),node('near','root'),node('child','near',paths[1]),
  ...Array.from({length:24},(_,i)=>node('middle-'+i,'root')),node('far','root'),node('last','far',paths[2]),
  ...Array.from({length:30},(_,i)=>node('after-'+i)),node('alias','',paths[2])];
const test=base.extend({page:async({page},use)=>{
  const errors=[];page.on('pageerror',error=>errors.push(error.message));
  await page.route('**/api/reader',async route=>{const response=await route.fetch();await route.fulfill({response,json:{...await response.json(),nodes:rows,default_page:paths[0]}});});
  await use(page);expect(errors).toEqual([]);
}});
const row=(page,id)=>page.locator(`#book-tree [data-node="${id}"] > .toc-row`);
async function ready(page){await expect(page.locator('#book-main')).toHaveAttribute('aria-busy','false');await expect(page.locator('#book-error')).toBeHidden();}
async function visit(page){await page.goto(anna+'/read?root=root');await ready(page);await expect(page.locator('#book-follow-status')).toHaveText('目录跟随中');await inView(page,'root');}
async function inView(page,id){await expect.poll(async()=>{
  const item=await row(page,id).boundingBox(),scroll=await page.locator('#book-tree-scroll').boundingBox();
  return !!item&&item.y>=scroll.y-1&&item.y+item.height<=scroll.y+scroll.height+1;
}).toBe(true);}
async function offset(page,index,y=60){await page.locator(`.book-page[data-path="${paths[index]}"] iframe`).evaluate((frame,y)=>{
  const scroll=document.querySelector('#book-scroll');scroll.scrollTop+=frame.getBoundingClientRect().top-scroll.getBoundingClientRect().top+y;
},y);await expect(page.locator('#book-page-select')).toHaveValue(String(index));}
async function treeState(page){return page.evaluate(()=>({scroll:document.querySelector('#book-tree-scroll').scrollTop,expanded:[...document.querySelectorAll('#book-tree [aria-expanded=true]')].map(e=>e.dataset.node)}));}
async function preload(page){
  await page.locator('#book-page-select').selectOption('2');await ready(page);
  await page.locator('#book-page-select').selectOption('0');await ready(page);await inView(page,'root');
}

test('scrolling follows the scope member, expands ancestors, and scrolls only enough to reveal its row',async({page})=>{
  await visit(page);await preload(page);
  const history=await page.evaluate(()=>window.history.length);
  await offset(page,2,60);await inView(page,'last');
  await expect(row(page,'last').locator('a')).toHaveAttribute('aria-current','page');
  await expect(row(page,'alias').locator('a')).not.toHaveAttribute('aria-current','page');
  expect(new URL(page.url()).searchParams.get('root')).toBe('root');
  expect(await page.evaluate(()=>history.length)).toBe(history);
  const item=await row(page,'last').boundingBox(),scroll=await page.locator('#book-tree-scroll').boundingBox();
  expect(Math.abs(item.y+item.height-scroll.y-scroll.height)).toBeLessThan(2);
  const before=await treeState(page);
  await offset(page,2,90);await page.waitForTimeout(300);expect(await treeState(page)).toEqual(before);
  expect(await page.evaluate(()=>history.state.book.position.y)).toBeCloseTo(90,0);
});

test('automatic subtree expansion does not steal focus or change a reading range',async({page})=>{
  await visit(page);
  await page.getByRole('button',{name:'加载下一页：child',exact:true}).press('Enter');
  await expect(page.locator(`.book-page[data-path="${paths[1]}"] iframe`)).toHaveCSS('visibility','visible');
  await page.locator('#theme').focus();await offset(page,1,30);await inView(page,'child');
  await expect(page.locator('#theme')).toBeFocused();
  await expect(page.locator('#book-tree [data-node=near]')).toHaveAttribute('aria-expanded','true');
  await expect(page.locator('#book-scope-title')).toHaveText('root');
});

test('manual wheel scrolling pauses following; locate restores it without moving the source',async({page})=>{
  await visit(page);await preload(page);
  const box=await page.locator('#book-tree-scroll').boundingBox();await page.mouse.move(box.x+box.width/2,box.y+100);await page.mouse.wheel(0,-250);
  await expect(page.locator('#book-follow-status')).toHaveText('目录跟随已暂停');await page.waitForTimeout(250);
  const before=await treeState(page);await offset(page,2,60);await page.waitForTimeout(300);
  expect(await treeState(page)).toEqual(before);
  await expect(row(page,'last').locator('a')).toHaveAttribute('aria-current','page');
  await page.locator('#book-locate').click();await inView(page,'last');
  await expect(page.locator('#book-follow-status')).toHaveText('目录跟随中');
  expect(await page.evaluate(()=>history.state.book.position.y)).toBeCloseTo(60,0);
  await offset(page,0,60);await inView(page,'root');
});

test('filtering and collapse preserve manual browsing until locate explicitly resumes following',async({page})=>{
  await visit(page);await preload(page);await page.locator('#book-filter').fill('before');
  await offset(page,2,60);await page.waitForTimeout(300);
  await expect(page.locator('#book-filter')).toHaveValue('before');await expect(row(page,'last')).toHaveCount(0);
  await expect(page.locator('#book-follow-status')).toHaveText('目录跟随已暂停');
  await page.locator('#book-filter-clear').click();await expect(page.locator('#book-follow-status')).toHaveText('目录跟随已暂停');
  await page.locator('#book-collapse').click();const before=await treeState(page);
  await offset(page,0,60);await offset(page,2,60);await page.waitForTimeout(300);expect(await treeState(page)).toEqual(before);
  await page.locator('#book-locate').click();await inView(page,'last');await expect(page.locator('#book-filter')).toHaveValue('');
});

test('keyboard browsing retains its focused DOM node while current-page highlighting changes',async({page})=>{
  await visit(page);await preload(page);
  await page.locator('#book-tree [data-node=root]').focus();await page.keyboard.press('ArrowUp');
  await expect(page.locator('#book-follow-status')).toHaveText('目录跟随已暂停');
  const focused=await page.evaluate(()=>{document.activeElement.identity='preserved';return document.activeElement.dataset.node;});
  await offset(page,2,60);await page.waitForTimeout(300);
  expect(await page.evaluate(()=>({id:document.activeElement.dataset.node,identity:document.activeElement.identity}))).toEqual({id:focused,identity:'preserved'});
  await expect(row(page,'last').locator('a')).toHaveAttribute('aria-current','page');
});

test('paused state, filtering and directory position survive reload and history navigation',async({page})=>{
  await visit(page);await page.locator('#book-filter').fill('before');
  await page.locator('#book-tree-scroll').evaluate(e=>e.scrollTop=120);await page.waitForTimeout(100);
  await page.reload();await ready(page);await expect(page.locator('#book-filter')).toHaveValue('before');
  await expect(page.locator('#book-follow-status')).toHaveText('目录跟随已暂停');
  expect((await treeState(page)).scroll).toBe(120);
  await page.locator('#book-page-select').selectOption('2');await ready(page);
  await page.locator('#book-locate').click();await inView(page,'last');
  await page.goBack();await ready(page);await expect(page.locator('#book-filter')).toHaveValue('before');
  await expect(page.locator('#book-follow-status')).toHaveText('目录跟随已暂停');
  expect((await treeState(page)).scroll).toBe(120);
});

for(const width of [390,1280])test(`closed navigation stays closed during scrolling at ${width}px and follows when reopened`,async({page})=>{
  await page.setViewportSize({width,height:844});await page.goto(anna+'/read?root=root');await ready(page);
  await page.locator('#book-page-select').selectOption('2');await ready(page);
  await page.locator('#book-page-select').selectOption('0');await ready(page);
  if(await page.locator('#book-sidebar').isVisible())await page.locator(width>800?'#book-menu':'#book-close').click();
  await offset(page,2,60);await page.waitForTimeout(300);await expect(page.locator('#book-sidebar')).toBeHidden();
  await page.locator('#book-menu').click();await inView(page,'last');
  await expect(page.locator('#tab-contents')).toBeFocused();
  if(width===390){await page.keyboard.press('Escape');await expect(page.locator('#book-menu')).toBeFocused();}
  await page.setViewportSize({width:1280,height:800});
});

test('other navigation panes are not replaced by automatic directory following',async({page})=>{
  await visit(page);await preload(page);await page.locator('#tab-search').click();await page.locator('#book-search').fill('draft');
  await offset(page,2,60);await page.waitForTimeout(300);
  await expect(page.locator('#tab-search')).toHaveAttribute('aria-selected','true');await expect(page.locator('#book-search')).toHaveValue('draft');
  await page.locator('#tab-contents').click();await inView(page,'last');
});

test('brief page-boundary crossings do not expand a transient chapter',async({page})=>{
  await visit(page);
  await page.getByRole('button',{name:'加载下一页：child',exact:true}).press('Enter');
  await expect(page.locator(`.book-page[data-path="${paths[1]}"] iframe`)).toHaveCSS('visibility','visible');
  await offset(page,0,60);await page.waitForTimeout(300);
  await page.evaluate(()=>{
    window.transientExpansion=false;
    new MutationObserver(()=>{if(document.querySelector('#book-tree [data-node=near][aria-expanded=true]'))window.transientExpansion=true;})
      .observe(document.querySelector('#book-tree'),{subtree:true,childList:true,attributes:true});
  });
  await page.locator(`.book-page[data-path="${paths[1]}"] iframe`).evaluate(frame=>{
    const scroll=document.querySelector('#book-scroll');scroll.scrollTop+=frame.getBoundingClientRect().top-scroll.getBoundingClientRect().top+30;
  });
  await expect.poll(()=>page.locator('#book-page-select').inputValue(),{intervals:[10]}).toBe('1');
  await offset(page,0,60);await page.waitForTimeout(350);
  expect(await page.evaluate(()=>window.transientExpansion)).toBe(false);
  await expect(row(page,'root').locator('a')).toHaveAttribute('aria-current','page');
});

test('manual expand, touch scrolling and scrollbar intent pause following, but automatic directory scroll does not',async({page})=>{
  await visit(page);expect(await page.evaluate(()=>history.state.book.followContents)).toBe(true);
  await row(page,'root').locator('button').click();await expect(page.locator('#book-follow-status')).toHaveText('目录跟随已暂停');
  await page.locator('#book-locate').click();await expect(page.locator('#book-follow-status')).toHaveText('目录跟随中');
  await page.locator('#book-tree-scroll').dispatchEvent('touchmove');await expect(page.locator('#book-follow-status')).toHaveText('目录跟随已暂停');
  await page.locator('#book-locate').click();
  await page.locator('#book-tree-scroll').dispatchEvent('pointerdown');await expect(page.locator('#book-follow-status')).toHaveText('目录跟随已暂停');
});
