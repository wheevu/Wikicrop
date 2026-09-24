<?php

namespace MediaWiki\Extension\WikiKGExtractor;

use MediaWiki\Installer\DatabaseUpdater;
use MediaWiki\Installer\Hook\LoadExtensionSchemaUpdatesHook;

final class ClaimReviewSchemaHooks implements LoadExtensionSchemaUpdatesHook {
	/**
	 * Register each claim-review table separately so reruns can repair partial installs.
	 *
	 * @param DatabaseUpdater $updater
	 */
	public function onLoadExtensionSchemaUpdates( $updater ) {
		$dbType = $updater->getDB()->getType();
		if ( !in_array( $dbType, [ 'mysql', 'sqlite' ], true ) ) {
			return;
		}

		$schemaDir = dirname( __DIR__ ) . "/sql/$dbType";
		foreach ( [
			'wikikg_snapshot',
			'wikikg_source_page',
			'wikikg_claim',
			'wikikg_evidence',
			'wikikg_review_event',
		] as $table ) {
			$updater->addExtensionTable( $table, "$schemaDir/$table-generated.sql" );
		}

		if ( $dbType === 'sqlite' ) {
			// addExtensionTable skips a complete patch when the table already exists.
			// Register SQLite indexes separately so reruns repair interrupted table patches.
			foreach ( [
				[ 'wikikg_snapshot', 'wikikg_snapshot_created' ],
				[ 'wikikg_evidence', 'wikikg_evidence_revision' ],
				[ 'wikikg_review_event', 'wikikg_review_reviewer_timestamp' ],
			] as [ $table, $index ] ) {
				$updater->addExtensionIndex( $table, $index, "$schemaDir/patch-$index.sql" );
			}
		}
	}
}
