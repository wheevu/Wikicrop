<?php

$IP = getenv( 'MW_INSTALL_PATH' );
if ( $IP === false || $IP === '' ) {
	$IP = __DIR__ . '/../../..';
}
require_once "$IP/maintenance/Maintenance.php";

use MediaWiki\Extension\WikiKGExtractor\OfflineSourceInventory;
use MediaWiki\Maintenance\Maintenance;
use MediaWiki\Revision\RevisionRecord;
use MediaWiki\Revision\SlotRecord;
use MediaWiki\Permissions\PermissionManager;
use Wikimedia\Rdbms\IDBAccessObject;

class DiscoverOfflineSources extends Maintenance {
	private function privateInput( $path, $limit, $repositoryRoot, $documentRoot, $classification ) {
		if ( !is_string( $path ) || $path === '' || $path[0] !== DIRECTORY_SEPARATOR ) {
			$this->fatalError( 'Private metadata paths must be absolute.' );
		}
		$parts = explode( DIRECTORY_SEPARATOR, trim( $path, DIRECTORY_SEPARATOR ) );
		$cursor = DIRECTORY_SEPARATOR;
		foreach ( $parts as $part ) {
			if ( $part === '' || $part === '.' || $part === '..' ) {
				$this->fatalError( 'Private metadata path is malformed.' );
			}
			$cursor = rtrim( $cursor, DIRECTORY_SEPARATOR ) . DIRECTORY_SEPARATOR . $part;
			clearstatcache( true, $cursor );
			if ( is_link( $cursor ) ) {
				$this->fatalError( 'Private metadata paths may not contain symlinks.' );
			}
		}
		$parent = dirname( $path );
		$resolvedParent = realpath( $parent );
		$resolvedFile = realpath( $path );
		if ( !$resolvedParent || !$resolvedFile || !is_dir( $resolvedParent ) || !is_file( $resolvedFile )
			|| $resolvedParent !== $parent || $resolvedFile !== $path
			|| ( fileperms( $resolvedParent ) & 0777 ) !== 0700 || fileowner( $resolvedParent ) !== posix_geteuid()
			|| ( fileperms( $resolvedFile ) & 0777 ) !== 0600 || fileowner( $resolvedFile ) !== posix_geteuid()
			|| filesize( $resolvedFile ) > $limit
			|| $resolvedFile === $repositoryRoot || strpos( $resolvedFile, rtrim( $repositoryRoot, DIRECTORY_SEPARATOR ) . DIRECTORY_SEPARATOR ) === 0
			|| $resolvedFile === $documentRoot || strpos( $resolvedFile, rtrim( $documentRoot, DIRECTORY_SEPARATOR ) . DIRECTORY_SEPARATOR ) === 0
		) {
			$this->fatalError( $classification ? 'Classification input is not a bounded private file.'
				: 'Corpus manifest is not a bounded private file.' );
		}
		return $resolvedFile;
	}

	public function __construct() {
		parent::__construct();
		$this->addDescription( 'Create a bounded, private metadata-first offline source inventory.' );
		foreach ( [
			'actor' => [ 'Existing named reviewer account', true ],
			'offline-only' => [ 'Confirm offline-only operation', false ],
			'audience' => [ 'Must be actor-only', true ],
			'output-dir' => [ 'New private run directory under an existing private parent', true ],
			'document-root' => [ 'Canonical document root', true ],
			'manifest' => [ 'Hash-pinned corpus manifest metadata file', true ],
			'classifications' => [ 'Explicit JSON title-to-kind/crop classifications', true ],
			'metadata-only' => [ 'Write full metadata without reading source content', false ],
			'selected-pages' => [ 'Private JSON list of terminal page IDs for a collection batch', true ],
			'expected-metadata' => [ 'Private prepared metadata snapshot for pre-content drift checking', true ],
		] as $name => $option ) {
			$this->addOption( $name, $option[0], !in_array( $name, [ 'offline-only', 'metadata-only', 'selected-pages', 'expected-metadata' ], true ), $option[1] );
		}
	}

	public function execute() {
		if ( !$this->hasOption( 'offline-only' ) || $this->getOption( 'audience' ) !== 'actor-only' ) {
			$this->fatalError( 'Require --offline-only --audience=actor-only.' );
		}
		if ( !function_exists( 'posix_geteuid' ) || PHP_OS_FAMILY === 'Windows' ) {
			$this->fatalError( 'Cannot verify owner-only POSIX permissions.' );
		}
		$services = $this->getServiceContainer();
		$name = $this->getOption( 'actor' );
		$actor = is_string( $name ) ? $services->getUserFactory()->newFromName( $name ) : null;
		if ( !$actor || !$actor->isRegistered() || !$services->getPermissionManager()->userHasRight( $actor, 'wikikgextractor-review' ) ) {
			$this->fatalError( 'The named actor must be a registered reviewer.' );
		}
		$root = realpath( dirname( __DIR__, 3 ) );
		$web = realpath( $this->getOption( 'document-root' ) );
		$runtimeDocumentRoot = $_SERVER['DOCUMENT_ROOT'] ?? getenv( 'DOCUMENT_ROOT' );
		if ( !is_string( $runtimeDocumentRoot ) || $runtimeDocumentRoot === '' ) {
			$runtimeDocumentRoot = getenv( 'DOCUMENT_ROOT' );
		}
		$webOption = (string)$this->getOption( 'document-root' );
		if ( !$web || !is_dir( $web ) || $webOption === '' || $webOption[0] !== DIRECTORY_SEPARATOR
			|| ( is_string( $runtimeDocumentRoot ) && $runtimeDocumentRoot !== ''
				&& realpath( $runtimeDocumentRoot ) !== $web )
		) {
			$this->fatalError( '--document-root does not match the runtime document root.' );
		}
		$base = $this->getOption( 'output-dir' );
		$manifestPath = $this->getOption( 'manifest' );
		$classPath = $this->getOption( 'classifications' );
		$metadataOnly = $this->hasOption( 'metadata-only' );
		$selectedPath = $this->getOption( 'selected-pages' );
		$expectedPath = $this->getOption( 'expected-metadata' );
		if ( !$metadataOnly && ( !is_string( $selectedPath ) || $selectedPath === '' ) ) {
			$this->fatalError( 'Content collection requires explicit --selected-pages IDs.' );
		}
		if ( !$metadataOnly && ( !is_string( $expectedPath ) || $expectedPath === '' ) ) {
			$this->fatalError( 'Content collection requires prepared metadata for drift checking.' );
		}
		if ( !$root || !$web || !$base || !$manifestPath || !$classPath ) {
			$this->fatalError( 'Repository, document root, manifest, classifications and output paths are required.' );
		}
		if ( !is_string( $base ) || $base === '' || $base[0] !== DIRECTORY_SEPARATOR ) {
			$this->fatalError( 'Output parent must be an absolute private directory.' );
		}
		$baseParts = explode( DIRECTORY_SEPARATOR, trim( $base, DIRECTORY_SEPARATOR ) );
		$baseCursor = DIRECTORY_SEPARATOR;
		foreach ( $baseParts as $part ) {
			if ( $part === '' || $part === '.' || $part === '..' ) {
				$this->fatalError( 'Output parent path is malformed.' );
			}
			$baseCursor = rtrim( $baseCursor, DIRECTORY_SEPARATOR ) . DIRECTORY_SEPARATOR . $part;
			clearstatcache( true, $baseCursor );
			if ( is_link( $baseCursor ) ) {
				$this->fatalError( 'Output parent path may not contain symlinks.' );
			}
		}
		$base = realpath( $base );
		if ( !$base || !is_dir( $base ) || ( fileperms( $base ) & 0777 ) !== 0700 || fileowner( $base ) !== posix_geteuid()
			|| $base === $root || strpos( $base, $root . DIRECTORY_SEPARATOR ) === 0
			|| $base === $web || strpos( $base, rtrim( $web, DIRECTORY_SEPARATOR ) . DIRECTORY_SEPARATOR ) === 0
		) {
			$this->fatalError( 'Output parent must already be owner-only, outside repository and document root.' );
		}
		$manifestPath = $this->privateInput( $manifestPath, 20 * 1024 * 1024, $root, $web, false );
		$classPath = $this->privateInput( $classPath, 2 * 1024 * 1024, $root, $web, true );
		$selectedPages = [];
		if ( $selectedPath !== null && $selectedPath !== false && $selectedPath !== '' ) {
			$selectedPath = $this->privateInput( $selectedPath, 1024 * 1024, $root, $web, true );
			$selectedDoc = json_decode( file_get_contents( $selectedPath ), true );
			if ( !is_array( $selectedDoc ) || !array_is_list( $selectedDoc ) || count( $selectedDoc ) < 1 || count( $selectedDoc ) > 100 ) {
				$this->fatalError( 'Selected page IDs must be a private bounded JSON list.' );
			}
			foreach ( $selectedDoc as $id ) {
				if ( !is_int( $id ) || $id < 1 || $id > 4294967295 || isset( $selectedPages[$id] ) ) {
					$this->fatalError( 'Selected page IDs must be unique positive integers.' );
				}
				$selectedPages[$id] = true;
			}
		}
		$expectedMetadata = null;
		if ( is_string( $expectedPath ) && $expectedPath !== '' ) {
			$expectedPath = $this->privateInput( $expectedPath, 20 * 1024 * 1024, $root, $web, true );
			$expectedMetadata = json_decode( file_get_contents( $expectedPath ), true );
			if ( !is_array( $expectedMetadata ) || !is_array( $expectedMetadata['pages'] ?? null )
				|| !array_is_list( $expectedMetadata['pages'] ) || count( $expectedMetadata['pages'] ) < 1
				|| count( $expectedMetadata['pages'] ) > OfflineSourceInventory::MAX_PAGES
			) {
				$this->fatalError( 'Expected metadata snapshot is malformed or outside its bound.' );
			}
		}
		$manifestBytes = @file_get_contents( $manifestPath, false, null, 0, 20 * 1024 * 1024 + 1 );
		if ( $manifestBytes === false || strlen( $manifestBytes ) > 20 * 1024 * 1024
			|| hash( 'sha256', $manifestBytes ) !== OfflineSourceInventory::MANIFEST_SHA256
		) {
			$this->fatalError( 'Held-out manifest hash does not match the frozen protocol; no page content read.' );
		}
		$protocolPath = dirname( __DIR__ ) . '/protocol/rice-engineering-pilot.v2.json';
		$protocolBytes = @file_get_contents( $protocolPath, false, null, 0, 1024 * 1024 + 1 );
		$protocol = $protocolBytes === false ? null : json_decode( $protocolBytes, true );
		if ( $protocolBytes === false || hash( 'sha256', $protocolBytes ) !== OfflineSourceInventory::PROTOCOL_SHA256
			|| ( $protocol['artifacts']['manifest_sha256'] ?? null ) !== OfflineSourceInventory::MANIFEST_SHA256
		) {
			$this->fatalError( 'Frozen protocol hash does not match the offline discovery contract.' );
		}
		$manifest = json_decode( $manifestBytes, true );
		$classBytes = @file_get_contents( $classPath, false, null, 0, 2 * 1024 * 1024 + 1 );
		if ( $classBytes === false || strlen( $classBytes ) > 2 * 1024 * 1024 ) {
			$this->fatalError( 'Classification metadata exceeds its fixed read bound.' );
		}
		$classifications = json_decode( $classBytes, true );
		if ( !is_array( $manifest['pages'] ?? null ) || !is_array( $classifications ) ) {
			$this->fatalError( 'Manifest or explicit classification document is malformed.' );
		}
		$heldout = [];
		foreach ( $manifest['pages'] as $page ) {
			if ( ( $page['split'] ?? null ) !== 'test' ) {
				continue;
			}
			$heldout[] = $page;
			// A test target and every requested/resolved alias are metadata-only exclusions.
			foreach ( [ 'requested_title', 'resolved_title' ] as $field ) {
				if ( isset( $page[$field] ) ) {
					$heldout[] = [ 'title' => $page[$field], 'page_id' => $page['page_id'] ?? null ];
				}
			}
		}
		$db = $services->getConnectionProvider()->getPrimaryDatabase();
		if ( $expectedMetadata !== null
			&& ( ( $expectedMetadata['source_identity']['domain_id'] ?? null ) !== $db->getDomainID()
				|| ( $expectedMetadata['source_identity']['database_name'] ?? null ) !== $db->getDBname()
				|| ( $expectedMetadata['source_identity']['read_role'] ?? null ) !== 'primary' )
		) {
			$this->fatalError( 'Primary database identity differs from prepared plan; no page content read.' );
		}
		$pageRows = $db->newSelectQueryBuilder()->select( [ 'page_id', 'page_namespace', 'page_title', 'page_latest' ] )
			->from( 'page' )->orderBy( 'page_id' )->limit( OfflineSourceInventory::MAX_PAGES + 1 )->caller( __METHOD__ )->fetchResultSet();
		$pages = [];
		foreach ( $pageRows as $record ) {
			$id = (int)$record->page_id;
			$title = str_replace( '_', ' ', (string)$record->page_title );
			$redir = $db->newSelectQueryBuilder()->select( [ 'rd_namespace', 'rd_title', 'rd_interwiki' ] )
				->from( 'redirect' )->where( [ 'rd_from' => $id ] )->caller( __METHOD__ )->fetchRow();
			$target = $redir && empty( $redir->rd_interwiki ) && (int)$redir->rd_namespace === 0
				? str_replace( '_', ' ', (string)$redir->rd_title ) : null;
			$pages[] = [ 'page_id' => $id, 'title' => $title, 'namespace' => (int)$record->page_namespace,
				'revision_id' => (int)$record->page_latest, 'redirect_target' => $target,
				'redirect_namespace' => $redir ? (int)$redir->rd_namespace : 0,
				'redirect_external' => $redir && !empty( $redir->rd_interwiki ) ];
		}
		if ( $expectedMetadata !== null ) {
			$fields = [ 'page_id', 'revision_id', 'title', 'namespace', 'redirect_target', 'redirect_namespace', 'redirect_external' ];
			$expectedRows = $expectedMetadata['pages'];
			if ( count( $pages ) !== count( $expectedRows ) ) {
				$this->fatalError( 'Current full metadata differs from prepared plan; no page content read.' );
			}
			foreach ( $pages as $index => $current ) {
				$expected = $expectedRows[$index] ?? null;
				if ( !is_array( $expected ) ) {
					$this->fatalError( 'Prepared metadata row is malformed; no page content read.' );
				}
				foreach ( $fields as $field ) {
					$expectedValue = $expected[$field] ?? null;
					$currentValue = $current[$field] ?? null;
					if ( $expectedValue !== $currentValue ) {
						$this->fatalError( 'Current full metadata differs from prepared plan; no page content read.' );
					}
				}
			}
		}
		$run = $base . DIRECTORY_SEPARATOR . 'offline-' . gmdate( 'Ymd-His' ) . '-' . bin2hex( random_bytes( 5 ) );
		if ( !mkdir( $run, 0700 ) || !chmod( $run, 0700 )
			|| ( fileperms( $run ) & 0777 ) !== 0700 || fileowner( $run ) !== posix_geteuid()
		) {
			$this->fatalError( 'Could not create private run directory.' );
		}
		$inventory = new OfflineSourceInventory();
		$databaseIdentity = [ 'domain_id' => $db->getDomainID(), 'database_name' => $db->getDBname(), 'read_role' => 'primary' ];
		$started = microtime( true );
		$sourceBytes = 0;
		$encodedBytes = 0;
		$readCount = 0;
		$currentSource = static function ( array $identity ) use ( $services, $db, $actor ) {
			$row = $db->newSelectQueryBuilder()->select( [ 'page_latest', 'page_title', 'page_namespace', 'rev_page', 'rev_deleted' ] )
				->from( 'page' )->join( 'revision', null, 'rev_id = page_latest' )
				->where( [ 'page_id' => $identity['page_id'], 'rev_id' => $identity['revision_id'] ] )
				->caller( __METHOD__ )->fetchRow();
			if ( !$row || (int)$row->page_latest !== $identity['revision_id']
				|| (int)$row->page_namespace !== 0 || (int)$row->rev_page !== $identity['page_id']
				|| (int)$row->rev_deleted !== 0
			) {
				return null;
			}
			$page = $services->getPageStore()->getPageById( $identity['page_id'], IDBAccessObject::READ_LATEST );
			$title = $page ? \Title::newFromPageIdentity( $page ) : null;
			$expectedTitle = \Title::newFromText( $identity['title'] );
			if ( !$page || $page->getLatest() !== $identity['revision_id'] || !$title || !$expectedTitle
				|| $title->getNamespace() !== 0 || $title->getDBkey() !== $expectedTitle->getDBkey()
				|| !$services->getPermissionManager()->userCan( 'read', $actor, $title, PermissionManager::RIGOR_SECURE )
			) {
				return null;
			}
			$revision = $services->getRevisionLookup()->getRevisionById(
				$identity['revision_id'], IDBAccessObject::READ_LATEST, $page
			);
			if ( !$revision || $revision->getPageId() !== $identity['page_id']
				|| $revision->isDeleted( RevisionRecord::DELETED_TEXT | RevisionRecord::DELETED_USER
					| RevisionRecord::DELETED_COMMENT | RevisionRecord::DELETED_RESTRICTED )
			) {
				return null;
			}
			return [ $page, $title, $revision ];
		};
		$assertPrivateRun = static function () use ( $run ) {
			clearstatcache( true, $run );
			$mode = fileperms( $run );
			if ( !is_dir( $run ) || is_link( $run ) || $mode === false || ( $mode & 0777 ) !== 0700
				|| fileowner( $run ) !== posix_geteuid()
			) {
				throw new \RuntimeException( 'Private run directory changed.' );
			}
		};
		try {
			$document = $inventory->inventory( $pages, $heldout, function ( array $page, $classificationOnly = false ) use ( $services, $actor, $classifications, $run, $db, $started, $currentSource, $assertPrivateRun, &$sourceBytes, &$encodedBytes, &$readCount, $metadataOnly ) {
				$classification = $classifications[$page['title']] ?? null;
				if ( !is_array( $classification )
					|| !in_array( $classification['kind'] ?? null, [ 'species', 'variety' ], true )
					|| !is_string( $classification['crop'] ?? null ) || trim( $classification['crop'] ) === ''
				) {
					return [ 'kind' => null, 'crop' => null ];
				}
				if ( $metadataOnly || $classificationOnly ) {
					return [ 'kind' => $classification['kind'], 'crop' => $classification['crop'] ];
				}
				if ( ++$readCount > 100 || microtime( true ) - $started > 900 ) {
					return [ 'reason' => 'collection_limit_exceeded' ];
				}
				$source = $currentSource( $page );
				if ( !$source ) {
					return [ 'unreadable' => true ];
				}
				[ , $title, $revision ] = $source;
				$suppression = RevisionRecord::DELETED_TEXT | RevisionRecord::DELETED_USER
					| RevisionRecord::DELETED_COMMENT | RevisionRecord::DELETED_RESTRICTED;
				if ( $revision->isDeleted( $suppression ) ) {
					return [ 'suppressed' => true ];
				}
				$content = $revision->getContent( SlotRecord::MAIN );
				if ( !$content || $content->getModel() !== CONTENT_MODEL_WIKITEXT || !method_exists( $content, 'getText' ) ) {
					return [ 'unreadable' => true ];
				}
				$text = $content->getText();
				if ( !is_string( $text ) || preg_match( '//u', $text ) !== 1 ) {
					return [ 'unreadable' => true ];
				}
				if ( strlen( $text ) > 5 * 1024 * 1024 ) {
					return [ 'reason' => 'page_too_large' ];
				}
				$sourceBytes += strlen( $text );
				if ( $sourceBytes > 50 * 1024 * 1024 || microtime( true ) - $started > 900 ) {
					return [ 'reason' => 'collection_limit_exceeded' ];
				}
				$hash = hash( 'sha256', $text );
				$freshSource = $currentSource( $page );
				if ( !$freshSource ) {
					return [ 'reason' => 'stale_or_no_longer_readable' ];
				}
				[ , , $fresh ] = $freshSource;
				$freshContent = $fresh->getContent( SlotRecord::MAIN );
				if ( !$freshContent || $freshContent->getModel() !== CONTENT_MODEL_WIKITEXT
					|| !method_exists( $freshContent, 'getText' ) || !is_string( $freshContent->getText() )
					|| preg_match( '//u', $freshContent->getText() ) !== 1
					|| !hash_equals( $hash, hash( 'sha256', $freshContent->getText() ) )
				) {
					return [ 'reason' => 'source_hash_changed' ];
				}
				$file = 'page-' . $page['page_id'] . '-' . $page['revision_id'] . '.json';
				$payload = [ 'page' => [ 'title' => $page['title'], 'page_id' => $page['page_id'],
					'revision_id' => $page['revision_id'], 'kind' => $classification['kind'] ?? null,
					'crop' => $classification['crop'] ?? null, 'content_hash' => $hash,
					'wikitext' => $text ] ];
				$encoded = json_encode( $payload, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR );
				if ( strlen( $encoded ) > 5 * 1024 * 1024 ) {
					return [ 'reason' => 'page_too_large' ];
				}
				$encodedBytes += strlen( $encoded );
				if ( $encodedBytes > 50 * 1024 * 1024 ) {
					return [ 'reason' => 'collection_limit_exceeded' ];
				}
				$assertPrivateRun();
				$handle = @fopen( $run . DIRECTORY_SEPARATOR . $file, 'x' );
				if ( !$handle || !chmod( $run . DIRECTORY_SEPARATOR . $file, 0600 )
					|| ( fileperms( $run . DIRECTORY_SEPARATOR . $file ) & 0777 ) !== 0600
					|| fileowner( $run . DIRECTORY_SEPARATOR . $file ) !== posix_geteuid() ) {
					if ( $handle ) { fclose( $handle ); }
					throw new \RuntimeException( 'Private source write failed.' );
				}
				$offset = 0;
				while ( $offset < strlen( $encoded ) ) {
					$written = fwrite( $handle, substr( $encoded, $offset ) );
					if ( $written === false || $written === 0 ) {
						fclose( $handle );
						throw new \RuntimeException( 'Private source write failed.' );
					}
					$offset += $written;
				}
				fclose( $handle );
				return [ 'page_id' => $page['page_id'], 'revision_id' => $page['revision_id'],
					'kind' => $classification['kind'] ?? null, 'crop' => $classification['crop'] ?? null,
					'wikitext' => $text, 'source_file' => $file ];
			}, OfflineSourceInventory::MAX_PAGES, $metadataOnly, $selectedPages );
		} catch ( \Throwable $exception ) {
			$this->fatalError( 'Offline metadata discovery failed; source text was not emitted to diagnostics.' );
		}
		// Text is stored in the page files, never duplicated into metadata inventory.
		$document['protocol_sha256'] = OfflineSourceInventory::PROTOCOL_SHA256;
		$document['heldout_manifest_sha256'] = OfflineSourceInventory::MANIFEST_SHA256;
		$document['actor'] = $name;
		$document['source_identity'] = $databaseIdentity;
		$document['inventory_generated_at'] = gmdate( 'c' );
		$document['collection_limits'] = [ 'metadata_pages' => OfflineSourceInventory::MAX_PAGES,
			'content_pages' => 100, 'source_bytes' => 50 * 1024 * 1024, 'runtime_seconds' => 900 ];
		foreach ( $document['pages'] as &$row ) {
			if ( isset( $row['source_file'] ) ) {
				unset( $row['wikitext'] );
			}
		}
		unset( $row );
		$inventoryPath = $run . DIRECTORY_SEPARATOR . 'inventory.json';
		$inventoryBytes = json_encode( $document,
			JSON_PRETTY_PRINT | JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR );
		$assertPrivateRun();
		$inventoryHandle = @fopen( $inventoryPath, 'x' );
		if ( !$inventoryHandle || !chmod( $inventoryPath, 0600 )
			|| ( fileperms( $inventoryPath ) & 0777 ) !== 0600 || fileowner( $inventoryPath ) !== posix_geteuid()
		) {
			if ( $inventoryHandle ) { fclose( $inventoryHandle ); }
			$this->fatalError( 'Could not safely write private metadata inventory.' );
		}
		$offset = 0;
		while ( $offset < strlen( $inventoryBytes ) ) {
			$written = fwrite( $inventoryHandle, substr( $inventoryBytes, $offset ) );
			if ( $written === false || $written === 0 ) {
				fclose( $inventoryHandle );
				$this->fatalError( 'Could not safely write private metadata inventory.' );
			}
			$offset += $written;
		}
		fclose( $inventoryHandle );
		$reasons = [];
		$eligible = 0;
		foreach ( $document['pages'] as $row ) {
			if ( $row['eligible'] ) {
				$eligible++;
			} else {
				$reason = $row['reason'] ?? 'ineligible';
				$reasons[$reason] = ( $reasons[$reason] ?? 0 ) + 1;
			}
		}
		ksort( $reasons );
		$this->output( json_encode( [ 'status' => 'completed', 'inventory_path' => $inventoryPath,
			'metadata_rows' => count( $document['pages'] ), 'eligible_rows' => $eligible,
			'excluded_by_reason' => $reasons, 'source_bytes' => $sourceBytes,
			'encoded_source_bytes' => $encodedBytes ], JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR ) . "\n" );
	}
}

$maintClass = DiscoverOfflineSources::class;
require_once RUN_MAINTENANCE_IF_MAIN;
