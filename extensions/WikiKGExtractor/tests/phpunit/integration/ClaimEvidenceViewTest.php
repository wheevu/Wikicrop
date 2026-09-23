<?php

namespace MediaWiki\Extension\WikiKGExtractor\Tests\Integration;

use MediaWiki\Extension\WikiKGExtractor\ClaimEvidenceView;
use MediaWikiIntegrationTestCase;

/**
 * @group Database
 * @covers \MediaWiki\Extension\WikiKGExtractor\ClaimEvidenceView
 */
class ClaimEvidenceViewTest extends MediaWikiIntegrationTestCase {
	public function testShowsPendingRelationshipAndScalarClaimsWithPinnedRevisionLinks() {
		$document = self::documentWithClaims( [
			self::relationshipClaim( 'RESISTANT_TO', 'source_span' ),
			self::relationshipClaim( 'SUSCEPTIBLE_TO', 'page' ),
			self::propertyClaim()
		] );
		$firstSnapshot = $document['claims'][0]['snapshot_id'];

		$html = ClaimEvidenceView::render( $document, [
			$firstSnapshot => [ 'snapshot_id' => $firstSnapshot, 'status' => 'approved', 'reason' => 'checked' ]
		] );

		$this->assertStringContainsString( 'Chờ duyệt', $html );
		$this->assertStringContainsString( 'RESISTANT_TO', $html );
		$this->assertStringContainsString( 'SUSCEPTIBLE_TO', $html );
		$this->assertStringContainsString( 'growth_duration: 100 ngày', $html );
		$this->assertStringContainsString( 'mùa khô', $html );
		$this->assertStringContainsString( 'Mâu thuẫn, phía 1 / 2', $html );
		$this->assertStringContainsString( 'Mâu thuẫn, phía 2 / 2', $html );
		$this->assertStringContainsString( 'Đã duyệt', $html );
		$this->assertStringContainsString( 'Lý do: checked', $html );
		$this->assertStringContainsString( 'oldid=92', $html );
		$this->assertStringContainsString( '<details', $html );
		$this->assertStringContainsString( '<summary', $html );
		$this->assertStringNotContainsString( '<form', $html );
		$this->assertStringNotContainsString( '<script>', $html );
		$this->assertStringContainsString( 'Chỉ có bằng chứng cấp trang; không có trích dẫn hỗ trợ.', $html );

		$directReviewHtml = ClaimEvidenceView::render( $document, [
			'snapshot_id' => $firstSnapshot,
			'status' => 'approved',
			'reason' => 'checked'
		] );
		$this->assertStringContainsString( 'Đã duyệt', $directReviewHtml );
	}

	public function testEscapesClaimAndEvidenceTextAndBoundsLongQuotes() {
		$claim = self::propertyClaim();
		$claim['value'] = '<img src=x onerror=alert(1)>';
		$claim['evidence'][0]['supporting_span'] = '<script>alert(1)</script> & "quoted"';
		$document = self::documentWithClaims( [ $claim ] );

		$html = ClaimEvidenceView::render( $document );

		$this->assertStringNotContainsString( '<img src=x', $html );
		$this->assertStringNotContainsString( '<script>alert(1)</script>', $html );
		$this->assertStringContainsString( '&lt;img', $html );
		$this->assertStringContainsString( '&lt;script>', $html );

		$claim['evidence'][0]['supporting_span'] = str_repeat( 'x', 2200 );
		$longHtml = ClaimEvidenceView::render( self::documentWithClaims( [ $claim ] ) );
		$this->assertStringNotContainsString( str_repeat( 'x', 2200 ), $longHtml );
		$this->assertStringContainsString( str_repeat( 'x', 2000 ) . '…', $longHtml );
	}

	public function testDoesNotInventQuoteForPageTitleEvidence() {
		$claim = self::propertyClaim();
		$evidence = $claim['evidence'][0];
		unset(
			$evidence['source_offset_start'],
			$evidence['source_offset_end'],
			$evidence['supporting_span'],
			$evidence['span_hash']
		);
		$evidence['location_type'] = 'page_title';
		$evidence['location'] = 'Lúa Việt Nam';
		$claim['evidence'] = [ $evidence ];

		$html = ClaimEvidenceView::render( self::documentWithClaims( [ $claim ] ) );

		$this->assertStringContainsString( 'Chỉ dựa vào tiêu đề trang, không có trích dẫn nội dung.', $html );
		$this->assertStringNotContainsString( 'giống này chịu mặn.', $html );
		$this->assertStringContainsString( 'oldid=92', $html );
	}

	public function testConflictingClaimsRemainAdjacentWhenAnotherClaimSeparatesTheirInputRows() {
		$document = self::documentWithClaims( [
			self::relationshipClaim( 'RESISTANT_TO', 'source_span' ),
			self::propertyClaim(),
			self::relationshipClaim( 'SUSCEPTIBLE_TO', 'page' )
		] );

		$html = ClaimEvidenceView::render( $document );
		$this->assertLessThan( strpos( $html, 'growth_duration' ), strpos( $html, 'RESISTANT_TO' ) );
		$this->assertLessThan( strpos( $html, 'growth_duration' ), strpos( $html, 'SUSCEPTIBLE_TO' ) );
	}

	/** @param array<int,array<string,mixed>> $claims @return array<string,mixed> */
	private static function documentWithClaims( array $claims ) {
		foreach ( $claims as $index => &$claim ) {
			$claim['snapshot_id'] = str_pad( dechex( $index + 1 ), 64, '0', STR_PAD_LEFT );
		}
		unset( $claim );
		return [ 'schema_version' => '1.0', 'claims' => $claims ];
	}

	/** @return array<string,mixed> */
	private static function relationshipClaim( $predicate, $locationType ) {
		$evidence = self::baseEvidence( $locationType );
		if ( $locationType === 'page' ) {
			unset(
				$evidence['source_offset_start'],
				$evidence['source_offset_end'],
				$evidence['supporting_span'],
				$evidence['span_hash']
			);
		}
		return [
			'claim_id' => str_repeat( $predicate === 'RESISTANT_TO' ? 'a' : 'b', 64 ),
			'snapshot_id' => str_repeat( '0', 64 ),
			'claim_type' => 'relationship',
			'subject' => [ 'type' => 'Variety', 'id' => 'ST25' ],
			'predicate' => $predicate,
			'object' => [ 'type' => 'Pest', 'id' => 'Rầy nâu' ],
			'qualifiers' => [ 'level' => 'trung bình' ],
			'evidence' => [ $evidence ],
			'traceability_complete' => $locationType !== 'page',
			'review' => [ 'status' => 'pending' ]
		];
	}

	/** @return array<string,mixed> */
	private static function propertyClaim() {
		return [
			'claim_id' => str_repeat( 'c', 64 ),
			'snapshot_id' => str_repeat( '0', 64 ),
			'claim_type' => 'property',
			'subject' => [ 'type' => 'Variety', 'id' => 'ST25' ],
			'predicate' => 'growth_duration',
			'value' => '100 ngày',
			'qualifiers' => [ 'season' => 'mùa khô' ],
			'evidence' => [ self::baseEvidence( 'source_span' ) ],
			'traceability_complete' => true,
			'review' => [ 'status' => 'pending' ]
		];
	}

	/** @return array<string,mixed> */
	private static function baseEvidence( $locationType ) {
		return [
			'evidence_id' => str_repeat( 'd', 64 ),
			'page_id' => 17,
			'revision_id' => 92,
			'content_hash' => str_repeat( 'e', 64 ),
			'page_title' => 'Lúa Việt Nam',
			'extraction_method' => 'rules',
			'extractor_version' => 'test',
			'location_type' => $locationType,
			'source_offset_start' => 12,
			'source_offset_end' => 31,
			'supporting_span' => 'giống này chịu mặn.',
			'span_hash' => hash( 'sha256', 'giống này chịu mặn.' )
		];
	}
}
