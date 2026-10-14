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
		$cropWikitext = "{{InfoPlant1}}\nLúa kiểm thử.\n== Danh sách giống ==\n* [[{$varietyTitle}]]";
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
			"{{InfoPlant1}}\n== Giống ==\n* [[{$requestedTitle}]]"
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
		$this->editPage(
			$cropTitle,
			"{{InfoPlant1}}\n== Giống ==\n* [[{$jsonTitle}]]"
		);
		$this->editPage( $jsonTitle, new JsonContent( '{"name":"ST"}' ) );

		$pages = $this->extract( $cropTitle );
		$errors = array_values( array_filter( $pages, static function ( $page ) {
			return !empty( $page['error'] );
		} ) );

		$this->assertCount( 1, $errors );
		$this->assertStringContainsString( 'không phải wikitext', $errors[0]['error'] );
	}

	public function testRequiresAnActiveCropTemplateOnTheRootPage() {
		$cropTitle = 'Lúa kiểm thử mẫu gốc không hoạt động';
		$varietyTitle = $cropTitle . ' ST25';
		$this->editPage(
			$cropTitle,
			"<!-- {{InfoPlant1}} -->\n<nowiki>{{InfoCropPlant}}</nowiki>\n"
				. "<pre>{{InfoPlant1}}</pre>\n"
				. "== Danh sách giống ==\n* [[{$varietyTitle}]]"
		);
		$this->editPage( $varietyTitle, 'Nội dung giống kiểm thử.' );

		$pages = $this->extract( $cropTitle );
		$successfulPages = array_values( array_filter( $pages, static function ( $page ) {
			return !empty( $page['text'] );
		} ) );

		$this->assertSame( [], $successfulPages );
		$this->assertStringContainsString( 'mẫu cây trồng được nhận diện', $pages[0]['error'] );
	}

	public function testExplicitTemplateNamespaceIsAcceptedForRootAndVariety() {
		$cropTitle = 'Lúa kiểm thử tên mẫu';
		$varietyTitle = 'Giống kiểm thử tên mẫu';
		$this->editPage(
			$cropTitle,
			"{{Template:InfoPlant1}}\n== Danh sách giống ==\n* [[{$varietyTitle}]]"
		);
		$this->editPage(
			$varietyTitle,
			"{{Template:InfoPlant1Giong}}\nNội dung giống."
		);

		$pages = $this->extract( $cropTitle );
		$this->assertSame( $cropTitle, $this->pageByKind( $pages, 'species' )['title'] );
		$this->assertSame( $varietyTitle, $this->pageByKind( $pages, 'variety' )['title'] );
	}

	public function testPreformattedTemplateTextDoesNotQualifyUnrelatedLinkedPage() {
		$cropTitle = 'Lúa kiểm thử mẫu trong pre';
		$unrelatedTitle = 'Trang kiểm thử mẫu trong pre';
		$this->editPage(
			$cropTitle,
			"{{InfoPlant1}}\n== Danh sách giống ==\n* [[{$unrelatedTitle}]]"
		);
		$this->editPage(
			$unrelatedTitle,
			"<pre>{{InfoPlant1Giong}}</pre>\nNội dung không phải giống."
		);

		$pages = $this->extract( $cropTitle );
		$this->assertSame( $cropTitle, $this->pageByKind( $pages, 'species' )['title'] );
		$this->assertCount( 2, $pages );
		$this->assertStringContainsString( 'không có mẫu giống', $pages[1]['error'] );
	}

	public function testAcceptsVarietyTemplateOrCropTiedTitleButIgnoresInactiveTemplateText() {
		$cropTitle = 'Lúa kiểm thử quy tắc giống';
		$titledVariety = $cropTitle . ' ST25';
		$templatedVariety = 'Giống kiểm thử không gắn tên loài';
		$inactiveTemplatePage = 'Trang kiểm thử mẫu giống giả';
		$this->editPage(
			$cropTitle,
			"{{InfoPlant1}}\n== Danh sách giống ==\n"
				. "* [[{$titledVariety}]]\n"
				. "* [[{$templatedVariety}]]\n"
				. "* [[{$inactiveTemplatePage}]]"
		);
		$this->editPage( $titledVariety, 'Nội dung giống theo tên loài.' );
		$this->editPage(
			$templatedVariety,
			"{{InfoPlant1Giong}}\nNội dung giống theo mẫu."
		);
		$this->editPage(
			$inactiveTemplatePage,
			"<!-- {{InfoPlant1Giong}} -->\n<nowiki>{{InfoPlant1Giong}}</nowiki>\n"
				. 'Nội dung trang không liên quan.'
		);

		$pages = $this->extract( $cropTitle );
		$varieties = array_values( array_filter( $pages, static function ( $page ) {
			return ( $page['kind'] ?? '' ) === 'variety';
		} ) );

		$this->assertCount( 2, $varieties );
		$this->assertSame(
			[ $titledVariety, $templatedVariety ],
			array_column( $varieties, 'title' )
		);
	}

	public function testKeepsFivePageRiceFixtureParityAndSkipsUnrelatedPage() {
		$cropTitle = 'Lúa';
		$varietyTitles = [ 'Lúa OM5451', 'Lúa ST24', 'Lúa ST25' ];
		$unrelatedTitle = 'Kỹ thuật trồng và chăm sóc lúa';
		$links = array_merge( $varietyTitles, [ $unrelatedTitle ] );
		$linkText = implode( "\n", array_map( static function ( $title ) {
			return '* [[' . $title . ']]';
		}, $links ) );
		$this->editPage(
			$cropTitle,
			"{{InfoPlant1}}\nNội dung về lúa.\n"
				. "== Danh sách giống ==\n{$linkText}"
		);
		foreach ( $varietyTitles as $varietyTitle ) {
			$this->editPage( $varietyTitle, 'Nội dung giống lúa.' );
		}
		$this->editPage( $unrelatedTitle, 'Hướng dẫn canh tác, không phải trang giống.' );

		$pages = $this->extract( $cropTitle );
		$successfulPages = array_values( array_filter( $pages, static function ( $page ) {
			return !empty( $page['text'] );
		} ) );
		$varieties = array_values( array_filter( $successfulPages, static function ( $page ) {
			return ( $page['kind'] ?? '' ) === 'variety';
		} ) );
		$errors = array_values( array_filter( $pages, static function ( $page ) {
			return !empty( $page['error'] );
		} ) );

		$this->assertCount( 4, $successfulPages );
		$this->assertCount( 5, $pages );
		$this->assertSame( $varietyTitles, array_column( $varieties, 'title' ) );
		$this->assertSame( $unrelatedTitle, $errors[0]['title'] );
	}

	public function testPageCapCountsSuccessfulRootsAndResolvedVarietiesOnlyOnce() {
		$firstCrop = 'Lúa kiểm thử cap A';
		$secondCrop = 'Lúa kiểm thử cap B';
		$firstVariety = 'Lúa kiểm thử cap giống A';
		$secondVariety = 'Lúa kiểm thử cap giống B';
		$this->editPage(
			$firstCrop,
			"{{InfoPlant1}}\n== Giống ==\n* [[{$firstVariety}]]"
		);
		$this->editPage(
			$secondCrop,
			"{{InfoPlant1}}\n== Giống ==\n* [[{$secondVariety}]]"
		);
		$this->editPage( $firstVariety, "{{InfoPlant1Giong}}\nNội dung giống A." );
		$this->editPage( $secondVariety, "{{InfoPlant1Giong}}\nNội dung giống B." );

		foreach ( [ 2, 3 ] as $limit ) {
			$pages = $this->extractWithLimit( [ $firstCrop, $secondCrop ], $limit );
			$successful = array_values( array_filter( $pages, static function ( $page ) {
				return !empty( $page['text'] );
			} ) );
			$this->assertCount( $limit, $successful );
			$this->assertSame(
				$limit === 2 ? [ 'species', 'species' ] : [ 'species', 'species', 'variety' ],
				array_column( $successful, 'kind' )
			);
			$this->assertContains(
				'Đã đạt giới hạn số trang thu thập trong một lần chạy.',
				array_column( $pages, 'error' )
			);
			$this->assertCount( count( $successful ), array_unique( array_column( $successful, 'page_id' ) ) );
			$this->assertRawPagesAreValid( $successful );
		}
	}

	public function testRedirectAliasesDeduplicateAndMergeCropProvenance() {
		$firstCrop = 'Lúa kiểm thử alias A';
		$aliasCrop = 'Lúa kiểm thử alias B';
		$thirdCrop = 'Lúa kiểm thử alias C';
		$rootAlias = 'Lúa kiểm thử alias gốc';
		$linkedRootAlias = 'Lúa kiểm thử alias liên kết loài';
		$canonical = 'Lúa kiểm thử alias giống';
		$firstAlias = 'Lúa kiểm thử alias trực tiếp';
		$secondAlias = 'Lúa kiểm thử alias khác';
		$distinct = 'Lúa kiểm thử alias giống riêng';
		$this->editPage(
			$firstCrop,
			"{{InfoPlant1}}\n== Giống ==\n* [[{$firstAlias}]]\n* [[{$linkedRootAlias}]]"
		);
		$this->editPage(
			$aliasCrop,
			"{{InfoPlant1}}\n== Giống ==\n* [[{$secondAlias}]]"
		);
		$this->editPage(
			$thirdCrop,
			"{{InfoPlant1}}\n== Giống ==\n* [[{$firstAlias}]]\n* [[{$canonical}]]\n* [[{$distinct}]]"
		);
		$this->editPage( $rootAlias, "#REDIRECT [[{$firstCrop}]]" );
		$this->editPage( $linkedRootAlias, "#REDIRECT [[{$thirdCrop}]]" );
		$this->editPage( $firstAlias, "#REDIRECT [[{$canonical}]]" );
		$this->editPage( $secondAlias, "#REDIRECT [[{$canonical}]]" );
		$this->editPage( $canonical, "{{InfoPlant1Giong}}\nNội dung giống chuẩn." );
		$this->editPage( $distinct, "{{InfoPlant1Giong}}\nNội dung giống riêng." );

		$pages = $this->extractWithLimit(
			[ $firstCrop, $rootAlias, $aliasCrop, $thirdCrop ],
			5
		);
		$successful = array_values( array_filter( $pages, static function ( $page ) {
			return !empty( $page['text'] );
		} ) );
		$varieties = array_values( array_filter( $successful, static function ( $page ) {
			return ( $page['kind'] ?? '' ) === 'variety';
		} ) );
		$this->assertSame( [ $canonical, $distinct ], array_column( $varieties, 'title' ) );
		$this->assertSame( [ $firstCrop, $aliasCrop, $thirdCrop ], $varieties[0]['source_pages'] );
		$this->assertSame( $firstAlias, $varieties[0]['requested_title'] );
		$this->assertCount( count( $successful ), array_unique( array_column( $successful, 'page_id' ) ) );
		$this->assertCount( 3, array_filter( $successful, static function ( $page ) {
			return ( $page['kind'] ?? '' ) === 'species';
		} ) );
		$this->assertRawPagesAreValid( $successful );
	}

	/**
	 * @param string $cropTitle
	 * @return array<int,array<string,mixed>>
	 */
	private function extract( $cropTitle ) {
		return $this->extractWithLimit( [ $cropTitle ], 20 );
	}

	private function extractWithLimit( array $cropTitles, $limit ) {
		$user = $this->getTestSysop()->getUser();
		$extractor = new LocalWikiExtractor(
			$this->getServiceContainer(),
			$user,
			[ NS_MAIN ],
			$limit,
			250000
		);
		return $extractor->extract( $cropTitles );
	}

	private function assertRawPagesAreValid( array $pages ) {
		$validator = new \ReflectionMethod(
			\MediaWiki\Extension\WikiKGExtractor\ClaimDocument::class,
			'validateRawDocument'
		);
		$validator->setAccessible( true );
		$validator->invoke( null, [
			'schema_version' => '1.0',
			'pages' => array_values( $pages ),
			'page_count' => count( $pages )
		] );
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
