<?php
require '/var/www/html/w/maintenance/Maintenance.php';

use MediaWiki\Maintenance\Maintenance;
use MediaWiki\Revision\SlotRecord;

class SeedSyntheticVerification extends Maintenance {
	public function execute() {
		$private = '/private/wikikg';
		if ( getenv( 'WIKIKG_SYNTHETIC' ) !== '1'
			|| getenv( 'MW_CONFIG_FILE' ) !== $private . '/LocalSettings.synthetic.php'
			|| !is_file( $private . '/synthetic-marker' )
			|| trim( file_get_contents( $private . '/synthetic-marker' ) ) !== 'fresh' ) {
			throw new RuntimeException( 'Synthetic fixture guard refused.' );
		}
		$services = $this->getServiceContainer();
		$config = $services->getMainConfig();
		if ( $config->get( 'DBtype' ) !== 'sqlite' || $config->get( 'DBname' ) !== 'syntheticwiki'
			|| realpath( $config->get( 'SQLiteDataDir' ) ) !== $private . '/db'
			|| fileowner( $private ) !== posix_geteuid() || ( fileperms( $private ) & 07777 ) !== 0700 ) {
			throw new RuntimeException( 'Synthetic database identity refused.' );
		}
		$db = $services->getConnectionProvider()->getPrimaryDatabase();
		if ( $db->newSelectQueryBuilder()->select( '*' )->from( 'page' )->caller( __METHOD__ )->fetchRowCount() !== 1 ) {
			throw new RuntimeException( 'Synthetic seed requires a fresh installed wiki.' );
		}
		$user = $services->getUserFactory()->newFromName( 'Synthetic reviewer' );
		$services->getUserGroupManager()->addUserToGroup( $user, 'sysop', null, true );
		$db->query( "UPDATE sqlite_sequence SET seq = 1000000 WHERE name IN ('page', 'revision')", __METHOD__ );
		$db->query( "CREATE TABLE IF NOT EXISTS approve_queue (aq_page_id INTEGER NOT NULL, aq_revision_id INTEGER NOT NULL, aq_status TEXT NOT NULL, aq_is_latest INTEGER NOT NULL)", __METHOD__ );
		$fixtures = [
			'Verification crop' => [ 'species', 'Verification crop', 'Nguồn tổng hợp để kiểm tra phần mềm.' ],
			'Verification crop Swift' => [ 'variety', 'Verification crop', 'Giống tổng hợp Swift có thời gian sinh trưởng 95-98 ngày, năng suất 4-5,5 tấn/ha. Giống kháng bệnh đạo ôn.' ],
			'Verification crop Range' => [ 'variety', 'Verification crop', 'Giống tổng hợp Range có thời gian sinh trưởng 95-105 ngày. Giống dễ nhiễm bệnh đạo ôn.' ],
			'Other crop' => [ 'species', 'Other crop', 'Một cây trồng hoàn toàn tổng hợp khác.' ],
			'Other crop Later' => [ 'variety', 'Other crop', 'Giống tổng hợp Later có thời gian sinh trưởng 120 ngày.' ],
			'Unclassified fixture' => [ null, null, 'Không có phân loại, chỉ dùng kiểm tra lịch thu thập.' ],
			'Verification alias' => [ null, null, '#REDIRECT [[Verification crop Swift]]' ],
		];
		$identities = [];
		$classifications = [];
		foreach ( $fixtures as $name => [ $kind, $crop, $text ] ) {
			$title = Title::newFromText( $name );
			$page = $services->getWikiPageFactory()->newFromTitle( $title );
			$updater = $page->newPageUpdater( $user );
			$updater->setContent( SlotRecord::MAIN, ContentHandler::makeContent( $text, $title ) );
			$revision = $updater->saveRevision( CommentStoreComment::newUnsavedComment( 'Synthetic software fixture only' ), 0 );
			if ( !$revision || !$updater->getStatus()->isOK() ) {
				throw new RuntimeException( 'Synthetic seed edit failed.' );
			}
			$identity = [ 'title' => $name, 'page_id' => $revision->getPageId(), 'revision_id' => $revision->getId(), 'content_hash' => hash( 'sha256', $text ) ];
			$identities[] = $identity;
			if ( $kind !== null ) {
				$classifications[$name] = [ 'kind' => $kind, 'crop' => $crop ];
				$db->newInsertQueryBuilder()->insertInto( 'approve_queue' )->row( [ 'aq_page_id' => $identity['page_id'], 'aq_revision_id' => $identity['revision_id'], 'aq_status' => 'approved', 'aq_is_latest' => 1 ] )->caller( __METHOD__ )->execute();
			}
		}
		foreach ( [ 'seed-identities.json' => $identities, 'classifications.json' => $classifications ] as $name => $value ) {
			file_put_contents( $private . '/' . $name, json_encode( $value, JSON_UNESCAPED_UNICODE | JSON_THROW_ON_ERROR ) );
			chmod( $private . '/' . $name, 0600 );
		}
		file_put_contents( $private . '/synthetic-marker', 'seeded' );
		chmod( $private . '/synthetic-marker', 0600 );
		$this->output( json_encode( [ 'synthetic_pages' => count( $identities ), 'classified_pages' => count( $classifications ) ] ) . "\n" );
	}
}
$maintClass = SeedSyntheticVerification::class;
require_once RUN_MAINTENANCE_IF_MAIN;
