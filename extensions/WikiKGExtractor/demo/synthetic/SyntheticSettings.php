<?php
if ( getenv( 'WIKIKG_SYNTHETIC' ) !== '1' || !defined( 'MEDIAWIKI' )
	|| !is_dir( '/private/wikikg' ) || realpath( '/private/wikikg' ) !== '/private/wikikg' ) {
	throw new RuntimeException( 'Synthetic-only settings refused outside the isolated runtime.' );
}

$wgWikiKGSyntheticVerificationRoot = '/private/wikikg';
$wgServer = 'http://127.0.0.1:' . ( getenv( 'WIKIKG_HTTP_PORT' ) ?: '8787' );
$wgScriptPath = '/w';
$wgArticlePath = '/w/index.php?title=$1';
$wgSQLiteDataDir = '/private/wikikg/db';
$wgDBtype = 'sqlite';
$wgDBname = 'syntheticwiki';
$wgDBserver = '';
$wgDBuser = '';
$wgDBpassword = '';
$wgDBprefix = '';
$wgEnableUploads = false;
$wgGroupPermissions['*']['read'] = true;
$wgGroupPermissions['*']['edit'] = false;
$wgGroupPermissions['user']['read'] = true;
$wgGroupPermissions['sysop']['wikikgextractor-review'] = true;
$wgGroupPermissions['sysop']['read'] = true;
$wgGroupPermissions['sysop']['edit'] = true;
$wgGroupPermissions['sysop']['upload'] = false;
$wgGroupPermissions['sysop']['delete'] = false;
$wgSecretKey = hash( 'sha256', 'synthetic-only-' . file_get_contents( '/private/wikikg/secret-key' ) );
$wgUpgradeKey = '';
$wgDefaultSkin = 'vector';
wfLoadSkin( 'Vector' );
$wgLanguageCode = 'en';
$wgMainCacheType = CACHE_NONE;
$wgMessageCacheType = CACHE_NONE;
$wgParserCacheType = CACHE_NONE;
$wgJobRunRate = 0;
$wgCacheDirectory = '/private/wikikg/wiki-cache';
$wgWikiKGExtractorEnableKG = false;
$wgWikiKGExtractorGeminiApiKey = '';
$wgWikiKGExtractorPushToNeo4j = false;
$wgWikiKGExtractorOutputDirectory = '/private/wikikg/exports';
$wgWikiKGExtractorOutputBaseUrl = '';
$wgWikiKGExtractorPythonCommand = '/usr/local/bin/python3';

// The authorization worker toggles this private marker between PHP processes.
$denialMarker = '/private/wikikg/permission-denied';
if ( is_file( $denialMarker ) && trim( (string)file_get_contents( $denialMarker ) ) === 'deny' ) {
	$wgGroupPermissions['*']['read'] = false;
	$wgGroupPermissions['user']['read'] = false;
	$wgGroupPermissions['sysop']['read'] = false;
	$wgGroupPermissions['sysop']['wikikgextractor-review'] = false;
}

if ( getenv( 'WIKIKG_SYNTHETIC_TEST' ) === '1' ) {
	$wgSQLiteDataDir = '/private/wikikg/test-db';
	$wgDBname = 'wikitest';
	$wgCacheDirectory = '/private/wikikg/test-cache';
	$wgTmpDirectory = '/private/wikikg/test-temp';
}
