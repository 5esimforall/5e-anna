/* End-to-end browser verification for the live our5etools collection in Anna, Tour, and Stella. */
const {test:base,expect}=require('@playwright/test');
const fs=require('fs');

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

const picker=page=>page.getByRole('combobox',{name:'资料库'});
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

test('1. switching 5echm <-> our5etools, browser back, and reload preserve collection state',async({page})=>{
  await page.goto(anna+'/?type=feat');
  await resultsReady(page);
  await expect(picker(page)).toHaveValue('5echm');

  await picker(page).selectOption('our5etools');
  await expect(page).toHaveURL(anna+'/?collection=our5etools');
  await resultsReady(page);
  await expect(picker(page)).toHaveValue('our5etools');
  await expect(page.locator('#results tr').first()).toBeVisible();

  // Reload stays in our5etools
  await page.reload();
  await resultsReady(page);
  await expect(picker(page)).toHaveValue('our5etools');
  await expect(page).toHaveURL(anna+'/?collection=our5etools');

  // Browser back returns cleanly to 5echm
  await page.goBack();
  await resultsReady(page);
  await expect(picker(page)).toHaveValue('5echm');
  await expect(page).toHaveURL(anna+'/?type=feat');
});

test('2. Chinese & English search, 5e/5r stacking vs each, community homonym isolation, and Chinese-only entry',async({page})=>{
  // Chinese exact search: 火球术 stacks PHB (5e) + XPHB (5r) into 1 official row
  await page.goto(anna+'/?collection=our5etools&type=spell&q='+encodeURIComponent('火球术'));
  await resultsReady(page);
  const stackedCount=await page.locator('#results tr').count();
  expect(stackedCount).toBeGreaterThanOrEqual(1);

  await page.goto(anna+'/?collection=our5etools&type=spell&q='+encodeURIComponent('火球术')+'&versions=each');
  await resultsReady(page);
  const eachCount=await page.locator('#results tr').count();
  expect(eachCount).toBeGreaterThan(stackedCount);

  // English search: Fireball
  await page.goto(anna+'/?collection=our5etools&type=spell&q=Fireball');
  await resultsReady(page);
  await expect(page.locator('#results .entry-name').filter({hasText:'火球术'}).first()).toBeVisible();

  // Community package homonyms (下毒术 in 2 distinct brew packages) stay unmerged even in stacked mode!
  await page.goto(anna+'/?collection=our5etools&type=spell&q='+encodeURIComponent('下毒术'));
  await resultsReady(page);
  const homonymRows=page.locator('#results .entry-name').filter({hasText:/^下毒术$/});
  await expect(homonymRows).toHaveCount(2);

  // Chinese-only entry without English name (DND5e2014新人向科普) is searchable & openable
  await page.goto(anna+'/?collection=our5etools&q='+encodeURIComponent('DND5e2014新人向科普'));
  await resultsReady(page);
  const comicRow=page.locator('#results .entry-name').filter({hasText:'DND5e2014新人向科普'}).first();
  await expect(comicRow).toBeVisible();
  await comicRow.click();
  await expect(page.locator('#entry-title')).toHaveText('DND5e2014新人向科普');
  await expect(page.locator('#detail .o5e-provenance')).toBeVisible();
});

test('3. monster _copy parent ref button and local .opus audio playback route',async({page,request})=>{
  // Creature with _copy parent (阿弗纳斯的大公扎瑞尔 -> 扎瑞尔)
  await page.goto(anna+'/?collection=our5etools&type=creature&q='+encodeURIComponent('阿弗纳斯的大公扎瑞尔'));
  await resultsReady(page);
  await page.locator('#results .entry-name').filter({hasText:'阿弗纳斯的大公扎瑞尔'}).first().click();
  await expect(page.locator('#entry-title')).toHaveText('阿弗纳斯的大公扎瑞尔');
  const copyParentBtn=page.locator('#detail .o5e-provenance button.ref[data-entry^="o5e_"]').first();
  await expect(copyParentBtn).toBeVisible();
  const parentName=(await copyParentBtn.textContent()).trim();
  await copyParentBtn.click();
  await expect(page.locator('#entry-title')).toHaveText(parentName);
  expect(new URL(page.url()).searchParams.get('collection')).toBe('our5etools');

  // Creature with transcoded local .opus audio (吸血鬼 -> /our5etools-media/audio/bestiary/vampire.opus)
  await page.goto(anna+'/?collection=our5etools&type=creature&q='+encodeURIComponent('吸血鬼')+'&source=src%3AXMM');
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

test('4. class 1-20 progression table, {@filter} link, and button.ref stay strictly inside collection=our5etools',async({page})=>{
  await page.goto(anna+'/?collection=our5etools&type=class&q='+encodeURIComponent('法师')+'&source=src%3AXPHB');
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
  await expect(page).toHaveURL(new RegExp('collection=our5etools'));
  await expect(page).toHaveURL(new RegExp('entry='+targetId));

  // Return to Wizard and verify {@filter} link navigates within collection=our5etools with valid facets
  await page.goBack();
  await expect(page.locator('#entry-title')).toHaveText('法师');
  const filterLink=page.locator('#detail-content a.ref-filter[href*="collection=our5etools"]').first();
  await expect(filterLink).toBeVisible();
  const href=await filterLink.getAttribute('href');
  expect(href).toContain('collection=our5etools');
  await page.goto(anna+href);
  await resultsReady(page);
  await expect(picker(page)).toHaveValue('our5etools');
  expect(await page.locator('#results tr').count()).toBeGreaterThan(0);
});

test('5. provenance card and remote unbundled image notice render cleanly without claiming offline bundle',async({page})=>{
  await page.goto(anna+'/?collection=our5etools&type=magic_item&q='+encodeURIComponent('远古法师手册'));
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

test('5b. lazy media displays served image and collapses cleanly to fallback note on 404',async({page})=>{
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

  await page.goto(anna+'/?collection=our5etools&type=magic_item&q='+encodeURIComponent('远古法师手册'));
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

test('6. Tour (collection=our5etools): bookshelf & pack drill-down, scope banner, widen-to-all, and mobile back',async({page})=>{
  await page.setViewportSize({width:1280,height:800});
  await page.goto(anna+'/tour?collection=our5etools');
  await tourReady(page,0);
  await expect(tourLevel(page,0).locator('h3').first()).toHaveText('建角入门');

  // Drill into official books -> filter -> open 玩家手册（2024）
  await tourRow(page,0,'官方规则书').click();
  await tourReady(page,1);
  const filterInput=tourLevel(page,1).locator('.tour-filter-input').first();
  await expect(filterInput).toBeVisible();
  await filterInput.fill('玩家手册');
  await tourRow(page,1,'玩家手册（2024）').click();
  await tourReady(page,2);

  // Scoped banner & explicit widen-to-all
  const scopeBanner=tourLevel(page,2).locator('.tour-scope');
  await expect(scopeBanner).toBeVisible();
  await expect(scopeBanner).toContainText('当前范围');
  const widenLink=scopeBanner.locator('a[data-widen]');
  await expect(widenLink).toBeVisible();
  await expect(widenLink).toContainText('放宽到全库');

  // Scoped topic with explicit scope_source narrows and widens back cleanly
  await page.goto(anna+'/tour?collection=our5etools&at=spells-by-level&scope_source=src%3AXPHB');
  await tourReady(page,1);
  const scopedBanner=tourLevel(page,1).locator('.tour-scope');
  await expect(scopedBanner).toBeVisible();
  const scopedText=await scopedBanner.textContent();
  await scopedBanner.locator('a[data-widen]').click();
  await tourReady(page,1);
  await expect(tourLevel(page,1).locator('.tour-scope')).toHaveCount(0);
  expect(new URL(page.url()).searchParams.get('scope_source')).toBeNull();
  expect(scopedText).toContain('当前范围');

  // Drill into site packs
  await page.goto(anna+'/tour?collection=our5etools');
  await tourReady(page,0);
  await tourRow(page,0,'站点自有包（精选）').click();
  await tourReady(page,1);
  await tourLevel(page,1).locator('.tour-item').first().click();
  await tourReady(page,2);
  await expect(tourLevel(page,2).locator('.tour-scope')).toBeVisible();

  // Narrow mobile viewport (<760px): drill-down & back link
  await page.setViewportSize({width:375,height:812});
  await page.goto(anna+'/tour?collection=our5etools');
  await tourReady(page,0);
  await tourRow(page,0,'法术（按环阶）').click();
  await tourReady(page,1);
  const backBtn=tourLevel(page,1).locator('.tour-back');
  await expect(backBtn).toBeVisible();
  await backBtn.click();
  await tourReady(page,0);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(375);
});

test('7. Stella reader (/read?collection=our5etools): TOC, chapter iframe, paging, book search, reload & mobile 375px',async({page})=>{
  await page.setViewportSize({width:1280,height:800});
  await page.goto(anna+'/read?collection=our5etools&root=n_book_phb&page=books%2Fphb%2F0.html');
  await readerReady(page);
  expect(new URL(page.url()).searchParams.get('collection')).toBe('our5etools');

  // Chapter iframe is served from /our5etools-book/
  const frame=o5eFrame(page);
  expect(frame).toBeTruthy();
  await expect(frame.locator('body')).toContainText('玩家手册');

  // Next chapter paging stays inside collection=our5etools and renders chapter tables
  const nextBtn=page.locator('#book-next');
  await expect(nextBtn).toHaveAttribute('aria-disabled','false');
  await nextBtn.click();
  await readerReady(page);
  expect(new URL(page.url()).searchParams.get('collection')).toBe('our5etools');
  expect(new URL(page.url()).searchParams.get('page')).toBe('books/phb/1.html');
  const nextFrame=page.frames().find(f=>f.url().endsWith('/our5etools-book/books/phb/1.html'));
  expect(nextFrame).toBeTruthy();
  await expect(nextFrame.locator('table').first()).toBeVisible();

  // Reload restores location in collection=our5etools
  await page.reload();
  await readerReady(page);
  expect(new URL(page.url()).searchParams.get('collection')).toBe('our5etools');
  expect(new URL(page.url()).searchParams.get('page')).toBe('books/phb/1.html');

  // Book search finds 火球术 and stays inside collection=our5etools
  await page.locator('#tab-search').click();
  await page.locator('#book-search').fill('火球术');
  await page.locator('#book-search-form button').click();
  await expect(page.locator('#book-search-results li').first()).toBeVisible();
  const firstSearchLink=page.locator('#book-search-results li a').first();
  await expect(firstSearchLink).toHaveAttribute('href',/collection=our5etools/);

  // 375x812 mobile viewport has zero horizontal overflow
  await page.setViewportSize({width:375,height:812});
  await page.goto(anna+'/read?collection=our5etools&root=n_book_phb&page=books%2Fphb%2F0.html');
  await readerReady(page);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(375);
});

test('8. CSV export (5e-anna-our5etools.csv) and 5echm unscoped links non-regression',async({page})=>{
  await page.goto(anna+'/?collection=our5etools&type=spell&q='+encodeURIComponent('火球术'));
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

  // 5echm default links stay clean and unscoped (no collection=5echm)
  await page.goto(anna);
  await resultsReady(page);
  await expect(picker(page)).toHaveValue('5echm');
  expect(new URL(page.url()).searchParams.get('collection')).toBeNull();
  const tourSwitch=page.locator('.page-switch a').filter({hasText:'导览'});
  await expect(tourSwitch).toHaveAttribute('href',/^\/tour($|\?at=)/);

  await page.goto(anna+'/read');
  await readerReady(page);
  expect(new URL(page.url()).searchParams.get('collection')).toBeNull();
});
