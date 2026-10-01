const assert = require('node:assert/strict');
const {spawn} = require('node:child_process');
const path = require('node:path');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const root = path.resolve(__dirname, '..');
(async () => {
  const fixtureProcess = spawn('python3', [path.join(__dirname, 'web_fixture.py')], {cwd: root});
  let browser;
  let stderr = '';
  fixtureProcess.stderr.on('data', data => { stderr += data; });
  try {
    const fixture = await new Promise((resolve, reject) => {
      let buffer = '';
      fixtureProcess.stdout.on('data', data => {
        buffer += data;
        if (buffer.includes('\n')) resolve(JSON.parse(buffer.split('\n')[0]));
      });
      fixtureProcess.once('error', reject);
      fixtureProcess.once('exit', code => reject(new Error(`Fixture ${code}: ${stderr}`)));
    });
    browser = await chromium.launch({executablePath: process.env.CHROME_BIN, headless: true, args: ['--no-sandbox']});
    const page = await browser.newPage({viewport: {width: 1440, height: 1000}});
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(fixture.base);
    await page.waitForFunction(() => currentDetail != null);
    const session = await page.evaluate(async () => {
      const response = await fetch('/v1/conversations', {method: 'POST',
        headers: {'Content-Type': 'application/json', 'X-Brain-UI': '1'},
        body: JSON.stringify({runner_id: 'r'.repeat(32)})});
      if (!response.ok) throw new Error(await response.text());
      return response.json();
    });
    await page.goto(`${fixture.base}/#${session.session_id}`);
    await page.waitForFunction(id => currentDetail?.session_id === id, session.session_id);
    await page.locator('#message-input').fill('request cross-runner commands');
    await page.locator('#message-send').click();
    await page.waitForFunction(() => currentDetail?.pending_tool_calls?.length === 2 && !currentDetail.active);
    const beta = page.locator('[data-call-id="routing_beta"]');
    assert.match(await beta.locator('summary').innerText(), /admin@staging at 192\.0\.2\.25/);
    if (await beta.getAttribute('open') === null) await beta.locator('summary').click();
    assert.match(await beta.innerText(), /Trust saves this exact prefix for 192\.0\.2\.25/);
    assert.match(await beta.innerText(), /Target: admin@staging at 192\.0\.2\.25/);
    await beta.getByRole('button', {name: 'Trust', exact: true}).click();
    await page.waitForFunction(() => currentDetail?.pending_tool_calls?.length === 1 && !currentDetail.active);
    const policy = await page.evaluate(async () => (await getJson('/v1/servers')).servers);
    assert.deepEqual(policy.find(item => item.server_ip === '192.0.2.25').trusted_prefixes, [['printf']]);
    assert.deepEqual(policy.find(item => item.server_ip === '127.0.0.1').trusted_prefixes, []);
    const defaultCard = page.locator('[data-call-id="routing_default"]');
    assert.match(await defaultCard.innerText(), /deploy@production at 127\.0\.0\.1/);
    await defaultCard.getByRole('button', {name: 'Deny', exact: true}).click();
    await page.waitForFunction(() => currentDetail?.status === 'ready' && !currentDetail.active);
    assert.equal(await page.evaluate(() => currentDetail.runner_id), 'r'.repeat(32));
    assert.match(await beta.locator('summary').innerText(), /admin@staging at 192\.0\.2\.25/);
    for (const width of [390, 320]) {
      await page.setViewportSize({width, height: 900});
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    }
    assert.deepEqual(errors, []);
    console.log('runner routing browser tests: ok');
  } finally {
    if (browser) await browser.close();
    fixtureProcess.kill('SIGTERM');
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
