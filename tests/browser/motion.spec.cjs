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
    // The left and right edges of each box as drawn now, motion included.
    window.__edges=selectors=>selectors.map(selector=>{const box=document.querySelector(selector).getBoundingClientRect();return [Math.round(box.left),Math.round(box.right)];});
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
  // Leaving, the reader keeps its width and moves out whole; the results' edge follows it; the focus returns.
  const leaving=await page.evaluate(()=>{
    const reader=document.querySelector('#detail'),width=reader.getBoundingClientRect().width;
    document.querySelector('#close-detail').click();const style=getComputedStyle(reader);
    return {open:reader.open,beside:document.body.classList.contains('detail-open'),display:style.display,position:style.position,
      kept:Math.abs(parseFloat(style.width)-width)<1};
  });
  expect(leaving).toEqual({open:false,beside:false,display:'flex',position:'absolute',kept:true});
  await expect(page.locator('#results .entry-name').first()).toBeFocused();
  await expect(page.locator('#detail')).toBeHidden();
  // The results' edge reaches the far side once its motion ends.
  await expect.poll(()=>page.evaluate(()=>document.querySelector('.workspace>main').getBoundingClientRect().width===document.querySelector('.workspace').getBoundingClientRect().width)).toBeTruthy();
});

/* A panel that resizes its neighbour moves the neighbour's real edge, so the first frame of every such motion shows
   the layout as it was: nothing jumps to its final place and then slides back. */
test('the reader and the results move one shared edge, starting where it was',async({page})=>{
  await page.setViewportSize({width:1440,height:900});await page.goto(anna+'/?type=spell');await ready(page);await settled(page);
  const parts=['.workspace>main','#detail'];
  const opening=await page.evaluate(parts=>{
    const before=__edges(parts);document.querySelector('#results .entry-name').click();
    return {before,first:__edges(parts),workspace:Math.round(document.querySelector('.workspace').getBoundingClientRect().right),scrolled:document.scrollingElement.scrollLeft};
  },parts);
  // The results still reach the right edge, where the reader starts; the page never scrolls sideways to show it.
  expect(opening.first[0]).toEqual(opening.before[0]);
  expect(opening.first[1][0]).toBe(opening.workspace);
  expect(opening.scrolled).toBe(0);
  await read(page);await page.waitForTimeout(400);
  const open=await page.evaluate(parts=>__edges(parts),parts);
  expect(Math.abs(open[0][1]-open[1][0])).toBeLessThanOrEqual(1);
  const closing=await page.evaluate(parts=>{
    document.querySelector('#close-detail').click();return __edges(parts);
  },parts);
  expect(closing).toEqual(open);
});

test('full-width reading moves the reader’s edge over the results and back, with the text in its column',async({page})=>{
  await page.setViewportSize({width:1440,height:900});await page.goto(anna+'/?type=spell');await ready(page);await settled(page);
  await page.locator('#results tr').first().click();await read(page);await page.waitForTimeout(400);
  const toggle=()=>page.evaluate(()=>{
    const measure=()=>{const reader=document.querySelector('#detail').getBoundingClientRect(),title=document.querySelector('#entry-title').getBoundingClientRect();
      return {reader:Math.round(reader.left),title:Math.round(title.left),list:getComputedStyle(document.querySelector('.workspace>main')).visibility};};
    const before=measure();document.querySelector('#reader-full').click();return {before,first:measure()};
  });
  const widening=await toggle();
  expect(widening.first).toEqual(widening.before);
  await expect(page.locator('#reader-full')).toHaveAttribute('aria-pressed','true');
  await expect.poll(()=>page.locator('.workspace>main').evaluate(e=>getComputedStyle(e).visibility)).toBe('hidden');
  expect(await page.locator('#detail').evaluate(e=>Math.round(e.getBoundingClientRect().left))).toBe(0);
  const narrowing=await toggle();
  expect(narrowing.first).toEqual({...narrowing.before,list:'visible'});
  await page.waitForTimeout(400);
  expect(await page.locator('#detail').evaluate(e=>Math.round(e.getBoundingClientRect().left))).toBe(widening.before.reader);
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
  // Widening, the reader's left edge starts where it was.
  await page.locator('#reader-full').click();
  const widened=(await motions(page)).find(m=>m.target==='detail'&&m.keyframes.marginLeft);
  expect(parseFloat(widened.keyframes.marginLeft[0])).toBeGreaterThan(0);
});

test('conditions keep their identity and slide over as one leaves',async({page})=>{
  await page.setViewportSize({width:1440,height:900});await page.goto(anna+'/?type=spell&q=火&q=球');await ready(page);await settled(page);
  await motions(page);
  await page.evaluate(()=>document.querySelector('[data-key="q"] .condition-remove').click());
  const slid=(await motions(page)).filter(m=>/condition|join/.test(m.target)&&m.keyframes.translate);
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

test('Stella’s contents push the text’s edge, which starts where it was and keeps the bar in place',async({page})=>{
  await page.setViewportSize({width:1440,height:900});await page.goto(anna+'/read');
  await expect(page.locator('#book-main')).toHaveAttribute('aria-busy','false');await settled(page);await motions(page);
  const parts=['#book-main','#book-paging','#book-sidebar'];
  const toggle=()=>page.evaluate(parts=>{
    const before=__edges(parts);document.querySelector('#book-menu').click();
    const sidebar=document.querySelector('#book-sidebar'),style=getComputedStyle(sidebar);
    return {before,first:__edges(parts),open:sidebar.open,display:style.display,position:style.position};
  },parts);
  const closing=await toggle();
  expect(closing).toMatchObject({open:false,display:'flex',position:'absolute'});
  // The first frame is the layout as it was; the pager at the far edge never leaves its place.
  expect(closing.first).toEqual(closing.before);
  const [margin]=(await motions(page)).filter(m=>m.target==='book-main').map(m=>m.keyframes.marginLeft[0]);
  expect(parseFloat(margin)).toBe(closing.before[0][0]);
  await expect(page.locator('#book-sidebar')).toBeHidden();
  expect(await page.locator('#book-main').evaluate(e=>Math.round(e.getBoundingClientRect().left))).toBe(0);
  const opening=await toggle();
  expect(opening.first.slice(0,2)).toEqual(opening.before.slice(0,2));
  // The contents come in from the left, their right edge against the text's left edge.
  expect(opening.first[2][1]).toBeLessThanOrEqual(opening.first[0][0]+1);
  // Another pane comes in from the side its tab stands on.
  await motions(page);
  await page.locator('#tab-search').click();
  expect(moves(await motions(page),'panel-search')).toEqual(['8px 0px']);
});

/* The line being read: a character at the top of the view, and how far its line lies from that edge. The page is a
   document of the same origin, so its own layout says where the character went. */
const markLine=page=>page.evaluate(()=>{
  const view=document.querySelector('#book-scroll').getBoundingClientRect().top+2;
  const frame=[...document.querySelectorAll('.book-document')].find(f=>{const box=f.getBoundingClientRect();return box.top<=view&&box.bottom>view;});
  const box=frame.getBoundingClientRect(),range=frame.contentDocument.caretRangeFromPoint(box.width/2,view-box.top);
  window.__line={frame,node:range.startContainer,offset:range.startOffset};
});
const lineShift=page=>page.evaluate(()=>{
  const {frame,node,offset}=window.__line,range=node.ownerDocument.createRange();
  range.setStart(node,offset);range.setEnd(node,Math.min(node.length,offset+1));
  return Math.round(range.getClientRects()[0].top+frame.getBoundingClientRect().top-document.querySelector('#book-scroll').getBoundingClientRect().top);
});
const longPage='/read?page='+encodeURIComponent('5echm:topics/玩家手册2024/角色职业/野蛮人/野蛮人.htm');
// Narrow enough that the page's column re-wraps when the contents open or close.
async function readHalfway(page){
  await page.setViewportSize({width:1200,height:900});await page.goto(anna+longPage);
  await expect(page.locator('#book-main')).toHaveAttribute('aria-busy','false');await page.waitForTimeout(600);
  await page.locator('#book-scroll').evaluate(e=>{e.scrollTop=e.scrollHeight*.5;});await page.waitForTimeout(300);
}
test('the words being read stay at the top while the contents open or close beside them',async({page})=>{
  await readHalfway(page);await settled(page);await markLine(page);
  const shift=await lineShift(page);
  await page.locator('#book-menu').click();await expect(page.locator('#book-sidebar')).toBeHidden();await page.waitForTimeout(700);
  expect(Math.abs(await lineShift(page)-shift)).toBeLessThanOrEqual(2);
  await page.locator('#book-menu').click();await expect(page.locator('#book-sidebar')).toBeVisible();await page.waitForTimeout(700);
  expect(Math.abs(await lineShift(page)-shift)).toBeLessThanOrEqual(2);
});

test('a reader who scrolls while the contents close keeps the view they scrolled to',async({page})=>{
  await readHalfway(page);await settled(page);
  const box=await page.locator('#book-scroll').boundingBox();await page.mouse.move(box.x+box.width/2,box.y+box.height/2);
  const start=await page.evaluate(()=>{document.querySelector('#book-menu').click();return document.querySelector('#book-scroll').scrollTop;});
  // The wheel lands on the page's own document, which hands the scroll on to the reader's view.
  await page.mouse.wheel(0,600);await page.waitForTimeout(800);
  expect(await page.locator('#book-scroll').evaluate(e=>e.scrollTop)).toBeGreaterThan(start+300);
});

test('a branch of Stella’s contents opens from its row, uncovered as the rows below make room',async({page})=>{
  await page.setViewportSize({width:1440,height:900});await page.goto(anna+'/read');
  await expect(page.locator('#book-main')).toHaveAttribute('aria-busy','false');await settled(page);await motions(page);
  const branch=page.locator('#book-tree li[aria-expanded=false]').nth(2);
  const id=await branch.getAttribute('data-node');
  await branch.locator(':scope > .toc-row > button').click();
  const group=await page.locator(`#book-tree li[data-node="${id}"] > ul`).evaluate(e=>Math.round(e.getBoundingClientRect().height));
  const recorded=await motions(page);
  const opened=recorded.find(m=>m.keyframes.clipPath);
  expect(opened.keyframes.clipPath[0]).toBe(`inset(0px 0px ${group}px 0px)`);
  // The rows below slide down from where they stood, by the branch's height.
  const slid=recorded.filter(m=>m.target.includes('toc-row')&&m.keyframes.translate).map(m=>Math.round(parseFloat(m.keyframes.translate[0].split(' ')[1])));
  expect(slid.length).toBeGreaterThan(0);expect(slid.every(y=>Math.abs(y+group)<=1)).toBeTruthy();
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
  test('the words being read still stay at the top when the contents close at once',async({page})=>{
    await readHalfway(page);await markLine(page);
    const shift=await lineShift(page);
    await page.locator('#book-menu').click();await expect(page.locator('#book-sidebar')).toBeHidden();await page.waitForTimeout(700);
    expect(Math.abs(await lineShift(page)-shift)).toBeLessThanOrEqual(2);
    expect(await motions(page)).toEqual([]);
  });
});
