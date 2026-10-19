<?php
require '/var/www/html/w/maintenance/Maintenance.php';

use MediaWiki\Extension\WikiKGExtractor\ClaimReviewStore;
use MediaWiki\Maintenance\Maintenance;
use MediaWiki\Revision\SlotRecord;

class SyntheticMariaDbCompatibility extends Maintenance {
	public function execute() {
		$root = '/private/wikikg';
		if ( getenv( 'WIKIKG_SYNTHETIC' ) !== '1'
			|| getenv( 'MW_CONFIG_FILE' ) !== $root . '/LocalSettings.synthetic.php'
			|| !is_file( $root . '/synthetic-marker' )
			|| trim( file_get_contents( $root . '/synthetic-marker' ) ) !== 'fresh' ) {
			throw new RuntimeException( 'Synthetic fixture guard refused.' );
		}
		$services = $this->getServiceContainer();
		$config = $services->getMainConfig();
		$db = $services->getConnectionProvider()->getPrimaryDatabase();
		if ( $config->get( 'DBtype' ) !== 'mysql' || $config->get( 'DBname' ) !== 'synthetic_mariadb'
			|| $config->get( 'DBserver' ) !== 'db' || fileowner( $root ) !== posix_geteuid()
			|| ( fileperms( $root ) & 07777 ) !== 0700 ) {
			throw new RuntimeException( 'Synthetic database identity refused.' );
		}
		$tables = [ 'wikikg_snapshot', 'wikikg_source_page', 'wikikg_claim', 'wikikg_evidence', 'wikikg_review_event' ];
		foreach ( $tables as $table ) {
			if ( !$db->tableExists( $table, __METHOD__ ) ) {
				throw new RuntimeException( 'Extension table missing after update.' );
			}
			$primaryIndex = $db->indexInfo( $table, 'PRIMARY', __METHOD__ );
			if ( !$primaryIndex ) {
				throw new RuntimeException( 'Extension table index missing after update.' );
			}
		}
		foreach ( [
			'wikikg_snapshot' => 'wikikg_snapshot_created',
			'wikikg_evidence' => 'wikikg_evidence_revision',
			'wikikg_review_event' => 'wikikg_review_reviewer_timestamp',
		] as $table => $index ) {
			if ( !$db->indexInfo( $table, $index, __METHOD__ ) ) {
				throw new RuntimeException( 'Extension secondary index missing after update.' );
			}
		}
		foreach ( $tables as $table ) {
			if ( $db->newSelectQueryBuilder()->select( '*' )->from( $table )->caller( __METHOD__ )->fetchRowCount() !== 0 ) {
				throw new RuntimeException( 'Fixture requires empty extension claim tables.' );
			}
		}
		if ( $db->newSelectQueryBuilder()->select( '*' )->from( 'page' )->caller( __METHOD__ )->fetchRowCount() !== 1 ) {
			throw new RuntimeException( 'Fixture requires fresh installed wiki.' );
		}
		$db->query( 'ALTER TABLE ' . $db->tableName( 'page' ) . ' AUTO_INCREMENT = 1000001', __METHOD__ );
		$db->query( 'ALTER TABLE ' . $db->tableName( 'revision' ) . ' AUTO_INCREMENT = 1000001', __METHOD__ );
		$user = $services->getUserFactory()->newFromName( 'Synthetic reviewer' );
		$title = Title::newFromText( 'MariaDB compatibility crop' );
		$page = $services->getWikiPageFactory()->newFromTitle( $title );
		$updater = $page->newPageUpdater( $user );
		$text = 'Synthetic statement: thu hoạch 97 ngày.';
		$updater->setContent( SlotRecord::MAIN, ContentHandler::makeContent( $text, $title ) );
		$revision = $updater->saveRevision( CommentStoreComment::newUnsavedComment( 'Synthetic compatibility fixture' ), 0 );
		if ( !$revision || !$updater->getStatus()->isOK() || $revision->getPageId() < 1000001 || $revision->getId() < 1000001 ) {
			throw new RuntimeException( 'Synthetic page/revision save failed.' );
		}
		$pageId = $revision->getPageId();
		$revisionId = $revision->getId();
		$contentHash = hash( 'sha256', $text );
		$span = 'thu hoạch 97 ngày';
		$claimId = hash( 'sha256', 'mariadb-claim' );
		$snapshotId = hash( 'sha256', 'mariadb-snapshot' );
		$source = [ 'title' => $title->getPrefixedText(), 'page_id' => $pageId, 'revision_id' => $revisionId, 'content_hash' => $contentHash ];
		$claim = [
			'claim_id' => $claimId, 'snapshot_id' => $snapshotId, 'claim_type' => 'property',
			'subject' => [ 'type' => 'Crop', 'id' => 'MariaDB compatibility crop' ], 'predicate' => 'GROWTH_DURATION',
			'value' => '97 days', 'qualifiers' => [], 'traceability_complete' => true, 'review' => [ 'status' => 'pending' ],
			'evidence' => [ [
				'evidence_id' => hash( 'sha256', 'mariadb-evidence' ), 'page_id' => $pageId,
				'revision_id' => $revisionId, 'content_hash' => $contentHash, 'page_title' => $title->getPrefixedText(),
				'extraction_method' => 'rules', 'extractor_version' => '1.0', 'location_type' => 'source_span',
				'source_offset_start' => mb_strpos( $text, $span, 0, 'UTF-8' ),
				'source_offset_end' => mb_strpos( $text, $span, 0, 'UTF-8' ) + mb_strlen( $span, 'UTF-8' ),
				'supporting_span' => $span, 'span_hash' => hash( 'sha256', $span ),
			] ],
		];
		$document = [ 'schema_version' => '1.0', 'metadata' => [
			'extractor_version' => '1.0', 'extraction_method' => 'rules', 'review_state' => 'pending', 'source_pages' => [ $source ],
		], 'claims' => [ $claim ] ];
		$store = new ClaimReviewStore( $services->getConnectionProvider() );
		if ( $store->importVerified( $document ) !== [ $snapshotId ] || $store->importVerified( $document ) !== [ $snapshotId ] ) {
			throw new RuntimeException( 'Import idempotence failed.' );
		}
		$collision = $document;
		$collision['claims'][0]['value'] = '98 days';
		try {
			$store->importVerified( $collision );
			throw new RuntimeException( 'Snapshot collision was accepted.' );
		} catch ( UnexpectedValueException $e ) {
			// Expected changed-data collision.
		}
		$review = $store->getReview( $snapshotId );
		if ( !$review || $review['latest_review']['status'] !== 'pending'
			|| $review['evidence'][0]['content_hash'] !== $contentHash
			|| $review['evidence'][0]['span_hash'] !== hash( 'sha256', $span )
			|| $review['evidence'][0]['supporting_span'] !== $span
			|| $review['evidence'][0]['source_offset_start'] !== mb_strpos( $text, $span, 0, 'UTF-8' )
			|| $review['evidence'][0]['source_offset_end'] !== mb_strpos( $text, $span, 0, 'UTF-8' ) + mb_strlen( $span, 'UTF-8' )
			|| $review['metadata']['source_pages'] !== [ $source ] ) {
			throw new RuntimeException( 'Stored source/evidence invariants failed.' );
		}
		if ( !$store->recordDecision( $snapshotId, $user->getId(), 'approved', 'Synthetic approved check.', 0 )
			|| !$store->recordDecision( $snapshotId, $user->getId(), 'rejected', 'Synthetic rejected check.', 1 )
			|| $store->recordDecision( $snapshotId, $user->getId(), 'approved', 'Stale check.', 1 ) !== null ) {
			throw new RuntimeException( 'Versioned decision checks failed.' );
		}
		$review = $store->getReview( $snapshotId );
		if ( count( $review['review_events'] ) !== 2
			|| array_column( $review['review_events'], 'status' ) !== [ 'approved', 'rejected' ]
			|| $review['latest_review']['version'] !== 2
			|| $review['latest_review']['status'] !== 'rejected' ) {
			throw new RuntimeException( 'Append-only review history failed.' );
		}
		$this->output( json_encode( [ 'server_version' => $db->getServerVersion(), 'tables' => $tables,
			'page_id' => $pageId, 'revision_id' => $revisionId, 'store_cases' => [ 'idempotent_import', 'collision_rejected', 'pending_read', 'approved_v0', 'rejected_v1', 'stale_refused', 'history_invariants' ] ] ) . "\n" );
	}
}
$maintClass = SyntheticMariaDbCompatibility::class;
require_once RUN_MAINTENANCE_IF_MAIN;
