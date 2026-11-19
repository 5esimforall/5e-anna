/* Motion on the real corpus, with motion allowed. A change is complete at once and motion only shows where it came
   from; a page restoring itself appears in place. The other specs run with reduced motion, where nothing moves. */
const {test:base,expect}=require('@playwright/test');
const port=Number(process.env.ANNA_E2E_PORT||18380);
const anna=`http://127.0.0.1:${port}`;
const test=base.extend({context:async({context},use)=>{
  const errors=[];context.on('page',page=>page.on('pageerror',error=>errors.push(error.message)));
  // Script-drawn motion is recorded as it starts, so the checks never race its fraction of a second.
  await context.addInitScript(()=>{
    window.__motions=[];const animate=Element.prototype.animate;
    Element.prototype.animate=function(keyframes,options){
      window.__motions.push({target:this.id||String(this.className),keyframes:JSON.parse(JSON.stringify(keyframes))});
      return animate.call(this,keyframes,options);
    };
  });
  await use(context);expect(errors,'interface JavaScript errors').toEqual([]);
}});
test.use({reducedMotion:'no-preference'});
const motions=page=>page.evaluate(()=>window.__motions.splice(0));
const moves=(list,target)=>list.filter(m=>m.target===target&&m.keyframes.translate).map(m=>m.keyframes.translate[0]);
async function ready(page){await expect(page.locator('#result-count')).toContainText('找到');await expect(page.locator('#results')).toHaveAttribute('aria-busy','false');}
async function settled(page){await expect(page.locator('html')).toHaveAttribute('data-motion','');}
async function read(page){await expect(page.locator('#detail-content')).toHaveAttribute('aria-busy','false');await expect(page.locator('#detail')).toBeVisible();}

test('a page restoring its reader shows it in place; later changes move',async({page,request})=>{
  await page.setViewportSize({width:1440,height:900});
  const [first]=(await (await request.get(anna+'/api/entries?type=spell&limit=1')).json()).rows;
  await page.goto(anna+'/?'+new URLSearchParams({type:'spell',entry:first.id}));await ready(page);await read(page);await settled(page);
  expect(await motions(page)).toEqual([]);
  expect(await page.locator('#detail').evaluate(e=>e.getAnimations().length)).toBe(0);
  await page.locator('#entry-next').click();await read(page);
  expect(moves(await motions(page),'detail-content')).toEqual(['0px 8px']);
});

test('popups and the reader change state at once while they move',async({page})=>{
  await page.setViewportSize({width:1440,height:900});await page.goto(anna+'/?type=spell');await ready(page);await settled(page);
  const opened=await page.evaluate(()=>{
    const toggle=document.querySelector('#display-toggle'),panel=document.querySelector('#display-panel');toggle.click();
    return {expanded:toggle.getAttribute('aria-expanded'),hidden:panel.hidden,moving:panel.getAnimations().map(a=>a.transitionProperty)};
  });
  expect(opened).toMatchObject({expanded:'true',hidden:false});expect(opened.moving).toEqual(expect.arrayContaining(['opacity','translate']));
  // Closed at once and out of the pointer's way, the menu lifts away before it leaves the layout.
  const closed=await page.evaluate(()=>{
    document.querySelector('#display-toggle').click();const panel=document.querySelector('#display-panel'),style=getComputedStyle(panel);
    return {hidden:panel.hidden,display:style.display,pointer:style.pointerEvents};
  });
  expect(closed).toEqual({hidden:true,display:'grid',pointer:'none'});
  await expect(page.locator('#display-panel')).toBeHidden();

  await page.locator('#results tr').first().click();await read(page);
  const arriving=await page.locator('#detail').evaluate(e=>e.getAnimations().map(a=>a.transitionProperty));
  expect(arriving).toEqual(expect.arrayContaining(['translate']));
  // Leaving, the reader keeps its width over the results, which take their width back at once; the focus returns.
  const leaving=await page.evaluate(()=>{
    const reader=document.querySelector('#detail'),width=reader.getBoundingClientRect().width;
    document.querySelector('#close-detail').click();const style=getComputedStyle(reader);
    return {open:reader.open,beside:document.body.classList.contains('detail-open'),display:style.display,position:style.position,
      kept:Math.abs(parseFloat(style.width)-width)<1,list:document.querySelector('.workspace>main').getBoundingClientRect().width===document.querySelector('.workspace').getBoundingClientRect().width};
  });
  expect(leaving).toEqual({open:false,beside:false,display:'flex',position:'absolute',kept:true,list:true});
  await expect(page.locator('#results .entry-name').first()).toBeFocused();
  await expect(page.locator('#detail')).toBeHidden();
});

test('entries and tabs come in from the side they come from',async({page})=>{
  await page.setViewportSize({width:1440,height:900});await page.goto(anna+'/?type=spell');await ready(page);await settled(page);
  await page.locator('#results tr').first().click();await read(page);await motions(page);
  await page.locator('#entry-next').click();await read(page);
  expect(moves(await motions(page),'detail-content')).toEqual(['0px 8px']);
  await page.locator('#entry-prev').click();await read(page);
  expect(moves(await motions(page),'detail-content')).toEqual(['0px -8px']);
  await page.locator('#tab-fields').click();
  expect(moves(await motions(page),'detail-panel')).toEqual(['8px 0px']);
  await page.locator('#tab-body').click();
  expect(moves(await motions(page),'detail-panel')[0]).toBe('-8px 0px');
  // Widening, the reader uncovers its new width from the edge it had.
  await page.locator('#reader-full').click();
  const grown=(await motions(page)).find(m=>m.target==='detail'&&m.keyframes.clipPath);
  expect(grown.keyframes.clipPath[0]).toMatch(/^inset\(0px 0px 0px [1-9]\d*px\)$/);
});

test('conditions keep their identity and slide over as one leaves',async({page})=>{
  await page.setViewportSize({width:1440,height:900});await page.goto(anna+'/?type=spell&q=火&q=球');await ready(page);await settled(page);
  await motions(page);
  await page.evaluate(()=>document.querySelector('[data-remove="q0"]').click());
  const slid=(await motions(page)).filter(m=>/condition|and/.test(m.target)&&m.keyframes.translate);
  expect(slid.length).toBeGreaterThan(0);
  expect(slid.every(m=>parseFloat(m.keyframes.translate[0])>0)).toBeTruthy();
});

test('a theme change fades the whole page once, then lets controls fade their own colours again',async({page})=>{
  await page.goto(anna+'/?type=spell');await ready(page);await settled(page);
  await page.evaluate(()=>{window.__restyles=0;const start=document.startViewTransition.bind(document);document.startViewTransition=change=>{window.__restyles++;return start(change);};});
  await page.locator('#theme').click();await page.locator('#theme').click();
  await expect(page.locator('html')).toHaveAttribute('data-theme','dark');
  expect(await page.evaluate(()=>window.__restyles)).toBe(2);
  await expect(page.locator('html')).not.toHaveAttribute('data-restyling','');
});

test('moving between pages keeps the topbar and lets the two guides trade places',async({page})=>{
  const names=()=>page.evaluate(()=>[...document.querySelectorAll('.brand-logo,.companion-avatar')].filter(e=>e.offsetParent)
    .map(e=>e.className+':'+getComputedStyle(e).viewTransitionName));
  await page.goto(anna+'/?type=spell');await ready(page);
  expect(await page.locator('.topbar').evaluate(e=>getComputedStyle(e).viewTransitionName)).toBe('anna-topbar');
  await expect.poll(names).toEqual(['brand-logo:anna-guide-anna','companion-avatar:anna-guide-stella']);
  await page.goto(anna+'/read');await expect(page.locator('#book-main')).toHaveAttribute('aria-busy','false');
  await expect.poll(names).toEqual(['brand-logo:anna-guide-stella','companion-avatar:anna-guide-anna']);
  expect(await page.evaluate(()=>[...document.styleSheets].some(sheet=>[...sheet.cssRules].some(rule=>rule.constructor.name==='CSSViewTransitionRule')))).toBeTruthy();
});

test('Stella’s contents push the text aside and the text moves with them',async({page})=>{
  await page.setViewportSize({width:1440,height:900});await page.goto(anna+'/read');
  await expect(page.locator('#book-main')).toHaveAttribute('aria-busy','false');await settled(page);await motions(page);
  const closing=await page.evaluate(()=>{
    document.querySelector('#book-menu').click();const sidebar=document.querySelector('#book-sidebar'),style=getComputedStyle(sidebar);
    return {open:sidebar.open,display:style.display,position:style.position};
  });
  expect(closing).toEqual({open:false,display:'flex',position:'absolute'});
  expect(moves(await motions(page),'book-main')).toEqual(['280px 0px']);
  await expect(page.locator('#book-sidebar')).toBeHidden();
  await page.locator('#book-menu').click();
  expect(moves(await motions(page),'book-main')).toEqual(['-280px 0px']);
  // Another pane comes in from the side its tab stands on.
  await page.locator('#tab-search').click();
  expect(moves(await motions(page),'panel-search')).toEqual(['8px 0px']);
});

test('one tour level at a time: deeper levels come from the right, levels above from the left',async({page})=>{
  await page.setViewportSize({width:390,height:844});await page.goto(anna+'/tour');
  await expect(page.locator('.tour-column[data-depth="0"]')).toHaveAttribute('data-state','ready');await settled(page);await motions(page);
  await page.locator('.tour-column[data-depth="0"] .tour-item[data-at]').first().click();
  await expect(page.locator('.tour-column[data-depth="1"]')).toHaveAttribute('data-state','ready');
  expect(moves(await motions(page),'tour-column')).toEqual(['24px 0px']);
  await page.goBack();await expect(page.locator('.tour-column')).toHaveCount(1);
  expect(moves(await motions(page),'tour-column')).toEqual(['-24px 0px']);
});

test.describe('with reduced motion',()=>{
  test.use({reducedMotion:'reduce'});
  test('nothing moves',async({page})=>{
    await page.setViewportSize({width:1440,height:900});await page.goto(anna+'/?type=spell');await ready(page);await settled(page);
    await page.locator('#display-toggle').click();await page.locator('#display-toggle').click();
    await page.locator('#results tr').first().click();await read(page);
    await page.locator('#entry-next').click();await read(page);await page.locator('#tab-fields').click();
    expect(await motions(page)).toEqual([]);
    expect(await page.evaluate(()=>document.getAnimations().length)).toBe(0);
  });
});
