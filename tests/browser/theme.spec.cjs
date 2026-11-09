const {test,expect}=require('@playwright/test');
const port=Number(process.env.ANNA_E2E_PORT||18380);
const anna=`http://127.0.0.1:${port+1}`;
const root=page=>page.locator('html');
const control=page=>page.locator('#theme');
async function choose(page,value){
  if(value==='system')await control(page).click({modifiers:['Shift']});
  else for(let i=0;i<2 && await control(page).getAttribute('data-preference')!==value;i++)await control(page).click();
  await expect(control(page)).toHaveAttribute('data-preference',value);
}
async function ready(page){await expect(page.locator('#results')).toHaveAttribute('aria-busy','false');}

test('compact theme button replaces status text and supports pointer and keyboard toggles',async({page})=>{
  await page.goto(anna);await ready(page);
  await expect(page.locator('#snapshot')).toHaveCount(0);
  await expect(page.locator('.topbar')).not.toContainText('本地');
  await expect(page.locator('.topbar select')).toHaveCount(0);
  await expect(control(page).locator('svg:visible')).toHaveCount(1);
  await expect(control(page).locator('.theme-icon-system')).toBeVisible();
  await expect(control(page)).toHaveAccessibleName('切换为深色（当前：跟随系统）');
  await control(page).press('Enter');
  await expect(control(page).locator('.theme-icon-dark')).toBeVisible();
  await expect(root(page)).toHaveAttribute('data-theme','dark');
  await control(page).press('Space');
  await expect(control(page).locator('.theme-icon-light')).toBeVisible();
  await expect(root(page)).toHaveAttribute('data-theme','light');
  await control(page).press('Shift+Enter');
  await expect(control(page)).toHaveAttribute('data-preference','system');
  expect(await page.evaluate(()=>localStorage.getItem('anna.theme'))).toBeNull();
  await page.emulateMedia({colorScheme:'dark'});
  await expect(root(page)).toHaveAttribute('data-theme','dark');
  await control(page).click();
  await expect(root(page)).toHaveAttribute('data-theme','light');
  await control(page).press('Shift+Space');
  await expect(root(page)).toHaveAttribute('data-theme','dark');
  await expect(control(page).locator('.theme-icon-system')).toBeVisible();
});

test('theme follows the system, explicit choices persist without resetting query or reader',async({page})=>{
  await page.emulateMedia({colorScheme:'dark'});
  await page.goto(anna+'/?type=spell&q=Shield');await ready(page);
  await expect(root(page)).toHaveAttribute('data-theme','dark');
  await expect(control(page)).toHaveAttribute('data-preference','system');
  await expect(root(page)).toHaveCSS('color-scheme','dark');
  await page.emulateMedia({colorScheme:'light'});
  await expect(root(page)).toHaveAttribute('data-theme','light');
  await page.locator('#results .entry-name').first().click();
  await expect(page.locator('#detail-content')).toHaveAttribute('aria-busy','false');
  const url=page.url();
  const title=await page.locator('#entry-title').innerText();
  await choose(page,'dark');
  await expect(root(page)).toHaveAttribute('data-theme','dark');
  expect(page.url()).toBe(url);
  await expect(page.locator('#q')).toHaveValue('Shield');
  await expect(page.locator('#entry-title')).toHaveText(title);
  await expect(page.locator('#detail')).toBeVisible();
  await page.emulateMedia({colorScheme:'dark'});await page.emulateMedia({colorScheme:'light'});
  await expect(root(page)).toHaveAttribute('data-theme','dark');
  await page.reload();await ready(page);
  await expect(control(page)).toHaveAttribute('data-preference','dark');
  await expect(root(page)).toHaveAttribute('data-theme','dark');
  await choose(page,'light');
  await page.emulateMedia({colorScheme:'dark'});
  await expect(root(page)).toHaveAttribute('data-theme','light');
  await choose(page,'system');
  await expect(root(page)).toHaveAttribute('data-theme','dark');
  expect(await page.evaluate(()=>localStorage.getItem('anna.theme'))).toBeNull();
});

test('theme syncs to other tabs and source text, including reset to system',async({page,context,request})=>{
  await page.goto(anna);await choose(page,'dark');
  const row=(await (await request.get(anna+'/api/entries?limit=1')).json()).rows[0];
  const source=await context.newPage();
  await source.goto(anna+'/source?'+new URLSearchParams({path:row.page_path,start:row.start,end:row.end}));
  await expect(root(source)).toHaveAttribute('data-theme','dark');
  await expect(source.locator('mark')).toBeVisible();
  const other=await context.newPage();await other.goto(anna);
  await expect(control(other)).toHaveAttribute('data-preference','dark');
  await choose(page,'light');
  await expect(control(other)).toHaveAttribute('data-preference','light');
  await expect(root(source)).toHaveAttribute('data-theme','light');
  await choose(page,'system');
  await expect(control(other)).toHaveAttribute('data-preference','system');
  await other.emulateMedia({colorScheme:'dark'});
  await expect(root(other)).toHaveAttribute('data-theme','dark');
  await choose(page,'dark');
  await page.evaluate(()=>localStorage.clear());
  await expect(control(other)).toHaveAttribute('data-preference','system');
});

test('invalid stored preferences fall back to the system',async({page})=>{
  const errors=[];page.on('pageerror',error=>errors.push(error.message));
  await page.addInitScript(()=>localStorage.setItem('anna.theme','invalid'));
  await page.emulateMedia({colorScheme:'dark'});
  await page.goto(anna);await ready(page);
  await expect(control(page)).toHaveAttribute('data-preference','system');
  await expect(root(page)).toHaveAttribute('data-theme','dark');
  expect(errors).toEqual([]);
});

test('blocked storage does not prevent theme changes or query startup',async({browser})=>{
  const context=await browser.newContext({colorScheme:'dark'});
  await context.addInitScript(()=>{
    for(const method of ['getItem','setItem','removeItem'])Object.defineProperty(Storage.prototype,method,{value(){throw new DOMException('Storage blocked','SecurityError');}});
  });
  const page=await context.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(anna);await ready(page);
  await expect(control(page)).toHaveAttribute('data-preference','system');
  await choose(page,'light');await expect(root(page)).toHaveAttribute('data-theme','light');
  await choose(page,'system');await expect(root(page)).toHaveAttribute('data-theme','dark');
  expect(errors).toEqual([]);await context.close();
});

test('dark palette keeps text contrast, narrow controls and error messages readable',async({page},testInfo)=>{
  await page.goto(anna+'/?type=spell');await ready(page);
  await choose(page,'dark');
  const contrast=await page.evaluate(()=>{
    const css=getComputedStyle(document.documentElement);
    function luminance(name){const hex=css.getPropertyValue('--anna-'+name).trim().slice(1);const rgb=[0,2,4].map(i=>parseInt(hex.slice(i,i+2),16)/255).map(n=>n<=.04045?n/12.92:((n+.055)/1.055)**2.4);return rgb[0]*.2126+rgb[1]*.7152+rgb[2]*.0722;}
    return [['ink','paper'],['secondary','surface'],['muted','paper'],['brand','selected'],['on-brand','brand'],['warning','warning-bg'],['success','success-bg'],['error','error-bg'],['mark-ink','mark']].map(([fg,bg])=>{const a=luminance(fg),b=luminance(bg);return {fg,bg,ratio:(Math.max(a,b)+.05)/(Math.min(a,b)+.05)};});
  });
  for(const pair of contrast)expect(pair.ratio,`${pair.fg} / ${pair.bg}`).toBeGreaterThanOrEqual(4.5);
  await page.locator('#results .entry-name').first().click();
  await expect(page.locator('#detail-content')).toHaveAttribute('aria-busy','false');
  await expect(page.locator('#detail')).toHaveCSS('background-color','rgb(33, 36, 41)');
  await testInfo.attach('dark-desktop',{body:await page.screenshot(),contentType:'image/png'});
  await page.locator('#close-detail').click();
  for(const width of [320,390,760]){
    await page.setViewportSize({width,height:844});
    const bounds=await control(page).boundingBox();
    expect(bounds.x).toBeGreaterThanOrEqual(0);expect(bounds.x+bounds.width).toBeLessThanOrEqual(width);
    await control(page).focus();await page.keyboard.press('Tab');await page.keyboard.press('Shift+Tab');await expect(control(page)).toBeFocused();
    await expect(control(page)).toHaveCSS('outline-style','solid');
    await expect(page.locator('.brand-logo')).toHaveCSS('width','36px');
  }
  await page.route('**/api/entries?**',route=>route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({error:'测试读取失败'})}));
  await page.locator('#q').fill('unavailable');
  await expect(page.locator('#error')).toBeVisible();
  await expect(page.locator('#error')).toHaveCSS('background-color','rgb(61, 39, 36)');
  await testInfo.attach('dark-error',{body:await page.screenshot(),contentType:'image/png'});
});

test('theme labels are localized independently of the query app',async({page})=>{
  await page.route(anna+'/',async route=>{
    const response=await route.fetch();await route.fulfill({response,body:(await response.text()).replace('lang="zh-CN"','lang="en"')});
  });
  await page.goto(anna);
  await expect(page.getByRole('button',{name:'Switch to Dark (current: System)',exact:true})).toBeVisible();
  await expect(control(page)).toHaveAttribute('title',/Shift\+click/);
  await control(page).click();
  await expect(control(page)).toHaveAccessibleName('Switch to Light (current: Dark)');
});
