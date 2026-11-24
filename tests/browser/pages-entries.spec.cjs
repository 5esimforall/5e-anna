/* Browser verification of static Pages entry buckets and async Option A version patching. */
const {test: base, expect} = require('@playwright/test');
const fs = require('node:fs');
const path = require('node:path');
const zlib = require('node:zlib');

const port = Number(process.env.ANNA_E2E_PORT || 18380);
const anna = `http://127.0.0.1:${port}`;
const webDir = path.resolve(__dirname, '../../web');

const test = base.extend({
  context: async ({context}, use) => {
    const errors = [];
    context.on('page', page => page.on('pageerror', e => errors.push(e.message)));
    await use(context);
    expect(errors, 'uncaught pages-entries browser errors').toEqual([]);
  },
});

test('Pages mode opens entries from static gzip bucket without hitting worker, and patches versions asynchronously without resetting layout scroll', async ({page}) => {
  await page.setViewportSize({width: 1440, height: 900});

  // Fetch two real spell entries and their layouts from the test server beforehand to pack into bucket 000.bin.
  const listData = await (await page.request.get(`${anna}/api/entries?type=spell&limit=10`)).json();
  const baseRow = listData.rows[0];
  const altRow = listData.rows[1];
  const baseDetail = await (await page.request.get(`${anna}/api/entry?id=${encodeURIComponent(baseRow.id)}`)).json();
  const baseLayout = await (await page.request.get(`${anna}/api/entry-layout?id=${encodeURIComponent(baseRow.id)}`)).json();
  const altDetail = await (await page.request.get(`${anna}/api/entry?id=${encodeURIComponent(altRow.id)}`)).json();
  const altLayout = await (await page.request.get(`${anna}/api/entry-layout?id=${encodeURIComponent(altRow.id)}`)).json();

  // Build synthetic copied_from provenance on baseDetail so Option A auto-diff activation is also verified.
  const paddedLayout = {
    ...baseLayout,
    html: (baseLayout.html || '<p>正文</p>') + Array.from({length: 25}, (_, i) => `<p>测试段落 ${i}：补充内容用于滚动保持验证。</p>`).join(''),
  };
  const baseWithCopy = {
    ...baseDetail,
    collection: '5echm',
    versions: null,
    provenance: {...(baseDetail.provenance || {}), copied_from: {id: altRow.id, name: altRow.name_zh || altRow.name}},
  };
  const bucketMap = {
    [baseRow.id]: {detail: baseWithCopy, layout: paddedLayout, has_extra_versions: true},
    [altRow.id]: {detail: {...altDetail, collection: '5echm'}, layout: altLayout},
  };

  // Serve initial table via normal HTTP /api/meta, /api/facets, /api/entries, then switch pages-mode bucket check.
  // Note: kit.js only calls pages.js when data-anna-pages is set, and we want /api/meta+/api/entries to work too
  // without Pyodide WASM in test server, so intercept pages-worker.js? No: simpler — let's run in Pages mode for
  // entry clicks by intercepting pages.js ONLY or wrapping pages.js in test? Wait: kit.js loads pages.js only when
  // data-anna-pages is set, and in Pages mode pages.js handles /api/meta+/api/facets+/api/entries via worker.
  // So let's verify BOTH:
  // 1. Direct pages-entries.js bucket + deduplication + gzip decode in real Chromium.
  // 2. Full detail.js + pages.js + pages-entries.js UI integration on live page!
  const bucketGzip = zlib.gzipSync(Buffer.from(JSON.stringify(bucketMap), 'utf-8'));
  const seen = {bucket: 0, versions: 0, worker: 0, workerEntryCalls: 0};

  // In this Chromium session, keep /api/meta, /api/facets, /api/entries on normal fetch by patching pages.js in-route
  // to delegate non-entry routes to fetch() instead of starting pages-worker.js, while keeping pages-entries.js 100% real!
  const realPagesJs = fs.readFileSync(path.join(webDir, 'pages.js'), 'utf-8');
  const testPagesJs = realPagesJs.replace(
    'connect();\n  const id = ++nextId, priority = priorityOf(path);',
    'if (path.startsWith("/api/entry?") || path.startsWith("/api/entry-layout?")) await fetch("/__worker_entry_fallback");\n  return fetch(path, {signal});\n  connect();\n  const id = ++nextId, priority = priorityOf(path);'
  );
  expect(testPagesJs).not.toBe(realPagesJs);

  await page.route('**/*', async route => {
    const u = new URL(route.request().url());
    if (u.pathname === '/') {
      const resp = await route.fetch();
      const html = (await resp.text()).replace('<html', '<html data-anna-pages data-anna-entries="1"');
      return route.fulfill({status: resp.status(), headers: resp.headers(), body: html});
    }
    if (u.pathname === '/pages.js') {
      return route.fulfill({status: 200, contentType: 'text/javascript; charset=utf-8', body: testPagesJs});
    }
    if (u.pathname === '/pages-entries.js') {
      return route.fulfill({
        status: 200,
        contentType: 'text/javascript; charset=utf-8',
        body: fs.readFileSync(path.join(webDir, 'pages-entries.js'), 'utf-8'),
      });
    }
    if (u.pathname === '/pages-worker.js') {
      seen.worker++;
      return route.abort();
    }
    if (u.pathname === '/__worker_entry_fallback') {
      seen.workerEntryCalls++;
      return route.fulfill({status: 204, body: ''});
    }
    if (u.pathname === '/pages-entries/000.bin') {
      seen.bucket++;
      return route.fulfill({status: 200, contentType: 'application/octet-stream', body: bucketGzip});
    }
    return route.continue();
  });

  // Case 1a: Deep link in default 5echm-only library -> opens directly from static bucket with 0 worker loads and 0 /api/entry-versions calls
  await page.goto(`${anna}/?type=spell&entry=${encodeURIComponent(baseRow.id)}`);
  await expect(page.locator('#detail')).toBeVisible();
  await expect(page.locator('#entry-title')).toHaveText(baseRow.name_zh || baseRow.name);
  await expect(page.locator('#detail-content')).toHaveAttribute('aria-busy', 'false');
  await expect(page.locator('#detail-panel .layout-reading')).toBeVisible();
  expect(seen.bucket).toBe(1);
  expect(seen.worker).toBe(0);
  expect(seen.workerEntryCalls).toBe(0);

  // Case 1b: Table click in default 5echm-only library -> hits static bucket (never falls back to worker)
  await page.goto(`${anna}/?type=spell`);
  await expect(page.locator('#results')).toHaveAttribute('aria-busy', 'false');
  await page.locator('#results .entry-name').first().click();
  await expect(page.locator('#detail')).toBeVisible();
  await expect(page.locator('#entry-title')).toHaveText(baseRow.name_zh || baseRow.name);
  await expect(page.locator('#detail-content')).toHaveAttribute('aria-busy', 'false');
  await expect(page.locator('#detail-panel .layout-reading')).toBeVisible();
  expect(seen.bucket).toBe(2);
  expect(seen.workerEntryCalls).toBe(0);

  // Case 2: Dual-library scope -> renders immediately from bucket while /api/entry-versions is gated,
  // preserves .layout-reading node identity & scrollTop, and patches versions + difference-bar + versions tab!
  let releaseVersions;
  const versionsGate = new Promise(resolve => { releaseVersions = resolve; });
  let versionsStarted;
  const versionsWaiting = new Promise(resolve => { versionsStarted = resolve; });

  await page.route('**/api/entry-versions?*', async route => {
    seen.versions++;
    versionsStarted();
    await versionsGate;
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        id: baseRow.id,
        collection: '5echm',
        versions: {
          group_id: 'grp_test',
          status: 'library_candidate',
          members: [
            {
              id: baseRow.id, collection: '5echm', name: baseDetail.name, name_zh: baseDetail.name_zh,
              name_en: baseDetail.name_en, ruleset: baseDetail.ruleset, source_ids: baseDetail.source_ids,
              source_names: baseDetail.source_names, fields: baseDetail.fields, quickref: baseDetail.quickref,
              legacy: baseDetail.legacy, relation: null,
            },
            {
              id: altRow.id, collection: '5echm', name: altDetail.name, name_zh: altDetail.name_zh,
              name_en: altDetail.name_en, ruleset: altDetail.ruleset, source_ids: altDetail.source_ids,
              source_names: altDetail.source_names, fields: altDetail.fields, quickref: altDetail.quickref,
              legacy: altDetail.legacy, relation: 'derived',
            },
          ],
          fields: [{field: 'spell_level', values: [baseDetail.fields?.spell_level, altDetail.fields?.spell_level], differs: true}],
        },
      }),
    });
  });

  await page.evaluate(() => {
    localStorage.setItem('anna.library', JSON.stringify({order: ['5echm', 'demo'], enabled: ['5echm', 'demo']}));
  });
  await page.goto(`${anna}/?type=spell`);
  await expect(page.locator('#results')).toHaveAttribute('aria-busy', 'false');
  await page.locator('#results .entry-name').first().click();

  await versionsWaiting;
  // Body + prerendered layout must already be visible before /api/entry-versions resolves!
  await expect(page.locator('#detail-content')).toHaveAttribute('aria-busy', 'false');
  await expect(page.locator('#detail-panel .layout-reading')).toBeVisible();
  await expect(page.locator('#detail-panel .difference-bar')).toHaveCount(0);

  // Scroll down inside #detail-content and mark .layout-reading node so we verify zero replacement or scroll reset.
  await page.evaluate(() => {
    const dc = document.getElementById('detail-content');
    dc.scrollTop = 180;
    document.querySelector('#detail-panel .layout-reading').dataset.keepNode = 'yes';
  });

  releaseVersions();

  // Patched version pills, tab count, and auto-activated copied_from difference-bar arrive without resetting layout/scroll!
  await expect(page.locator('#detail-content .reader-versions .version-pill')).toHaveCount(2);
  await expect(page.locator('#tab-versions')).toContainText('2');
  await expect(page.locator('#detail-panel .difference-bar [data-difference]')).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('#detail-panel .difference-status')).not.toHaveText('');
  expect(seen.versions).toBe(1);
  expect(seen.workerEntryCalls).toBe(0);

  const preserved = await page.evaluate(() => ({
    scrollTop: document.getElementById('detail-content').scrollTop,
    sameNode: document.querySelector('#detail-panel .layout-reading')?.dataset.keepNode === 'yes',
  }));
  expect(preserved.sameNode).toBe(true);
  expect(preserved.scrollTop).toBeGreaterThanOrEqual(150);

  // Clicking the patched versions tab renders the version table cleanly.
  await page.locator('#tab-versions').click();
  await expect(page.locator('#detail-panel .version-table')).toBeVisible();
});

test('Multi-scope static home cache serves dual-library homepage with 0 worker loads', async ({page}) => {
  await page.setViewportSize({width: 1440, height: 900});
  const libData = await (await page.request.get(`${anna}/api/library`)).json();
  const metaData = await (await page.request.get(`${anna}/api/meta`)).json();
  const facetsData = await (await page.request.get(`${anna}/api/facets`)).json();
  const entriesData = await (await page.request.get(`${anna}/api/entries?offset=0&limit=150`)).json();

  const homeDefault = 'home.1111111111111111.json';
  const homeScoped = 'home.2222222222222222.json';
  const scopedPayload = JSON.stringify({
    '/api/library': libData,
    '/api/meta?libraries=5echm,demo': metaData,
    '/api/facets?libraries=5echm,demo': facetsData,
    '/api/entries?offset=0&limit=150&libraries=5echm,demo': entriesData,
  });

  const seen = {defaultHome: 0, scopedHome: 0, worker: 0};
  await page.route('**/*', async route => {
    const u = new URL(route.request().url());
    if (u.pathname === '/') {
      const resp = await route.fetch();
      const html = (await resp.text()).replace(
        '<html',
        `<html data-anna-pages data-anna-entries="1" data-anna-home="${homeDefault}" data-anna-home-scopes="5echm,demo=${homeScoped}"`,
      );
      return route.fulfill({status: resp.status(), headers: resp.headers(), body: html});
    }
    if (u.pathname === `/${homeDefault}`) {
      seen.defaultHome++;
      return route.abort();
    }
    if (u.pathname === `/${homeScoped}`) {
      seen.scopedHome++;
      return route.fulfill({status: 200, contentType: 'application/json', body: scopedPayload});
    }
    if (u.pathname === '/pages-worker.js') {
      seen.worker++;
      return route.abort();
    }
    return route.continue();
  });

  await page.addInitScript(() => {
    localStorage.setItem('anna.library', JSON.stringify({order: ['5echm', 'demo'], enabled: ['5echm', 'demo']}));
  });
  await page.goto(`${anna}/`);
  await expect(page.locator('#results')).toHaveAttribute('aria-busy', 'false');
  await expect(page.locator('#results .entry-name').first()).toBeVisible();
  expect(seen.scopedHome).toBe(1);
  expect(seen.defaultHome).toBe(0);
  expect(seen.worker).toBe(0);
});
