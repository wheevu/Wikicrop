<?php

namespace MediaWiki\Extension\WikiKGExtractor\Tests\Integration;

use MediaWiki\Context\RequestContext;
use MediaWiki\Extension\WikiKGExtractor\ClaimEvidenceAccess;
use MediaWiki\Extension\WikiKGExtractor\ClaimReviewStore;
use MediaWiki\Extension\WikiKGExtractor\ReviewedProjectionExport;
use MediaWiki\Permissions\Authority;
use MediaWiki\Revision\RevisionRecord;
use MediaWikiIntegrationTestCase;
use RuntimeException;
use UnexpectedValueException;
use Wikimedia\Rdbms\IConnectionProvider;

/**
 * @group Database
 * @covers \MediaWiki\Extension\WikiKGExtractor\ClaimReviewStore
 * @covers \MediaWiki\Extension\WikiKGExtractor\ClaimEvidenceAccess
 * @covers \MediaWiki\Extension\WikiKGExtractor\ReviewedProjectionExport
 */
class ReviewedProjectionExportIntegrationTest extends MediaWikiIntegrationTestCase {
	private ClaimReviewStore $store;
	private array $outputDirectories = [];
	private array $possibleRepositoryOutputs = [];

	protected function setUp(): void {
		parent::setUp();
		$this->store = new ClaimReviewStore(
			$this->getServiceContainer()->getConnectionProvider()
		);
	}

	protected function tearDown(): void {
		foreach ( $this->possibleRepositoryOutputs as $path ) {
			if ( file_exists( $path ) || is_link( $path ) ) {
				unlink( $path );
			}
		}
		foreach ( $this->outputDirectories as $directory ) {
			foreach ( scandir( $directory ) ?: [] as $entry ) {
				if ( $entry !== '.' && $entry !== '..' ) {
					unlink( $directory . DIRECTORY_SEPARATOR . $entry );
				}
			}
			rmdir( $directory );
		}
		parent::tearDown();
	}

	public function testKeysetExportIncludesMoreThanFiftyClaimsAndCountsEveryState(): void {
		$source = $this->sourcePage( 'keyset-export' );
		$claims = [];
		for ( $index = 0; $index < 105; $index++ ) {
			$claims[] = $this->claim(
				sprintf( 'keyset-%03d', $index ),
				$source,
				'property',
				sprintf( 'Duration %03d days', $index ),
				[],
				$index !== 0
			);
		}
		$snapshotIds = $this->importClaims( $claims, [ $source ] );
		foreach ( array_slice( $snapshotIds, 0, 103 ) as $snapshotId ) {
			$this->approve( $snapshotId );
		}
		$this->reject( $snapshotIds[104] );

		$document = $this->exporter()->export( $this->sysopAuthority() );

		$this->assertSame(
			[ 'schema_version', 'snapshot_count', 'source_watermark', 'claims' ],
			array_keys( $document )
		);
		$this->assertSame( 'wikikg.reviewed-projection.v1', $document['schema_version'] );
		$this->assertSame( 105, $document['snapshot_count'] );
		$this->assertCount( 102, $document['claims'] );
		$this->assertSame(
			$document['claims'],
			$this->sortClaimsBySnapshotId( $document['claims'] )
		);
		$this->assertSame(
			$this->expectedWatermark( $snapshotIds ),
			$document['source_watermark']
		);
		$this->assertSchemaValid( $document );
	}

	public function testApprovedConflictsRemainSeparateAndBothClaimShapesMatchSchema(): void {
		$source = $this->sourcePage( 'conflicts-and-shapes' );
		$propertyA = $this->claim(
			'conflict-100-days',
			$source,
			'property',
			'100 days',
			[ 'season' => 'dry', 'source_context' => (object)[] ]
		);
		$propertyB = $this->claim(
			'conflict-180-days',
			$source,
			'property',
			'180 days'
		);
		$relationship = $this->claim(
			'relationship-shape',
			$source,
			'relationship',
			null
		);
		$ids = $this->importClaims( [ $propertyA, $propertyB, $relationship ], [ $source ] );
		foreach ( $ids as $snapshotId ) {
			$this->approve( $snapshotId );
		}

		$document = $this->exporter()->export( $this->sysopAuthority() );
		$this->assertCount( 3, $document['claims'] );
		$this->assertSchemaValid( $document );

		$values = [];
		$relationshipExport = null;
		foreach ( $document['claims'] as $claim ) {
			$this->assertSame( [
				'snapshot_id', 'snapshot_hash', 'claim_id', 'review_version', 'claim_type',
				'subject', 'predicate', 'qualifiers', 'evidence',
				$claim['claim_type'] === 'property' ? 'value' : 'object',
			], array_keys( $claim ) );
			$this->assertSame( 1, $claim['review_version'] );
			$this->assertCount( 1, $claim['evidence'] );
			$this->assertSame( array_keys( $propertyA['evidence'][0] ), array_keys( $claim['evidence'][0] ) );
			if ( $claim['claim_type'] === 'property' ) {
				$values[] = $claim['value'];
				$this->assertInstanceOf( \stdClass::class, $claim['qualifiers'] );
				if ( $claim['value'] === '100 days' ) {
					$this->assertInstanceOf( \stdClass::class, $claim['qualifiers']->source_context );
				}
			} else {
				$relationshipExport = $claim;
			}
		}
		sort( $values, SORT_STRING );
		$this->assertSame( [ '100 days', '180 days' ], $values );
		$this->assertNotNull( $relationshipExport );
		$this->assertArrayHasKey( 'object', $relationshipExport );
		$this->assertArrayNotHasKey( 'value', $relationshipExport );
		$this->assertSame( $propertyA['evidence'], $this->exportedClaimForId(
			$document,
			$propertyA['snapshot_id']
		)['evidence'] );
	}

	public function testLaterRejectionRemovesPreviouslyApprovedClaim(): void {
		$source = $this->sourcePage( 'approval-then-rejection' );
		$claim = $this->claim( 'approval-revocation', $source, 'property', '100 days' );
		[ $snapshotId ] = $this->importClaims( [ $claim ], [ $source ] );
		$this->approve( $snapshotId );
		$approvedDocument = $this->exporter()->export( $this->sysopAuthority() );
		$this->assertCount( 1, $approvedDocument['claims'] );

		$this->reject( $snapshotId, 1 );
		$rejectedDocument = $this->exporter()->export( $this->sysopAuthority() );

		$this->assertSame( 1, $rejectedDocument['snapshot_count'] );
		$this->assertSame( [], $rejectedDocument['claims'] );
		$this->assertNotSame( $approvedDocument['source_watermark'], $rejectedDocument['source_watermark'] );
		$this->assertSame(
			hash( 'sha256', json_encode(
				[ [ $snapshotId, 'rejected', 2 ] ],
				JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR
			) ),
			$rejectedDocument['source_watermark']
		);
	}

	public function testRegularUserCannotExportApprovedClaims(): void {
		$source = $this->sourcePage( 'unprivileged-export' );
		$claim = $this->claim( 'unprivileged-export', $source, 'property', 'Private value' );
		[ $snapshotId ] = $this->importClaims( [ $claim ], [ $source ] );
		$this->approve( $snapshotId );

		$this->expectException( RuntimeException::class );
		$this->expectExceptionMessage( 'requires the review right' );
		$this->exporter()->export( $this->getTestUser()->getAuthority() );
	}

	public function testNamedActorsAuthorityCanUseTheFileExportContract(): void {
		$actor = $this->getTestUser( [ 'sysop' ] );
		$this->assertInstanceOf( Authority::class, $actor->getAuthority() );
		$directory = $this->privateOutputDirectory();
		$document = $this->exporter()->exportToFile(
			$actor->getAuthority(), $directory . '/named-authority.json',
			$this->repositoryRoot(), $this->repositoryRoot()
		);
		$this->assertSame( [], $document['claims'] );
		$this->assertSame( 0600, fileperms( $directory . '/named-authority.json' ) & 0777 );
	}

	public function testEmptyAndWhitespaceReviewReasonsDoNotBlockApprovedOrRejectedExports(): void {
		$source = $this->sourcePage( 'optional-review-reasons' );
		$cases = [
			[ 'approved', '' ],
			[ 'approved', " \t\n" ],
			[ 'rejected', '' ],
			[ 'rejected', " \t\n" ],
		];
		$claims = [];
		foreach ( $cases as $index => $case ) {
			$claims[] = $this->claim( "optional-reason-$index", $source, 'property', "Value $index" );
		}
		$ids = $this->importClaims( $claims, [ $source ] );
		foreach ( $cases as $index => [ $status, $reason ] ) {
			$this->assertNotNull( $this->store->recordDecision(
				$ids[$index], 7, $status, $reason, 0
			) );
			$review = $this->store->getReview( $ids[$index] );
			$this->assertSame( $reason, $review['latest_review']['reason'] );
			$this->assertSame( $reason, $review['review_events'][0]['reason'] );
		}

		$document = $this->exporter()->export( $this->sysopAuthority() );
		$this->assertSame( 4, $document['snapshot_count'] );
		$approvedIds = array_slice( $ids, 0, 2 );
		sort( $approvedIds, SORT_STRING );
		$this->assertSame( $approvedIds, array_column( $document['claims'], 'snapshot_id' ) );
		$this->assertSchemaValid( $document );
	}

	public function testMultipleClaimsUnderOneSnapshotAbortRatherThanDropAClaim(): void {
		$source = $this->sourcePage( 'duplicate-snapshot-claim' );
		$claim = $this->claim( 'duplicate-snapshot-claim', $source, 'property', '100 days' );
		[ $snapshotId ] = $this->importClaims( [ $claim ], [ $source ] );
		$this->approve( $snapshotId );
		$db = $this->getServiceContainer()->getConnectionProvider()->getPrimaryDatabase();
		$row = $db->newSelectQueryBuilder()
			->select( [ 'wikikg_semantic_data' ] )
			->from( 'wikikg_claim' )
			->where( [ 'wikikg_snapshot_id' => $snapshotId ] )
			->fetchRow();
		$db->newInsertQueryBuilder()
			->insertInto( 'wikikg_claim' )
			->row( [
				'wikikg_snapshot_id' => $snapshotId,
				'wikikg_claim_id' => hash( 'sha256', 'unexpected-second-claim' ),
				'wikikg_semantic_data' => $row->wikikg_semantic_data,
				'wikikg_traceability_complete' => 1,
			] )
			->caller( __METHOD__ )
			->execute();

		$this->expectException( UnexpectedValueException::class );
		$this->expectExceptionMessage( 'multiple claims' );
		$this->exporter()->export( $this->sysopAuthority() );
	}

	public function testChangedSourceRevisionIsOmittedWithoutChangingTheReviewWatermark(): void {
		$source = $this->sourcePage( 'changed-source-revision' );
		$claim = $this->claim( 'stale-source', $source, 'property', '100 days' );
		[ $snapshotId ] = $this->importClaims( [ $claim ], [ $source ] );
		$this->approve( $snapshotId );
		$before = $this->exporter()->export( $this->sysopAuthority() );
		$this->assertCount( 1, $before['claims'] );

		$this->editPage( $source['title'], 'A newer synthetic source revision.' );
		$after = $this->exporter()->export( $this->sysopAuthority() );

		$this->assertSame( 1, $after['snapshot_count'] );
		$this->assertSame( [], $after['claims'] );
		$this->assertSame( $before['source_watermark'], $after['source_watermark'] );
	}

	public function testSuppressedAndUnreadableEvidenceAreOmitted(): void {
		$suppressedSource = $this->sourcePage( 'suppressed-evidence' );
		$unreadableSource = $this->sourcePage( 'unreadable-evidence' );
		$claims = [
			$this->claim( 'suppressed-claim', $suppressedSource, 'property', 'Suppressed value' ),
			$this->claim( 'unreadable-claim', $unreadableSource, 'property', 'Unreadable value' ),
		];
		$ids = $this->importClaims( $claims, [ $suppressedSource, $unreadableSource ] );
		foreach ( $ids as $snapshotId ) {
			$this->approve( $snapshotId );
		}
		$this->editPage( $suppressedSource['title'], 'A newer synthetic source revision.' );
		$this->suppressRevision( $suppressedSource['revision_id'] );
		$sysop = $this->sysopAuthority();
		$this->assertFalse( ClaimEvidenceAccess::canRead(
			$sysop,
			$this->store->getReview( $ids[0] )['evidence']
		) );
		$this->setGroupPermissions( [
			'*' => [ 'read' => false ],
			'user' => [ 'read' => false ],
			'autoconfirmed' => [ 'read' => false ],
			'sysop' => [ 'read' => false ],
			'bureaucrat' => [ 'read' => false ],
		] );

		$document = $this->exporter()->export( $this->sysopAuthority() );

		$this->assertSame( 2, $document['snapshot_count'] );
		$this->assertSame( [], $document['claims'] );
	}

	public function testExportFileIsOwnerOnlyCompleteAndNeverOverwrites(): void {
		$source = $this->sourcePage( 'private-file' );
		$claim = $this->claim( 'private-file-claim', $source, 'property', '100 days' );
		[ $snapshotId ] = $this->importClaims( [ $claim ], [ $source ] );
		$this->approve( $snapshotId );
		$directory = $this->privateOutputDirectory();
		$outputPath = $directory . '/reviewed.json';
		$exporter = $this->exporter();

		$document = $exporter->exportToFile(
			$this->sysopAuthority(),
			$outputPath,
			$this->repositoryRoot(),
			$this->repositoryRoot()
		);
		$decoded = json_decode( file_get_contents( $outputPath ), true, 512, JSON_THROW_ON_ERROR );

		$this->assertSame( 0600, fileperms( $outputPath ) & 0777 );
		$this->assertSame( 1, $decoded['snapshot_count'] );
		$this->assertCount( 1, $decoded['claims'] );
		$this->assertSame( $document['source_watermark'], $decoded['source_watermark'] );
		$this->assertSame( [ '.', '..', 'reviewed.json' ], scandir( $directory ) );
		$this->expectException( \InvalidArgumentException::class );
		$exporter->exportToFile(
			$this->sysopAuthority(),
			$outputPath,
			$this->repositoryRoot(),
			$this->repositoryRoot()
		);
	}

	public function testExportRefusesRepositoryAndDocumentRootDestinations(): void {
		$exporter = $this->exporter();
		$repositoryRoot = $this->repositoryRoot();
		$repositoryPath = $repositoryRoot . '/reviewed-projection-' . bin2hex( random_bytes( 8 ) ) . '.json';
		$this->possibleRepositoryOutputs[] = $repositoryPath;
		try {
			$exporter->exportToFile(
				$this->sysopAuthority(),
				$repositoryPath,
				$repositoryRoot,
				'/tmp'
			);
			$this->fail( 'Repository paths must be rejected before collection.' );
		} catch ( \InvalidArgumentException $exception ) {
			$this->assertStringContainsString( 'outside', $exception->getMessage() );
		}
		$this->assertFileDoesNotExist( $repositoryPath );

		$documentRoot = $this->privateOutputDirectory();
		$documentRootPath = $documentRoot . '/reviewed-projection.json';
		try {
			$exporter->exportToFile(
				$this->sysopAuthority(),
				$documentRootPath,
				$repositoryRoot,
				$documentRoot
			);
			$this->fail( 'Document-root paths must be rejected before collection.' );
		} catch ( \InvalidArgumentException $exception ) {
			$this->assertStringContainsString( 'outside', $exception->getMessage() );
		}
		$this->assertFileDoesNotExist( $documentRootPath );
	}

	public function testExportRefusesDirectoryOwnedByAnotherUserBeforeReadingTheStore(): void {
		if ( !function_exists( 'posix_geteuid' ) || posix_geteuid() !== 0 ) {
			$this->markTestSkipped( 'Different-owner fixture requires an isolated privileged test process.' );
		}
		$directory = $this->privateOutputDirectory();
		$this->assertTrue( chown( $directory, 65534 ) );
		$provider = $this->createMock( IConnectionProvider::class );
		$provider->expects( $this->never() )->method( 'getPrimaryDatabase' );
		$exporter = new ReviewedProjectionExport( new ClaimReviewStore( $provider ) );
		$outputPath = $directory . '/other-owner.json';
		try {
			$exporter->exportToFile(
				$this->sysopAuthority(),
				$outputPath,
				$this->repositoryRoot(),
				$this->repositoryRoot()
			);
			$this->fail( 'Another user must not control the private export directory.' );
		} catch ( \InvalidArgumentException $exception ) {
			$this->assertStringContainsString( 'owned by the current process', $exception->getMessage() );
		} finally {
			$this->assertTrue( chown( $directory, posix_geteuid() ) );
		}
		$this->assertFileDoesNotExist( $outputPath );
		$this->assertSame( [ '.', '..' ], scandir( $directory ) );
	}

	public function testOutputDirectoryLosingPrivacyDuringExportLeavesNoPayload(): void {
		$source = $this->sourcePage( 'directory-changes-during-export' );
		$claim = $this->claim( 'directory-changes-during-export', $source, 'property', '100 days' );
		[ $snapshotId ] = $this->importClaims( [ $claim ], [ $source ] );
		$this->approve( $snapshotId );
		$directory = $this->privateOutputDirectory();
		$outputPath = $directory . '/privacy-changed.json';
		$changed = false;
		$this->setTemporaryHook(
			'getUserPermissionsErrorsExpensive',
			function ( $title, $user, $action, &$result ) use ( &$changed, $directory, $source ) {
				if ( !$changed && $action === 'read' && $title->getText() === $source['title'] ) {
					$changed = true;
					chmod( $directory, 0755 );
				}
			},
			false
		);
		try {
			$this->exporter()->exportToFile(
				$this->sysopAuthority(),
				$outputPath,
				$this->repositoryRoot(),
				$this->repositoryRoot()
			);
			$this->fail( 'A directory that loses privacy during export must be rejected.' );
		} catch ( \InvalidArgumentException $exception ) {
			$this->assertStringContainsString( 'must be private', $exception->getMessage() );
		} finally {
			chmod( $directory, 0700 );
		}
		$this->assertTrue( $changed );
		$this->assertFileDoesNotExist( $outputPath );
		$this->assertSame( [ '.', '..' ], scandir( $directory ) );
	}

	public function testStoreFailureLeavesNoPartialExportFile(): void {
		$provider = $this->createMock( IConnectionProvider::class );
		$provider->method( 'getPrimaryDatabase' )
			->willThrowException( new RuntimeException( 'Synthetic database outage.' ) );
		$exporter = new ReviewedProjectionExport( new ClaimReviewStore( $provider ) );
		$directory = $this->privateOutputDirectory();
		$outputPath = $directory . '/failed.json';
		try {
			$exporter->exportToFile(
				$this->sysopAuthority(),
				$outputPath,
				$this->repositoryRoot(),
				$this->repositoryRoot()
			);
			$this->fail( 'Database failure must abort the export.' );
		} catch ( RuntimeException $exception ) {
			$this->assertSame( 'Synthetic database outage.', $exception->getMessage() );
		}

		$this->assertFileDoesNotExist( $outputPath );
		$this->assertSame( [ '.', '..' ], scandir( $directory ) );
	}

	public function testMalformedStoredClaimAbortsWithoutCreatingAnExportFile(): void {
		$source = $this->sourcePage( 'malformed-store-record' );
		$claim = $this->claim( 'malformed-store-record', $source, 'property', '100 days' );
		[ $snapshotId ] = $this->importClaims( [ $claim ], [ $source ] );
		$db = $this->getServiceContainer()->getConnectionProvider()->getPrimaryDatabase();
		$semantic = [
			'claim_type' => 'property',
			'subject' => [ 'type' => 'Crop', 'id' => 'Rice' ],
			'predicate' => 'GROWTH_DURATION',
			'qualifiers' => (object)[],
			'value' => '100 days',
		];
		$malformedSemantic = $semantic;
		$malformedSemantic['unexpected_field'] = 'corrupt fixture';
		$db->newUpdateQueryBuilder()
			->update( 'wikikg_claim' )
			->set( [ 'wikikg_semantic_data' => json_encode(
				$malformedSemantic,
				JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR
			) ] )
			->where( [ 'wikikg_snapshot_id' => $snapshotId ] )
			->caller( __METHOD__ )
			->execute();
		$directory = $this->privateOutputDirectory();
		$outputPath = $directory . '/malformed.json';

		try {
			$this->exporter()->exportToFile(
				$this->sysopAuthority(),
				$outputPath,
				$this->repositoryRoot(),
				$this->repositoryRoot()
			);
			$this->fail( 'Unexpected stored fields must abort the export.' );
		} catch ( UnexpectedValueException $exception ) {
			$this->assertStringContainsString( 'fields are invalid', $exception->getMessage() );
		}

		$this->assertFileDoesNotExist( $outputPath );
		$this->assertSame( [ '.', '..' ], scandir( $directory ) );

		$db->newUpdateQueryBuilder()
			->update( 'wikikg_claim' )
			->set( [
				'wikikg_semantic_data' => json_encode(
					$semantic,
					JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR
				),
				'wikikg_traceability_complete' => 2,
			] )
			->where( [ 'wikikg_snapshot_id' => $snapshotId ] )
			->caller( __METHOD__ )
			->execute();
		$flagPath = $directory . '/malformed-flag.json';
		try {
			$this->exporter()->exportToFile(
				$this->sysopAuthority(),
				$flagPath,
				$this->repositoryRoot(),
				$this->repositoryRoot()
			);
			$this->fail( 'Unexpected traceability flags must abort the export.' );
		} catch ( UnexpectedValueException $exception ) {
			$this->assertStringContainsString( 'traceability flag is invalid', $exception->getMessage() );
		}
		$this->assertFileDoesNotExist( $flagPath );
		$this->assertSame( [ '.', '..' ], scandir( $directory ) );
	}

	public function testReviewChangeDuringCollectionAbortsBeforeInstallingTheFile(): void {
		$source = $this->sourcePage( 'review-change-during-export' );
		$claim = $this->claim( 'review-changes-during-export', $source, 'property', '100 days' );
		[ $snapshotId ] = $this->importClaims( [ $claim ], [ $source ] );
		$this->approve( $snapshotId );
		$authority = $this->sysopAuthority();
		$directory = $this->privateOutputDirectory();
		$outputPath = $directory . '/changed.json';
		$changed = false;
		$this->setTemporaryHook(
			'getUserPermissionsErrorsExpensive',
			function ( $title, $user, $action, &$result ) use ( &$changed, $snapshotId, $source ) {
				if ( !$changed && $action === 'read' && $title->getText() === $source['title'] ) {
					$changed = true;
					$this->reject( $snapshotId, 1 );
				}
			},
			false
		);
		try {
			$this->exporter()->exportToFile(
				$authority,
				$outputPath,
				$this->repositoryRoot(),
				$this->repositoryRoot()
			);
			$this->fail( 'A review change during collection must abort the export.' );
		} catch ( RuntimeException $exception ) {
			$this->assertSame( 'Review state changed during export.', $exception->getMessage() );
		}

		$this->assertTrue( $changed, 'The permission check must trigger the concurrent state change.' );
		$this->assertSame( 'rejected', $this->store->getLatestReview( $snapshotId )['status'] );
		$this->assertFileDoesNotExist( $outputPath );
		$this->assertSame( [ '.', '..' ], scandir( $directory ) );
	}

	public function testNewlyEligibleOutOfScopeClaimDuringCollectionAbortsWithoutPayload(): void {
		$allowedSource = $this->sourcePage( 'scope-allowed-first' );
		$outsideSource = $this->sourcePage( 'scope-outside-later' );
		$allowed = $this->claim( 'scope-allowed-first', $allowedSource, 'property', '100 days' );
		$outside = $this->claim( 'scope-outside-later', $outsideSource, 'property', '200 days' );
		while ( strcmp( $allowed['snapshot_id'], $outside['snapshot_id'] ) > 0 ) {
			$allowed = $this->claim( 'scope-allowed-first-' . bin2hex( random_bytes( 2 ) ), $allowedSource, 'property', '100 days' );
		}
		[ $allowedId, $outsideId ] = $this->importClaims( [ $allowed, $outside ], [ $allowedSource, $outsideSource ] );
		$this->approve( $allowedId );
		$changed = false;
		$this->setTemporaryHook(
			'getUserPermissionsErrorsExpensive',
			function ( $title, $user, $action, &$result ) use ( &$changed, $allowedSource, $outsideId ) {
				if ( !$changed && $action === 'read' && $title->getText() === $allowedSource['title'] ) {
					$changed = true;
					$this->approve( $outsideId );
				}
			},
			false
		);
		$directory = $this->privateOutputDirectory();
		$output = $directory . '/scope-race.json';
		$exporter = new ReviewedProjectionExport(
			$this->store,
			static function ( array $claim ) use ( $allowedSource ): void {
				foreach ( $claim['evidence'] as $evidence ) {
					if ( $evidence['page_title'] !== $allowedSource['title'] ) {
						throw new RuntimeException( 'Projection claim is outside development scope.' );
					}
				}
			}
		);
		try {
			$exporter->exportToFile( $this->sysopAuthority(), $output, $this->repositoryRoot(), $this->repositoryRoot() );
			$this->fail( 'A claim becoming eligible outside scope during collection must abort.' );
		} catch ( RuntimeException $exception ) {
			$this->assertSame( 'Projection claim is outside development scope.', $exception->getMessage() );
		}
		$this->assertTrue( $changed );
		$this->assertFileDoesNotExist( $output );
		$this->assertSame( [ '.', '..' ], scandir( $directory ) );
	}

	public function testScopeAssertionRunsAgainOnFreshCurrentnessClaimBeforeWriting(): void {
		$source = $this->sourcePage( 'scope-second-pass' );
		$claim = $this->claim( 'scope-second-pass', $source, 'property', '100 days' );
		[ $snapshotId ] = $this->importClaims( [ $claim ], [ $source ] );
		$this->approve( $snapshotId );
		$calls = 0;
		$exporter = new ReviewedProjectionExport(
			$this->store,
			static function ( array $projection ) use ( &$calls ): void {
				$calls++;
				if ( $calls === 2 ) {
					throw new RuntimeException( 'Fresh source identity is no longer in scope.' );
				}
			}
		);
		$directory = $this->privateOutputDirectory();
		$output = $directory . '/late-scope-change.json';
		try {
			$exporter->exportToFile( $this->sysopAuthority(), $output, $this->repositoryRoot(), $this->repositoryRoot() );
			$this->fail( 'A currentness-pass scope failure must abort before installation.' );
		} catch ( RuntimeException $exception ) {
			$this->assertSame( 'Fresh source identity is no longer in scope.', $exception->getMessage() );
		}
		$this->assertSame( 2, $calls );
		$this->assertFileDoesNotExist( $output );
		$this->assertSame( [ '.', '..' ], scandir( $directory ) );
	}

	public function testSourceChangedAfterCollectionAbortsBeforeInstallingTheFile(): void {
		$source = $this->sourcePage( 'late-source-change' );
		$claim = $this->claim( 'late-source-change', $source, 'property', '100 days' );
		[ $snapshotId ] = $this->importClaims( [ $claim ], [ $source ] );
		$this->approve( $snapshotId );
		$readChecks = 0;
		$changed = false;
		$this->setTemporaryHook(
			'getUserPermissionsErrorsExpensive',
			function ( $title, $user, $action, &$result ) use ( &$readChecks, &$changed, $source ) {
				if ( $action === 'read' && $title->getText() === $source['title'] ) {
					$readChecks++;
					if ( !$changed && $readChecks === 2 ) {
						$changed = true;
						$this->editPage( $source['title'], 'Changed after the export collection pass.' );
					}
				}
			},
			false
		);
		$directory = $this->privateOutputDirectory();
		$output = $directory . '/late-source-change.json';
		try {
			$this->exporter()->exportToFile(
				$this->sysopAuthority(), $output, $this->repositoryRoot(), $this->repositoryRoot()
			);
			$this->fail( 'A source revision changing after collection must abort the export.' );
		} catch ( RuntimeException $exception ) {
			$this->assertSame( 'Evidence access changed during export.', $exception->getMessage() );
		}
		$this->assertTrue( $changed );
		$this->assertFileDoesNotExist( $output );
		$this->assertSame( [ '.', '..' ], scandir( $directory ) );
	}

	private function exporter(): ReviewedProjectionExport {
		return new ReviewedProjectionExport( $this->store );
	}

	private function sysopAuthority(): Authority {
		return $this->getTestUser( [ 'sysop' ] )->getAuthority();
	}

	private function sourcePage( string $key ): array {
		$title = 'WikiKG reviewed projection source ' . $key;
		$revision = $this->editPage( $title, 'Synthetic source text for ' . $key . '.' )->getNewRevision();
		return [
			'title' => $title,
			'page_id' => $revision->getPageId(),
			'revision_id' => $revision->getId(),
			'content_hash' => hash( 'sha256', $title . ':' . $revision->getId() ),
		];
	}

	private function claim(
		string $key,
		array $source,
		string $claimType,
		?string $value,
		array $qualifiers = [],
		bool $traceabilityComplete = true
	): array {
		$span = 'Synthetic evidence for ' . $key . '.';
		$claim = [
			'claim_id' => hash( 'sha256', 'claim:' . $key ),
			'snapshot_id' => hash( 'sha256', 'snapshot:' . $key ),
			'claim_type' => $claimType,
			'subject' => [ 'type' => 'Crop', 'id' => 'Rice' ],
			'predicate' => $claimType === 'property' ? 'GROWTH_DURATION' : 'AFFECTED_BY',
			'qualifiers' => $qualifiers,
			'evidence' => [ [
				'evidence_id' => hash( 'sha256', 'evidence:' . $key ),
				'page_id' => $source['page_id'],
				'revision_id' => $source['revision_id'],
				'content_hash' => $source['content_hash'],
				'page_title' => $source['title'],
				'extraction_method' => 'rules',
				'extractor_version' => '2.4.0',
				'location_type' => 'source_span',
				'source_offset_start' => 0,
				'source_offset_end' => strlen( $span ),
				'supporting_span' => $span,
				'span_hash' => hash( 'sha256', $span ),
			] ],
			'traceability_complete' => $traceabilityComplete,
			'review' => [ 'status' => 'pending' ],
		];
		if ( $claimType === 'relationship' ) {
			$claim['object'] = [ 'type' => 'Pest', 'id' => 'Brown planthopper' ];
		} else {
			$claim['value'] = $value;
		}
		return $claim;
	}

	private function importClaims( array $claims, array $sourcePages ): array {
		return $this->store->importVerified( [
			'schema_version' => '1.0',
			'metadata' => [
				'extractor_version' => '2.4.0',
				'extraction_method' => 'rules',
				'review_state' => 'pending',
				'source_pages' => $sourcePages,
			],
			'claims' => $claims,
		] );
	}

	private function approve( string $snapshotId ): void {
		$this->assertNotNull( $this->store->recordDecision(
			$snapshotId,
			7,
			'approved',
			'Approved for the synthetic integration test.',
			0
		) );
	}

	private function reject( string $snapshotId, int $expectedVersion = 0 ): void {
		$this->assertNotNull( $this->store->recordDecision(
			$snapshotId,
			8,
			'rejected',
			'Rejected for the synthetic integration test.',
			$expectedVersion
		) );
	}

	private function suppressRevision( int $revisionId ): void {
		$revision = $this->getServiceContainer()->getRevisionLookup()->getRevisionById( $revisionId );
		$this->assertNotNull( $revision );
		$context = RequestContext::getMain();
		$previousUser = $context->getUser();
		$context->setUser( $this->getTestUser( [ 'suppress' ] )->getUser() );
		try {
			$status = \RevisionDeleter::createList(
				'revision',
				$context,
				$revision->getPage(),
				[ $revisionId ]
			)->setVisibility( [
				'value' => [
					RevisionRecord::DELETED_TEXT => 1,
					RevisionRecord::DELETED_COMMENT => 1,
					RevisionRecord::DELETED_USER => 1,
					RevisionRecord::DELETED_RESTRICTED => 1,
				],
				'comment' => 'Suppress synthetic reviewed-projection fixture revision.',
			] );
			$this->assertStatusGood( $status );
		} finally {
			$context->setUser( $previousUser );
		}
	}

	/** @param string[] $snapshotIds */
	private function expectedWatermark( array $snapshotIds ): string {
		sort( $snapshotIds, SORT_STRING );
		$tuples = [];
		foreach ( $snapshotIds as $snapshotId ) {
			$latest = $this->store->getLatestReview( $snapshotId );
			$tuples[] = [ $snapshotId, $latest['status'], $latest['version'] ];
		}
		return hash( 'sha256', json_encode(
			$tuples,
			JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR
		) );
	}

	private function assertSchemaValid( array $document ): void {
		[ $status, $diagnostics ] = $this->runSchemaOracle( $document );
		$this->assertSame( 0, $status, "Draft 2020-12 schema oracle failed: $diagnostics" );
	}

	public function testSchemaOracleRejectsWrongSchemaVersion(): void {
		$document = [
			'schema_version' => 'wikikg.reviewed-projection.v2',
			'snapshot_count' => 0,
			'source_watermark' => str_repeat( 'a', 64 ),
			'claims' => [],
		];
		$this->assertSchemaInvalid( $document );
	}

	public function testSchemaOracleRejectsManyErrorsWithoutBlockingDiagnostics(): void {
		[ $status, $diagnostics ] = $this->runSchemaOracle( [
			'schema_version' => 'wikikg.reviewed-projection.v1',
			'snapshot_count' => 25,
			'source_watermark' => str_repeat( 'a', 64 ),
			'claims' => array_fill( 0, 25, str_repeat( 'x', 1024 ) ),
		] );
		$this->assertSame( 1, $status, $diagnostics );
		$this->assertLessThan( 11000, strlen( $diagnostics ) );
		$this->assertStringContainsString( '5 more validation errors', $diagnostics );
	}

	private function assertSchemaInvalid( array $document ): void {
		[ $status, $diagnostics ] = $this->runSchemaOracle( $document );
		$this->assertSame( 1, $status, "Invalid document must fail schema validation: $diagnostics" );
	}

	private function runSchemaOracle( array $document ): array {
		$extensionRoot = dirname( __DIR__, 3 );
		$validator = $extensionRoot . '/tests/python/validate_reviewed_projection_schema.py';
		$python = getenv( 'WIKIKG_SCHEMA_PYTHON' ) ?: 'python3';
		$input = tmpfile();
		$output = tmpfile();
		$this->assertIsResource( $input );
		$this->assertIsResource( $output );
		try {
			$payload = json_encode( $document, JSON_THROW_ON_ERROR );
			$this->assertSame( strlen( $payload ), fwrite( $input, $payload ) );
			rewind( $input );
			$process = proc_open(
				[ $python, $validator ],
				[ 0 => $input, 1 => $output, 2 => $output ],
				$pipes
			);
			$this->assertIsResource( $process, 'Could not start Draft 2020-12 schema oracle; check WIKIKG_SCHEMA_PYTHON.' );
			$status = proc_close( $process );
			rewind( $output );
			return [ $status, trim( stream_get_contents( $output, 16384 ) ) ];
		} finally {
			fclose( $input );
			fclose( $output );
		}
	}

	private function sortClaimsBySnapshotId( array $claims ): array {
		usort( $claims, static function ( array $left, array $right ): int {
			return strcmp( $left['snapshot_id'], $right['snapshot_id'] );
		} );
		return $claims;
	}

	private function exportedClaimForId( array $document, string $snapshotId ): array {
		foreach ( $document['claims'] as $claim ) {
			if ( $claim['snapshot_id'] === $snapshotId ) {
				return $claim;
			}
		}
		$this->fail( 'Expected snapshot is absent from the export.' );
	}

	private function privateOutputDirectory(): string {
		$directory = sys_get_temp_dir() . '/wikikg-reviewed-export-' . bin2hex( random_bytes( 8 ) );
		$this->assertTrue( mkdir( $directory, 0700 ) );
		$this->outputDirectories[] = $directory;
		return $directory;
	}

	private function repositoryRoot(): string {
		return dirname( __DIR__, 5 );
	}
}
