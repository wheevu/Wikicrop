<?php

namespace MediaWiki\Extension\WikiKGExtractor\Tests\Integration;

use MediaWiki\Context\RequestContext;
use MediaWiki\Extension\WikiKGExtractor\ClaimEvidenceAccess;
use MediaWiki\Extension\WikiKGExtractor\ClaimReviewStore;
use MediaWiki\Extension\WikiKGExtractor\SpecialWikiKGReview;
use MediaWiki\Extension\WikiKGExtractor\SpecialWikiKGReviewedGraph;
use MediaWiki\Permissions\SimpleAuthority;
use MediaWiki\Request\FauxRequest;
use MediaWiki\Revision\RevisionRecord;
use PermissionsError;
use RuntimeException;
use SpecialPageTestBase;
use Wikimedia\Rdbms\IConnectionProvider;

/**
 * @group Database
 * @covers \MediaWiki\Extension\WikiKGExtractor\SpecialWikiKGReviewedGraph
 * @covers \MediaWiki\Extension\WikiKGExtractor\SpecialWikiKGReview
 * @covers \MediaWiki\Extension\WikiKGExtractor\ReviewedGraph
 * @covers \MediaWiki\Extension\WikiKGExtractor\ClaimEvidenceAccess
 */
class SpecialWikiKGReviewedGraphIntegrationTest extends SpecialPageTestBase {
	private ClaimReviewStore $store;
	private bool $failStore = false;
	private bool $openReviewList = false;

	protected function setUp(): void {
		parent::setUp();
		$this->store = new ClaimReviewStore(
			$this->getServiceContainer()->getConnectionProvider()
		);
	}

	protected function newSpecialPage() {
		if ( $this->openReviewList ) {
			return new SpecialWikiKGReview();
		}
		if ( !$this->failStore ) {
			return new SpecialWikiKGReviewedGraph();
		}

		$provider = $this->createMock( IConnectionProvider::class );
		$provider->method( 'getPrimaryDatabase' )
			->willThrowException( new RuntimeException( 'Synthetic database outage.' ) );
		return new class( $provider ) extends SpecialWikiKGReviewedGraph {
			private IConnectionProvider $connectionProvider;

			public function __construct( IConnectionProvider $connectionProvider ) {
				$this->connectionProvider = $connectionProvider;
				parent::__construct();
			}

			protected function newReviewStore(): ClaimReviewStore {
				return new ClaimReviewStore( $this->connectionProvider );
			}
		};
	}

	public function testPendingAndRejectedClaimsDoNotAppearAsApproved(): void {
		$pending = $this->createReviewedClaim( 'pending', 'Hidden pending value' );
		$rejected = $this->createReviewedClaim( 'rejected', 'Hidden rejected value' );
		$this->store->recordDecision( $rejected['snapshot_id'], 7, 'rejected', 'Not supported.', 0 );
		$sysop = $this->getTestSysop();

		[ $html ] = $this->executeSpecialPage( '', null, 'vi', $sysop->getAuthority() );

		$this->assertStringContainsString( 'không có claim đã duyệt nào', mb_strtolower( $html ) );
		$this->assertStringContainsString( '50 snapshot', mb_strtolower( $html ) );
		$this->assertStringContainsString( 'có thể còn claim trên trang tiếp theo', mb_strtolower( $html ) );
		$this->assertStringContainsString( 'trang snapshot này', mb_strtolower( $html ) );
		$this->assertStringNotContainsString( 'Hidden pending value', $html );
		$this->assertStringNotContainsString( 'Hidden rejected value', $html );
		$this->assertSame( 'pending', $this->store->getReview( $pending['snapshot_id'] )['latest_review']['status'] );
		$this->assertSame( 'rejected', $this->store->getReview( $rejected['snapshot_id'] )['latest_review']['status'] );
	}

	public function testApprovalAppearsAndLaterRejectionRemovesItFromTheGraph(): void {
		$fixture = $this->createReviewedClaim( 'approval-revocation', '100 days' );
		$this->approve( $fixture['snapshot_id'] );
		$sysop = $this->getTestSysop();

		[ $approvedHtml ] = $this->executeSpecialPage( '', null, 'vi', $sysop->getAuthority() );
		$this->assertStringContainsString( '100 days', $approvedHtml );
		$this->assertStringContainsString( 'wikikg-graph-nodes', $approvedHtml );
		$this->assertStringContainsString( 'wikikg-graph-canvas', $approvedHtml );

		$this->store->recordDecision(
			$fixture['snapshot_id'],
			8,
			'rejected',
			'Review changed.',
			1
		);
		[ $rejectedHtml ] = $this->executeSpecialPage( '', null, 'vi', $sysop->getAuthority() );

		$this->assertStringNotContainsString( '100 days', $rejectedHtml );
		$review = $this->store->getReview( $fixture['snapshot_id'] );
		$this->assertSame( 'rejected', $review['latest_review']['status'] );
		$this->assertSame( [ 'approved', 'rejected' ], array_column( $review['review_events'], 'status' ) );
	}

	public function testContradictoryApprovedPropertyClaimsRemainSeparateRows(): void {
		$source = $this->createSource( 'conflicting-properties' );
		$claims = [
			$this->claim( 'duration-100', $source, 'property', 'GROWTH_DURATION', '100 days' ),
			$this->claim( 'duration-180', $source, 'property', 'GROWTH_DURATION', '180 days' ),
		];
		$snapshotIds = $this->importClaims( $source, $claims );
		foreach ( $snapshotIds as $snapshotId ) {
			$this->approve( $snapshotId );
		}
		$sysop = $this->getTestSysop();

		[ $html ] = $this->executeSpecialPage( '', null, 'vi', $sysop->getAuthority() );

		$this->assertStringContainsString( '100 days', $html );
		$this->assertStringContainsString( '180 days', $html );
		$this->assertSame( 2, substr_count( $html, 'Mở lịch sử' ) );
		foreach ( $snapshotIds as $snapshotId ) {
			$this->assertStringContainsString( $this->reviewUrl( $snapshotId ), $html );
		}
	}

	public function testPropertySeasonsAndRelationshipLevelStayVisibleWithReviewLinks(): void {
		$source = $this->createSource( 'qualifier-distinctions' );
		$drySeason = $this->claim(
			'duration-dry-season',
			$source,
			'property',
			'GROWTH_DURATION',
			'100 days'
		);
		$drySeason['qualifiers'] = [ 'season' => 'dry' ];
		$wetSeason = $this->claim(
			'duration-wet-season',
			$source,
			'property',
			'GROWTH_DURATION',
			'100 days'
		);
		$wetSeason['qualifiers'] = [ 'season' => 'wet' ];
		$leveledRelationship = $this->claim(
			'leveled-relationship',
			$source,
			'relationship',
			'AFFECTED_BY',
			'',
			[ 'type' => 'Crop', 'id' => 'Rice' ],
			[ 'type' => 'Pest', 'id' => 'Brown planthopper' ]
		);
		$leveledRelationship['qualifiers'] = [ 'level' => 'moderate' ];
		$snapshotIds = $this->importClaims( $source, [
			$drySeason,
			$wetSeason,
			$leveledRelationship,
		] );
		foreach ( $snapshotIds as $snapshotId ) {
			$this->approve( $snapshotId );
		}
		$sysop = $this->getTestSysop();

		[ $html ] = $this->executeSpecialPage( '', null, 'vi', $sysop->getAuthority() );

		$this->assertSame( 2, substr_count( $html, '100 days' ) );
		$this->assertStringContainsString( 'season: dry', $html );
		$this->assertStringContainsString( 'season: wet', $html );
		$this->assertStringContainsString( 'level: moderate', $html );
		$this->assertSame( 3, substr_count( $html, 'Mở lịch sử' ) );
		foreach ( $snapshotIds as $snapshotId ) {
			$this->assertStringContainsString( $this->reviewUrl( $snapshotId ), $html );
		}
	}

	public function testApprovedRelationshipRendersAnEdgeAndItsReviewLink(): void {
		$fixture = $this->createReviewedClaim(
			'relationship-edge',
			'',
			'relationship',
			'AFFECTED_BY',
			[ 'type' => 'Crop', 'id' => 'Rice' ],
			[ 'type' => 'Pest', 'id' => 'Brown planthopper' ]
		);
		$this->approve( $fixture['snapshot_id'] );
		$sysop = $this->getTestSysop();

		[ $html ] = $this->executeSpecialPage( '', null, 'vi', $sysop->getAuthority() );

		$this->assertStringContainsString( 'wikikg-graph-edges', $html );
		$this->assertStringContainsString( 'Bị gây hại bởi', $html );
		$this->assertStringContainsString( 'Brown planthopper', $html );
		$this->assertStringContainsString( $this->reviewUrl( $fixture['snapshot_id'] ), $html );
	}

	public function testChangedSourceRevisionHidesApprovalButKeepsItsAuditHistory(): void {
		$fixture = $this->createReviewedClaim( 'revised-source', '100 days' );
		$this->approve( $fixture['snapshot_id'] );
		$this->editPage( $fixture['title'], 'A newer source revision.' );
		$sysop = $this->getTestSysop();

		[ $html ] = $this->executeSpecialPage( '', null, 'vi', $sysop->getAuthority() );

		$this->assertStringNotContainsString( '100 days', $html );
		$this->assertStringNotContainsString( 'A newer source revision.', $html );
		$review = $this->store->getReview( $fixture['snapshot_id'] );
		$this->assertSame( 'approved', $review['latest_review']['status'] );
		$this->assertCount( 1, $review['review_events'] );
	}

	public function testOneStaleEvidenceItemHidesTheWholeClaim(): void {
		$firstSource = $this->createSource( 'multi-evidence-first' );
		$secondSource = $this->createSource( 'multi-evidence-second' );
		$claim = $this->claim(
			'multi-evidence-claim',
			$firstSource,
			'property',
			'GROWTH_DURATION',
			'Only show when every source is current'
		);
		$secondClaim = $this->claim(
			'multi-evidence-extra',
			$secondSource,
			'property',
			'GROWTH_DURATION',
			'Not imported as a separate approved claim'
		);
		$claim['evidence'][] = $secondClaim['evidence'][0];
		[ $snapshotId ] = $this->importClaims( $firstSource, [ $claim ], [ $secondSource ] );
		$this->approve( $snapshotId );
		$this->editPage( $secondSource['title'], 'A newer second source revision.' );
		$sysop = $this->getTestSysop();

		[ $html ] = $this->executeSpecialPage( '', null, 'vi', $sysop->getAuthority() );

		$this->assertStringNotContainsString( 'Only show when every source is current', $html );
		$this->assertStringNotContainsString( 'A newer second source revision.', $html );
	}

	public function testSuppressedCitedRevisionIsOmittedWithoutLeakingClaimOrEvidence(): void {
		$fixture = $this->createReviewedClaim( 'suppressed-source', 'Secret approved value' );
		$this->approve( $fixture['snapshot_id'] );
		$this->editPage( $fixture['title'], 'A newer visible source revision.' );
		$this->suppressRevision( $fixture['revision_id'] );
		$sysop = $this->getTestSysop();
		$review = $this->store->getReview( $fixture['snapshot_id'] );
		$this->assertFalse( ClaimEvidenceAccess::canRead(
			$sysop->getAuthority(),
			$review['evidence']
		) );

		[ $html ] = $this->executeSpecialPage( '', null, 'vi', $sysop->getAuthority() );

		$this->assertStringNotContainsString( 'Secret approved value', $html );
		$this->assertStringNotContainsString( $fixture['span'], $html );
		$this->assertSame( 'approved', $this->store->getReview( $fixture['snapshot_id'] )['latest_review']['status'] );
	}

	public function testReadRevocationHidesApprovedClaim(): void {
		$fixture = $this->createReviewedClaim( 'read-revoked', 'Secret read-revoked value' );
		$this->approve( $fixture['snapshot_id'] );
		$this->setGroupPermissions( [
			'*' => [ 'read' => false ],
			'user' => [ 'read' => false ],
			'autoconfirmed' => [ 'read' => false ],
			'sysop' => [ 'read' => false ],
			'bureaucrat' => [ 'read' => false ],
		] );
		$sysop = $this->getTestSysop();

		[ $html ] = $this->executeSpecialPage( '', null, 'vi', $sysop->getAuthority() );

		$this->assertStringNotContainsString( 'Secret read-revoked value', $html );
		$this->assertStringNotContainsString( $fixture['title'], $html );
	}

	public function testPropertyAndRelationshipLabelsAreEscaped(): void {
		$source = $this->createSource( 'escaped-labels' );
		$claim = $this->claim(
			'escaped-claim',
			$source,
			'property',
			'GROWTH_DURATION',
			'<img src=x onerror=alert(1)>',
			[ 'type' => 'Crop', 'id' => '<script>alert(2)</script>' ]
		);
		$claim['qualifiers'] = [ 'condition' => '<img src=x onerror=alert(3)>' ];
		[ $snapshotId ] = $this->importClaims( $source, [ $claim ] );
		$this->approve( $snapshotId );
		$sysop = $this->getTestSysop();

		[ $html ] = $this->executeSpecialPage( '', null, 'vi', $sysop->getAuthority() );

		$this->assertStringNotContainsString( '<img src=x onerror=alert(1)>', $html );
		$this->assertStringNotContainsString( '<img src=x onerror=alert(3)>', $html );
		$this->assertStringNotContainsString( '<script>alert(2)</script>', $html );
		$this->assertStringContainsString( '&lt;img', $html );
		$this->assertStringContainsString( '&lt;script', $html );
		$this->assertStringNotContainsString( $claim['evidence'][0]['supporting_span'], $html );
	}

	public function testStoreFailureDoesNotRenderAClaimThatWasApprovedEarlier(): void {
		$fixture = $this->createReviewedClaim( 'store-failure', 'Must stay hidden' );
		$this->approve( $fixture['snapshot_id'] );
		$this->failStore = true;
		$sysop = $this->getTestSysop();

		[ $html ] = $this->executeSpecialPage( '', null, 'vi', $sysop->getAuthority() );

		$this->assertStringContainsString( 'Không thể tải đồ thị đã duyệt', $html );
		$this->assertStringNotContainsString( 'Must stay hidden', $html );
		$this->assertStringNotContainsString( $fixture['title'], $html );
	}

	public function testReviewListLinksToReviewedSnapshotPages(): void {
		$this->openReviewList = true;
		$sysop = $this->getTestSysop();

		[ $html ] = $this->executeSpecialPage( '', null, 'vi', $sysop->getAuthority() );

		$this->assertStringContainsString(
			\SpecialPage::getTitleFor( 'WikiKGReviewedGraph' )->getLocalURL(),
			$html
		);
		$this->assertStringContainsString( 'Xem các trang snapshot đã duyệt', $html );
	}

	public function testMoreThanFiftySnapshotsHaveExplicitNextAndPreviousPages(): void {
		$source = $this->createSource( 'paging' );
		$claims = [];
		for ( $index = 0; $index < 55; $index++ ) {
			$key = sprintf( 'paging-%02d', $index );
			$claims[] = $this->claim(
				$key,
				$source,
				'property',
				'GROWTH_DURATION',
				sprintf( 'Duration %02d days', $index )
			);
		}
		$snapshotIds = $this->importClaims( $source, $claims );
		foreach ( $snapshotIds as $index => $snapshotId ) {
			$this->approve( $snapshotId );
			$this->setSnapshotCreated(
				$snapshotId,
				gmdate( 'YmdHis', strtotime( '2024-01-01 00:00:00 UTC' ) + $index )
			);
		}
		$sysop = $this->getTestSysop();

		[ $firstPageHtml ] = $this->executeSpecialPage( '', null, 'vi', $sysop->getAuthority() );
		$this->assertStringContainsString( 'Duration 54 days', $firstPageHtml );
		$this->assertStringNotContainsString( 'Duration 04 days', $firstPageHtml );
		$this->assertStringContainsString( 'Trang sau', $firstPageHtml );
		$this->assertStringContainsString( 'offset=50', $firstPageHtml );

		[ $secondPageHtml ] = $this->executeSpecialPage(
			'',
			new FauxRequest( [ 'offset' => '50' ] ),
			'vi',
			$sysop->getAuthority()
		);
		$this->assertStringContainsString( 'Duration 04 days', $secondPageHtml );
		$this->assertStringContainsString( 'Duration 00 days', $secondPageHtml );
		$this->assertStringNotContainsString( 'Duration 54 days', $secondPageHtml );
		$this->assertStringContainsString( 'Trang trước', $secondPageHtml );
		$this->assertStringNotContainsString( 'Trang sau', $secondPageHtml );
	}

	public function testAnonymousUserCannotOpenReviewedGraph(): void {
		$user = $this->getServiceContainer()->getUserFactory()->newAnonymous();
		$this->expectException( PermissionsError::class );
		$this->executeSpecialPage( '', null, 'vi', new SimpleAuthority( $user, [] ) );
	}

	public function testRegularUserCannotOpenReviewedGraph(): void {
		$this->expectException( PermissionsError::class );
		$this->executeSpecialPage( '', null, 'vi', $this->getTestUser()->getAuthority() );
	}

	private function createReviewedClaim(
		string $key,
		string $value,
		string $claimType = 'property',
		string $predicate = 'GROWTH_DURATION',
		?array $subject = null,
		?array $object = null
	): array {
		$source = $this->createSource( $key );
		$claim = $this->claim(
			$key,
			$source,
			$claimType,
			$predicate,
			$value,
			$subject,
			$object
		);
		[ $snapshotId ] = $this->importClaims( $source, [ $claim ] );
		return [
			'snapshot_id' => $snapshotId,
			'title' => $source['title'],
			'revision_id' => $source['revision_id'],
			'span' => $claim['evidence'][0]['supporting_span'],
		];
	}

	private function createSource( string $key ): array {
		$title = 'WikiKG reviewed graph source ' . $key;
		$revision = $this->editPage( $title, 'Source text for ' . $key . '.' )->getNewRevision();
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
		string $predicate,
		string $value,
		?array $subject = null,
		?array $object = null
	): array {
		$span = 'Evidence for ' . $key . '.';
		$claim = [
			'claim_id' => hash( 'sha256', 'claim:' . $key ),
			'snapshot_id' => hash( 'sha256', 'snapshot:' . $key ),
			'claim_type' => $claimType,
			'subject' => $subject ?? [ 'type' => 'Crop', 'id' => 'Rice' ],
			'predicate' => $predicate,
			'qualifiers' => [],
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
				'source_offset_end' => mb_strlen( $span, 'UTF-8' ),
				'supporting_span' => $span,
				'span_hash' => hash( 'sha256', $span ),
			] ],
			'traceability_complete' => true,
			'review' => [ 'status' => 'pending' ],
		];
		if ( $claimType === 'relationship' ) {
			$claim['object'] = $object ?? [ 'type' => 'Variety', 'id' => 'ST25' ];
		} else {
			$claim['value'] = $value;
		}
		return $claim;
	}

	private function importClaims( array $source, array $claims, array $additionalSources = [] ): array {
		return $this->store->importVerified( [
			'schema_version' => '1.0',
			'metadata' => [
				'extractor_version' => '2.4.0',
				'extraction_method' => 'rules',
				'review_state' => 'pending',
				'source_pages' => array_merge( [ $source ], $additionalSources ),
			],
			'claims' => $claims,
		] );
	}

	private function approve( string $snapshotId ): void {
		$this->assertNotNull( $this->store->recordDecision(
			$snapshotId,
			7,
			'approved',
			'Verified for the test.',
			0
		) );
	}

	private function reviewUrl( string $snapshotId ): string {
		return \SpecialPage::getTitleFor( 'WikiKGReview', $snapshotId )->getLocalURL();
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
				'comment' => 'Suppress reviewed-graph fixture revision.',
			] );
			$this->assertStatusGood( $status );
		} finally {
			$context->setUser( $previousUser );
		}
	}

	private function setSnapshotCreated( string $snapshotId, string $timestamp ): void {
		$db = $this->getServiceContainer()->getConnectionProvider()->getPrimaryDatabase();
		$db->newUpdateQueryBuilder()
			->update( 'wikikg_snapshot' )
			->set( [ 'wikikg_created' => $timestamp ] )
			->where( [ 'wikikg_snapshot_id' => $snapshotId ] )
			->caller( __METHOD__ )
			->execute();
	}
}
