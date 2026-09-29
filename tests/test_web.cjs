/* Browser regression checks against a temporary Brain with simulated execution.
 * npm install --prefix /tmp/brain-web-tools playwright@1.63.0
 * PLAYWRIGHT_MODULE=/tmp/brain-web-tools/node_modules/playwright CHROME_BIN=/path/to/chrome node tests/test_web.cjs
 */
const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const root = path.resolve(__dirname, '..');
const artifacts = process.env.WEB_TEST_ARTIFACTS || '/tmp/brain-web-artifacts';
fs.mkdirSync(artifacts, { recursive: true });

async function waitText(page, selector, text) {
  await page.waitForFunction(({ selector, text }) => document.querySelector(selector)?.textContent.includes(text), { selector, text });
}
async function openChat(page, fixture, name) {
  await page.goto(`${fixture.base}/#${fixture.sessions[name]}`);
  await page.locator('#message-input').waitFor();
  await waitText(page, '#connection-text', 'Connected');
  await page.waitForFunction(id => currentDetail?.session_id === id, fixture.sessions[name]);
}
async function noOverflow(page) {
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, 'page overflow');
  for (const selector of ['.topbar', '#message-form', '#servers', 'dialog[open]']) {
    const bounds = await page.locator(selector).evaluateAll(nodes => nodes.filter(n => n.getClientRects().length).map(n => {
      const r = n.getBoundingClientRect(); return { left: r.left, right: r.right, width: innerWidth };
    }));
    for (const r of bounds) assert.ok(r.left >= -1 && r.right <= r.width + 1, `${selector} overflow`);
  }
}
(async () => {
  const fixtureProcess = spawn('python3', [path.join(__dirname, 'web_fixture.py')], { cwd: root });
  fixtureProcess.stderr.on('data', data => fs.appendFileSync(path.join(artifacts, 'server.log'), data));
  let browser;
  try {
    const fixture = await new Promise((resolve, reject) => {
      let buffer = '';
      fixtureProcess.stdout.on('data', data => {
        buffer += data;
        if (buffer.includes('\n')) { try { resolve(JSON.parse(buffer.split('\n')[0])); } catch (error) { reject(error); } }
      });
      fixtureProcess.once('error', reject);
      fixtureProcess.once('exit', code => reject(new Error(`Fixture exited: ${code}`)));
    });
    browser = await chromium.launch({ executablePath: process.env.CHROME_BIN || undefined, headless: true, args: ['--no-sandbox'] });
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, colorScheme: 'light' });
    await context.grantPermissions(['clipboard-read', 'clipboard-write'], { origin: fixture.base });
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    page.setDefaultTimeout(10000);
    await openChat(page, fixture, 'main');
    await waitText(page, '#ai-model-value', 'test-model');
    await page.getByRole('button', { name: 'Configure AI' }).click();
    await waitText(page, '#ai-server-list', 'Main model');
    await page.waitForFunction(() => !aiConfigBusy);
    assert.equal(await page.locator('#ai-server-form').count(), 1);
    assert.equal(await page.locator('#ai-server-add, #ai-server-name').count(), 0);
    await page.locator('#ai-server-save').click();
    await waitText(page, '#ai-config-feedback', 'Server saved');
    const chatPicker = page.getByRole('button', { name: 'Choose main model on Configured AI' });
    assert.equal(await chatPicker.locator('.model-picker-value').innerText(), 'test-model');
    await chatPicker.press('Enter');
    await page.getByRole('listbox', { name: 'Main model options on Configured AI' }).waitFor();
    await page.waitForFunction(() => document.activeElement?.classList.contains('model-picker-option'));
    assert.equal(await page.getByRole('listbox', { name: 'Main model options on Configured AI' }).getByRole('option', { name: 'test-model' }).evaluate(node => node === document.activeElement), true);
    await page.keyboard.press('ArrowDown');
    assert.equal(await page.getByRole('listbox', { name: 'Main model options on Configured AI' }).getByRole('option', { name: 'alternate-model' }).evaluate(node => node === document.activeElement), true);
    await page.keyboard.press('Enter');
    await waitText(page, '#ai-model-value', 'alternate-model');
    await waitText(page, '#ai-config-feedback', 'Model updated');
    const supportPicker = page.getByRole('button', { name: 'Choose support model on Configured AI' });
    assert.equal(await supportPicker.locator('.model-picker-value').innerText(), 'Not configured');
    await supportPicker.click();
    await page.getByRole('listbox', { name: 'Support model options on Configured AI' })
      .getByRole('option', { name: 'Same as main' }).click();
    await waitText(page, '#ai-config-feedback', 'Support model updated');
    assert.equal(await page.getByRole('button', { name: 'Choose support model on Configured AI' }).locator('.model-picker-value').innerText(), 'Same as main');
    const waitForMain = page.getByRole('checkbox', { name: 'Wait for main LLM completion' });
    assert.equal(await waitForMain.isChecked(), false);
    await waitForMain.check();
    await waitText(page, '#ai-config-feedback', 'Title timing updated');
    assert.equal(await page.getByRole('checkbox', { name: 'Wait for main LLM completion' }).isChecked(), true);
    const researchPicker = page.getByRole('button', { name: 'Choose web research model on Configured AI' });
    assert.equal(await researchPicker.locator('.model-picker-value').innerText(), 'Same as main');
    await researchPicker.click();
    await page.getByRole('listbox', { name: 'Web research model options on Configured AI' })
      .getByRole('option', { name: 'alternate-model' }).click();
    await waitText(page, '#ai-config-feedback', 'Web research model updated');
    assert.equal(await page.getByRole('button', { name: 'Choose web research model on Configured AI' }).locator('.model-picker-value').innerText(), 'alternate-model');
    await noOverflow(page);
    await page.getByRole('button', { name: 'Close settings' }).click();

    // Web settings, saved research card, live modal, and mobile layout.
    await page.getByRole('button', { name: 'Configure AI' }).click();
    await page.getByRole('tab', { name: 'Web research' }).click();
    await page.locator('#searxng-url').fill('http://search.example:8888');
    await page.locator('#searxng-results').fill('12');
    await page.locator('#web-tools-save').click();
    await waitText(page, '#web-tools-feedback', 'saved');
    await page.getByRole('tab', { name: 'LLM & models' }).click();
    await page.getByRole('button', { name: 'Choose web research model on Configured AI' }).click();
    await page.getByRole('listbox', { name: 'Web research model options on Configured AI' })
      .getByRole('option', { name: 'Same as main' }).click();
    await waitText(page, '#ai-config-feedback', 'Web research model updated');
    await page.locator('#ai-config-close').click();
    await page.locator('#conversation-filter').selectOption('all');
    await openChat(page, fixture, 'research');
    await page.locator('.response-activity summary').first().click();
    await page.locator('.web-tool-card').getByText('Deep research').waitFor();
    await page.locator('.web-tool-card summary').click();
    await page.getByRole('button', { name: 'Open research chat' }).click();
    await waitText(page, '#research-dialog', 'release notes');
    assert.equal(await page.locator('.research-full-chat').getAttribute('open'), null);
    await page.locator('.research-full-chat summary').click();
    await waitText(page, '.research-full-chat', 'Read release notes');
    assert.equal(await page.locator('.research-sources a').getAttribute('rel'), 'noopener noreferrer');
    await noOverflow(page);
    await page.screenshot({ path: path.join(artifacts, 'research-desktop.png') });
    await page.locator('#research-dialog-close').click();
    await page.locator('#research-progress').click();
    await page.locator('#research-dialog[open]').waitFor();
    await page.locator('#research-dialog-close').click();
    await openChat(page, fixture, 'research_live');
    await page.locator('#research-dialog[open]').waitFor();
    await page.locator('#research-stop').waitFor();
    await page.locator('#research-dialog-close').click();
    assert.equal(await page.locator('#research-dialog[open]').count(), 0);
    await page.locator('#research-progress').click();
    await page.locator('#research-dialog[open]').waitFor();
    await page.locator('#research-dialog-close').click();
    await openChat(page, fixture, 'research_pending');
    assert.equal(await page.locator('#research-progress:visible').count(), 0);
    await page.evaluate(() => fetch('/fixture/research-start'));
    await page.locator('#research-dialog[open]').waitFor();
    await page.locator('#research-dialog-close').click();
    await page.evaluate(() => fetch('/fixture/research-update'));
    await waitText(page, '#research-progress-meta', '3 steps');
    assert.equal(await page.locator('#research-dialog[open]').count(), 0);
    await page.locator('#research-progress').click();
    await waitText(page, '#research-dialog', 'change log');
    await page.locator('.research-full-chat summary').click();
    const scroll = await page.locator('#research-dialog').evaluate(dialog => {
      const body = dialog.querySelector('.research-dialog-body');
      return { outer: dialog.scrollHeight > dialog.clientHeight + 1,
        inner: body.scrollHeight > body.clientHeight + 1 };
    });
    assert.equal(scroll.outer, false, 'research dialog has second scrollbar');
    assert.equal(scroll.inner, true, 'research content should scroll');
    await page.locator('#research-dialog-close').click();
    const researchMobile = await context.newPage();
    await researchMobile.setViewportSize({ width: 390, height: 844 });
    await researchMobile.goto(fixture.base);
    await researchMobile.locator('#conversation-filter').selectOption('all');
    await openChat(researchMobile, fixture, 'research');
    await researchMobile.locator('.response-activity summary').first().click();
    await researchMobile.locator('.web-tool-card summary').click();
    await researchMobile.getByRole('button', { name: 'Open research chat' }).click();
    await noOverflow(researchMobile);
    const mobileDialog = await researchMobile.locator('#research-dialog').boundingBox();
    assert.ok(Math.abs(mobileDialog.x) < 1 && Math.abs(mobileDialog.width - 390) < 1, 'research dialog fills mobile viewport');
    await researchMobile.screenshot({ path: path.join(artifacts, 'research-mobile.png') });
    await researchMobile.close();
    console.log('Web research settings and popup desktop/mobile passed');
    await page.locator('#conversation-filter').selectOption('current');
    await openChat(page, fixture, 'main');

    // Context opens with fresh memories, explicit references, and unavailable host paths in chat-only mode.
    await page.getByRole('button', { name: 'Add context' }).click();
    await page.getByRole('tab', { name: 'Memories' }).click();
    await page.getByRole('button', { name: /workspace.owner/ }).waitFor();
    assert.equal(await page.getByRole('button', { name: /workspace.root/ }).count(), 0);
    assert.equal(await page.getByRole('tab', { name: 'Server files' }).isDisabled(), true);
    await page.getByRole('button', { name: /workspace.owner/ }).click();
    await page.getByRole('button', { name: 'Remove workspace.owner' }).waitFor();
    assert.equal(await page.locator('#message-input').inputValue(), '');
    await page.getByRole('button', { name: 'Remove workspace.owner' }).click();
    assert.equal(await page.locator('#context-references').isVisible(), false);
    console.log('Context memories, chips, and chat-only host guard passed');

    const mobilePage = await context.newPage();
    await mobilePage.setViewportSize({ width: 390, height: 844 });
    await openChat(mobilePage, fixture, 'main');
    await mobilePage.getByRole('button', { name: 'Configure AI' }).click();
    await waitText(mobilePage, '#ai-server-list', 'Main model');
    assert.equal(await mobilePage.locator('#ai-config-dialog').evaluate(node => Math.round(node.getBoundingClientRect().width)), 390);
    await noOverflow(mobilePage);
    await mobilePage.close();
    console.log('AI configuration desktop/mobile passed');
    await page.locator('.markdown table').waitFor();
    await waitText(page, '#context-meter-label', '/ 32k');
    assert.equal(await page.locator('.markdown img, .markdown script, .markdown iframe').count(), 0);
    assert.equal(await page.evaluate(() => window.pwned), undefined);
    assert.equal(await page.locator('.markdown a[href^="javascript:"]').count(), 0);
    assert.equal(await page.locator('.code-block pre').innerText(), 'docker compose ps\n');
    await page.getByRole('button', { name: 'Copy code', exact: true }).click();
    await waitText(page, '#copy-status', 'Copied');
    const messageActionSizes = await page.locator('.message-action').evaluateAll(buttons => buttons.map(button => {
      const buttonStyle = getComputedStyle(button);
      const iconStyle = getComputedStyle(button.querySelector('svg'));
      return [button.textContent, buttonStyle.width, buttonStyle.height, iconStyle.width, iconStyle.height];
    }));
    assert.ok(messageActionSizes.length >= 4);
    for (const size of messageActionSizes) assert.deepEqual(size, ['', '32px', '32px', '18px', '18px']);
    console.log('Markdown, injection filtering, copy passed');

    // Resend/edit create saved response branches; arrows switch without losing streams.
    await page.getByRole('button', { name: 'Resend message and create new response branch' }).click();
    await page.waitForFunction(() => currentDetail?.status === 'ready'
      && !currentDetail.active && !sessionState().branchBusy);
    await waitText(page, '.branch-count', '2 / 2');
    await page.getByRole('button', { name: 'Previous response branch' }).click();
    await page.waitForFunction(() => document.querySelector('.branch-count')?.textContent.includes('1 / 2'));
    await page.getByRole('button', { name: 'Edit message and create new response branch' }).click();
    await page.locator('.message-editor-input').fill('Check server health, focus on errors.');
    await page.locator('.message-editor').getByRole('button', { name: 'Send', exact: true }).click();
    await page.waitForFunction(() => currentDetail?.status === 'ready'
      && !currentDetail.active && !sessionState().branchBusy);
    await waitText(page, '.branch-count', '3 / 3');
    assert.equal(await page.locator('.message-row.user .message-content').innerText(), 'Check server health, focus on errors.');
    await page.getByRole('button', { name: 'Previous response branch' }).click();
    await page.waitForFunction(() => document.querySelector('.branch-count')?.textContent.includes('2 / 3'));
    await page.getByRole('button', { name: 'Previous response branch' }).click();
    await page.waitForFunction(() => document.querySelector('.branch-count')?.textContent.includes('1 / 3'));
    assert.equal(await page.locator('.message-row.user .message-content').innerText(), 'Check server health and suggest next steps.');
    console.log('Message resend, edit, branch navigation passed');

    // Filtering must never change the selected chat; pin/rename persist across pages.
    await page.locator('#sidebar-search').fill('no matching title');
    await waitText(page, '#conversation-list', 'No matching');
    assert.equal(new URL(page.url()).hash.slice(1), fixture.sessions.main);
    await page.locator('#sidebar-search').fill('');
    await page.locator('#conversation-menu summary').click();
    await page.locator('#rename-button').click();
    await page.locator('#edit-input').fill('Production overview renamed');
    await page.locator('#edit-submit').click();
    await page.locator('#edit-dialog').waitFor({ state: 'hidden' });
    await waitText(page, '#conversation-title', 'Production overview renamed');
    await page.locator('#conversation-menu summary').click();
    await page.locator('#pin-button').click();
    await page.waitForFunction(() => !currentDetail.pinned);
    await page.locator('#conversation-menu summary').click();
    await page.locator('#pin-button').click();
    await page.waitForFunction(() => currentDetail.pinned);
    await page.reload();
    await waitText(page, '#conversation-title', 'Production overview renamed');
    assert.equal(await page.locator('#conversation-list .conversation-item').first().getAttribute('data-focus'), fixture.sessions.main);
    console.log('Search, rename, pin persistence passed');

    // Drafts, IME Enter, streaming and late fetch completion stay session-scoped.
    await page.locator('#message-input').fill('Draft for production');
    await page.locator(`[data-focus="${fixture.sessions.other}"]`).click();
    await page.waitForFunction(id => currentDetail?.session_id === id, fixture.sessions.other);
    assert.equal(await page.locator('#message-input').inputValue(), '');
    await page.locator('#message-input').fill('Draft for maintenance');
    await page.reload();
    await page.waitForFunction(() => currentDetail !== null);
    assert.equal(await page.locator('#message-input').inputValue(), 'Draft for maintenance');
    await page.locator('#message-input').dispatchEvent('keydown', { key: 'Enter', isComposing: true });
    assert.equal(await page.locator('#message-input').inputValue(), 'Draft for maintenance');
    await page.locator(`[data-focus="${fixture.sessions.main}"]`).click();
    await page.waitForFunction(id => currentDetail?.session_id === id, fixture.sessions.main);
    assert.equal(await page.locator('#message-input').inputValue(), 'Draft for production');
    let release;
    const gate = new Promise(resolve => { release = resolve; });
    let responseStarted;
    const started = new Promise(resolve => { responseStarted = resolve; });
    await page.route(`**/v1/conversations/${fixture.sessions.main}/turns`, async route => {
      responseStarted();
      await gate;
      await route.fulfill({ status: 200, contentType: 'text/event-stream', body: 'event: done\ndata: {}\n\n' });
    });
    await page.locator('#message-send').click();
    await started;
    await page.locator('#message-input').fill('New production draft');
    await page.locator(`[data-focus="${fixture.sessions.other}"]`).click();
    await page.waitForFunction(id => currentDetail?.session_id === id, fixture.sessions.other);
    release();
    await page.waitForFunction(id => !sessionState(id).messageBusy, fixture.sessions.main);
    assert.equal(await page.locator('#message-input').inputValue(), 'Draft for maintenance');
    await page.locator(`[data-focus="${fixture.sessions.main}"]`).click();
    await page.waitForFunction(id => currentDetail?.session_id === id, fixture.sessions.main);
    assert.equal(await page.locator('#message-input').inputValue(), 'New production draft');
    await page.unroute(`**/v1/conversations/${fixture.sessions.main}/turns`);
    await page.locator('#message-input').fill('Check health');
    await page.locator('#message-send').click();
    await page.waitForFunction(() => currentDetail?.active);
    assert.equal(await page.locator('#message-input').isEnabled(), true);
    await page.locator('#message-stop').waitFor();
    const stopSize = await page.locator('#message-stop').evaluate(button => {
      const style = getComputedStyle(button);
      return [parseFloat(style.width), parseFloat(style.height)];
    });
    assert.ok(stopSize.every(size => size >= 44), 'stop control remains easy to tap');
    await page.locator('#message-input').fill('Next question');
    await page.locator('#message-stop').click();
    await page.waitForFunction(() => !currentDetail?.active && !sessionState().messageBusy);
    await waitText(page, '#message-hint', 'Generation stopped');
    assert.equal(await page.locator('.message-stopped').last().innerText(), 'Stopped');
    assert.equal(await page.locator('#message-send').isEnabled(), true);
    await page.locator('#message-send').click();
    await page.waitForFunction(() => !currentDetail?.active && !sessionState().messageBusy);
    await page.locator('#message-input').fill('Next question');
    assert.equal(await page.locator('#message-input').inputValue(), 'Next question');
    assert.equal(await page.locator('.response-activity').first().getAttribute('open'), null);
    await page.locator('.response-activity summary').first().click();
    await page.locator('#conversation-menu summary').click();
    await page.getByRole('button', { name: 'Show answers only' }).click();
    assert.equal(await page.locator('.response-activity').first().isVisible(), false);
    await page.locator('#conversation-menu summary').click();
    assert.equal(await page.getByRole('button', { name: 'Show answers only' }).getAttribute('aria-pressed'), 'true');
    await page.getByRole('button', { name: 'Show answers only' }).click();
    await page.locator('#transcript').evaluate(node => { node.scrollTop = 0; });
    await page.locator('#jump-latest').waitFor();
    await page.evaluate(() => { conversationStream.close(); conversationStream.onerror(); });
    await page.locator('#connection-retry').click();
    await page.waitForFunction(() => connected && currentDetail !== null);
    assert.equal(await page.locator('#transcript').evaluate(node => node.scrollTop), 0, 'reconnect preserves reading position');
    assert.equal(await page.locator('.response-activity').first().getAttribute('open'), '');
    assert.equal(await page.locator('#message-input').inputValue(), 'Next question');
    await page.locator('#jump-latest').click();
    await page.locator('#jump-latest').waitFor({ state: 'hidden' });
    console.log('Draft isolation, IME, streaming, reconnect, disclosures passed');

    // Remote approval uses actual Brain endpoints, but execution is mocked by the fixture.
    await openChat(page, fixture, 'pending');
    await page.locator('#approval-banner').waitFor();
    await page.waitForTimeout(3200); // Banner must survive dashboard polling.
    assert.equal(await page.locator('#approval-banner').isVisible(), true);
    await page.locator('#approval-banner').click();
    await page.getByRole('button', { name: 'Allow once', exact: true }).click();
    await page.waitForFunction(() => currentDetail?.status === 'ready' && !currentDetail.active && !sessionState().commandBusy);
    await waitText(page, '#transcript', 'Simulated command output');
    await openChat(page, fixture, 'failed');
    await page.getByRole('button', { name: 'Retry', exact: true }).click();
    await page.waitForFunction(() => currentDetail?.status === 'ready' && !currentDetail.active && !sessionState().commandBusy);
    await openChat(page, fixture, 'multi');
    await waitText(page, '#approval-banner', '3 commands need review');
    assert.equal(await page.locator('.response-group > .tool-card').count(), 3);
    assert.equal(await page.locator('.response-group > .tool-card[open]').count(), 1);
    await page.getByRole('button', { name: 'Allow once', exact: true }).click();
    await page.waitForFunction(() => currentDetail?.pending_tool_calls?.length === 2
      && !currentDetail.active && !sessionState().commandBusy);
    await waitText(page, '#approval-banner', '2 commands need review');
    assert.equal(await page.locator('.response-group > .tool-card[open]').count(), 1);
    await page.getByRole('button', { name: 'Trust', exact: true }).click();
    await page.waitForFunction(() => currentDetail?.pending_tool_calls?.length === 1
      && !currentDetail.active && !sessionState().commandBusy);
    assert.equal(await page.getByRole('button', { name: 'Deny', exact: true }).isEnabled(), true);
    await page.getByRole('button', { name: 'Deny', exact: true }).click();
    await page.waitForFunction(() => currentDetail?.status === 'ready'
      && !currentDetail.active && !sessionState().commandBusy);
    await openChat(page, fixture, 'resume');
    await page.locator('#message-input').fill('Continue investigation');
    await page.locator('#message-send').click();
    await page.waitForFunction(() => currentDetail?.status === 'ready' && !currentDetail.active && !sessionState().messageBusy);
    console.log('Approval queue, runner retry, interrupted continuation passed');

    await page.goto(`${fixture.base}/#servers/127.0.0.1`);
    await page.locator('#server-name').waitFor();
    await waitText(page, '.runner-version', 'v3 · Latest');
    await page.locator('#server-name').fill('Production renamed');
    await page.getByRole('button', { name: 'Save name', exact: true }).click();
    await waitText(page, '#conversation-title', 'Production renamed');
    await page.locator('#trust-prefix').fill('docker ps');
    await page.getByRole('button', { name: 'Add prefix', exact: true }).click();
    await page.getByRole('button', { name: 'Remove trust for docker ps', exact: true }).waitFor();
    await page.getByRole('button', { name: 'Remove trust for docker ps', exact: true }).click();
    await page.getByRole('button', { name: 'Check now', exact: true }).click();
    await waitText(page, '#servers', 'Check passed');
    await page.getByRole('button', { name: 'Install / repair', exact: true }).click();
    await page.locator('.setup-command').waitFor();
    assert.ok((await page.locator('.setup-command').innerText()).includes('/runner/install/'));
    await page.locator('#sidebar-search').fill('192.0.2.25');
    await page.locator('#server-list button').click();
    await page.locator('.runner-diagnostic').waitFor();
    assert.equal(await page.locator('.runner-diagnostic').getAttribute('open'), null);
    await page.locator('.runner-diagnostic summary').click();
    await page.waitForTimeout(3200);
    assert.equal(await page.locator('.runner-diagnostic').getAttribute('open'), '');
    await page.reload();
    await waitText(page, '#conversation-title', 'Staging');
    await page.goBack();
    await waitText(page, '#conversation-title', 'Production renamed');
    console.log('Server naming, trust edits, health/setup, navigation passed');

    await openChat(page, fixture, 'other');
    await page.locator('#conversation-menu summary').click();
    await page.locator('#archive-button').click();
    await page.locator('#conversation-filter').selectOption('all');
    await openChat(page, fixture, 'other');
    await waitText(page, '#status-badge', 'Archived');
    assert.equal(await page.locator('#message-input').isDisabled(), true);
    await page.locator('#conversation-menu summary').click();
    await page.locator('#archive-button').click();
    await page.waitForFunction(id => currentDetail?.session_id === id && !currentDetail.archived && !sessionState().actionBusy, fixture.sessions.other);
    await page.locator('#conversation-menu summary').click();
    await page.locator('#delete-button').click();
    assert.equal(await page.locator('#confirm-cancel').evaluate(node => node === document.activeElement), true);
    await page.keyboard.press('Escape');
    await page.locator('#conversation-menu summary').click();
    await page.locator('#delete-button').click();
    await page.locator('#confirm-submit').click();
    await page.locator('#confirm-dialog').waitFor({ state: 'hidden' });
    await page.waitForFunction(id => currentDetail && currentDetail.session_id !== id, fixture.sessions.other);
    await page.locator('#conversation-filter').selectOption('current');
    console.log('Archive, restore, delete confirmation passed');

    // Target-loading failure must remain visible; chat-only creation still works.
    await page.route('**/v1/runners', route => route.fulfill({ status: 503, body: '{}' }));
    await page.locator('#new-conversation').click();
    await waitText(page, '#new-conversation-feedback', 'Could not refresh runners');
    await page.unroute('**/v1/runners');
    await page.locator('#new-conversation-feedback button').click();
    await page.getByRole('radio', { name: /deploy@production/ }).waitFor();
    await page.getByRole('radio', { name: /deploy@production/ }).click();
    await page.locator('#create-conversation').click();
    await page.locator('#new-conversation-dialog').waitFor({ state: 'hidden' });
    await page.waitForFunction(() => currentDetail?.runner_id === 'r'.repeat(32) && currentDetail?.message_count === 1);
    await page.locator('#runner-picker-summary').click();
    await page.locator('#runner-menu [data-runner-id=""]').click();
    await page.waitForFunction(() => currentDetail && !currentDetail.runner_id);
    await page.waitForFunction(() => !document.querySelector('#runner-picker').open);
    assert.equal(await page.locator('#runner-picker').getAttribute('open'), null);
    console.log('New conversation, target errors/retry, target changes passed');

    await openChat(page, fixture, 'files');
    const fileCard = page.locator('.file-edit-list .file-edit-card');
    await fileCard.waitFor();
    assert.equal(await fileCard.locator('.file-diff .add').count() > 0, true);
    await fileCard.getByRole('button', { name: 'Edit file' }).click();
    await page.locator('#file-editor-dialog[open]').waitFor();
    assert.equal(await page.locator('#file-editor-content').inputValue(), 'value = 2\n');
    await page.locator('#file-editor-content').fill('value = 3\n');
    await page.locator('#file-editor-save').click();
    await page.locator('#file-editor-dialog').waitFor({ state: 'hidden' });
    await waitText(page, '.file-edit-card', 'Edited by you');
    assert.equal(await fileCard.locator('.file-diff').innerText().then(text => text.includes('+value = 3')), true);
    await fileCard.getByRole('button', { name: 'Restore original' }).click();
    await page.locator('#confirm-submit').click();
    await page.locator('#confirm-dialog').waitFor({ state: 'hidden' });
    await waitText(page, '.file-edit-card', 'Restored');
    assert.equal(await fileCard.getByRole('button', { name: 'Restore original' }).isDisabled(), true);
    await openChat(page, fixture, 'created_file');
    const createdCard = page.locator('.file-edit-list .file-edit-card');
    assert.equal(await createdCard.getByRole('button', { name: 'Restore original' }).count(), 0);
    await createdCard.getByRole('button', { name: 'Delete file' }).click();
    assert.equal(await page.locator('#confirm-title').textContent(), 'Delete file?');
    await page.locator('#confirm-submit').click();
    await page.locator('#confirm-dialog').waitFor({ state: 'hidden' });
    await waitText(page, '.file-edit-card', 'Deleted');
    assert.equal(await createdCard.getByRole('button', { name: 'Delete file' }).isDisabled(), true);
    await openChat(page, fixture, 'files');
    for (const width of [320, 390]) {
      await page.setViewportSize({ width, height: 844 });
      await noOverflow(page);
      await fileCard.getByRole('button', { name: 'Edit file' }).click();
      await page.locator('#file-editor-dialog[open]').waitFor();
      await noOverflow(page);
      await page.locator('#file-editor-cancel').click();
    }
    console.log('File diff, inline edit, restore, mobile layout passed');

    // Visual and keyboard acceptance matrix.
    for (const width of [320, 390, 768, 1440]) {
      await page.setViewportSize({ width, height: width < 600 ? 844 : 1000 });
      for (const theme of ['light', 'dark']) {
        await page.evaluate(theme => {
          setTheme(theme);
        }, theme);
        await openChat(page, fixture, 'main');
        assert.equal(await page.evaluate(() => getComputedStyle(document.documentElement).colorScheme), theme);
        await noOverflow(page);
        await page.screenshot({ path: path.join(artifacts, `chat-${width}-${theme}.png`), animations: 'disabled' });
        await page.locator('#runner-picker-summary').click();
        assert.equal(await page.locator('#runner-menu .runner-menu-option[aria-pressed="true"]').count(), 1);
        await noOverflow(page);
        await page.screenshot({ path: path.join(artifacts, `target-menu-${width}-${theme}.png`), animations: 'disabled' });
        await page.keyboard.press('Escape');
        await page.goto(`${fixture.base}/#servers/127.0.0.1`);
        await page.locator('#server-name').waitFor();
        await noOverflow(page);
        await page.screenshot({ path: path.join(artifacts, `servers-${width}-${theme}.png`), animations: 'disabled' });
      }
      if (width < 900) {
        assert.equal(await page.locator('#header-new-conversation').getAttribute('aria-label'), 'Add server');
        await page.locator('#header-new-conversation').click();
        await page.locator('#add-server-dialog[open]').waitFor();
        await page.locator('#add-server-close').click();
        await page.evaluate(() => setTheme('light'));
        await page.locator('#menu-button').click();
        assert.equal(await page.locator('.main').evaluate(node => node.inert), true);
        await page.locator('#settings-button').click();
        if (width <= 390) {
          const settingsTabs = await page.locator('.settings-tab').evaluateAll(nodes => nodes.map(node => {
            const bounds = node.getBoundingClientRect();
            return { label: node.textContent.trim(), left: bounds.left, right: bounds.right,
              top: bounds.top, bottom: bounds.bottom, contentWidth: node.scrollWidth, width: node.clientWidth };
          }));
          assert.equal(settingsTabs.length, 4);
          for (const tab of settingsTabs) {
            assert.ok(tab.left >= -1 && tab.right <= width + 1
              && tab.top >= -1 && tab.bottom <= 844, tab.label + ' clipped by viewport');
            assert.ok(tab.contentWidth <= tab.width + 1, tab.label + ' text clipped');
          }
        }
        await page.getByRole('tab', { name: 'Appearance' }).click();
        await page.locator('#theme-picker-summary').click();
        await noOverflow(page);
        await page.screenshot({ path: path.join(artifacts, 'appearance-' + width + '.png'), animations: 'disabled' });
        await page.keyboard.press('Escape');
        assert.equal(await page.locator('#theme-picker').getAttribute('open'), null);
        assert.equal(await page.locator('#ai-config-dialog').getAttribute('open'), '');
        await page.locator('#theme-picker-summary').click();
        await page.locator('#theme-menu [data-theme="dark"]').click();
        assert.equal(await page.evaluate(() => localStorage.getItem('brain.theme')), 'dark');
        assert.equal(await page.evaluate(() => getComputedStyle(document.documentElement).colorScheme), 'dark');
        assert.equal(await page.locator('#theme-picker').getAttribute('open'), null);
        await page.locator('#ai-config-close').click();
        assert.equal(await page.locator('#menu-button').evaluate(node => node === document.activeElement), true);
        await page.locator('#menu-button').click();
        await page.locator('#settings-button').focus();
        await page.keyboard.press('Tab');
        assert.equal(await page.locator('#conversations-view').evaluate(node => node === document.activeElement), true);
        await page.keyboard.press('Escape');
        assert.equal(await page.locator('#menu-button').evaluate(node => node === document.activeElement), true);
        await page.locator('#menu-button').click();
      }
      await page.locator('#conversations-view').click();
      if (width < 900) await page.locator('#menu-button').click();
      await page.locator('#new-conversation').click();
      await page.getByRole('radio', { name: /Chat only/ }).waitFor();
      await noOverflow(page);
      await page.screenshot({ path: path.join(artifacts, `new-chat-${width}.png`), animations: 'disabled' });
      await page.keyboard.press('Escape');
    }
    const touch = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
    const mobile = await touch.newPage();
    await openChat(mobile, fixture, 'main');
    const mobileActionSize = await mobile.locator('.message-action').first().evaluate(button => {
      const buttonStyle = getComputedStyle(button);
      const iconStyle = getComputedStyle(button.querySelector('svg'));
      return [buttonStyle.width, buttonStyle.height, iconStyle.width, iconStyle.height];
    });
    assert.deepEqual(mobileActionSize, ['44px', '44px', '19px', '19px']);
    await mobile.locator('#message-input').fill('Mobile draft');
    await mobile.locator('#message-input').press('Enter');
    assert.equal(await mobile.locator('#message-input').inputValue(), 'Mobile draft\n');
    await touch.close();
    await page.setViewportSize({ width: 768, height: 700 });
    await openChat(page, fixture, 'main');
    await page.evaluate(() => { document.documentElement.style.zoom = '2'; });
    await noOverflow(page);
    assert.deepEqual(errors, []);
    console.log(`Browser checks passed. Screenshots: ${artifacts}`);
  } finally {
    if (browser) await browser.close();
    fixtureProcess.kill();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
