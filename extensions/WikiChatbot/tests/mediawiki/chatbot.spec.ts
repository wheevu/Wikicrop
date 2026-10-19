import { test, expect, type Page } from '@playwright/test';

const requestFailure = 'Không nhận được phản hồi. Vui lòng thử lại.';
const invalidAnswer = 'Không nhận được phản hồi hợp lệ.';
const timeoutFailure = 'Yêu cầu đã hết thời gian. Vui lòng thử lại.';
const literalAnswer = '<img src=x onerror=alert(1)> literal synthetic answer';
let unexpected: string[];
let errors: string[];

function environment(): { base: string; api: string; control: string } {
  const base = process.env.WIKIKG_WIKI_URL;
  const api = process.env.WIKIKG_CHATBOT_STUB_URL;
  if (!base || !api) throw new Error('synthetic browser environment required');
  return { base, api, control: new URL('/synthetic-control', api).href };
}

test.beforeEach(async ({ page }) => {
  const { base, api } = environment();
  unexpected = [];
  errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/*', route => {
    const origin = new URL(route.request().url()).origin;
    if (origin === new URL(base).origin || origin === new URL(api).origin) return route.continue();
    unexpected.push(origin);
    return route.abort();
  });
});

test.afterEach(async ({ page }, testInfo) => {
  await page.screenshot({ path: testInfo.outputPath('chatbot.png'), fullPage: true });
  expect(unexpected).toEqual([]);
  expect(errors).toEqual([]);
});

async function openChat(page: Page): Promise<void> {
  const { base, api } = environment();
  await page.goto(base + '/w/index.php?title=Main_Page', { timeout: 60000 });
  await expect(page.locator('#chatbot-toggler')).toBeVisible();
  await expect.poll(() => page.evaluate(() => mw.loader.getState('ext.wikichatbot'))).toBe('ready');
  expect(await page.evaluate(() => mw.config.get('WikiChatbotApiUrl'))).toBe(api);
  await page.locator('#chatbot-toggler').click();
  await expect(page.locator('#chat-input')).toBeVisible();
}

async function submit(page: Page, query: string): Promise<void> {
  await page.locator('#chat-input').fill(query);
  await page.locator('#send-btn').click();
}

async function control(page: Page, action: 'await_late' | 'release_alpha'): Promise<void> {
  const { base, control: url } = environment();
  const response = await page.request.post(url, {
    headers: { Origin: base }, data: { action }, timeout: 20000,
  });
  expect(response.status()).toBe(200);
  expect(await response.json()).toEqual({ completed: true });
}

test('real PHP hook renders interactive chat and displays fixture response as text', async ({ page }) => {
  await openChat(page);
  await submit(page, 'synthetic question');
  await expect(page.getByText(literalAnswer, { exact: true })).toBeVisible();
  await expect(page.locator('#chat-messages img')).toHaveCount(0);
  await submit(page, 'synthetic failure');
  await expect(page.getByText(requestFailure, { exact: true })).toBeVisible();
  await submit(page, 'synthetic question');
  await expect(page.getByText(literalAnswer, { exact: true })).toHaveCount(2);
  await page.screenshot({ path: process.env.WIKIKG_SCREENSHOT!, fullPage: true });
});

test('malformed JSON and invalid answers refuse safely and recover', async ({ page }) => {
  await openChat(page);
  let validAnswers = 0;
  for (const [query, refusal] of [
    ['synthetic malformed json', requestFailure],
    ['synthetic invalid answer', invalidAnswer],
    ['synthetic empty answer', invalidAnswer],
    ['synthetic missing answer', invalidAnswer],
  ]) {
    const priorRefusals = await page.getByText(refusal, { exact: true }).count();
    await submit(page, query);
    await expect(page.getByText(refusal, { exact: true })).toHaveCount(priorRefusals + 1);
    await submit(page, 'synthetic question');
    await expect(page.getByText(literalAnswer, { exact: true })).toHaveCount(++validAnswers);
  }
  await expect(page.getByText('Đang suy nghĩ...', { exact: true })).toHaveCount(0);
});

test('real 30-second timeout aborts the request and recovers after the late server attempt', async ({ page }) => {
  const { api } = environment();
  await openChat(page);
  const initialBotMessages = await page.locator('#chat-messages .bot-msg').allTextContents();
  const aborted = page.waitForEvent('requestfailed', {
    predicate: request => request.url() === api && request.postData()?.includes('synthetic timeout late') === true,
    timeout: 35000,
  });
  await submit(page, 'synthetic timeout late');
  await expect(page.getByText(timeoutFailure, { exact: true })).toBeVisible({ timeout: 35000 });
  expect((await aborted).failure()?.errorText).toContain('ERR_ABORTED');
  await submit(page, 'synthetic question');
  await expect(page.getByText(literalAnswer, { exact: true })).toBeVisible();
  // Fetch is really aborted, so no late browser response event is expected.
  // Wait for the fixture's actual late write attempt before checking the DOM.
  await control(page, 'await_late');
  await expect(page.getByText('late fixture answer', { exact: true })).toHaveCount(0);
  await expect(page.getByText(timeoutFailure, { exact: true })).toHaveCount(1);
  await expect(page.getByText(literalAnswer, { exact: true })).toHaveCount(1);
  await expect(page.locator('#chat-messages .bot-msg')).toHaveText([
    ...initialBotMessages, timeoutFailure, literalAnswer,
  ]);
});

test('out-of-order concurrent replies stay beside their submitted messages', async ({ page }) => {
  await openChat(page);
  const messages = page.locator('#chat-messages');
  await submit(page, 'synthetic concurrent alpha');
  await submit(page, 'synthetic concurrent beta');
  const alpha = messages.getByText('synthetic concurrent alpha', { exact: true });
  const beta = messages.getByText('synthetic concurrent beta', { exact: true });
  await expect(messages.getByText('fixture answer for beta', { exact: true })).toBeVisible();
  await expect(messages.getByText('fixture answer for alpha', { exact: true })).toHaveCount(0);
  expect(await alpha.evaluate(node => node.nextElementSibling?.textContent)).toBe('Đang suy nghĩ...');
  expect(await beta.evaluate(node => node.nextElementSibling?.textContent)).toBe('fixture answer for beta');
  // This fixed fixture action releases only the waiting alpha response.
  // It cannot select a URL, file, answer body or production operation.
  await control(page, 'release_alpha');
  await expect(messages.getByText('fixture answer for alpha', { exact: true })).toBeVisible();
  expect(await alpha.evaluate(node => node.nextElementSibling?.textContent)).toBe('fixture answer for alpha');
  expect(await beta.evaluate(node => node.nextElementSibling?.textContent)).toBe('fixture answer for beta');
  await expect(messages.getByText('Đang suy nghĩ...', { exact: true })).toHaveCount(0);
});
