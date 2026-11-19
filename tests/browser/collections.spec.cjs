/* The test server registers demo, reserved-demo, live our5etools (when built), and reserved 5etools. */
const {test,expect}=require('@playwright/test');
const anna=`http://127.0.0.1:${Number(process.env.ANNA_E2E_PORT||18380)}`;
const picker=page=>page.getByRole('combobox',{name:'资料库'});
const resultsReady=page=>expect(page.locator('#results')).toHaveAttribute('aria-busy','false');

test('reserved libraries (5etools & reserved-demo) are visible but unusable, while live our5etools is enabled, and an unavailable link has a way back',async({page})=>{
  await page.setViewportSize({width:375,height:812});
  await page.goto(anna);
  await resultsReady(page);
  await expect(picker(page)).toHaveValue('5echm');
  const our5etools=picker(page).locator('option[value="our5etools"]');
  await expect(our5etools).toHaveText('our5etools');
  await expect(our5etools).not.toHaveAttribute('disabled','');
  const fiveEtools=picker(page).locator('option[value="5etools"]');
  await expect(fiveEtools).toHaveText('5etools（未接入）');
  await expect(fiveEtools).toHaveAttribute('disabled','');
  const reserved=picker(page).locator('option[value="reserved-demo"]');
  await expect(reserved).toHaveText('预留测试库（未接入）');
  await expect(reserved).toHaveAttribute('disabled','');
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(375);
  await page.goto(anna+'/?collection=5etools');
  await expect(page.getByRole('alert').filter({hasText:'这个资料库尚未接入。'})).toBeVisible();
  await expect(page.locator('#results tr')).toHaveCount(0);
  await page.goto(anna+'/?collection=reserved-demo');
  await expect(page.getByRole('alert').filter({hasText:'这个资料库尚未接入。'})).toBeVisible();
  await expect(page.locator('#results tr')).toHaveCount(0);
  await page.getByRole('link',{name:'回到默认资料库'}).click();
  await resultsReady(page);
  await expect(picker(page)).toHaveValue('5echm');
});

test('switching collections scopes queries, reading, reload and export without fabricating a Stella location',async({page})=>{
  await page.goto(anna+'/?type=feat');
  await resultsReady(page);
  await picker(page).selectOption('demo');
  await expect(page).toHaveURL(anna+'/?collection=demo');
  await resultsReady(page);
  await expect(page.locator('#results tr')).toHaveCount(2);
  await page.goBack();
  await resultsReady(page);
  await expect(page).toHaveURL(anna+'/?type=feat');
  await expect(picker(page)).toHaveValue('5echm');

  await page.goto(anna+'/?collection=demo&type=spell');
  await resultsReady(page);
  await expect(page.locator('#results tr')).toHaveCount(1);
  const shield=page.locator('#results .entry-name').first();
  await expect(shield).toHaveText('护盾术');
  await shield.click();
  await expect(page.locator('#entry-title')).toHaveText('护盾术');
  await expect(page.locator('#detail .detail-links')).toHaveCount(0);
  await expect(page.locator('.companion')).toBeHidden();
  await page.reload();
  await expect(page.locator('#entry-title')).toHaveText('护盾术');
  await expect(picker(page)).toHaveValue('demo');
  await page.locator('#close-detail').click();
  await page.getByRole('button',{name:'清空',exact:true}).click();
  await expect(page).toHaveURL(anna+'/?collection=demo');
  await resultsReady(page);
  await expect(page.locator('#results tr')).toHaveCount(2);
  const [download]=await Promise.all([page.waitForEvent('download'),page.locator('#export').click()]);
  expect(download.suggestedFilename()).toBe('5e-anna-demo.csv');
});

test('before restarting an old service only the original unscoped queries are allowed',async({page,request})=>{
  const meta=await (await request.get(anna+'/api/meta')).json();
  for(const key of ['collections','collection','default_collection','source_text'])delete meta[key];
  await page.route('**/api/meta*',route=>route.fulfill({json:meta}));
  await page.goto(anna+'/?type=spell');
  await resultsReady(page);
  await expect(picker(page)).toBeHidden();
  await page.locator('#results .entry-name').first().click();
  await expect(page.locator('#detail .detail-links a[href^="/source?"]')).toBeVisible();
  await page.goto(anna+'/?collection=demo');
  await expect(page.locator('#error')).toContainText('请重启 make up');
  await expect(page.locator('#results tr')).toHaveCount(0);
});
