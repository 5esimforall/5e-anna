/* Actual corpus: acquisition constraints, same-branch filtering and evidence navigation. */
const {test,expect}=require('@playwright/test');
const anna=`http://127.0.0.1:${process.env.ANNA_E2E_PORT||18380}`;
async function ready(page){await expect(page.locator('#results')).toHaveAttribute('aria-busy','false');await expect(page.locator('#result-count')).toContainText('找到');}
test('casting conditions stay branch-scoped; background chain reaches effects, evidence and Stella',async({page,context})=>{
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(anna);await ready(page);
  await page.locator('#toggle-filters').click();await page.locator('#casting-filters summary').click();
  await page.locator('[data-casting=mode]').selectOption('prepared');
  await page.locator('[data-casting=resource]').selectOption('slot_free');
  await page.locator('[data-casting=recovery]').selectOption('long_rest');
  await page.locator('[data-casting=spell]').fill('疗伤术');
  await page.locator('[data-casting-apply]').click();await ready(page);
  const params=new URL(page.url()).searchParams;expect(JSON.parse(params.get('casting')).recovery).toBe('long_rest');
  await page.locator('#results .entry-name').filter({hasText:'侍僧'}).click();
  await page.getByRole('tab',{name:'规则链',exact:true}).click();
  await expect(page.locator('.rule-chain')).toContainText('牧师');
  const choices=page.locator('.chain-choices').filter({has:page.locator('[data-chain-spell]').filter({hasText:'疗伤术'})});
  await choices.locator('summary').click();
  await choices.locator('[data-chain-spell]').filter({hasText:'疗伤术'}).click();
  await expect(page.locator('.chain-effect')).toHaveCount(2);
  await expect(page.locator('.rule-chain')).toContainText('2d8');
  await expect(page.locator('.chain-effect').first()).toContainText('恢复生命值');
  const selected=new URL(page.url()).searchParams.get('chain_spell');expect(selected).toBeTruthy();
  await page.reload();await expect(page.getByRole('tab',{name:'规则链',exact:true})).toHaveAttribute('aria-selected','true');
  await expect(page.locator('.chain-effect')).toHaveCount(2);
  const exported=await page.request.get(anna+await page.locator('.rule-chain > a[download]').getAttribute('href'));
  expect((await exported.json()).chain.selected_spell_id).toBe(selected);
  const ev=page.locator('.chain-effect .chain-evidence').first();await ev.locator('summary').click();
  const popup=page.waitForEvent('popup');await ev.getByRole('link').click();const evidence=await popup;
  await expect(evidence.locator('#evidence')).toContainText('2d8');await evidence.close();
  const reading=page.waitForEvent('popup');await page.locator('.knowledge-record').filter({has:page.locator('.chain-effect')}).getByRole('link',{name:'在 Stella 中阅读 ↗'}).click();
  const stella=await reading;await expect(stella).toHaveURL(/\/read\?entry=/);await expect(stella.locator('.reader-error')).toHaveCount(0);await stella.close();
  await page.setViewportSize({width:390,height:844});
  await page.getByRole('tab',{name:'规则链',exact:true}).focus();await page.keyboard.press('ArrowLeft');
  await expect(page.getByRole('tab',{name:/关系/})).toHaveAttribute('aria-selected','true');
  await page.keyboard.press('ArrowRight');await expect(page.getByRole('tab',{name:'规则链',exact:true})).toHaveAttribute('aria-selected','true');
  await page.screenshot({path:'output/playwright/casting-chain-mobile.png'});
  await page.setViewportSize({width:1280,height:800});
  expect(errors).toEqual([]);
});

async function corpusEntry(request,name){
  const result=await (await request.get(anna+'/api/entries?'+new URLSearchParams({q:name,versions:'each'}))).json();
  const entry=result.rows.find(r=>r.name_zh===name&&r.ruleset==='5r');expect(entry).toBeTruthy();return entry;
}

test('chain reading retains the question through conditional effects, ambiguous rules, history and Stella',async({page,request})=>{
  const feat=await corpusEntry(request,'妖精触碰'),spell=await corpusEntry(request,'魅惑类人');
  await page.goto(anna+'/?'+new URLSearchParams({q:'妖精触碰',entry:feat.id,tab:'chain',chain_spell:spell.id}));
  const path=page.locator('.chain-path');await expect(path).toContainText('魅惑类人');
  await expect(page.locator('.chain-effect[data-effect=effect_2]')).toContainText('豁免 effect_1 · 失败');
  await expect(page.locator('.chain-effect[data-effect=effect_3]')).toContainText('effect_2');
  await expect(page.locator('.chain-effect[data-effect=effect_2]')).toContainText('满足任一');
  await page.locator('.chain-coverage > summary').click();
  await expect(page.locator('.chain-coverage')).toContainText('仍有未确定的通则目标');
  const card=page.locator('.knowledge-record').filter({has:page.locator('[data-effect=effect_2]')});
  await card.locator('.chain-rules > summary').click();
  const saveRule=card.locator('.chain-rules details').filter({has:page.locator('summary').filter({hasText:'可选法术'})}).first();
  await saveRule.locator('summary').click();
  const candidate=saveRule.locator('[data-chain-entry]').first(),candidateId=await candidate.getAttribute('data-chain-entry');
  await candidate.click();await expect(path.locator('[aria-current=step]')).toHaveAttribute('data-chain-path',JSON.stringify([feat.id,spell.id,candidateId]));
  const url=new URL(page.url());expect(url.searchParams.get('entry')).toBe(feat.id);expect(url.searchParams.get('q')).toBe('妖精触碰');
  await expect(page.locator('.chain-step')).toContainText('目标有歧义');
  const exportUrl=await page.locator('.rule-chain > a[download]').getAttribute('href');
  const exported=await (await request.get(anna+exportUrl)).json();
  expect(exported.chain.reading.path).toEqual([feat.id,spell.id,candidateId]);
  expect(exported.chain.reading.steps.at(-1).relations[0].status).toBe('ambiguous');
  await page.reload();await expect(path).toContainText('妖精触碰');await expect(page.locator('.chain-step')).toContainText('目标有歧义');
  await path.locator('button').first().click();await expect(path.locator('button')).toHaveCount(1);
  await page.goBack();await expect(path.locator('button')).toHaveCount(3);
  await page.goForward();await expect(path.locator('button')).toHaveCount(1);
  await page.goBack();await expect(path.locator('button')).toHaveCount(3);
  const popup=page.waitForEvent('popup');await page.locator('.chain-source').evaluate(el=>el.open=true);await page.locator('.chain-source > a').click();
  const stella=await popup;await expect(stella.locator('#book-main')).toHaveAttribute('aria-busy','false');
  await stella.locator('.companion-more').click();
  const resume=stella.locator('#companion-menu').getByRole('group',{name:'最近'}).getByRole('menuitem').filter({hasText:'妖精触碰'});
  await expect(resume).toHaveAttribute('href',/chain_path=/);await resume.click();
  await expect(stella.locator('.chain-path button')).toHaveCount(3);
  await expect(stella.getByRole('tab',{name:'规则链',exact:true})).toHaveAttribute('aria-selected','true');await stella.close();
});

test('reverse exception reading keeps scope and can continue to spell effects',async({page,request})=>{
  const feat=await corpusEntry(request,'妖精触碰');
  const chain=await (await request.get(anna+'/api/knowledge-chain?id='+feat.id)).json();
  const edge=chain.edges.find(e=>e.relation==='exception_to'&&e.target_id&&e.status==='resolved');expect(edge).toBeTruthy();
  await page.goto(anna+'/?'+new URLSearchParams({entry:edge.target_id,tab:'chain'}));
  await page.locator('.chain-modifiers > summary').click();
  await page.locator(`.chain-modifiers [data-chain-entry="${feat.id}"]`).first().click();
  await expect(page.locator('.chain-step')).toContainText('被这些能力修改');
  await expect(page.locator('.chain-step')).toContainText(edge.scope.branch_id);
  await expect(page.locator('.chain-step')).toContainText('spell_slot.consumed → false');
  const choices=page.locator('.chain-choices').filter({has:page.locator('[data-chain-spell]').filter({hasText:'魅惑类人'})});
  await choices.locator('summary').click();await choices.locator('[data-chain-spell]').filter({hasText:'魅惑类人'}).click();
  await expect(page.locator('.chain-path button')).toHaveCount(3);
  await expect(page.locator('[data-effect=effect_2]')).toContainText('豁免 effect_1');
  expect(new URL(page.url()).searchParams.get('entry')).toBe(edge.target_id);
});

test('late chain responses cannot reopen a tab the reader left',async({page,request})=>{
  const feat=await corpusEntry(request,'妖精触碰');
  await page.goto(anna+'/?'+new URLSearchParams({entry:feat.id,tab:'chain'}));
  const choices=page.locator('.chain-choices').filter({has:page.locator('[data-chain-spell]').filter({hasText:'魅惑类人'})});
  await choices.locator('summary').click();
  let release,started;const waiting=new Promise(done=>started=done),gate=new Promise(done=>release=done);
  await page.route('**/api/knowledge-chain?*',async route=>{started();await gate;await route.continue();});
  await choices.locator('[data-chain-spell]').filter({hasText:'魅惑类人'}).click();await waiting;
  await page.getByRole('tab',{name:'正文',exact:true}).click();release();
  await page.waitForResponse(r=>r.url().includes('/api/knowledge-chain?'));
  await expect(page.getByRole('tab',{name:'正文',exact:true})).toHaveAttribute('aria-selected','true');
  expect(new URL(page.url()).searchParams.has('chain_spell')).toBe(false);
});
