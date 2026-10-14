const { test, expect } = require('@playwright/test');
const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '../..');
const script = path.join(root, 'modules/ext.chatbot.js');
const style = path.join(root, 'modules/ext.chatbot.css');

async function fixture(page, includeDom = true) {
  await page.setContent(includeDom ? `
    <button id="chatbot-toggler">Open</button><section id="chatbot-container">
      <button id="close-chat">Close</button><div id="chat-messages"></div>
      <input id="chat-input"><button id="send-btn">Send</button>
    </section>` : '<main></main>');
  await page.addStyleTag({ content: fs.readFileSync(style, 'utf8') });
  await page.evaluate(() => {
    window.chatApiUrl = 'http://chatbot.test/custom-chat';
    window.mw = { config: { get: key => key === 'WikiChatbotApiUrl' ? window.chatApiUrl : undefined } };
    window.$ = callback => callback();
  });
  await page.addScriptTag({ path: script });
  if (includeDom) await page.locator('#chatbot-toggler').click();
}

async function send(page, text) {
  await page.locator('#chat-input').fill(text);
  await page.locator('#send-btn').click();
}

test('renders user and answer as literal text, preserving newlines and configured request', async ({ page }) => {
  await fixture(page);
  let request;
  await page.route('http://chatbot.test/custom-chat', async route => {
    request = route.request().postDataJSON();
    await route.fulfill({ json: { answer: '<script>window.pwned=1</script>\n<img src=x onerror="window.pwned=2">' } });
  });
  await send(page, '  <img onerror="alert(1)">  ');
  const messages = page.locator('#chat-messages');
  await expect(messages.locator('.user-msg')).toHaveText('<img onerror="alert(1)">');
  await expect(messages.locator('.bot-msg').last()).toHaveText('<script>window.pwned=1</script>\n<img src=x onerror="window.pwned=2">');
  await expect(messages.locator('script, img')).toHaveCount(0);
  expect(request).toEqual({ query: '<img onerror="alert(1)">' });
  expect(await page.evaluate(() => window.pwned)).toBeUndefined();
  expect(await messages.locator('.bot-msg').last().evaluate(el => getComputedStyle(el).whiteSpace)).toBe('pre-wrap');
});

test('replaces only its loading message for failures and invalid answers', async ({ page }) => {
  await fixture(page);
  const responses = [
    route => route.fulfill({ status: 503, body: 'private detail' }),
    route => route.fulfill({ status: 200, body: '{bad json' }),
    route => route.fulfill({ json: { answer: 42 } }),
    route => route.abort(),
  ];
  let index = 0;
  await page.route('http://chatbot.test/custom-chat', route => responses[index++](route));
  for (let i = 0; i < responses.length; i++) {
    await send(page, `question ${i}`);
    await expect(page.locator('#chat-messages .bot-msg').last()).not.toHaveText('Đang suy nghĩ...');
  }
  const text = await page.locator('#chat-messages').innerText();
  expect(text).not.toContain('private detail');
  expect(text).toContain('Không nhận được phản hồi hợp lệ.');
  expect(text).toContain('Không nhận được phản hồi. Vui lòng thử lại.');
});

test('timeout wins when fetch ignores abort and a late answer arrives', async ({ page }) => {
  await page.clock.install();
  await fixture(page);
  await page.evaluate(() => {
    window.fetchSignal = null;
    window.fetch = (_url, options) => {
      window.fetchSignal = options.signal;
      return new Promise(resolve => { window.resolveChatFetch = resolve; });
    };
  });
  await send(page, 'wait');
  await page.clock.fastForward(30000);
  await expect(page.locator('#chat-messages .bot-msg').last()).toHaveText('Yêu cầu đã hết thời gian. Vui lòng thử lại.');
  expect(await page.evaluate(() => window.fetchSignal.aborted)).toBe(true);
  await page.evaluate(() => window.resolveChatFetch({ ok: true, json: () => Promise.resolve({ answer: 'late answer' }) }));
  await page.evaluate(() => new Promise(resolve => setTimeout(resolve, 0)));
  await expect(page.locator('#chat-messages .bot-msg')).toHaveCount(1);
  await expect(page.locator('#chat-messages')).not.toContainText('late answer');
});

test('concurrent replies keep their own loading positions when responses arrive out of order', async ({ page }) => {
  await fixture(page);
  const pending = {};
  await page.route('http://chatbot.test/custom-chat', route => new Promise(resolve => {
    const query = route.request().postDataJSON().query;
    pending[query] = answer => route.fulfill({ json: { answer } }).then(resolve);
  }));
  await send(page, 'one');
  await send(page, 'two');
  await expect(page.locator('#chat-messages .bot-msg')).toHaveCount(2);
  pending.two('answer two');
  await expect(page.locator('#chat-messages .bot-msg').nth(1)).toHaveText('answer two');
  pending.one('answer one');
  await expect(page.locator('#chat-messages .bot-msg')).toHaveCount(2);
  await expect(page.locator('#chat-messages .bot-msg').nth(0)).toHaveText('answer one');
  await expect(page.locator('#chat-messages .bot-msg').nth(1)).toHaveText('answer two');
});

test('missing markup and blank submissions are harmless', async ({ page }) => {
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await fixture(page, false);
  await page.locator('body').click();
  expect(errors).toEqual([]);
  await fixture(page);
  await send(page, '   ');
  await expect(page.locator('#chat-messages')).toBeEmpty();
});

test('rejects null, array, missing, and empty answers and falls back to localhost', async ({ page }) => {
  await fixture(page);
  await page.evaluate(() => { window.chatApiUrl = ''; });
  const invalid = [null, [], {}, { answer: '' }];
  let index = 0;
  await page.route('http://localhost:8000/ask', route => route.fulfill({ json: invalid[index++] }));
  for (let i = 0; i < invalid.length; i++) {
    await send(page, `invalid ${i}`);
    await expect(page.locator('#chat-messages .bot-msg').last()).toHaveText('Không nhận được phản hồi hợp lệ.');
  }
  expect(index).toBe(invalid.length);
});

test('ignores Enter during IME composition and sends on ordinary Enter', async ({ page }) => {
  await fixture(page);
  let requestCount = 0;
  await page.route('http://chatbot.test/custom-chat', async route => {
    requestCount++;
    await route.fulfill({ json: { answer: 'ok' } });
  });
  await page.locator('#chat-input').fill('composing');
  await page.locator('#chat-input').dispatchEvent('keydown', { key: 'Enter', isComposing: true });
  await expect(page.locator('#chat-messages')).toBeEmpty();
  await page.locator('#chat-input').fill('ordinary');
  await page.locator('#chat-input').press('Enter');
  await expect(page.locator('#chat-messages .bot-msg')).toHaveText(['ok']);
  expect(requestCount).toBe(1);
});

test('clears its timeout after a successful response', async ({ page }) => {
  await page.clock.install();
  await fixture(page);
  await page.route('http://chatbot.test/custom-chat', route => route.fulfill({ json: { answer: 'done' } }));
  await send(page, 'quick');
  await expect(page.locator('#chat-messages .bot-msg')).toHaveText(['done']);
  await page.clock.fastForward(30000);
  await expect(page.locator('#chat-messages .bot-msg')).toHaveText(['done']);
});
