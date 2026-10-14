/* A disposable synthetic wiki is required. Never run this against a real site. */
const fs = require( 'node:fs' );
const path = require( 'node:path' );
const { test, expect } = require( '@playwright/test' );

const baseURL = process.env.WIKIKG_SYNTHETIC_URL;
if ( !baseURL || new URL( baseURL ).hostname !== '127.0.0.1' ||
	new URL( baseURL ).protocol !== 'http:' || new URL( baseURL ).username ||
	new URL( baseURL ).password || new URL( baseURL ).pathname !== '/' ||
	new URL( baseURL ).search || new URL( baseURL ).hash ) {
	throw new Error( 'Use a disposable loopback wiki and its synthetic password file.' );
}

function privateFile( variable ) {
	const file = process.env[ variable ];
	if ( !file || !path.isAbsolute( file ) || fs.realpathSync( file ) !== file ) {
		throw new Error( 'Set ' + variable + ' to a private, non-symlink fixture file.' );
	}
	const info = fs.statSync( file );
	const parent = fs.statSync( path.dirname( file ) );
	if ( !info.isFile() || info.size > 8000000 || ( info.mode & 0o777 ) !== 0o600 ||
		( parent.mode & 0o777 ) !== 0o700 || info.uid !== process.getuid() ||
		parent.uid !== process.getuid() ) {
		throw new Error( variable + ' requires owner-only fixture storage.' );
	}
	return fs.readFileSync( file, 'utf8' );
}

const password = privateFile( 'WIKIKG_SYNTHETIC_PASSWORD_FILE' ).trim();
const seeds = JSON.parse( privateFile( 'WIKIKG_SYNTHETIC_IDENTITIES_FILE' ) );
const manifest = JSON.parse( privateFile( 'WIKIKG_SYNTHETIC_MANIFEST_FILE' ) );
const swift = seeds.find( ( seed ) => seed.title === 'Verification crop Swift' );
if ( !swift ) {
	throw new Error( 'The synthetic Swift source identity is missing.' );
}
const membership = manifest.claims.find( ( claim ) => claim.predicate === 'HAS_VARIETY' &&
	claim.evidence.some( ( evidence ) => evidence.page_id === swift.page_id ) );
if ( !membership || manifest.claims.length !== 9 ||
	!membership.evidence.some( ( evidence ) => evidence.revision_id === swift.revision_id &&
		evidence.content_hash === swift.content_hash ) ) {
	throw new Error( 'Use the completed, unchanged synthetic workflow fixtures.' );
}

// Charter: Synthetic reviewer starts with nine approved fixture snapshots.
// Click the rendered Swift membership edge, follow its claim and review links,
// and open exactly the seed's source revision. Background click resets the panel.
// The keyboard/table route must also work without JavaScript; signed-out access
// must disclose no claims. Stop at the revision, without changing review state.
// Manifest plus seed IDs/hashes are the oracle, not the current DOM or a numeric URL.
test.use( { baseURL } );

async function localRequestsOnly( context ) {
	await context.route( '**/*', ( route ) => {
		if ( new URL( route.request().url() ).origin === new URL( baseURL ).origin ) {
			return route.continue();
		}
		return route.abort( 'blockedbyclient' );
	} );
}

test.beforeEach( async ( { context } ) => localRequestsOnly( context ) );

async function login( page ) {
	await page.goto( '/w/index.php?title=Special:UserLogin' );
	await page.locator( '#wpName1' ).fill( 'Synthetic reviewer' );
	await page.locator( '#wpPassword1' ).fill( password );
	await page.locator( '#wpLoginAttempt' ).click();
	await expect( page.locator( '#pt-userpage' ) ).toContainText( 'Synthetic reviewer' );
}

async function clickSwiftEdge( page ) {
	const graph = page.locator( '[data-wikikg-graph]' );
	await graph.scrollIntoViewIfNeeded();
	await expect( graph ).toHaveAttribute( 'data-wikikg-ready', '1' );
	// Canvas edges have no DOM locator. Read their geometry, then use a real
	// pointer event. Never select the edge or emit a Cytoscape event in test code.
	const point = await graph.evaluate( ( element, snapshot ) => {
		const edge = element._cyreg.cy.edges().filter( ( candidate ) =>
			candidate.data( 'supporting_snapshot_ids' ).includes( snapshot ) ).first();
		return edge.renderedMidpoint();
	}, membership.snapshot_id );
	const box = await graph.boundingBox();
	expect( box ).not.toBeNull();
	expect( point.x ).toBeGreaterThan( 0 );
	expect( point.x ).toBeLessThan( box.width );
	expect( point.y ).toBeGreaterThan( 0 );
	expect( point.y ).toBeLessThan( box.height );
	await page.mouse.click( box.x + point.x, box.y + point.y );
}

function exactRevisionURL( href ) {
	const url = new URL( href, baseURL );
	expect( url.origin ).toBe( new URL( baseURL ).origin );
	expect( url.pathname ).toBe( '/w/index.php' );
	expect( url.searchParams.get( 'oldid' ) ).toBe( String( swift.revision_id ) );
	return url.href;
}

test( 'reviewer clicks a rendered edge and follows its exact claim and source revision', async ( { page } ) => {
	const errors = [];
	page.on( 'pageerror', ( error ) => errors.push( error.message ) );
	await login( page );
	await page.goto( '/w/index.php?title=Special:WikiKGReviewedGraph' );
	const claims = page.locator( '[data-wikikg-snapshot-id]' );
	// Only relationship rows carry the snapshot attribute; the nine approved
	// fixture claims are five relationships and four properties.
	await expect( claims ).toHaveCount( 5 );
	await expect( page.locator( '[data-wikikg-graph]' ) ).toHaveAttribute( 'data-wikikg-ready', '1' );
	const panel = page.locator( '[data-wikikg-edge-evidence]' );
	await expect( panel ).toContainText( 'Select an edge' );
	await clickSwiftEdge( page );
	const claimLink = panel.getByRole( 'link', { name: 'Open claim row', exact: true } );
	await expect( claimLink ).toHaveAttribute( 'href', '#wikikg-reviewed-claim-' + membership.snapshot_id );
	await claimLink.focus();
	await page.keyboard.press( 'Enter' );
	await expect( page ).toHaveURL( new RegExp( '#wikikg-reviewed-claim-' + membership.snapshot_id + '$' ) );
	const row = page.locator( '#wikikg-reviewed-claim-' + membership.snapshot_id );
	await expect( row ).toBeVisible();
	const revisionLink = row.locator( 'a[href*="oldid="]' ).first();
	const revisionURL = exactRevisionURL( await revisionLink.getAttribute( 'href' ) );
	await row.getByRole( 'link', { name: 'Open review', exact: true } ).click();
	await expect( page ).toHaveURL( new RegExp( membership.snapshot_id ) );
	await expect( page.locator( '.wikikgreview-form' ) ).toBeVisible();
	await page.goto( revisionURL );
	await expect( page ).toHaveURL( revisionURL );
	await expect( page.locator( '#mw-content-text' ) ).toContainText( 'Giống tổng hợp Swift' );
	await page.goto( '/w/index.php?title=Special:WikiKGReviewedGraph' );
	await expect( page.locator( '[data-wikikg-graph]' ) ).toHaveAttribute( 'data-wikikg-ready', '1' );
	await clickSwiftEdge( page );
	await expect( panel.getByRole( 'link', { name: 'Open claim row', exact: true } ) ).toBeVisible();
	await page.locator( '[data-wikikg-graph]' ).click( { position: { x: 8, y: 8 } } );
	await expect( panel ).toContainText( 'Select an edge' );
	await page.screenshot( { path: test.info().outputPath( 'reviewed-graph.png' ), fullPage: true } );
	expect( errors ).toEqual( [] );
} );

test( 'claim table and revision links work without JavaScript', async ( { browser } ) => {
	const context = await browser.newContext( { baseURL, javaScriptEnabled: false } );
	try {
		await localRequestsOnly( context );
		const page = await context.newPage();
		await login( page );
		await page.goto( '/w/index.php?title=Special:WikiKGReviewedGraph' );
		await expect( page.locator( '[data-wikikg-snapshot-id]' ) ).toHaveCount( 5 );
		await expect( page.locator( '.wikikg-graph-visual' ) ).toBeHidden();
		const revision = page.locator( '#wikikg-reviewed-claim-' + membership.snapshot_id )
			.locator( 'a[href*="oldid="]' ).first();
		const revisionURL = exactRevisionURL( await revision.getAttribute( 'href' ) );
		await revision.focus();
		await page.keyboard.press( 'Enter' );
		await expect( page ).toHaveURL( revisionURL );
		await expect( page.locator( '#mw-content-text' ) ).toContainText( 'Giống tổng hợp Swift' );
	} finally {
		await context.close();
	}
} );

test( 'signed-out visitors cannot see graph claims', async ( { page } ) => {
	await page.goto( '/w/index.php?title=Special:WikiKGReviewedGraph' );
	await expect( page.locator( '[data-wikikg-snapshot-id]' ) ).toHaveCount( 0 );
	await expect( page.locator( '#mw-content-text' ) ).toContainText( /permission|log in/i );
} );
