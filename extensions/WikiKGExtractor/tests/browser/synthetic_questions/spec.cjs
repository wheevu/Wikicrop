/* Real-browser checks against a disposable synthetic wiki; never use a real site. */
const fs = require( 'node:fs' );
const path = require( 'node:path' );
const { test, expect } = require( '@playwright/test' );

const baseURL = process.env.WIKIKG_SYNTHETIC_URL;
if ( !baseURL || new URL( baseURL ).hostname !== '127.0.0.1' || new URL( baseURL ).protocol !== 'http:' ) {
	throw new Error( 'Use a disposable loopback synthetic wiki.' );
}
function privateFile( variable ) {
	const file = process.env[ variable ];
	if ( !file || !path.isAbsolute( file ) || fs.realpathSync( file ) !== file ) {
		throw new Error( 'Missing private fixture: ' + variable );
	}
	const info = fs.statSync( file );
	if ( !info.isFile() || info.uid !== process.getuid() || ( info.mode & 0o777 ) !== 0o600 ) {
		throw new Error( 'Fixture must be owner-only: ' + variable );
	}
	return fs.readFileSync( file, 'utf8' ).trim();
}
const password = privateFile( 'WIKIKG_SYNTHETIC_PASSWORD_FILE' );
const identities = JSON.parse( privateFile( 'WIKIKG_SYNTHETIC_IDENTITIES_FILE' ) );
const catalog = JSON.parse( privateFile( 'WIKIKG_SYNTHETIC_CATALOG_FILE' ) );
const scenarios = JSON.parse( fs.readFileSync( path.resolve( __dirname, '../../../demo/synthetic/questions/scenarios.json' ), 'utf8' ) );
const supported = catalog.questions.find( ( item ) => item.id === 'supported-compound' );
const supportedOracle = scenarios.scenarios.find( ( item ) => item.id === 'supported-compound' );
	if ( !supported || !supportedOracle || !identities.sources || typeof identities.sources !== 'object' ) {
	throw new Error( 'Supported scenario/catalog identities are missing.' );
}
test.use( { baseURL } );
const phase = process.env.WIKIKG_SYNTHETIC_BROWSER_PHASE || 'pre-source';

async function localOnly( context ) {
	await context.route( '**/*', ( route ) => new URL( route.request().url() ).origin === new URL( baseURL ).origin ?
		route.continue() : route.abort( 'blockedbyclient' ) );
}
async function login( page ) {
	await page.goto( '/w/index.php?title=Special:UserLogin' );
	await page.locator( '#wpName1' ).fill( 'Synthetic reviewer' );
	await page.locator( '#wpPassword1' ).fill( password );
	await page.locator( '#wpLoginAttempt' ).click();
	await expect( page.locator( '#pt-userpage' ) ).toContainText( 'Synthetic reviewer' );
}

test( 'signed-out visitor receives no question evidence', async ( { page, context } ) => {
	test.skip( phase !== 'pre-source' );
	await localOnly( context );
	await page.goto( '/w/index.php?title=Special:WikiKGSyntheticQuestions' );
	await expect( page.locator( '[data-wikikg-claim]' ) ).toHaveCount( 0 );
	await expect( page.locator( '#mw-content-text' ) ).toContainText( /permission|log in/i );
} );

test( 'reviewer gets supported compound result with exact evidence handles', async ( { page, context } ) => {
	test.skip( phase !== 'pre-source' );
	await localOnly( context );
	await login( page );
	await page.goto( '/w/index.php?title=Special:WikiKGSyntheticQuestions' );
	const form = page.locator( 'form' );
	await expect( form.getByLabel( 'Example question' ) ).toBeVisible();
	await form.getByLabel( 'Example question' ).selectOption( 'supported-compound' );
	await form.getByRole( 'button', { name: 'Run question', exact: true } ).click();
	const result = page.locator( '[data-wikikg-question-status]' );
	await expect( result ).toBeVisible();
	await expect( result ).toHaveAttribute( 'data-wikikg-question-status', 'ok' );
	await expect( result.locator( '[data-wikikg-generation]' ) ).toHaveAttribute( 'data-wikikg-generation', /[a-f0-9]{64}/ );
	await expect( result.locator( '[data-wikikg-root]' ).first() ).toBeVisible();
	await expect( result.locator( '[data-wikikg-branch]' ).first() ).toBeVisible();
	await expect( result.locator( '[data-wikikg-claim]' ).first() ).toHaveAttribute( 'data-wikikg-claim', /.+/ );
	const actualClaims = new Set( await result.locator( '[data-wikikg-claim]' ).evaluateAll( ( rows ) =>
		rows.map( ( row ) => row.getAttribute( 'data-wikikg-claim' ) ) ) );
	for ( const label of supportedOracle.expected.required_claim_labels ) {
		expect( actualClaims.has( identities.claim_labels[ label ] ), label ).toBeTruthy();
	}
	for ( const label of supportedOracle.expected.forbidden_claim_labels ) {
		expect( actualClaims.has( identities.claim_labels[ label ] ), label ).toBeFalsy();
	}
	expect( actualClaims ).toEqual( new Set( supportedOracle.expected.required_claim_labels.map( ( label ) =>
		identities.claim_labels[ label ] ) ) );
	const link = result.locator( 'a.wikikg-question-source' ).first();
	await expect( link ).toBeVisible();
	const url = new URL( await link.getAttribute( 'href' ), baseURL );
	expect( url.origin ).toBe( new URL( baseURL ).origin );
	expect( url.searchParams.has( 'oldid' ) ).toBeTruthy();
	const labelSource = new Map();
	for ( const article of scenarios.fixtures.articles ) {
		for ( const claim of article.claims ) {
			labelSource.set( claim.label, article.key );
		}
	}
	const expectedRevisions = new Set( supportedOracle.expected.required_claim_labels.map( ( label ) =>
		String( identities.sources[ labelSource.get( label ) ].revision_id ) ) );
	const actualRevisions = new Set( await result.locator( 'a.wikikg-question-source' ).evaluateAll( ( links ) =>
		links.map( ( anchor ) => new URL( anchor.href ).searchParams.get( 'oldid' ) ).filter( Boolean ) ) );
	for ( const revision of expectedRevisions ) {
		expect( actualRevisions.has( revision ), 'expected source revision ' + revision ).toBeTruthy();
	}
	expect( actualRevisions ).toEqual( expectedRevisions );
} );

test( 'the labelled question form submits by keyboard and without JavaScript', async ( { browser } ) => {
	test.skip( phase !== 'pre-source' );
	const context = await browser.newContext( { baseURL, javaScriptEnabled: false,
		viewport: { width: 390, height: 844 } } );
	try {
		await localOnly( context );
		const page = await context.newPage();
		await login( page );
		await page.goto( '/w/index.php?title=Special:WikiKGSyntheticQuestions' );
		await expect( page.locator( 'form' ) ).toBeVisible();
		expect( await page.evaluate( () => document.documentElement.scrollWidth ) ).toBeLessThanOrEqual( 390 );
		const select = page.getByLabel( 'Example question' );
		await select.selectOption( 'supported-compound' );
		await select.focus();
		await page.keyboard.press( 'Tab' );
		await page.keyboard.press( 'Enter' );
		await expect( page.locator( '[data-wikikg-question-status]' ) ).toHaveAttribute( 'data-wikikg-question-status', 'ok' );
	} finally {
		await context.close();
	}
} );

test( 'unknown selector and invalid CSRF token disclose no evidence', async ( { page, context } ) => {
	test.skip( phase !== 'pre-source' );
	await localOnly( context );
	await login( page );
	await page.goto( '/w/index.php?title=Special:WikiKGSyntheticQuestions' );
	const form = page.locator( 'form' );
	const action = await form.getAttribute( 'action' ) || page.url();
	const token = await form.locator( 'input[name="wpEditToken"]' ).inputValue();
	const unknown = await context.request.post( action, { form: { question: 'not-in-catalog', wpEditToken: token } } );
	const unknownHtml = await unknown.text();
	expect( unknownHtml ).not.toContain( 'data-wikikg-claim=' );
	expect( unknownHtml ).not.toContain( 'data-wikikg-root=' );
	const forged = await context.request.post( action, { form: { question: 'supported-compound', wpEditToken: 'invalid-token' } } );
	const forgedHtml = await forged.text();
	expect( forgedHtml ).not.toContain( 'data-wikikg-claim=' );
	expect( forgedHtml ).not.toContain( 'data-wikikg-root=' );
} );

test( 'uncertain numeric and opposing evidence remain diagnostics, not a positive answer', async ( { page, context } ) => {
	test.skip( phase !== 'pre-source' );
	await localOnly( context );
	await login( page );
	for ( const [ id, diagnostic ] of [
		[ 'numeric-uncertain', /uncertain|numeric/i ],
		[ 'opposing-claims', /oppos|conflict|susceptible/i ]
	] ) {
		await page.goto( '/w/index.php?title=Special:WikiKGSyntheticQuestions' );
		const form = page.locator( 'form' );
		await form.getByLabel( 'Example question' ).selectOption( id );
		await form.getByRole( 'button', { name: 'Run question', exact: true } ).click();
		const result = page.locator( '[data-wikikg-question-status]' );
		await expect( result ).toBeVisible();
		await expect( result ).not.toHaveAttribute( 'data-wikikg-question-status', 'ok' );
		await expect( result ).toContainText( diagnostic );
	}
} );

test( 'reviewer rejection invalidates the old question binding instead of using graph fallback', async ( { page, context } ) => {
	test.skip( phase !== 'pre-source' );
	await localOnly( context );
	await login( page );
	await page.goto( '/w/index.php?title=Special:WikiKGReview/' +
		JSON.parse( privateFile( 'WIKIKG_SYNTHETIC_IDENTITIES_FILE' ) ).rejected_snapshot_id );
	const review = page.locator( '.wikikgreview-form' );
	await expect( review ).toBeVisible();
	await expect( review.locator( 'input[name="expected_version"]' ) ).toHaveValue( '1' );
	await review.locator( 'input[name="decision"][value="rejected"]' ).check();
	await review.locator( 'textarea[name="reason"]' ).fill( 'Synthetic connected-question rejection test' );
	await review.locator( 'button[type="submit"]' ).click();
	await expect( page.locator( '#mw-content-text' ) ).toContainText( /Từ chối|rejected/i );
	await page.goto( '/w/index.php?title=Special:WikiKGSyntheticQuestions' );
	const form = page.locator( 'form' );
	await form.getByLabel( 'Example question' ).selectOption( 'supported-review-sensitive' );
	await form.getByRole( 'button', { name: 'Run question', exact: true } ).click();
	const result = page.locator( '[data-wikikg-question-status]' );
	await expect( result ).toHaveAttribute( 'data-wikikg-question-status', /dependency|refus|error/i );
	await expect( result.locator( '[data-wikikg-claim]' ) ).toHaveCount( 0 );
	await expect( result.locator( 'a.wikikg-question-source' ) ).toHaveCount( 0 );
} );

test( 'source edit after refreshed positive binding is denied by the original UI manifest', async ( { page, context } ) => {
	test.skip( phase !== 'source-change' );
	await localOnly( context );
	await login( page );
	const sourceIdentities = JSON.parse( privateFile( 'WIKIKG_SYNTHETIC_IDENTITIES_FILE' ) );
	const source = sourceIdentities.sources[ sourceIdentities.source_change_source_label ];
	await page.goto( '/w/index.php?title=' + encodeURIComponent( source.title ) + '&action=edit' );
	await expect( page.locator( '#wpTextbox1' ) ).toBeVisible();
	await page.locator( '#wpTextbox1' ).fill( 'Changed source revision for the synthetic graph authorization test.' );
	await page.locator( '#wpSave' ).click();
	await page.goto( '/w/index.php?title=Special:WikiKGSyntheticQuestions' );
	await page.locator( 'form' ).getByLabel( 'Example question' ).selectOption( 'supported-compound' );
	await page.locator( 'form' ).getByRole( 'button', { name: 'Run question', exact: true } ).click();
	await expect( page.locator( '[data-wikikg-question-status]' ) ).toHaveAttribute( 'data-wikikg-question-status', /dependency|refus|error/i );
	await expect( page.locator( '[data-wikikg-claim]' ) ).toHaveCount( 0 );
} );
