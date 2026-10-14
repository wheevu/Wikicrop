<?php

namespace MediaWiki\Extension\WikiKGExtractor\Tests\Integration;

use MediaWiki\Extension\WikiKGExtractor\ClaimReviewStore;
use MediaWiki\Extension\WikiKGExtractor\CurrentEvidenceGate;
use MediaWiki\Extension\WikiKGExtractor\ReviewedProjectionExport;
use MediaWiki\Permissions\Authority;
use MediaWikiIntegrationTestCase;
use Wikimedia\Rdbms\IDatabase;

/**
 * @group Database
 * @covers \MediaWiki\Extension\WikiKGExtractor\CurrentEvidenceGate
 */
class CurrentEvidenceGateIntegrationTest extends MediaWikiIntegrationTestCase {
	private ClaimReviewStore $store;
	private CurrentEvidenceGate $gate;
	private Authority $authority;
	private IDatabase $gateDb;
	private bool $queueCreated = false;

	protected function setUp(): void {
		parent::setUp();
		$this->store = new ClaimReviewStore( $this->getServiceContainer()->getConnectionProvider() );
		$this->gate = new CurrentEvidenceGate( $this->store );
		$this->authority = $this->getTestSysop()->getAuthority();
		$this->gateDb = $this->getServiceContainer()->getConnectionProvider()->getPrimaryDatabase();
		$this->createQueueFixture();
	}

	protected function tearDown(): void {
		if ( $this->queueCreated ) {
			$this->gateDb->query(
				'DROP TABLE ' . $this->gateDb->tableName( 'approve_queue' ),
				__METHOD__
			);
		}
		parent::tearDown();
	}

	public function testApprovedClaimReturnsCanonicalCurrentStoreClaim(): void {
		$fixture = $this->approvedFixture( 'authorized-source' );
		$candidate = $this->candidate( $fixture );

		$result = $this->gate->authorize( $this->authority, [ $candidate ] );

		$this->assertSame( 'ok', $result['status'] );
		$this->assertCount( 1, $result['claims'] );
		$this->assertSame( $fixture['snapshot_id'], $result['claims'][0]['snapshot_id'] );
		$this->assertSame( '140 days', $result['claims'][0]['value'] );
		$this->assertArrayNotHasKey( 'client_text', $result['claims'][0] );
	}

	public function testRejectionAfterSuccessPreservesHistoryAndRevokesEvidence(): void {
		$fixture = $this->approvedFixture( 'rejected-after-success' );
		$candidate = $this->candidate( $fixture );
		$this->assertSame( 'ok', $this->gate->authorize( $this->authority, [ $candidate ] )['status'] );

		$this->store->recordDecision( $fixture['snapshot_id'], $this->reviewerId(), 'rejected', 'Revoked.', 1 );

		$this->assertSame(
			[ 'status' => 'dependency_failure', 'claims' => [] ],
			$this->gate->authorize( $this->authority, [ $candidate ] )
		);
		$review = $this->store->getReview( $fixture['snapshot_id'] );
		$this->assertSame( [ 'approved', 'rejected' ], array_column( $review['review_events'], 'status' ) );
	}

	public function testSourceRevisionChangeRevokesPinnedClaim(): void {
		$fixture = $this->approvedFixture( 'source-revision-change' );
		$candidate = $this->candidate( $fixture );
		$this->assertSame( 'ok', $this->gate->authorize( $this->authority, [ $candidate ] )['status'] );
		$this->editPage( $fixture['title'], 'Revised source text.' );

		$this->assertSame( 'dependency_failure', $this->gate->authorize(
			$this->authority, [ $candidate ]
		)['status'] );
	}

	public function testReadPermissionLossRevokesPreviouslyApprovedEvidence(): void {
		$fixture = $this->approvedFixture( 'read-permission-loss' );
		$candidate = $this->candidate( $fixture );
		$this->assertSame( 'ok', $this->gate->authorize( $this->authority, [ $candidate ] )['status'] );
		$this->setGroupPermissions( '*', 'read', false );
		$this->setGroupPermissions( 'user', 'read', false );
		$this->setGroupPermissions( 'sysop', 'read', false );

		$this->assertSame( 'dependency_failure', $this->gate->authorize(
			$this->getTestSysop()->getAuthority(), [ $candidate ]
		)['status'] );
	}

	public function testMalformedVersionHashAndDuplicateIdentitiesFailClosed(): void {
		$fixture = $this->approvedFixture( 'malformed-identities' );
		$candidate = $this->candidate( $fixture );
		foreach ( [
			[ [ 'snapshot_id' => 'forged' ] ],
			[ [ 'snapshot_id' => $candidate['snapshot_id'], 'snapshot_hash' => $candidate['snapshot_hash'], 'review_version' => 0 ] ],
			[ [ 'snapshot_id' => $candidate['snapshot_id'], 'snapshot_hash' => str_repeat( '0', 64 ), 'review_version' => 1 ] ],
			[ $candidate, $candidate ],
		] as $candidates ) {
			$this->assertSame( 'dependency_failure', $this->gate->authorize(
				$this->authority, $candidates
			)['status'] );
		}
	}

	public function testObjectKeyOrderDoesNotAlterIdentity(): void {
		$fixture = $this->approvedFixture( 'reordered-identity' );
		$candidate = array_reverse( $this->candidate( $fixture ), true );
		$this->assertSame( 'ok', $this->gate->authorize( $this->authority, [ $candidate ] )['status'] );
	}

	public function testNewlyApprovedSiblingInvalidatesOldManifestWatermark(): void {
		$fixture = $this->approvedFixture( 'watermark-original' );
		$watermark = ( new ReviewedProjectionExport( $this->store ) )->currentWatermark();
		$candidates = [ $this->candidate( $fixture ) ];
		$this->assertSame( 'ok', $this->gate->authorize( $this->authority, $candidates, $watermark )['status'] );
		$this->approvedFixture( 'watermark-new-assertion' );
		$this->assertSame( [ 'status' => 'dependency_failure', 'claims' => [] ],
			$this->gate->authorize( $this->authority, $candidates, $watermark ) );
	}

	public function testSourceTitleNormalizationPreservesPinnedUnderscoreIdentity(): void {
		$fixture = $this->approvedFixture( 'normalized_source_title' );
		$this->assertSame( 'ok', $this->gate->authorize(
			$this->authority, [ $this->candidate( $fixture ) ]
		)['status'] );
	}

	public function testSuppressionAfterSuccessDeniesEvenPrivilegedReviewer(): void {
		$fixture = $this->approvedFixture( 'suppressed-source' );
		$candidate = $this->candidate( $fixture );
		$this->assertSame( 'ok', $this->gate->authorize( $this->authority, [ $candidate ] )['status'] );
		$this->gateDb->newUpdateQueryBuilder()->update( 'revision' )->set( [ 'rev_deleted' => 1 ] )
			->where( [ 'rev_id' => $fixture['revision_id'] ] )->caller( __METHOD__ )->execute();
		$this->assertSame( [ 'status' => 'dependency_failure', 'claims' => [] ],
			$this->gate->authorize( $this->authority, [ $candidate ] ) );
	}

	public function testInternallyValidSpanAbsentFromActualSourceCannotBeAuthorized(): void {
		$fixture = $this->approvedFixture( 'forged-source-span' );
		$candidate = $this->candidate( $fixture );
		$forged = str_repeat( 'x', mb_strlen( $fixture['source_text'], 'UTF-8' ) );
		$this->gateDb->newUpdateQueryBuilder()->update( 'wikikg_evidence' )->set( [
			'wikikg_supporting_span' => $forged, 'wikikg_span_hash' => hash( 'sha256', $forged ),
		] )->where( [ 'wikikg_snapshot_id' => $fixture['snapshot_id'] ] )->caller( __METHOD__ )->execute();
		$this->assertSame( [ 'status' => 'dependency_failure', 'claims' => [] ],
			$this->gate->authorize( $this->authority, [ $candidate ] ) );
	}

	public function testCorruptStoreDependencyFailsClosed(): void {
		$fixture = $this->approvedFixture( 'corrupt-store' );
		$candidate = $this->candidate( $fixture );
		$this->gateDb->newUpdateQueryBuilder()->update( 'wikikg_snapshot' )
			->set( [ 'wikikg_metadata' => '{broken' ] )
			->where( [ 'wikikg_snapshot_id' => $fixture['snapshot_id'] ] )->caller( __METHOD__ )->execute();
		$this->assertSame( [ 'status' => 'dependency_failure', 'claims' => [] ],
			$this->gate->authorize( $this->authority, [ $candidate ] ) );
	}

	public function testRequestBoundDeniesRatherThanReturningPartialEvidence(): void {
		$fixture = $this->approvedFixture( 'candidate-bound' );
		$candidates = array_fill( 0, 101, $this->candidate( $fixture ) );
		$this->assertSame( [ 'status' => 'dependency_failure', 'claims' => [] ],
			$this->gate->authorize( $this->authority, $candidates ) );
	}

	public function testTextAuthorizationReturnsSourceTextAndRechecksApprovalQueue(): void {
		$fixture = $this->approvedFixture( 'text-authorized' );
		$textCandidate = [
			'page_id' => $fixture['page_id'], 'revision_id' => $fixture['revision_id'],
			'content_hash' => $fixture['content_hash'],
		];
		$result = $this->gate->authorizeText( $this->authority, [ $textCandidate ] );
		$this->assertSame( 'ok', $result['status'] );
		$this->assertSame( $fixture['source_text'], $result['text'][0]['text'] );

		$this->gateDb->newUpdateQueryBuilder()->update( 'approve_queue' )
			->set( [ 'aq_status' => 'rejected' ] )
			->where( [ 'aq_page_id' => $fixture['page_id'], 'aq_is_latest' => 1 ] )
			->caller( __METHOD__ )->execute();
		$this->assertSame( [ 'status' => 'dependency_failure', 'text' => [] ],
			$this->gate->authorizeText( $this->authority, [ $textCandidate ] ) );
	}

	public function testTextAuthorizationFailsOnHashMismatchAmbiguityAndMissingQueue(): void {
		$fixture = $this->approvedFixture( 'text-fail-closed' );
		$identity = [ 'page_id' => $fixture['page_id'], 'revision_id' => $fixture['revision_id'],
			'content_hash' => str_repeat( 'f', 64 ) ];
		$this->assertSame( 'dependency_failure', $this->gate->authorizeText(
			$this->authority, [ $identity ]
		)['status'] );
		$this->gateDb->newInsertQueryBuilder()->insertInto( 'approve_queue' )->row( [
			'aq_page_id' => $fixture['page_id'], 'aq_revision_id' => $fixture['revision_id'],
			'aq_status' => 'approved', 'aq_is_latest' => 1,
		] )->caller( __METHOD__ )->execute();
		$this->assertSame( 'dependency_failure', $this->gate->authorizeText(
			$this->authority, [ [ 'page_id' => $fixture['page_id'],
				'revision_id' => $fixture['revision_id'], 'content_hash' => $fixture['content_hash'] ] ]
		)['status'] );
		$this->gateDb->query( 'DROP TABLE ' . $this->gateDb->tableName( 'approve_queue' ), __METHOD__ );
		$this->queueCreated = false;
		$this->assertSame( [ 'status' => 'dependency_failure', 'text' => [] ],
			$this->gate->authorizeText( $this->authority, [ [ 'page_id' => $fixture['page_id'],
				'revision_id' => $fixture['revision_id'], 'content_hash' => $fixture['content_hash'] ] ] ) );
	}

	private function approvedFixture( string $key ): array {
		$title = 'Current evidence gate ' . $key;
		$sourceText = 'Source evidence for ' . $key . '.';
		$revision = $this->editPage( $title, $sourceText )->getNewRevision();
		$source = [
			'title' => $title, 'page_id' => $revision->getPageId(), 'revision_id' => $revision->getId(),
			'content_hash' => hash( 'sha256', $sourceText ),
		];
		$span = $sourceText;
		$claim = [
			'claim_id' => hash( 'sha256', 'claim:' . $key ),
			'snapshot_id' => hash( 'sha256', 'snapshot:' . $key ),
			'claim_type' => 'property', 'subject' => [ 'type' => 'Crop', 'id' => 'Rice' ],
			'predicate' => 'GROWTH_DURATION', 'value' => '140 days', 'qualifiers' => [],
			'evidence' => [ [
				'evidence_id' => hash( 'sha256', 'evidence:' . $key ),
				'page_id' => $source['page_id'], 'revision_id' => $source['revision_id'],
				'content_hash' => $source['content_hash'], 'page_title' => $source['title'],
				'extraction_method' => 'rules', 'extractor_version' => 'phase6-test',
				'location_type' => 'source_span', 'source_offset_start' => 0,
				'source_offset_end' => mb_strlen( $span, 'UTF-8' ), 'supporting_span' => $span,
				'span_hash' => hash( 'sha256', $span ),
			] ],
			'traceability_complete' => true, 'review' => [ 'status' => 'pending' ],
		];
		$this->store->importVerified( [
			'schema_version' => '1.0',
			'metadata' => [ 'extractor_version' => 'phase6-test', 'extraction_method' => 'rules',
				'review_state' => 'pending', 'source_pages' => [ $source ] ],
			'summary' => [ 'claim_count' => 1, 'traceability_complete_count' => 1,
				'claims_by_predicate' => [ 'GROWTH_DURATION' => 1 ] ],
			'claims' => [ $claim ],
		] );
		$this->store->recordDecision( $claim['snapshot_id'], $this->reviewerId(), 'approved', 'Checked.', 0 );
		$this->gateDb->newInsertQueryBuilder()->insertInto( 'approve_queue' )->row( [
			'aq_page_id' => $source['page_id'], 'aq_revision_id' => $source['revision_id'],
			'aq_status' => 'approved', 'aq_is_latest' => 1,
		] )->caller( __METHOD__ )->execute();
		return $source + [ 'source_text' => $sourceText, 'snapshot_id' => $claim['snapshot_id'] ];
	}

	private function candidate( array $fixture ): array {
		$review = $this->store->getReview( $fixture['snapshot_id'] );
		return [ 'snapshot_id' => $fixture['snapshot_id'], 'snapshot_hash' => $review['snapshot_hash'],
			'review_version' => 1 ];
	}

	private function reviewerId(): int {
		return (int)$this->getTestSysop()->getUser()->getId();
	}

	private function createQueueFixture(): void {
		$this->gateDb->query(
			'CREATE TEMPORARY TABLE ' . $this->gateDb->tableName( 'approve_queue' ) . ' '
			. '(aq_page_id INT NOT NULL, aq_revision_id INT NOT NULL, '
			. 'aq_status VARCHAR(16) NOT NULL, aq_is_latest INT NOT NULL)',
			__METHOD__
		);
		$this->queueCreated = true;
	}
}
