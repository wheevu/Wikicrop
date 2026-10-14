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

/** Append exactly one named actor's explicit offline review decision. */
class ReviewOfflineClaim extends Maintenance {
	public function __construct() {
		parent::__construct();
		$this->addDescription( 'Append one explicit offline decision to a claim snapshot' );
		foreach ( [ 'actor', 'snapshot', 'status', 'expected-version', 'audience' ] as $option ) {
			$this->addOption( $option, 'Required review input', true, true );
		}
		$this->addOption( 'reason', 'Optional review reason', false, true );
		$this->addOption( 'offline-only', 'Confirm isolated offline-only review', false, false );
		$this->addOption( 'synthetic-only', 'Explicitly select synthetic wiki mode', false, false );
		$this->addOption( 'corpus-manifest', 'Hash-pinned development corpus metadata manifest', false, true );
	}

	public function execute() {
		$manifest = $this->getOption( 'corpus-manifest' );
		$synthetic = $this->hasOption( 'synthetic-only' );
		if ( !$this->hasOption( 'offline-only' ) || $this->getOption( 'audience' ) !== 'actor-only'
			|| $synthetic === ( is_string( $manifest ) && $manifest !== '' )
		) {
			$this->fatalError(
				'Choose --synthetic-only or --offline-only --audience=actor-only --corpus-manifest.'
			);
		}
		$services = $this->getServiceContainer();
		$name = $this->getOption( 'actor' );
		$actor = is_string( $name ) ? $services->getUserFactory()->newFromName( $name ) : null;
		if ( !$actor || !$actor->isRegistered()
			|| !$services->getPermissionManager()->userHasRight( $actor, 'wikikgextractor-review' )
		) {
			$this->fatalError( 'The named actor must be registered and have the review right.' );
		}
		$snapshot = $this->getOption( 'snapshot' );
		$status = $this->getOption( 'status' );
		$version = filter_var( $this->getOption( 'expected-version' ), FILTER_VALIDATE_INT );
		if ( !is_string( $snapshot ) || !preg_match( '/\A[a-f0-9]{64}\z/D', $snapshot )
			|| !in_array( $status, [ 'approved', 'rejected' ], true ) || $version === false || $version < 0
		) {
			$this->fatalError( 'Snapshot, status, or expected version is invalid.' );
		}
		try {
			$scope = $manifest ? new OfflineDevelopmentScope( $manifest ) : null;
			$store = new ClaimReviewStore( $services->getConnectionProvider() );
			$review = $store->getReview( $snapshot );
			if ( $review === null ) {
				$this->fatalError( 'Snapshot does not exist.' );
			}
			foreach ( $review['evidence'] as $source ) {
				if ( $scope ) {
					$scope->assertSource( $source );
				}
				if ( $status === 'approved' ) {
					OfflineReviewedImport::assertLiveCurrentSource( $source, $actor );
				}
			}
			$event = $store->recordDecision(
				$snapshot,
				$actor->getId(),
				$status,
				(string)( $this->getOption( 'reason' ) ?? '' ),
				$version
			);
			if ( $event === null ) {
				$this->fatalError( 'Snapshot is missing or its review version is stale.' );
			}
			$this->output( json_encode( [
				'status' => 'recorded',
				'snapshot_id' => $event['snapshot_id'],
				'review_version' => $event['version'],
				'decision_status' => $event['status'],
			], JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR ) . "\n" );
		} catch ( \Throwable $exception ) {
			$this->fatalError( json_encode( [ 'status' => 'error', 'code' => 'offline_review_refused' ] ) );
		}
	}
}

$maintClass = ReviewOfflineClaim::class;
require_once RUN_MAINTENANCE_IF_MAIN;
