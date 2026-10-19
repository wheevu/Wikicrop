<?php
require_once '/var/www/html/w/maintenance/Maintenance.php';

use MediaWiki\Maintenance\Maintenance;
use MediaWiki\Content\TextContent;
use MediaWiki\Revision\SlotRecord;
use MediaWiki\MediaWikiServices;
use MediaWiki\Revision\RevisionRecord;
use MediaWiki\Extension\WikiKGExtractor\ClaimReviewStore;

class SyntheticRestoreCapture extends Maintenance {
	public function execute() {
		$root = '/private/wikikg';
		if ( getenv( 'WIKIKG_SYNTHETIC' ) !== '1'
			|| getenv( 'MW_CONFIG_FILE' ) !== $root . '/LocalSettings.synthetic.php'
			|| !is_file( $root . '/synthetic-marker' )
			|| trim( file_get_contents( $root . '/synthetic-marker' ) ) !== 'seeded'
			|| !is_file( $root . '/LocalSettings.synthetic.php' )
			|| !is_file( $root . '/db/syntheticwiki.sqlite' )
		) {
			throw new RuntimeException( 'Synthetic restore capture refused.' );
		}
		$services = MediaWikiServices::getInstance();
		$config = $services->getMainConfig();
		if ( $config->get( 'DBtype' ) !== 'sqlite' || $config->get( 'DBname' ) !== 'syntheticwiki'
			|| realpath( $config->get( 'SQLiteDataDir' ) ) !== $root . '/db'
			|| fileowner( $root ) !== posix_geteuid() || ( fileperms( $root ) & 07777 ) !== 0700 ) {
			throw new RuntimeException( 'Synthetic database identity refused.' );
		}
		$store = new ClaimReviewStore( $services->getConnectionProvider() );
		$ids = [];
		$cursor = null;
		do {
			$page = $store->listSnapshotIdsAfter( $cursor, 100 );
			foreach ( $page as $id ) {
				$ids[] = $id;
				$cursor = $id;
			}
		} while ( count( $page ) === 100 );
		$records = [];
		$sources = [];
		$pages = $services->getWikiPageFactory();
		foreach ( $ids as $id ) {
			$record = $store->getReview( $id );
			if ( $record === null ) {
				throw new RuntimeException( 'Snapshot disappeared during capture.' );
			}
			$records[] = $record;
			foreach ( $record['evidence'] as $evidence ) {
				$key = $evidence['page_id'] . ':' . $evidence['revision_id'];
				if ( isset( $sources[$key] ) ) {
					continue;
				}
				$revision = $services->getRevisionLookup()->getRevisionById( $evidence['revision_id'] );
				$page = $pages->newFromID( $evidence['page_id'] );
				if ( !$revision || !$page || $revision->getPageId() !== $evidence['page_id']
					|| $page->getId() !== $evidence['page_id'] || $page->getLatest() !== $evidence['revision_id']
				) {
					throw new RuntimeException( 'Source identity is not the current page revision.' );
				}
				$content = $revision->getContent( SlotRecord::MAIN, RevisionRecord::RAW );
				if ( !$content instanceof TextContent ) {
					throw new RuntimeException( 'Source revision content unavailable.' );
				}
				$text = $content->getText();
				if ( !is_string( $text ) || hash( 'sha256', $text ) !== $evidence['content_hash'] ) {
					throw new RuntimeException( 'Actual revision content differs from the stored evidence.' );
				}
				$sources[$key] = [ 'page_id' => $evidence['page_id'], 'revision_id' => $evidence['revision_id'],
					'content_hash' => hash( 'sha256', $text ) ];
			}
		}
		ksort( $sources, SORT_STRING );
		print json_encode( [ 'records' => $records, 'sources' => array_values( $sources ) ],
			JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE | JSON_THROW_ON_ERROR );
	}
}

$maintClass = SyntheticRestoreCapture::class;
require_once RUN_MAINTENANCE_IF_MAIN;
