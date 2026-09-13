<?php

namespace MediaWiki\Extension\WikiKGExtractor\Tests\Integration;

use JsonContent;
use MediaWiki\Extension\WikiKGExtractor\LocalWikiExtractor;
use MediaWikiIntegrationTestCase;

/**
 * @group Database
 * @covers \MediaWiki\Extension\WikiKGExtractor\LocalWikiExtractor
 */
class LocalWikiExtractorIntegrationTest extends MediaWikiIntegrationTestCase {
	public function testPreservesRawWikitextAndRendersReadableText() {
		$cropTitle = 'Lúa kiểm thử nguồn';
		$varietyTitle = 'Lúa kiểm thử nguồn ST25';
		$cropWikitext = "Lúa kiểm thử.\n== Danh sách giống ==\n* [[{$varietyTitle}]]";
		$varietyWikitext = "{{InfoCropPlant\n|ThoiGianSinhTruong=100 ngày\n}}\nGiống ST25.";
		$this->editPage( $cropTitle, $cropWikitext );
		$this->editPage( $varietyTitle, $varietyWikitext );

		$pages = $this->extract( $cropTitle );
		$crop = $this->pageByKind( $pages, 'species' );
		$variety = $this->pageByKind( $pages, 'variety' );

		$this->assertSame( $cropWikitext, $crop['wikitext'] );
		$this->assertSame( hash( 'sha256', $cropWikitext ), $crop['content_hash'] );
		$this->assertStringContainsString( 'Danh sách giống', $crop['text'] );
		$this->assertStringNotContainsString( '==', $crop['text'] );
		$this->assertSame( $varietyWikitext, $variety['wikitext'] );
		$this->assertGreaterThan( 0, $variety['revision_id'] );
	}

	public function testResolvedRedirectKeepsRequestedAndResolvedIdentity() {
		$cropTitle = 'Lúa kiểm thử redirect';
		$requestedTitle = 'Lúa kiểm thử redirect A';
		$resolvedTitle = 'Lúa kiểm thử redirect B';
		$this->editPage(
			$cropTitle,
			"== Giống ==\n* [[{$requestedTitle}]]"
		);
		$this->editPage( $requestedTitle, "#REDIRECT [[{$resolvedTitle}]]" );
		$this->editPage( $resolvedTitle, 'Nội dung giống được chuyển hướng.' );

		$pages = $this->extract( $cropTitle );
		$variety = $this->pageByKind( $pages, 'variety' );

		$this->assertSame( $requestedTitle, $variety['requested_title'] );
		$this->assertSame( $resolvedTitle, $variety['resolved_title'] );
		$this->assertSame( 'Nội dung giống được chuyển hướng.', $variety['wikitext'] );
	}

	public function testNonWikitextLinkedPageReturnsErrorInsteadOfCandidate() {
		$cropTitle = 'Lúa kiểm thử content model';
		$jsonTitle = 'Lúa kiểm thử content model JSON';
		$this->editPage( $cropTitle, "== Giống ==\n* [[{$jsonTitle}]]" );
		$this->editPage( $jsonTitle, new JsonContent( '{"name":"ST"}' ) );

		$pages = $this->extract( $cropTitle );
		$errors = array_values( array_filter( $pages, static function ( $page ) {
			return !empty( $page['error'] );
		} ) );

		$this->assertCount( 1, $errors );
		$this->assertStringContainsString( 'không phải wikitext', $errors[0]['error'] );
	}

	/**
	 * @param string $cropTitle
	 * @return array<int,array<string,mixed>>
	 */
	private function extract( $cropTitle ) {
		$user = $this->getTestSysop()->getUser();
		$extractor = new LocalWikiExtractor(
			$this->getServiceContainer(),
			$user,
			[ NS_MAIN ],
			20,
			250000
		);
		return $extractor->extract( [ $cropTitle ] );
	}

	/**
	 * @param array<int,array<string,mixed>> $pages
	 * @param string $kind
	 * @return array<string,mixed>
	 */
	private function pageByKind( array $pages, $kind ) {
		foreach ( $pages as $page ) {
			if ( ( $page['kind'] ?? '' ) === $kind ) {
				return $page;
			}
		}
		$this->fail( "Không tìm thấy page kind {$kind}." );
	}
}
