const fs = require( 'node:fs' );
const path = require( 'node:path' );
process.umask( 0o077 );
const root = process.env.WIKIKG_SYNTHETIC_BROWSER_ROOT;
if ( !root || !path.isAbsolute( root ) || fs.realpathSync( root ) !== root ) {
	throw new Error( 'Set WIKIKG_SYNTHETIC_BROWSER_ROOT to an existing private root.' );
}
const info = fs.statSync( root );
if ( !info.isDirectory() || info.uid !== process.getuid() || ( info.mode & 0o777 ) !== 0o700 ) {
	throw new Error( 'Browser artifacts require an owner-only root.' );
}
const output = process.env.WIKIKG_SYNTHETIC_BROWSER_OUTPUT;
if ( !output || !path.isAbsolute( output ) || !output.startsWith( root + path.sep ) ||
	fs.realpathSync( path.dirname( output ) ) !== path.dirname( output ) ) {
	throw new Error( 'Set an owner-only output directory beneath the private root.' );
}
module.exports = {
	testDir: __dirname,
	testMatch: 'spec.cjs',
	outputDir: path.join( output, 'artifacts' ),
	workers: 1,
	retries: 0,
	forbidOnly: true,
	timeout: 90000,
	use: { browserName: 'chromium', headless: true, serviceWorkers: 'block',
		viewport: { width: 1280, height: 900 }, screenshot: 'only-on-failure', trace: 'retain-on-failure' },
	reporter: [ [ 'list' ], [ 'json', { outputFile: path.join( output, 'report.json' ) } ] ]
};
