<?php

$IP = getenv( 'MW_INSTALL_PATH' );
if ( !is_string( $IP ) || $IP === '' ) {
	throw new RuntimeException( 'Synthetic MediaWiki install path is required.' );
}
require_once "$IP/maintenance/Maintenance.php";

use MediaWiki\Maintenance\Maintenance;
use MediaWiki\Revision\SlotRecord;

/** Mutate one fixed synthetic fixture through MediaWiki's revision/review APIs. */
class WikiKGAuthorizationFixture extends Maintenance {
	public function __construct() {
		parent::__construct();
		$this->addOption( 'mode', 'Select the fixed Other crop Later synthetic source edit', true, true );
		$this->addOption( 'private-root', 'Owner-only synthetic verification root', true, true );
		$this->addOption( 'actor', 'Synthetic reviewer account', true, true );
	}

	public function execute() {
		try {
			$root = $this->safePrivateRoot( (string)$this->getOption( 'private-root' ) );
			$this->assertSyntheticRuntime( $root );
			$seeds = $this->readPrivateJson( $root . '/seed-identities.json' );
			$actorName = (string)$this->getOption( 'actor' );
			if ( $actorName !== 'Synthetic reviewer' ) {
				throw new RuntimeException( 'Unexpected fixture actor.' );
			}
			$services = $this->getServiceContainer();
			$actor = $services->getUserFactory()->newFromName( $actorName );
			if ( !$actor || !$actor->isRegistered()
				|| !$services->getPermissionManager()->userHasRight( $actor, 'wikikgextractor-review' )
			) {
				throw new RuntimeException( 'Fixture actor is not authorized.' );
			}
			$mode = (string)$this->getOption( 'mode' );
			if ( $mode !== 'source-edit' ) {
				throw new RuntimeException( 'Unsupported fixture operation.' );
			}
			$this->editSeedPage( $seeds, $actor );
		} catch ( Throwable $exception ) {
			$this->fatalError( json_encode( [ 'status' => 'refused', 'reason' => 'fixture_guard_failed' ],
				JSON_THROW_ON_ERROR ), 2 );
		}
	}

	private function safePrivateRoot( string $path ): string {
		if ( $path !== '/private/wikikg' || is_link( $path ) ) {
			throw new RuntimeException( 'Unexpected private root.' );
		}
		$real = realpath( $path );
		if ( $real !== $path || !is_dir( $real ) || fileowner( $real ) !== posix_geteuid()
			|| ( fileperms( $real ) & 07777 ) !== 0700
		) {
			throw new RuntimeException( 'Unsafe private root.' );
		}
		return $real;
	}

	private function assertSyntheticRuntime( string $root ): void {
		global $wgDBtype, $wgDBname, $wgSQLiteDataDir, $wgWikiKGSyntheticVerificationRoot;
		$config = getenv( 'MW_CONFIG_FILE' );
		$configPath = $root . '/LocalSettings.synthetic.php';
		if ( is_link( $configPath ) || !is_file( $configPath )
			|| fileowner( $configPath ) !== posix_geteuid() || ( fileperms( $configPath ) & 07777 ) !== 0600
		) {
			throw new RuntimeException( 'Unsafe synthetic wiki config.' );
		}
		if ( getenv( 'WIKIKG_SYNTHETIC' ) !== '1' || $config !== $configPath
			|| $wgWikiKGSyntheticVerificationRoot !== $root || $wgDBtype !== 'sqlite'
			|| $wgDBname !== 'syntheticwiki' || !is_string( $wgSQLiteDataDir )
			|| realpath( $wgSQLiteDataDir ) !== realpath( $root . '/db' )
		) {
			throw new RuntimeException( 'Not the configured synthetic SQLite wiki.' );
		}
		$marker = $root . '/permission-denied';
		if ( is_link( $marker ) || !is_file( $marker ) || fileowner( $marker ) !== posix_geteuid()
			|| ( fileperms( $marker ) & 07777 ) !== 0600
			|| file_get_contents( $marker ) !== 'allow'
		) {
			throw new RuntimeException( 'Synthetic authorization marker must be allow.' );
		}
	}

	private function readPrivateJson( string $path ): array {
		if ( is_link( $path ) || !is_file( $path ) || fileowner( $path ) !== posix_geteuid()
			|| ( fileperms( $path ) & 07777 ) !== 0600 || filesize( $path ) > 1048576
		) {
			throw new RuntimeException( 'Unsafe seed or export file.' );
		}
		$value = json_decode( file_get_contents( $path ), true, 32, JSON_THROW_ON_ERROR );
		if ( !is_array( $value ) ) {
			throw new RuntimeException( 'Malformed private fixture data.' );
		}
		return $value;
	}

	private function seed( array $seeds, string $title ): array {
		foreach ( $seeds as $seed ) {
			if ( is_array( $seed ) && ( $seed['title'] ?? null ) === $title
				&& is_int( $seed['page_id'] ?? null ) && is_int( $seed['revision_id'] ?? null )
				&& is_string( $seed['content_hash'] ?? null )
				&& preg_match( '/\A[a-f0-9]{64}\z/D', $seed['content_hash'] )
			) {
				return $seed;
			}
		}
		throw new RuntimeException( 'Expected fixture identity is absent.' );
	}

	private function editSeedPage( array $seeds, $actor ): void {
		$titleText = 'Other crop Later';
		$seed = $this->seed( $seeds, $titleText );
		$title = Title::newFromText( $titleText );
		$services = $this->getServiceContainer();
		$page = $title ? $services->getWikiPageFactory()->newFromTitle( $title ) : null;
		$revision = $page ? $page->getRevisionRecord() : null;
		$content = $revision ? $revision->getContent( SlotRecord::MAIN ) : null;
		$text = $content && method_exists( $content, 'getText' ) ? $content->getText() : null;
		if ( !$page || $page->getId() !== $seed['page_id'] || !$revision
			|| $revision->getId() !== $seed['revision_id'] || !is_string( $text )
			|| !hash_equals( $seed['content_hash'], hash( 'sha256', $text ) )
		) {
			throw new RuntimeException( 'Seed source no longer has its exact starting identity.' );
		}
		$newText = "Synthetic source revision changed by the authorization verifier.\n";
		$updater = $page->newPageUpdater( $actor );
		$updater->setContent( SlotRecord::MAIN, \ContentHandler::makeContent( $newText, $title ) );
		$revision = $updater->saveRevision(
			\CommentStoreComment::newUnsavedComment( 'Synthetic offline authorization verification' ), 0 );
		if ( !$revision || !$updater->getStatus()->isOK() ) {
			throw new RuntimeException( 'Synthetic source revision edit failed.' );
		}
		$this->output( json_encode( [ 'status' => 'edited', 'fixture' => $titleText,
			'page_id' => $seed['page_id'], 'revision_id' => $revision->getId() ],
			JSON_THROW_ON_ERROR ) . "\n" );
	}
}

$maintClass = WikiKGAuthorizationFixture::class;
require_once RUN_MAINTENANCE_IF_MAIN;
