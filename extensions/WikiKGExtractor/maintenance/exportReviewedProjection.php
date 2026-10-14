<?php

$IP = getenv( 'MW_INSTALL_PATH' );
if ( $IP === false || $IP === '' ) {
	$IP = __DIR__ . '/../../..';
}
require_once "$IP/maintenance/Maintenance.php";

use MediaWiki\Extension\WikiKGExtractor\ClaimReviewStore;
use MediaWiki\Extension\WikiKGExtractor\ReviewedProjectionExport;
use MediaWiki\Maintenance\Maintenance;

/**
 * Export reviewed claims for an isolated, synthetic-only offline projection.
 *
 * Example:
 * php extensions/WikiKGExtractor/maintenance/exportReviewedProjection.php \
 *   --actor='Wiki Admin' --synthetic-only --document-root='/var/www/html' \
 *   --output='/home/wiki/private/reviewed.json'
 *
 * The destination directory must already exist with owner-only permissions.
 */
class ExportReviewedProjection extends Maintenance {
	public function __construct() {
		parent::__construct();
		$this->addDescription(
			'Write a complete reviewed-claim export for an isolated synthetic-only offline projection'
		);
		$this->addOption(
			'actor',
			'Existing named account with the wikikgextractor-review right; no password is used',
			true,
			true
		);
		$this->addOption(
			'synthetic-only',
			'Confirm that this wiki contains synthetic data only and is not held-out evaluation data',
			false,
			false
		);
		$this->addOption(
			'output',
			'New absolute file path outside the repository and document root, in an owner-only directory',
			true,
			true
		);
		$this->addOption(
			'document-root',
			'Canonical document-root path; required when the CLI environment does not expose DOCUMENT_ROOT',
			false,
			true
		);
	}

	public function execute() {
		if ( !$this->hasOption( 'synthetic-only' ) ) {
			$this->fatalError( 'Refusing export without the explicit --synthetic-only confirmation.' );
		}

		$services = $this->getServiceContainer();
		$actorName = $this->getOption( 'actor' );
		$actor = is_string( $actorName )
			? $services->getUserFactory()->newFromName( $actorName )
			: null;
		if ( !$actor || !$actor->isRegistered() ) {
			$this->fatalError( 'The --actor option must name an existing registered account.' );
		}
		if ( !$services->getPermissionManager()->userHasRight( $actor, 'wikikgextractor-review' ) ) {
			$this->fatalError( 'The selected actor does not have the wikikgextractor-review right.' );
		}

		$outputPath = $this->getOption( 'output' );
		if ( !is_string( $outputPath ) || $outputPath === '' ) {
			$this->fatalError( 'The --output option must name a new absolute private file path.' );
		}

		$repositoryRoot = realpath( dirname( __DIR__, 3 ) );
		$serverDocumentRoot = $_SERVER['DOCUMENT_ROOT'] ?? '';
		if ( $serverDocumentRoot === '' ) {
			$serverDocumentRoot = getenv( 'DOCUMENT_ROOT' );
		}
		if ( is_string( $serverDocumentRoot ) && $serverDocumentRoot !== '' ) {
			$serverDocumentRoot = realpath( $serverDocumentRoot );
			if ( $serverDocumentRoot === false || !is_dir( $serverDocumentRoot ) ) {
				$this->fatalError( 'DOCUMENT_ROOT does not resolve to an existing directory.' );
			}
		} else {
			$serverDocumentRoot = false;
		}

		$optionDocumentRoot = $this->getOption( 'document-root' );
		if ( $optionDocumentRoot !== null && $optionDocumentRoot !== false && $optionDocumentRoot !== '' ) {
			if ( !is_string( $optionDocumentRoot ) || $optionDocumentRoot[0] !== DIRECTORY_SEPARATOR ) {
				$this->fatalError( '--document-root must be an absolute directory path.' );
			}
			$optionDocumentRoot = realpath( $optionDocumentRoot );
			if ( $optionDocumentRoot === false || !is_dir( $optionDocumentRoot ) ) {
				$this->fatalError( '--document-root must resolve to an existing directory.' );
			}
		} else {
			$optionDocumentRoot = false;
		}
		if ( $serverDocumentRoot !== false && $optionDocumentRoot !== false
			&& $serverDocumentRoot !== $optionDocumentRoot
		) {
			$this->fatalError( 'The --document-root option does not match DOCUMENT_ROOT.' );
		}
		$documentRoot = $serverDocumentRoot !== false
			? $serverDocumentRoot
			: $optionDocumentRoot;
		if ( $repositoryRoot === false || $documentRoot === false ) {
			$this->fatalError(
				'Could not determine repository and document-root boundaries; set --document-root for CLI.'
			);
		}

		$exporter = new ReviewedProjectionExport(
			new ClaimReviewStore( $services->getConnectionProvider() )
		);
		try {
			// User implements MediaWiki's Authority interface on this MediaWiki
			// version, so passing it directly keeps the actor's real groups and rights.
			$document = $exporter->exportToFile(
				$actor,
				$outputPath,
				$repositoryRoot,
				$documentRoot
			);
		} catch ( \Throwable $exception ) {
			$this->fatalError( 'Reviewed projection export failed; no partial output was installed.' );
		}

		$this->output( sprintf(
			"Wrote %d claims from %d snapshots. Watermark: %s\n",
			count( $document['claims'] ),
			$document['snapshot_count'],
			$document['source_watermark']
		) );
	}
}

$maintClass = ExportReviewedProjection::class;
require_once RUN_MAINTENANCE_IF_MAIN;
