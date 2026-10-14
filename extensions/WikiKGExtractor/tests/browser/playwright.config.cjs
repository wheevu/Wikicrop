const fs = require( 'node:fs' );
const path = require( 'node:path' );

// Never clear a previous run or put session artifacts in the checkout.
process.umask( 0o077 );
const privateRoot = process.env.WIKIKG_SYNTHETIC_BROWSER_ROOT;
if ( !privateRoot || !path.isAbsolute( privateRoot ) ||
	fs.realpathSync( privateRoot ) !== privateRoot ) {
	throw new Error( 'Set WIKIKG_SYNTHETIC_BROWSER_ROOT to an existing private directory.' );
}
const info = fs.statSync( privateRoot );
const repository = path.resolve( __dirname, '../../../..' );
if ( !info.isDirectory() || ( info.mode & 0o777 ) !== 0o700 ||
	info.uid !== process.getuid() || privateRoot === repository ||
	privateRoot.startsWith( repository + path.sep ) ) {
	throw new Error( 'Browser artifacts require an owner-only directory outside the checkout.' );
}
const output = fs.mkdtempSync( path.join( privateRoot, 'browser-run-' ) );

module.exports = {
	testDir: __dirname,
	testMatch: '*.spec.cjs',
	outputDir: path.join( output, 'results' ),
	workers: 1,
	retries: 0,
	forbidOnly: true,
	timeout: 90000,
	use: {
		browserName: 'chromium',
		headless: true,
		serviceWorkers: 'block',
		viewport: { width: 1440, height: 1000 },
		screenshot: 'only-on-failure',
		trace: 'retain-on-failure'
	},
	reporter: [ [ 'list' ], [ 'json', { outputFile: path.join( output, 'report.json' ) } ] ]
};
