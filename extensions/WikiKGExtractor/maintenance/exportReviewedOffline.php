<?php

$IP = getenv( 'MW_INSTALL_PATH' );
if ( $IP === false || $IP === '' ) {
	$IP = __DIR__ . '/../../..';
}
require_once "$IP/maintenance/Maintenance.php";

use MediaWiki\Extension\WikiKGExtractor\ClaimReviewStore;
use MediaWiki\Extension\WikiKGExtractor\OfflineDevelopmentScope;
use MediaWiki\Extension\WikiKGExtractor\OfflineReviewedImport;
use MediaWiki\Extension\WikiKGExtractor\ReviewedProjectionExport;
use MediaWiki\Maintenance\Maintenance;

/** Export only currently eligible reviewed claims within the pinned dev split. */
class ExportReviewedOffline extends Maintenance {
	public function __construct() {
		parent::__construct();
		$this->addDescription( 'Write a private projection containing only hash-pinned development evidence' );
		foreach ( [ 'actor', 'output', 'audience', 'corpus-manifest' ] as $option ) {
			$this->addOption( $option, 'Required actor-only export input', true, true );
		}
		$this->addOption( 'offline-only', 'Confirm offline-only processing', false, false );
		$this->addOption(
			'document-root',
			'Canonical document root when CLI environment does not expose DOCUMENT_ROOT',
			false,
			true
		);
	}

	public function execute() {
		if ( !$this->hasOption( 'offline-only' ) || $this->getOption( 'audience' ) !== 'actor-only' ) {
			$this->fatalError( 'Real-data export requires --offline-only --audience=actor-only.' );
		}
		$services = $this->getServiceContainer();
		$name = $this->getOption( 'actor' );
		$actor = is_string( $name ) ? $services->getUserFactory()->newFromName( $name ) : null;
		if ( !$actor || !$actor->isRegistered()
			|| !$services->getPermissionManager()->userHasRight( $actor, 'wikikgextractor-review' )
		) {
			$this->fatalError( 'The named actor must be registered and have the review right.' );
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
		if ( !$repo || !$docRoot ) {
			$this->fatalError( 'Repository and document-root boundaries must be known.' );
		}
		try {
			$scope = new OfflineDevelopmentScope( (string)$this->getOption( 'corpus-manifest' ) );
			$exporter = new ReviewedProjectionExport(
				new ClaimReviewStore( $services->getConnectionProvider() ),
				static function ( array $claim ) use ( $scope, $actor ): void {
					$scope->assertProjectionClaim( $claim );
					foreach ( $claim['evidence'] as $source ) {
						OfflineReviewedImport::assertLiveCurrentSource( $source, $actor );
					}
				}
			);
			$document = $exporter->exportToFile(
				$actor,
				(string)$this->getOption( 'output' ),
				$repo,
				$docRoot
			);
			$this->output( json_encode( [
				'status' => 'written',
				'claim_count' => count( $document['claims'] ),
				'snapshot_count' => $document['snapshot_count'],
				'source_watermark' => $document['source_watermark'],
			], JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR ) . "\n" );
		} catch ( \Throwable $exception ) {
			$this->fatalError( json_encode( [ 'status' => 'error', 'code' => 'offline_export_refused' ] ) );
		}
	}
}

$maintClass = ExportReviewedOffline::class;
require_once RUN_MAINTENANCE_IF_MAIN;
