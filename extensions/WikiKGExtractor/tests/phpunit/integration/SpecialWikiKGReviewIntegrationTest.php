<?php

namespace MediaWiki\Extension\WikiKGExtractor\Tests\Integration;

use MediaWiki\Extension\WikiKGExtractor\ClaimReviewStore;
use MediaWiki\Extension\WikiKGExtractor\SpecialWikiKGReview;
use MediaWiki\Permissions\SimpleAuthority;
use MediaWiki\Request\FauxRequest;
use MediaWiki\Revision\RevisionRecord;
use PermissionsError;
use SpecialPageTestBase;

/**
 * @group Database
 * @covers \MediaWiki\Extension\WikiKGExtractor\SpecialWikiKGReview
 */
class SpecialWikiKGReviewIntegrationTest extends SpecialPageTestBase {
	private ClaimReviewStore $store;

	protected function setUp(): void {
		parent::setUp();
		$this->store = new ClaimReviewStore(
			$this->getServiceContainer()->getConnectionProvider()
		);
	}

	protected function newSpecialPage(): SpecialWikiKGReview {
		return new SpecialWikiKGReview();
	}

	public function testAnonymousUserCannotOpenReviewPage() {
		$user = $this->getServiceContainer()->getUserFactory()->newAnonymous();
		$this->expectException( PermissionsError::class );
		$this->executeSpecialPage( '', null, 'vi', new SimpleAuthority( $user, [] ) );
	}

	public function testRegularUserCannotOpenReviewPage() {
		$this->expectException( PermissionsError::class );
		$this->executeSpecialPage( '', null, 'vi', $this->getTestUser()->getAuthority() );
	}

	public function testSysopCanReadSnapshotsWithoutWritingAndSeeEvidenceHistoryAndCurrentState() {
		$fixture = $this->createSnapshot( 'sysop-read' );
		$sysop = $this->getTestSysop();

		[ $listHtml ] = $this->executeSpecialPage( '', null, 'vi', $sysop->getAuthority() );
		$this->assertStringContainsString( substr( $fixture['snapshot_id'], 0, 12 ), $listHtml );
		$this->assertStringContainsString( 'href=', $listHtml );
		$this->assertStringContainsString( '<a ', $listHtml );
		$this->assertStringNotContainsString( '&lt;a ', $listHtml );
		$this->assertStringNotContainsString( $fixture['span'], $listHtml );
		$this->assertSame( 'pending', $this->store->getReview( $fixture['snapshot_id'] )['latest_review']['status'] );
		$this->assertSame( [], $this->store->getReview( $fixture['snapshot_id'] )['review_events'] );

		[ $detailHtml ] = $this->executeSpecialPage(
			$fixture['snapshot_id'],
			null,
			'vi',
			$sysop->getAuthority()
		);
		$this->assertStringContainsString( 'Trạng thái đánh giá hiện tại', $detailHtml );
		$this->assertStringContainsString( 'Chờ duyệt - phiên bản 0', $detailHtml );
		$this->assertStringContainsString( $fixture['span'], $detailHtml );
		$this->assertStringContainsString( 'oldid=' . $fixture['revision_id'], $detailHtml );
		$this->assertStringContainsString( 'method="post"', $detailHtml );
		$this->assertStringContainsString( 'name="expected_version"', $detailHtml );
		$this->assertStringContainsString( 'value="0"', $detailHtml );
		$this->assertStringNotContainsString( '<script>', $detailHtml );
		$this->assertStringNotContainsString( 'raw_data.json', $detailHtml );
	}

	public function testValidDecisionIsAuditOnlyAndStaleVersionDoesNotWrite() {
		$fixture = $this->createSnapshot( 'decision', null, '100 days' );
		$sysop = $this->getTestSysop();
		$reason = '<script>alert("review")</script>';

		[ $html ] = $this->postDecision( $fixture['snapshot_id'], $sysop, [
			'decision' => 'approved',
			'reason' => $reason,
			'expected_version' => '0',
		] );

		$review = $this->store->getReview( $fixture['snapshot_id'] );
		$this->assertSame( 'approved', $review['latest_review']['status'] );
		$this->assertSame( $reason, $review['review_events'][0]['reason'] );
		$this->assertSame( 1, $review['latest_review']['version'] );
		$this->assertSame( '100 days', $review['value'] );
		$this->assertStringContainsString( 'Đã duyệt - phiên bản 1', $html );
		$this->assertStringContainsString( '&lt;script>alert("review")&lt;/script>', $html );
		$this->assertStringNotContainsString( $reason, $html );
		$this->assertStringContainsString( 'Phiên bản 1', $html );
		$this->assertStringNotContainsString( 'class="wikikg-claim-status">Chờ duyệt', $html );

		[ $staleHtml ] = $this->postDecision( $fixture['snapshot_id'], $sysop, [
			'decision' => 'rejected',
			'reason' => 'Stale form.',
			'expected_version' => '0',
		] );
		$afterStale = $this->store->getReview( $fixture['snapshot_id'] );
		$this->assertStringContainsString( 'Hãy tải lại trang trước khi duyệt tiếp', $staleHtml );
		$this->assertSame( 'approved', $afterStale['latest_review']['status'] );
		$this->assertCount( 1, $afterStale['review_events'] );
	}

	public function testInvalidCsrfAndInvalidReasonDoNotWrite() {
		$fixture = $this->createSnapshot( 'invalid-post' );
		$sysop = $this->getTestSysop();

		[ $csrfHtml ] = $this->postDecision( $fixture['snapshot_id'], $sysop, [
			'decision' => 'approved',
			'reason' => 'Valid reason.',
			'expected_version' => '0',
		], 'invalid-token' );
		$this->assertStringContainsString( 'Token bảo mật không hợp lệ', $csrfHtml );
		$this->assertSame( 'pending', $this->store->getReview( $fixture['snapshot_id'] )['latest_review']['status'] );

		[ $reasonHtml ] = $this->postDecision( $fixture['snapshot_id'], $sysop, [
			'decision' => 'approved',
			'reason' => '   ',
			'expected_version' => '0',
		] );
		$this->assertStringContainsString( 'nhập lý do không quá 4.096 byte', $reasonHtml );
		$this->assertSame( [], $this->store->getReview( $fixture['snapshot_id'] )['review_events'] );

		[ $longReasonHtml ] = $this->postDecision( $fixture['snapshot_id'], $sysop, [
			'decision' => 'approved',
			'reason' => str_repeat( 'x', 4097 ),
			'expected_version' => '0',
		] );
		$this->assertStringContainsString( 'nhập lý do không quá 4.096 byte', $longReasonHtml );
		$this->assertStringNotContainsString( str_repeat( 'x', 4097 ), $longReasonHtml );
		$this->assertSame( [], $this->store->getReview( $fixture['snapshot_id'] )['review_events'] );
	}

	public function testUnreadableEvidenceIsNotRenderedAndCannotBeReviewed() {
		$fixture = $this->createSnapshot( 'unreadable' );
		$this->setGroupPermissions( [
			'*' => [ 'read' => false ],
			'user' => [ 'read' => false ],
			'autoconfirmed' => [ 'read' => false ],
			'sysop' => [ 'read' => false ],
			'bureaucrat' => [ 'read' => false ],
		] );
		$sysop = $this->getTestSysop();

		[ $detailHtml ] = $this->executeSpecialPage(
			$fixture['snapshot_id'],
			null,
			'vi',
			$sysop->getAuthority()
		);
		$this->assertStringContainsString( 'Snapshot không tồn tại hoặc nguồn không còn khả dụng', $detailHtml );
		$this->assertStringNotContainsString( $fixture['span'], $detailHtml );
		[ $listHtml ] = $this->executeSpecialPage( '', null, 'vi', $sysop->getAuthority() );
		$this->assertStringNotContainsString( $fixture['span'], $listHtml );
		$this->assertStringNotContainsString( 'Rice unreadable', $listHtml );

		[ $postHtml ] = $this->postDecision( $fixture['snapshot_id'], $sysop, [
			'decision' => 'approved',
			'reason' => 'Cannot read source.',
			'expected_version' => '0',
		] );
		$this->assertStringContainsString( 'Snapshot không tồn tại hoặc nguồn không còn khả dụng', $postHtml );
		$this->assertSame( [], $this->store->getReview( $fixture['snapshot_id'] )['review_events'] );
	}

	public function testMissingAndSuppressedSourcesFailClosed() {
		$missing = $this->createSnapshot( 'missing' );
		$page = $this->getServiceContainer()->getPageStore()->getPageById( $missing['page_id'] );
		$this->deletePage( $page, 'Remove review fixture source.' );
		$sysop = $this->getTestSysop();
		[ $missingHtml ] = $this->executeSpecialPage(
			$missing['snapshot_id'],
			null,
			'vi',
			$sysop->getAuthority()
		);
		$this->assertStringContainsString( 'nguồn không còn khả dụng', $missingHtml );
		$this->assertStringNotContainsString( $missing['span'], $missingHtml );

		$suppressed = $this->createSnapshot( 'suppressed' );
		$this->editPage( $suppressed['title'], 'Later visible revision.' );
		$this->revisionDelete( $suppressed['revision_id'], [
			RevisionRecord::DELETED_TEXT => 1,
			RevisionRecord::DELETED_COMMENT => 1,
			RevisionRecord::DELETED_USER => 1,
			RevisionRecord::DELETED_RESTRICTED => 1,
		] );
		[ $suppressedHtml ] = $this->executeSpecialPage(
			$suppressed['snapshot_id'],
			null,
			'vi',
			$sysop->getAuthority()
		);
		$this->assertStringContainsString( 'nguồn không còn khả dụng', $suppressedHtml );
		$this->assertStringNotContainsString( $suppressed['span'], $suppressedHtml );
		$this->assertSame( [], $this->store->getReview( $suppressed['snapshot_id'] )['review_events'] );
	}

	public function testEmptyUnreadablePageStillHasNavigationToOlderReadableSnapshots() {
		$readable = $this->createSnapshot( 'pagination-readable' );
		$this->setSnapshotCreated( $readable['snapshot_id'], '20240101000000' );
		for ( $index = 0; $index < 50; $index++ ) {
			$unreadable = $this->createSnapshot( 'pagination-hidden-' . $index );
			$this->setSnapshotCreated( $unreadable['snapshot_id'], '20250101000000' );
			$page = $this->getServiceContainer()->getPageStore()->getPageById(
				$unreadable['page_id']
			);
			$this->assertNotNull( $page );
			$this->deletePage( $page, 'Remove unreadable pagination fixture.' );
		}
		$sysop = $this->getTestSysop();

		[ $firstPageHtml ] = $this->executeSpecialPage(
			'', null, 'vi', $sysop->getAuthority()
		);

		$this->assertStringContainsString( 'Chưa có claim nào có nguồn bạn có thể đọc.', $firstPageHtml );
		$this->assertStringNotContainsString( substr( $readable['snapshot_id'], 0, 12 ), $firstPageHtml );
		$this->assertStringContainsString( 'Trang sau', $firstPageHtml );
		$this->assertStringContainsString( 'offset=50', $firstPageHtml );

		[ $secondPageHtml ] = $this->executeSpecialPage(
			'', new FauxRequest( [ 'offset' => '50' ] ), 'vi', $sysop->getAuthority()
		);

		$this->assertStringContainsString( substr( $readable['snapshot_id'], 0, 12 ), $secondPageHtml );
		$this->assertStringContainsString( 'Trang trước', $secondPageHtml );
	}

	public function testRevisionPageIdMismatchFailsClosed() {
		$otherTitle = 'WikiKG review alternate page';
		$otherRevision = $this->editPage( $otherTitle, 'Different page content.' )->getNewRevision();
		$fixture = $this->createSnapshot( 'wrong-page-id', null, '100 days', [
			'page_id' => $otherRevision->getPageId(),
			'title' => $otherTitle,
		] );
		$sysop = $this->getTestSysop();

		[ $html ] = $this->executeSpecialPage(
			$fixture['snapshot_id'],
			null,
			'vi',
			$sysop->getAuthority()
		);

		$this->assertStringContainsString( 'nguồn không còn khả dụng', $html );
		$this->assertStringNotContainsString( $fixture['span'], $html );
		$this->assertSame( [], $this->store->getReview( $fixture['snapshot_id'] )['review_events'] );
	}

	/** @return array<string,mixed> */
	private function createSnapshot(
		string $key,
		?string $span = null,
		string $value = '<img src=x onerror=alert(1)>',
		?array $storedPage = null
	): array {
		$span ??= 'Source sentence for ' . $key . '.';
		$title = 'WikiKG review source ' . $key;
		$revision = $this->editPage( $title, $span )->getNewRevision();
		$source = [
			'title' => $storedPage['title'] ?? $title,
			'page_id' => $storedPage['page_id'] ?? $revision->getPageId(),
			'revision_id' => $revision->getId(),
			'content_hash' => hash( 'sha256', $span ),
		];
		$claim = [
			'claim_id' => hash( 'sha256', 'claim:' . $key ),
			'snapshot_id' => hash( 'sha256', 'snapshot:' . $key ),
			'claim_type' => 'property',
			'subject' => [ 'type' => 'Crop', 'id' => 'Rice ' . $key ],
			'predicate' => 'GROWTH_DURATION',
			'value' => $value,
			'qualifiers' => [],
			'evidence' => [ [
				'evidence_id' => hash( 'sha256', 'evidence:' . $key ),
				'page_id' => $source['page_id'],
				'revision_id' => $source['revision_id'],
				'content_hash' => $source['content_hash'],
				'page_title' => $source['title'],
				'extraction_method' => 'rules',
				'extractor_version' => '2.3.1',
				'location_type' => 'source_span',
				'source_offset_start' => 0,
				'source_offset_end' => mb_strlen( $span, 'UTF-8' ),
				'supporting_span' => $span,
				'span_hash' => hash( 'sha256', $span ),
			] ],
			'traceability_complete' => true,
			'review' => [ 'status' => 'pending' ],
		];
		$this->store->importVerified( [
			'schema_version' => '1.0',
			'metadata' => [
				'extractor_version' => '2.3.1',
				'extraction_method' => 'rules',
				'review_state' => 'pending',
				'source_pages' => [ $source ],
			],
			'claims' => [ $claim ],
		] );

		return [
			'snapshot_id' => $claim['snapshot_id'],
			'title' => $title,
			'page_id' => $revision->getPageId(),
			'revision_id' => $revision->getId(),
			'span' => $span,
		];
	}

	/** @param array<string,string> $fields */
	private function postDecision(
		string $snapshotId,
		$testUser,
		array $fields,
		?string $token = null
	): array {
		$fields['wpEditToken'] = $token ?? $testUser->getUser()->getEditToken();
		return $this->executeSpecialPage(
			$snapshotId,
			new FauxRequest( $fields, true ),
			'vi',
			$testUser->getAuthority()
		);
	}

	/**
	 * @param string $snapshotId
	 * @param string $timestamp
	 */
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
