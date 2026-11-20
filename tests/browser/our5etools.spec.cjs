/* End-to-end browser verification for the live our5etools collection in Anna, Tour, and Stella. */
const {test:base,expect}=require('@playwright/test');
const fs=require('node:fs');
const {bookAddress}=require('./locations.cjs');

const port=Number(process.env.ANNA_E2E_PORT||18380);
const anna=`http://127.0.0.1:${port}`;
const o5eBook=`http://127.0.0.1:${port}/our5etools-book`;

const test=base.extend({
  context:async({context},use)=>{
    const errors=[];
    context.on('page',page=>page.on('pageerror',err=>errors.push(err.message)));
    // Strictly block any non-local network requests: offline reading must never depend on remote hosts.
    await context.route('**/*',route=>{
      const host=new URL(route.request().url()).hostname;
      return ['127.0.0.1','localhost'].includes(host)?route.continue():route.abort();
    });
    await use(context);
    expect(errors,'our5etools JavaScript pageerrors').toEqual([]);
  },
});

const summary=page=>page.locator('.library-summary');
const both=['5echm','our5etools'];
const ourAddress=local=>'our5etools:'+local;
const readerURL=params=>'/read?'+new URLSearchParams(params);
const tourURL=at=>'/tour?'+new URLSearchParams({at});
async function chooseLibraries(page,enabled){
  await page.goto(anna+'/anna-logo.png');
  await page.evaluate(enabled=>localStorage.setItem('anna.library',JSON.stringify({order:enabled,enabled})),enabled);
}
test.beforeEach(async({page})=>chooseLibraries(page,['our5etools']));
const resultsReady=page=>expect(page.locator('#results')).toHaveAttribute('aria-busy','false');
const tourLevel=(page,depth)=>page.locator(`.tour-column[data-depth="${depth}"]`);
const tourNamed=name=>new RegExp('^'+name.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')+'(\\s|$)');
const tourRow=(page,depth,name)=>tourLevel(page,depth).locator('.tour-item')
  .filter({has:page.locator('.tour-item-title',{hasText:tourNamed(name)})}).first();

async function tourReady(page,depth){
  await expect(tourLevel(page,depth)).toHaveAttribute('data-state','ready');
  await expect(page.locator('.tour-column')).toHaveCount(depth+1);
}

async function readerReady(page){
  await expect(page.locator('#book-main')).toHaveAttribute('aria-busy','false');
  await expect(page.locator('#book-error')).toBeHidden();
  await expect(page.locator('#content')).toBeVisible();
  await expect(page.locator('#content')).toHaveCSS('visibility','visible');
}

function o5eFrame(page){
  return page.frames().find(f=>f.url().startsWith(o5eBook+'/'));
}

test('same-name versions share a row, and Stella opens the selected version in its own text',async({page,context})=>{
  await chooseLibraries(page,both);
  await page.goto(anna+'/?type=spell&q='+encodeURIComponent('火球术'));
  await resultsReady(page);
  const row=page.locator('#results tr').filter({has:page.locator('.entry-name',{hasText:/^火球术$/})}).first();
  await expect(row.locator('.version-pill.shown')).toHaveText('PHB24 2024');
  await expect(row.locator('.version-pill').filter({hasText:/^our5etools · /})).toHaveCount(2);
  // Same-name homebrew stacks for display, but the two version IDs stay distinct.
  const brew=await (await page.request.get(anna+'/api/entries?libraries=5echm,our5etools&q='+encodeURIComponent('下毒术')+'&type=spell')).json();
  const [envenom,...other]=brew.rows.filter(r=>r.name_zh==='下毒术');
  expect(other).toEqual([]);
  expect(new Set(envenom.versions.map(v=>v.id)).size).toBe(2);
  // The selected version stays in its own collection through Anna → Stella → Anna.
  const version=row.locator('.version-pill').filter({hasText:/^our5etools · /}).first();
  const id=await version.getAttribute('data-entry');
  await version.click();
  await expect(page.locator('#entry-title')).toHaveText('火球术');
  await expect(page.locator('#detail .o5e-provenance')).toContainText('our5etools');
  const bookLink=page.locator('#detail .book-definition');
  await expect(bookLink).toHaveAttribute('href',readerURL({entry:ourAddress(id)}));
  const [stella]=await Promise.all([context.waitForEvent('page'),bookLink.click()]);
  await readerReady(stella);
  expect(new URL(stella.url()).searchParams.get('entry')).toBe(ourAddress(id));
  expect(o5eFrame(stella)).toBeTruthy();
  await stella.locator('#tab-outline').click();
  const entryLink=stella.locator(`#book-outline a[href="/?entry=${id}"]`);
  const [entry]=await Promise.all([context.waitForEvent('page'),entryLink.click()]);
  await expect(entry.locator('#entry-title')).toHaveText('火球术');
  await expect(entry.locator('#detail .o5e-provenance')).toContainText('our5etools');
  expect(new URL(entry.url()).searchParams.get('entry')).toBe(id);
});

test('Chinese and English search works in one library, with and without stacking',async({page})=>{
  // Chinese exact search: 火球术 stacks PHB (5e) + XPHB (5r) into 1 official row
  await page.goto(anna+'/?type=spell&q='+encodeURIComponent('火球术'));
  await resultsReady(page);
  const stackedCount=await page.locator('#results tr').count();
  expect(stackedCount).toBeGreaterThanOrEqual(1);

  // One row per version is a library choice, not part of the link.
  await page.evaluate(()=>{
    const settings=JSON.parse(localStorage.getItem('anna.library'));
    localStorage.setItem('anna.library',JSON.stringify({...settings,versions:'each'}));
  });
  await page.reload();
  await resultsReady(page);
  const eachCount=await page.locator('#results tr').count();
  expect(eachCount).toBeGreaterThan(stackedCount);
  await page.evaluate(()=>{
    const settings=JSON.parse(localStorage.getItem('anna.library'));
    localStorage.setItem('anna.library',JSON.stringify({...settings,versions:'stacked'}));
  });

  // English search: Fireball
  await page.goto(anna+'/?type=spell&q=Fireball');
  await resultsReady(page);
  await expect(page.locator('#results .entry-name').filter({hasText:'火球术'}).first()).toBeVisible();

  // Even when only our5etools is selected, the two community versions share a row.
  await page.goto(anna+'/?type=spell&q='+encodeURIComponent('下毒术'));
  await resultsReady(page);
  const homonymRows=page.locator('#results .entry-name').filter({hasText:/^下毒术$/});
  await expect(homonymRows).toHaveCount(1);
  await expect(homonymRows.locator('xpath=ancestor::tr').locator('.version-pill')).toHaveCount(2);

  // Chinese-only entry without English name (DND5e2014新人向科普) is searchable & openable
  await page.goto(anna+'/?q='+encodeURIComponent('DND5e2014新人向科普'));
  await resultsReady(page);
  const comicRow=page.locator('#results .entry-name').filter({hasText:'DND5e2014新人向科普'}).first();
  await expect(comicRow).toBeVisible();
  await comicRow.click();
  await expect(page.locator('#entry-title')).toHaveText('DND5e2014新人向科普');
  await expect(page.locator('#detail .o5e-provenance')).toBeVisible();
});

test('monster copy parent resolves to its entry and audio plays from the local route',async({page,request})=>{
  // Creature with _copy parent (阿弗纳斯的大公扎瑞尔 -> 扎瑞尔)
  await page.goto(anna+'/?type=creature&q='+encodeURIComponent('阿弗纳斯的大公扎瑞尔'));
  await resultsReady(page);
  await page.locator('#results .entry-name').filter({hasText:'阿弗纳斯的大公扎瑞尔'}).first().click();
  await expect(page.locator('#entry-title')).toHaveText('阿弗纳斯的大公扎瑞尔');
  const copyParentBtn=page.locator('#detail .o5e-provenance button.ref[data-entry^="o5e_"]').first();
  await expect(copyParentBtn).toBeVisible();
  const parentName=(await copyParentBtn.textContent()).trim();
  await copyParentBtn.click();
  await expect(page.locator('#entry-title')).toHaveText(parentName);
  expect(new URL(page.url()).searchParams.has('collection')).toBe(false);

  // Creature with transcoded local .opus audio (吸血鬼 -> /our5etools-media/audio/bestiary/vampire.opus)
  await page.goto(anna+'/?type=creature&q='+encodeURIComponent('吸血鬼')+'&source=src%3AXMM');
  await resultsReady(page);
  await page.locator('#results .entry-name').filter({hasText:/^吸血鬼$/}).first().click();
  await expect(page.locator('#entry-title')).toHaveText('吸血鬼');
  const audio=page.locator('#detail audio[src*="/our5etools-media/audio/bestiary/vampire.opus"]').first();
  await expect(audio).toBeVisible();
  const audioSrc=await audio.getAttribute('src');
  const resp=await request.get(anna+audioSrc);
  expect(resp.status()).toBe(200);
  expect(resp.headers()['content-type']).toContain('audio/ogg');
  const rangeResp=await request.get(anna+audioSrc,{headers:{Range:'bytes=0-63'}});
  expect(rangeResp.status()).toBe(206);
});

test('class progression, filter links and references retain their targets',async({page})=>{
  await page.goto(anna+'/?type=class&q='+encodeURIComponent('法师')+'&source=src%3AXPHB');
  await resultsReady(page);
  await page.locator('#results .entry-name').filter({hasText:/^法师$/}).first().click();
  await expect(page.locator('#entry-title')).toHaveText('法师');

  // 1-20 progression table (header + 20 levels)
  const progTable=page.locator('#detail table.o5e-progression').first();
  await expect(progTable).toBeVisible();
  const rowCount=await progTable.locator('tbody tr').count();
  expect(rowCount).toBeGreaterThanOrEqual(20);

  // button.ref stays inside our5etools (o5e_* ID)
  const refBtn=page.locator('#detail-content button.ref[data-entry^="o5e_"]').first();
  await expect(refBtn).toBeVisible();
  const targetId=await refBtn.getAttribute('data-entry');
  await refBtn.click();
  await expect(page).toHaveURL(new RegExp('entry='+targetId));

  // The rendered filter link keeps its query; it does not set the reader’s collection.
  await page.goBack();
  await expect(page.locator('#entry-title')).toHaveText('法师');
  const filterLink=page.locator('#detail-content a.ref-filter').first();
  await expect(filterLink).toBeVisible();
  const href=await filterLink.getAttribute('href');
  await page.goto(anna+href);
  await resultsReady(page);
  await expect(summary(page)).toHaveText('our5etools');
  expect(new URL(page.url()).searchParams.has('collection')).toBe(false);
  const requested=new URL(href,anna).searchParams;
  for(const [key,value] of requested)if(key!=='collection')expect(new URL(page.url()).searchParams.getAll(key)).toContain(value);
  expect(await page.locator('#results tr').count()).toBeGreaterThan(0);
});

test('the provenance card distinguishes an unbundled image from local media',async({page})=>{
  await page.goto(anna+'/?type=magic_item&q='+encodeURIComponent('远古法师手册'));
  await resultsReady(page);
  await page.locator('#results .entry-name').filter({hasText:'远古法师手册'}).first().click();
  await expect(page.locator('#entry-title')).toHaveText('远古法师手册');

  const prov=page.locator('#detail .o5e-provenance');
  await expect(prov).toBeVisible();
  await expect(prov).toContainText('our5etools');
  await expect(prov).toContainText('社区');
  await expect(prov).toContainText('Adult Swim');
  await expect(prov).toContainText('C');

  // Remote image notice is visible & explicitly marked as remote/unbundled (with compact CSS and lazy-media hook)
  const remoteFig=page.locator('#detail .o5e-media-remote').first();
  await expect(remoteFig).toBeVisible();
  await expect(remoteFig).toContainText('远端配图（未离线收录）');
  await expect(page.locator('#detail [data-lazy-media]').first()).toBeVisible();
});

test('lazy media displays a served image and falls back to the note on 404',async({page})=>{
  const okPng=Buffer.from(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=',
    'base64',
  );
  await page.route('**/our5etools-media/img/test-ok/Ok.png',route=>
    route.fulfill({status:200,contentType:'image/png',body:okPng}),
  );
  await page.route('**/our5etools-media/img/test-fail/Fail.webp',route=>
    route.fulfill({status:404,contentType:'text/plain',body:'missing'}),
  );
  await page.route('**/api/entry-layout*',async route=>{
    const resp=await route.fetch();
    const json=await resp.json();
    if(typeof json.html==='string' && json.html.includes('o5e-media-remote')){
      json.html=
        '<figure class="o5e-figure" data-testid="lazy-ok">' +
          '<img src="/our5etools-media/img/test-ok/Ok.png" alt="测试图" data-o5e-fallback="1">' +
          '<span class="o5e-media-fallback-note" hidden>远端配图（未离线收录）</span>' +
        '</figure>' +
        '<figure class="o5e-figure" data-testid="lazy-fail">' +
          '<img src="/our5etools-media/img/test-fail/Fail.webp" alt="缺失图" data-o5e-fallback="1">' +
          '<span class="o5e-media-fallback-note" hidden>远端配图（未离线收录）</span>' +
        '</figure>' +
        json.html;
    }
    await route.fulfill({status:resp.status(),headers:resp.headers(),body:JSON.stringify(json)});
  });

  await page.goto(anna+'/?type=magic_item&q='+encodeURIComponent('远古法师手册'));
  await resultsReady(page);
  await page.locator('#results .entry-name').filter({hasText:'远古法师手册'}).first().click();

  const okFig=page.locator('#detail [data-testid="lazy-ok"]');
  await expect(okFig.locator('img')).toBeVisible();
  await expect.poll(()=>okFig.locator('img').evaluate(img=>img.complete && img.naturalWidth===1)).toBe(true);
  await expect(okFig.locator('.o5e-media-fallback-note')).toBeHidden();

  const failFig=page.locator('#detail [data-testid="lazy-fail"]');
  await expect(failFig).toHaveClass(/o5e-img-failed/);
  await expect(failFig.locator('img')).toBeHidden();
  await expect(failFig.locator('.o5e-media-fallback-note')).toBeVisible();
  await expect(failFig.locator('.o5e-media-fallback-note')).toContainText('远端配图（未离线收录）');
});

test('the unified tour lists extra books and packages, and its chapter follows the chosen text',async({page,request})=>{
  await chooseLibraries(page,both);
  const collections=await (await request.get(anna+'/api/library?works=1')).json();
  const ourPHB=collections.collections.find(c=>c.id==='our5etools').works.find(w=>w.reader_root==='n_book_xphb');
  const spells=await (await request.get(anna+'/api/entries?libraries=5echm,our5etools&type=spell&limit=1')).json();

  await page.goto(anna+tourURL('create'));await tourReady(page,1);
  const read=tourLevel(page,1).locator('.tour-go[href^="/read?"]');
  await expect(read).toHaveAttribute('href',/^\/read\?root=5echm%3A/);
  await page.locator('.library-button').click();
  const editions=page.locator('.library-editions');
  await editions.locator('summary').click();
  await editions.locator(`input[data-edition][value="${ourAddress(ourPHB.id)}"]`).check();
  await page.keyboard.press('Escape');
  await expect(read).toHaveAttribute('href',/^\/read\?root=our5etools%3A/);
  await expect(page).toHaveURL(anna+tourURL('create'));

  await page.goto(anna+'/tour');await tourReady(page,0);
  const root=tourLevel(page,0);
  await expect(root.locator('h3').first()).toHaveText('开始游戏');
  await expect(tourRow(page,0,'官方规则书').locator('.tour-item-hint')).toHaveText('our5etools');
  await expect(root.locator('[data-segment="spells"] .tour-item-count')).toHaveText(spells.versions_total.toLocaleString('zh-CN'));

  await page.setViewportSize({width:375,height:812});
  await tourRow(page,0,'站点自有包（精选）').click();await tourReady(page,1);
  const first=tourLevel(page,1).locator('.tour-item[data-segment^="our5etools:pack."]').first();
  const segment=await first.getAttribute('data-segment');
  await first.click();await tourReady(page,2);
  expect(new URL(page.url()).searchParams.get('at')).toBe('packs-ours/'+segment);
  const packQuery=tourLevel(page,2).locator('.tour-go[href*="package="]');
  await expect(packQuery).toBeVisible();
  const packageURL=new URL(await packQuery.getAttribute('href'),anna);
  expect(packageURL.searchParams.get('package')).toBeTruthy();
  expect(packageURL.searchParams.has('collection')).toBe(false);
  await tourLevel(page,2).locator('.tour-back').click();await tourReady(page,1);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(375);
  await page.setViewportSize({width:1280,height:800});
});

test('subclasses in the unified tour open directly, switch versions, and show our5etools features',async({page})=>{
  await chooseLibraries(page,both);
  await page.setViewportSize({width:1440,height:900});
  await page.goto(anna+tourURL('subclasses'));await tourReady(page,1);
  const classFilter=tourLevel(page,1).locator('.tour-filter-input');
  await expect(classFilter).toBeVisible();
  await tourRow(page,1,'法师').click();await tourReady(page,2);
  expect(new URL(page.url()).pathname).toBe('/tour');
  expect(new URL(page.url()).searchParams.get('at')).toBe('subclasses/subclasses.wizard');
  await expect(tourLevel(page,2).locator('.tour-go')).toHaveAttribute('href',/^\S*type=subclass.*f_owner_class=/);
  // Filter across origin groups by English name, then open the our5etools version pill directly in the tour.
  const subFilter=tourLevel(page,2).locator('.tour-filter-input');
  await subFilter.fill('Evoker');
  const evokerLi=tourLevel(page,2).locator('li:not([hidden])').filter({has:page.locator('.tour-item-title',{hasText:/^塑能师$/})}).first();
  const o5eVersion=evokerLi.locator('.tour-versions a[data-segment^="our5etools:"]').first();
  await expect(o5eVersion).toBeVisible();
  await o5eVersion.click();await tourReady(page,3);
  await expect(o5eVersion).toHaveAttribute('aria-current','true');
  await expect(tourLevel(page,3).locator('.tour-lead')).toContainText('元素力量');
  await expect(tourLevel(page,3).locator('[data-group=class] .tour-item')).toHaveCount(1);
  // Data-only our5etools subclasses without a book page do not advertise a broken Stella link.
  await expect(page.locator('.companion-go')).not.toHaveAttribute('href',/entry=our5etools/);
  await tourRow(page,3,'6级：法术塑形').click();await tourReady(page,4);
  await expect(tourLevel(page,4)).toHaveAttribute('data-shape','detail');
  await expect(tourLevel(page,4).locator('.tour-body')).toContainText('塑能学派法术');
  await expect(page.locator('.companion-go')).not.toHaveAttribute('href',/entry=our5etools/);
});

test('Stella opens our5etools chapters on the shared shelf and restores paging after reload',async({page})=>{
  await chooseLibraries(page,both);
  const initial={root:ourAddress('n_book_phb'),page:ourAddress('books/phb/0.html')};
  await page.goto(anna+readerURL(initial));await readerReady(page);
  expect(o5eFrame(page)).toBeTruthy();
  await expect(o5eFrame(page).locator('body')).toContainText('玩家手册');

  const nextBtn=page.locator('#book-next');
  await expect(nextBtn).toHaveAttribute('aria-disabled','false');await nextBtn.click();await readerReady(page);
  const at=()=>Object.fromEntries(new URL(page.url()).searchParams);
  expect(at()).toMatchObject({root:initial.root,page:ourAddress('books/phb/1.html')});
  const nextFrame=page.frames().find(f=>f.url().endsWith('/our5etools-book/books/phb/1.html'));
  expect(nextFrame).toBeTruthy();
  await expect(nextFrame.locator('table').first()).toBeVisible();

  await page.reload();await readerReady(page);
  expect(at()).toMatchObject({root:initial.root,page:ourAddress('books/phb/1.html')});

  await page.setViewportSize({width:375,height:812});
  await page.goto(anna+readerURL(initial));await readerReady(page);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(375);
  await page.setViewportSize({width:1280,height:800});
});

test('Stella shows one text for a shared work and switches to the matching chapter',async({page,request})=>{
  await chooseLibraries(page,both);
  const shelf=await (await request.get(anna+'/api/reader?libraries=5echm,our5etools')).json();
  const nativeRoot=shelf.nodes.find(n=>n.label==='玩家手册2024'&&!n.parent_id);
  const otherRoot=shelf.nodes.find(n=>n.id===ourAddress('n_book_xphb'));
  const chapter=root=>shelf.nodes.find(n=>n.parent_id===root.id&&n.label.endsWith('章：进行游戏'));
  const native=chapter(nativeRoot), other=chapter(otherRoot);
  const url=()=>Object.fromEntries(new URL(page.url()).searchParams);
  const treeNode=root=>page.locator(`#book-tree [data-node="${root.id}"]`);
  const texts=page.getByRole('group',{name:'这部作品在资料库中有 2 份文本'});
  const nativeText=texts.getByRole('button',{name:'不全书',exact:true});
  const otherText=texts.getByRole('button',{name:'our5etools',exact:true});

  await page.goto(anna+readerURL({root:native.id}));await readerReady(page);
  await expect(treeNode(nativeRoot)).toBeVisible();
  await expect(treeNode(otherRoot)).toHaveCount(0);
  await expect(nativeText).toHaveAttribute('aria-pressed','true');
  await expect(otherText).toHaveAttribute('aria-pressed','false');

  await otherText.press('Enter');
  await expect(otherText).toHaveAttribute('aria-pressed','true');await readerReady(page);
  expect(url()).toMatchObject({root:other.id,node:other.id,page:other.path});
  await expect(treeNode(nativeRoot)).toHaveCount(0);
  await expect(treeNode(otherRoot).locator('.toc-collection')).toHaveText('our5etools');
  await expect(page.frameLocator('#content').locator('body')).toContainText('进行游戏');

  await page.goBack();await readerReady(page);
  expect(url().root).toBe(native.id);
  await expect(nativeText).toHaveAttribute('aria-pressed','true');
  await page.goForward();await readerReady(page);
  await page.reload();await readerReady(page);
  expect(url()).toMatchObject({root:other.id,node:other.id,page:other.path});
  await expect(otherText).toHaveAttribute('aria-pressed','true');

  // Reordering the shelf uses the new scope without reloading the text being read.
  const frame=o5eFrame(page);
  await frame.evaluate(()=>window.testReadingMarker='kept');
  await page.locator('.library-button').click();
  const reordered=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/reader' &&
    new URL(r.url()).searchParams.get('libraries')==='our5etools,5echm' && r.ok());
  await page.getByRole('button',{name:'上移 our5etools'}).click();
  await page.keyboard.press('Escape');
  await reordered;
  await expect(treeNode(otherRoot).locator('.toc-collection')).toHaveCount(0);
  expect(await o5eFrame(page).evaluate(()=>window.testReadingMarker)).toBe('kept');
  expect(url()).toMatchObject({root:other.id,node:other.id,page:other.path});
});

test('full-text results from both libraries identify their source and open the exact chapter',async({page})=>{
  await chooseLibraries(page,both);
  await page.goto(anna+'/read');await readerReady(page);
  await page.locator('#tab-search').click();
  await page.locator('#book-search').fill('进行游戏');
  await page.locator('#book-title-only').check();
  await page.locator('#book-search-form button').click();
  const native=page.locator('#book-search-results li').filter({has:page.getByRole('link',{name:'第一章：进行游戏',exact:true})});
  const other=page.locator('#book-search-results li').filter({has:page.getByRole('link',{name:'玩家手册（2024） — 第 1 章：进行游戏',exact:true})});
  await expect(native.locator('small')).toContainText('不全书');
  await expect(other.locator('small')).toContainText('our5etools');
  expect(new URL(await native.getByRole('link').getAttribute('href'),anna).searchParams.get('page')).toBe(bookAddress('topics/玩家手册2024/第一章：进行游戏.htm'));
  await other.getByRole('link').click();await readerReady(page);
  expect(new URL(page.url()).searchParams.get('page')).toBe(ourAddress('books/xphb/1.html'));
  expect(o5eFrame(page)).toBeTruthy();
});

test('an address outside the selected shelf explains how to enable its collection',async({page})=>{
  await chooseLibraries(page,['5echm']);
  const url=anna+readerURL({page:ourAddress('books/phb/0.html')});
  await page.goto(url);
  await expect(page.locator('#book-error')).toContainText('不在你的资料库设置中');
  await expect(page.locator('#content')).toHaveCount(0);
  await page.getByRole('button',{name:'资料库设置…'}).press('Enter');
  const dialog=page.getByRole('dialog',{name:'资料库'});
  await expect(dialog).toBeVisible();
  const included=dialog.locator('input[data-library="our5etools"]');
  await expect(included).not.toBeChecked();await included.check();
  await page.keyboard.press('Escape');
  await page.reload();await readerReady(page);
  expect(o5eFrame(page)).toBeTruthy();
});

test('a reading root outside the selected shelf offers library settings',async({page})=>{
  const root=ourAddress('n_book_phb'),chapter=ourAddress('books/phb/1.html');
  for(const [enabled,target] of [
    [['5echm'],{root}],
    [['demo'],{root}], // The only enabled collection has no reader.
    [['5echm'],{root,page:chapter}],
  ]){
    await chooseLibraries(page,enabled);
    await page.goto(anna+readerURL(target));
    await expect(page.locator('#book-error')).toContainText('不在你的资料库设置中');
    await expect(page.getByRole('button',{name:'资料库设置…'})).toBeVisible();
    await expect(page.locator('#content')).toHaveCount(0);
  }

  await page.getByRole('button',{name:'资料库设置…'}).press('Enter');
  const dialog=page.getByRole('dialog',{name:'资料库'});
  await expect(dialog).toBeVisible();
  const included=dialog.locator('input[data-library="our5etools"]');
  await expect(included).not.toBeChecked();await included.check();
  await page.keyboard.press('Escape');
  await expect(dialog).toBeHidden();
  await page.reload();await readerReady(page);
  expect(Object.fromEntries(new URL(page.url()).searchParams)).toMatchObject({root,page:chapter});
  expect(o5eFrame(page).url()).toBe(o5eBook+'/books/phb/1.html');
});

test('export uses the selected library and navigation links stay unscoped',async({page})=>{
  await page.goto(anna+'/?type=spell&q='+encodeURIComponent('火球术'));
  await resultsReady(page);
  const [download]=await Promise.all([
    page.waitForEvent('download'),
    page.locator('#export').click(),
  ]);
  expect(download.suggestedFilename()).toBe('5e-anna-our5etools.csv');
  const dlPath=await download.path();
  const raw=fs.readFileSync(dlPath);
  expect(raw.slice(0,3).equals(Buffer.from([0xef,0xbb,0xbf]))).toBe(true);
  const csvText=raw.toString('utf8');
  expect(csvText).toContain('火球术');
  expect(csvText).toContain('our5etools');

  // Navigation links stay clean; changing the library, not the link, brings the default source back.
  await chooseLibraries(page,['5echm']);
  await page.goto(anna);
  await resultsReady(page);
  await expect(summary(page)).toHaveText('不全书');
  expect(new URL(page.url()).searchParams.get('collection')).toBeNull();
  const tourSwitch=page.locator('.page-switch a').filter({hasText:'导览'});
  await expect(tourSwitch).toHaveAttribute('href',/^\/tour($|\?at=)/);

  await page.goto(anna+'/read');
  await readerReady(page);
  expect(new URL(page.url()).searchParams.get('collection')).toBeNull();
});
