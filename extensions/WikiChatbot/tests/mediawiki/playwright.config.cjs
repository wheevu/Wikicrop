const fs = require('node:fs');
const path = require('node:path');
process.umask(0o077);
const root = process.env.ARTIFACT_ROOT;
const repository = path.resolve(__dirname, '../../../..');
if (!root || !path.isAbsolute(root) || fs.realpathSync(root) !== root ||
    (fs.statSync(root).mode & 0o777) !== 0o700 || fs.statSync(root).uid !== process.getuid() ||
    root === repository || root.startsWith(repository + path.sep) ||
    root === '/var/www/html' || root.startsWith('/var/www/html/')) {
  throw new Error('ARTIFACT_ROOT must be an owner-only directory.');
}
// Playwright clears outputDir on startup. Never point it at a previous run.
const output = fs.mkdtempSync(path.join(root, 'browser-run-'));
module.exports = {
  testDir: __dirname,
  testMatch: 'chatbot.spec.ts',
  timeout: 90000,
  forbidOnly: true,
  retries: 0,
  workers: 1,
  outputDir: path.join(output, 'results'),
  use: { headless: true, channel: 'chrome', trace: 'retain-on-failure', screenshot: 'only-on-failure' },
  reporter: [['list'], ['json', { outputFile: path.join(output, 'report.json') }]],
};
