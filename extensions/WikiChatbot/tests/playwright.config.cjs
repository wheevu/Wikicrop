const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

module.exports = {
	testDir: path.join(__dirname, 'playwright'),
	testMatch: '**/*.spec.cjs',
	outputDir: process.env.WIKICROP_BROWSER_OUTPUT_DIR ||
		fs.mkdtempSync(path.join(os.tmpdir(), 'wikicrop-chatbot-browser-')),
	forbidOnly: true,
	retries: 0,
	workers: 1,
	reporter: 'list',
	use: {
		browserName: 'chromium',
		channel: process.env.WIKICROP_BROWSER_CHANNEL || undefined,
		trace: 'retain-on-failure',
		screenshot: 'only-on-failure'
	}
};
