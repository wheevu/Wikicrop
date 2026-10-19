<?php
if ( getenv( 'WIKIKG_SYNTHETIC' ) !== '1' || !defined( 'MEDIAWIKI' ) ) {
	throw new RuntimeException( 'Synthetic-only settings refused.' );
}
$root = '/private/wikikg';
$expected = getenv( 'WIKIKG_SYNTHETIC_ROOT' );
if ( $expected !== $root || realpath( $root ) !== $root || fileowner( $root ) !== posix_geteuid()
	|| ( fileperms( $root ) & 07777 ) !== 0700 ) {
	throw new RuntimeException( 'Synthetic root identity refused.' );
}
$passwordFile = getenv( 'WIKIKG_DB_PASSWORD_FILE' );
if ( $passwordFile !== $root . '/db-password' || !is_file( $passwordFile ) ) {
	throw new RuntimeException( 'Synthetic database credential refused.' );
}
$wgServer = 'http://localhost';
$wgScriptPath = '/w';
$wgDBtype = 'mysql';
$wgDBserver = 'db';
$wgDBname = 'synthetic_mariadb';
$wgDBuser = 'wikikg_synthetic';
$wgDBpassword = trim( file_get_contents( $passwordFile ) );
$wgDBprefix = '';
$wgEnableUploads = false;
$wgSecretKey = hash( 'sha256', 'synthetic-' . trim( file_get_contents( $root . '/secret-key' ) ) );
$wgUpgradeKey = '';
$wgMainCacheType = CACHE_NONE;
$wgMessageCacheType = CACHE_NONE;
$wgParserCacheType = CACHE_NONE;
$wgJobRunRate = 0;
$wgWikiKGExtractorEnableKG = false;
$wgWikiKGExtractorPushToNeo4j = false;
wfLoadExtension( 'WikiKGExtractor' );
