<?php

namespace MediaWiki\Extension\WikiKGExtractor\Tests\Integration;

use MediaWiki\Extension\WikiKGExtractor\ClaimReviewStore;
use MediaWikiIntegrationTestCase;

/**
 * @group Database
 * @covers \MediaWiki\Extension\WikiKGExtractor\ClaimReviewStore
 */
class ClaimReviewStoreIntegrationTest extends MediaWikiIntegrationTestCase {
	private ClaimReviewStore $store;

	protected function setUp(): void {
		parent::setUp();
		$this->store = new ClaimReviewStore(
			$this->getServiceContainer()->getConnectionProvider()
		);
	}

	public function testClaimsFromOneRunCanHaveDifferentDecisions(): void {
		$first = $this->claim( 'duration-a', 'snapshot-a', 900001, 10, 'Crop A', '100 days' );
		$second = $this->claim( 'duration-b', 'snapshot-b', 900002, 20, 'Crop B', '180 days' );
		$document = $this->document(
			[ $first, $second ],
			[ $this->sourcePage( 900001, 10, 'Crop A' ), $this->sourcePage( 900002, 20, 'Crop B' ) ]
		);

		$snapshotIds = $this->store->importVerified( $document );
		$this->assertSame( [ $first['snapshot_id'], $second['snapshot_id'] ], $snapshotIds );
		$this->store->recordDecision( $snapshotIds[0], 7, 'approved', 'Checked.', 0 );
		$this->store->recordDecision( $snapshotIds[1], 8, 'rejected', 'Not supported.', 0 );

		$this->assertSame( 'approved', $this->store->getReview( $snapshotIds[0] )['latest_review']['status'] );
		$this->assertSame( 'rejected', $this->store->getReview( $snapshotIds[1] )['latest_review']['status'] );
		$this->assertSame( '100 days', $this->store->getReview( $snapshotIds[0] )['value'] );
		$this->assertSame( '180 days', $this->store->getReview( $snapshotIds[1] )['value'] );
		$snapshotsById = [];
		foreach ( $this->store->listSnapshots( 100 ) as $snapshot ) {
			$snapshotsById[$snapshot['snapshot_id']] = $snapshot;
		}
		$this->assertSame( 1, $snapshotsById[$snapshotIds[0]]['claim_count'] );
		$this->assertSame( '180 days', $snapshotsById[$snapshotIds[1]]['claim']['value'] );
	}

	public function testChangingOneClaimsEvidenceCreatesOnlyItsNewPendingSnapshot(): void {
		$firstClaim = $this->claim( 'duration-a', 'snapshot-a-v1', 900011, 11, 'Crop A', '100 days' );
		$stableClaim = $this->claim( 'duration-b', 'snapshot-b-v1', 900012, 12, 'Crop B', '180 days' );
		$firstRun = $this->document(
			[ $firstClaim, $stableClaim ],
			[ $this->sourcePage( 900011, 11, 'Crop A' ), $this->sourcePage( 900012, 12, 'Crop B' ) ]
		);
		$firstIds = $this->store->importVerified( $firstRun );
		$this->store->recordDecision( $firstIds[0], 7, 'rejected', 'Old evidence.', 0 );
		$this->store->recordDecision( $firstIds[1], 8, 'approved', 'Still valid.', 0 );

		$changedClaim = $this->claim( 'duration-a', 'snapshot-a-v2', 900011, 13, 'Crop A', '100 days' );
		$secondRun = $this->document(
			[ $changedClaim, $stableClaim ],
			[ $this->sourcePage( 900011, 13, 'Crop A' ), $this->sourcePage( 900012, 12, 'Crop B' ) ]
		);
		$secondIds = $this->store->importVerified( $secondRun );

		$this->assertNotSame( $firstIds[0], $secondIds[0] );
		$this->assertSame( $firstIds[1], $secondIds[1] );
		$this->assertSame( 'pending', $this->store->getReview( $secondIds[0] )['latest_review']['status'] );
		$this->assertSame( 13, $this->store->getReview( $secondIds[0] )['evidence'][0]['revision_id'] );
		$this->assertSame( 'approved', $this->store->getReview( $secondIds[1] )['latest_review']['status'] );
		$this->assertSame( 'rejected', $this->store->getReview( $firstIds[0] )['latest_review']['status'] );
	}

	public function testRerunIgnoresUnrelatedSourcePageChanges(): void {
		$claim = $this->claim( 'duration-a', 'snapshot-a', 900021, 21, 'Crop A', '100 days' );
		$relevantPage = $this->sourcePage( 900021, 21, 'Crop A' );
		$firstRun = $this->document( [ $claim ], [
			$relevantPage,
			$this->sourcePage( 900022, 22, 'Unrelated page v1' ),
		] );
		$firstIds = $this->store->importVerified( $firstRun );

		$secondRun = $this->document( [ $claim ], [
			$relevantPage,
			$this->sourcePage( 900023, 23, 'Unrelated page v2' ),
		] );
		$secondIds = $this->store->importVerified( $secondRun );

		$this->assertSame( $firstIds, $secondIds );
		$review = $this->store->getReview( $firstIds[0] );
		$this->assertSame( 1, $review['source_count'] );
		$this->assertSame( [ 'Crop A' ], array_column( $review['metadata']['source_pages'], 'title' ) );
	}

	public function testReusingSnapshotIdForChangedEvidenceIsRejected(): void {
		$original = $this->claim( 'duration-a', 'same-snapshot', 900031, 31, 'Crop A', '100 days' );
		$this->store->importVerified( $this->document(
			[ $original ], [ $this->sourcePage( 900031, 31, 'Crop A' ) ]
		) );

		$changed = $this->claim( 'duration-a', 'same-snapshot', 900031, 32, 'Crop A', '100 days' );
		$this->expectException( \UnexpectedValueException::class );
		$this->store->importVerified( $this->document(
			[ $changed ], [ $this->sourcePage( 900031, 32, 'Crop A' ) ]
		) );
	}

	public function testImportRejectsMoreThanFiveHundredClaimsAtTheStorageSeam(): void {
		$claim = $this->claim( 'duration-a', 'over-limit', 900035, 35, 'Crop A', '100 days' );
		$document = $this->document(
			array_fill( 0, 501, $claim ),
			[ $this->sourcePage( 900035, 35, 'Crop A' ) ]
		);

		$this->expectException( \InvalidArgumentException::class );
		$this->store->importVerified( $document );
	}

	public function testConflictingClaimsRemainSeparateSnapshots(): void {
		$first = $this->claim( 'duration-100', 'conflict-100', 900041, 41, 'Crop A', '100 days' );
		$second = $this->claim( 'duration-180', 'conflict-180', 900041, 41, 'Crop A', '180 days' );
		$ids = $this->store->importVerified( $this->document(
			[ $first, $second ],
			[ $this->sourcePage( 900041, 41, 'Crop A' ) ]
		) );

		$this->assertCount( 2, $ids );
		$this->assertNotSame( $ids[0], $ids[1] );
		$this->assertSame( '100 days', $this->store->getReview( $ids[0] )['value'] );
		$this->assertSame( '180 days', $this->store->getReview( $ids[1] )['value'] );
	}

	public function testStaleDecisionDoesNotWriteAndHistoryStaysPerClaim(): void {
		$claim = $this->claim( 'duration-a', 'history-snapshot', 900051, 51, 'Crop A', '100 days' );
		$this->store->importVerified( $this->document(
			[ $claim ], [ $this->sourcePage( 900051, 51, 'Crop A' ) ]
		) );

		$rejected = $this->store->recordDecision(
			$claim['snapshot_id'], 8, 'rejected', 'Evidence is insufficient.', 0
		);
		$this->assertSame( 1, $rejected['version'] );
		$this->assertNull( $this->store->recordDecision(
			$claim['snapshot_id'], 9, 'approved', 'Stale request.', 0
		) );
		$approved = $this->store->recordDecision(
			$claim['snapshot_id'], 9, 'approved', 'Verified in the cited revision.', 1
		);
		$review = $this->store->getReview( $claim['snapshot_id'] );

		$this->assertSame( 2, $approved['version'] );
		$this->assertSame( 'approved', $review['latest_review']['status'] );
		$this->assertSame( [ 'rejected', 'approved' ], array_column( $review['review_events'], 'status' ) );
		$this->assertSame( [ 8, 9 ], array_column( $review['review_events'], 'reviewer_id' ) );
		$this->assertSame( 'Evidence is insufficient.', $review['review_events'][0]['reason'] );
	}

	public function testFailureInSecondClaimRollsBackTheWholeDocumentImport(): void {
		$first = $this->claim( 'duration-a', 'rollback-first', 900061, 61, 'Crop A', '100 days' );
		$second = $this->claim( 'duration-b', 'rollback-second', 900062, 62, 'Crop B', '180 days' );
		$this->store->importVerified( $this->document(
			[ $second ], [ $this->sourcePage( 900062, 62, 'Crop B' ) ]
		) );
		$changedSecond = $this->claim( 'duration-b', 'rollback-second', 900062, 63, 'Crop B', '180 days' );
		$document = $this->document( [ $first, $changedSecond ], [
			$this->sourcePage( 900061, 61, 'Crop A' ),
			$this->sourcePage( 900062, 63, 'Crop B' ),
		] );
		try {
			$this->store->importVerified( $document );
			$this->fail( 'The second claim must reject reuse of its snapshot ID.' );
		} catch ( \UnexpectedValueException $exception ) {
			$this->assertStringContainsString( 'collision', $exception->getMessage() );
		}

		$this->assertNull( $this->store->getReview( $first['snapshot_id'] ) );
		$this->assertSame( 62, $this->store->getReview( $second['snapshot_id'] )['evidence'][0]['revision_id'] );
		$db = $this->getServiceContainer()->getConnectionProvider()->getPrimaryDatabase();
		foreach ( [ 'wikikg_snapshot', 'wikikg_source_page', 'wikikg_claim', 'wikikg_evidence' ] as $tableName ) {
			$row = $db->newSelectQueryBuilder()
				->select( [ 'row_count' => 'COUNT(*)' ] )
				->from( $tableName )
				->where( [ 'wikikg_snapshot_id' => $first['snapshot_id'] ] )
				->fetchRow();
			$this->assertSame( 0, (int)$row->row_count, "$tableName should roll back." );
		}
	}

	private function claim(
		string $semanticKey,
		string $snapshotKey,
		int $pageId,
		int $revisionId,
		string $title,
		string $value
	): array {
		$page = $this->sourcePage( $pageId, $revisionId, $title );
		$span = 'Evidence: ' . $value . ' at revision ' . $revisionId . '.';
		return [
			'claim_id' => hash( 'sha256', 'semantic:' . $semanticKey ),
			'snapshot_id' => hash( 'sha256', 'snapshot:' . $snapshotKey ),
			'claim_type' => 'property',
			'subject' => [ 'type' => 'Crop', 'id' => $title ],
			'predicate' => 'GROWTH_DURATION',
			'value' => $value,
			'qualifiers' => [],
			'evidence' => [ [
				'evidence_id' => hash(
					'sha256',
					'evidence:' . $pageId . ':' . $revisionId . ':' . $page['content_hash'] . ':' . $span
				),
				'page_id' => $page['page_id'],
				'revision_id' => $page['revision_id'],
				'content_hash' => $page['content_hash'],
				'page_title' => $page['title'],
				'extraction_method' => 'rules',
				'extractor_version' => '2.3.1',
				'location_type' => 'source_span',
				'source_offset_start' => 0,
				'source_offset_end' => strlen( $span ),
				'supporting_span' => $span,
				'span_hash' => hash( 'sha256', $span ),
			] ],
			'traceability_complete' => true,
			'review' => [ 'status' => 'pending' ],
		];
	}

	private function sourcePage( int $pageId, int $revisionId, string $title ): array {
		return [
			'title' => $title,
			'page_id' => $pageId,
			'revision_id' => $revisionId,
			'content_hash' => hash( 'sha256', "$title:$pageId:$revisionId" ),
		];
	}

	private function document( array $claims, array $sourcePages ): array {
		$byPredicate = [];
		foreach ( $claims as $claim ) {
			$predicate = $claim['predicate'];
			$byPredicate[$predicate] = ( $byPredicate[$predicate] ?? 0 ) + 1;
		}
		return [
			'schema_version' => '1.0',
			'metadata' => [
				'extractor_version' => '2.3.1',
				'extraction_method' => 'rules',
				'review_state' => 'pending',
				'source_pages' => $sourcePages,
			],
			'summary' => [
				'claim_count' => count( $claims ),
				'traceability_complete_count' => count( $claims ),
				'claims_by_predicate' => $byPredicate,
			],
			'claims' => $claims,
		];
	}
}
