const {defineConfig} = require('@playwright/test');
const path = require('node:path');
const fs = require('node:fs');
const python = process.env.PYTHON || (fs.existsSync('.venv/bin/python') ? '.venv/bin/python' : 'python3');
const port = Number(process.env.ANNA_E2E_PORT || 18380);
process.env.PLAYWRIGHT_BROWSERS_PATH ||= path.resolve('.cache/ms-playwright');
module.exports = defineConfig({
  testDir: './tests/browser',
  testMatch: '**/*.spec.cjs',
  timeout: 45000,
  expect: {timeout: 10000},
  fullyParallel: false,
  workers: 1,
  retries: 0,
  outputDir: 'output/playwright/results',
  reporter: [['list'], ['html', {outputFolder:'output/playwright/report',open:'never'}]],
  use: {browserName:'chromium',headless:true,viewport:{width:1280,height:800},locale:'zh-CN',
    colorScheme:'light',reducedMotion:'reduce',screenshot:'only-on-failure',trace:'retain-on-failure'},
  webServer: {
    command:`"${python}" tests/browser/server.py`,
    url:`http://127.0.0.1:${port}/__ready`,
    reuseExistingServer:false,
    timeout:120000,
    gracefulShutdown:{signal:'SIGTERM',timeout:5000}
  }
});
