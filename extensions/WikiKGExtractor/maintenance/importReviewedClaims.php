<?php

$IP = getenv( 'MW_INSTALL_PATH' );
if ( $IP === false || $IP === '' ) {
	$IP = __DIR__ . '/../../..';
}
require_once "$IP/maintenance/Maintenance.php";

use MediaWiki\Extension\WikiKGExtractor\ClaimReviewStore;
use MediaWiki\Extension\WikiKGExtractor\OfflineDevelopmentScope;
use MediaWiki\Extension\WikiKGExtractor\OfflineReviewedImport;
use MediaWiki\Maintenance\Maintenance;

/** Offline, actor-only import of validated reviewed claim candidates. */
class ImportReviewedClaims extends Maintenance {
	public function __construct() {
		parent::__construct();
		$this->addDescription( 'Validate and optionally import offline candidate claims as pending snapshots' );
		foreach ( [ 'actor', 'claims', 'raw-data', 'audience' ] as $option ) {
			$this->addOption( $option, 'Required value for the offline import', true, true );
		}
		$this->addOption( 'offline-only', 'Confirm isolated offline-only processing', false, false );
		$this->addOption( 'corpus-manifest', 'Hash-pinned development corpus metadata manifest', false, true );
		$this->addOption( 'synthetic-only', 'Explicitly select synthetic wiki mode', false, false );
		$this->addOption(
			'confirm-import',
			'Persist verified claims as pending immutable snapshots',
			false,
			false
		);
		$this->addOption(
			'document-root',
			'Canonical document root when CLI environment does not expose DOCUMENT_ROOT',
			false,
			true
		);
	}

	public function execute() {
		$development = $this->getOption( 'corpus-manifest' );
		$synthetic = $this->hasOption( 'synthetic-only' );
		$hasDevelopmentManifest = is_string( $development ) && $development !== '';
		if ( !$this->hasOption( 'offline-only' ) || $this->getOption( 'audience' ) !== 'actor-only'
			|| $synthetic === $hasDevelopmentManifest
		) {
			$this->fatalError(
				'Select one guarded mode: development requires --offline-only --audience=actor-only '
				. '--corpus-manifest; synthetic requires --synthetic-only.'
			);
		}
		$services = $this->getServiceContainer();
		$name = $this->getOption( 'actor' );
		$actor = is_string( $name ) ? $services->getUserFactory()->newFromName( $name ) : null;
		if ( !$actor || !$actor->isRegistered()
			|| !$services->getPermissionManager()->userHasRight( $actor, 'wikikgextractor-review' )
		) {
			$this->fatalError( 'The named actor must be a registered account with the review right.' );
		}
		$claims = $this->getOption( 'claims' );
		$raw = $this->getOption( 'raw-data' );
		if ( !is_string( $claims ) || !is_string( $raw ) ) {
			$this->fatalError( 'Both artifact file paths are required.' );
		}
		$repo = realpath( dirname( __DIR__, 3 ) );
		$serverDocRootValue = $_SERVER['DOCUMENT_ROOT'] ?? getenv( 'DOCUMENT_ROOT' );
		if ( !is_string( $serverDocRootValue ) || $serverDocRootValue === '' ) {
			$serverDocRootValue = getenv( 'DOCUMENT_ROOT' );
		}
		$serverDocRoot = is_string( $serverDocRootValue ) && $serverDocRootValue !== ''
			? realpath( $serverDocRootValue ) : false;
		$optionDocRootValue = $this->getOption( 'document-root' );
		if ( $optionDocRootValue !== null && $optionDocRootValue !== false && $optionDocRootValue !== ''
			&& ( !is_string( $optionDocRootValue ) || $optionDocRootValue[0] !== DIRECTORY_SEPARATOR )
		) {
			$this->fatalError( '--document-root must be an absolute directory path.' );
		}
		$optionDocRoot = is_string( $optionDocRootValue ) && $optionDocRootValue !== ''
			? realpath( $optionDocRootValue ) : false;
		if ( ( is_string( $serverDocRootValue ) && $serverDocRootValue !== '' && $serverDocRoot === false )
			|| ( is_string( $optionDocRootValue ) && $optionDocRootValue !== '' && $optionDocRoot === false )
			|| ( $serverDocRoot !== false && !is_dir( $serverDocRoot ) )
			|| ( $optionDocRoot !== false && !is_dir( $optionDocRoot ) )
		) {
			$this->fatalError( 'Document-root path must resolve to an existing directory.' );
		}
		if ( $serverDocRoot !== false && $optionDocRoot !== false && $serverDocRoot !== $optionDocRoot ) {
			$this->fatalError( '--document-root does not match DOCUMENT_ROOT.' );
		}
		$docRoot = $serverDocRoot ?: $optionDocRoot;
		$artifactDir = realpath( dirname( $claims ) );
		if ( !$repo || !$docRoot || !$artifactDir
			|| self::within( $artifactDir, $repo ) || self::within( $artifactDir, $docRoot )
		) {
			$this->fatalError( 'Private job artifacts must be outside the repository and document root.' );
		}
		try {
			$scope = $hasDevelopmentManifest
				? new OfflineDevelopmentScope( $development )
				: null;
			$document = OfflineReviewedImport::verifyArtifacts( $claims, $raw, $actor, $scope );
			if ( !$this->hasOption( 'confirm-import' ) ) {
				$this->output( json_encode( [
					'status' => 'dry_run',
					'claim_count' => count( $document['claims'] ),
				], JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR ) . "\n" );
				return;
			}
			$result = OfflineReviewedImport::import(
				new ClaimReviewStore( $services->getConnectionProvider() ),
				$claims,
				$raw,
				$actor,
				$scope
			);
			$this->output( json_encode( [
				'status' => 'imported_pending',
				'claim_count' => count( $result['snapshot_ids'] ),
				'snapshot_ids' => $result['snapshot_ids'],
			], JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR ) . "\n" );
		} catch ( \Throwable $exception ) {
			$this->fatalError( json_encode( [ 'status' => 'error', 'code' => 'offline_import_refused' ] ) );
		}
	}

	private static function within( string $path, string $root ): bool {
		return $path === $root || str_starts_with(
			$path,
			rtrim( $root, DIRECTORY_SEPARATOR ) . DIRECTORY_SEPARATOR
		);
	}
}

$maintClass = ImportReviewedClaims::class;
require_once RUN_MAINTENANCE_IF_MAIN;
