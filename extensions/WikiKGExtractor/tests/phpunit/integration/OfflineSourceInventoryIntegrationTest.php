<?php

namespace MediaWiki\Extension\WikiKGExtractor\Tests\Integration;

use MediaWiki\Extension\WikiKGExtractor\OfflineSourceInventory;
use MediaWiki\Extension\WikiKGExtractor\ClaimDocument;
use MediaWikiIntegrationTestCase;
use RuntimeException;

/**
 * @group Database
 * @covers \MediaWiki\Extension\WikiKGExtractor\OfflineSourceInventory
 * @covers \MediaWiki\Extension\WikiKGExtractor\ClaimDocument
 */
class OfflineSourceInventoryIntegrationTest extends MediaWikiIntegrationTestCase {
	private array $artifactDirectories = [];

	protected function tearDown(): void {
		foreach ( $this->artifactDirectories as $directory ) {
			foreach ( scandir( $directory ) ?: [] as $file ) {
				if ( $file !== '.' && $file !== '..' ) {
					unlink( $directory . DIRECTORY_SEPARATOR . $file );
				}
			}
			rmdir( $directory );
		}
		parent::tearDown();
	}

	public function testHeldoutTargetAndAliasAreExcludedBeforeAnyContentRead(): void {
		$pages = [
			$this->page( 1, 'Public alias', 'Held out' ),
			$this->page( 2, 'Held out' ),
			$this->page( 3, 'Ordinary page' ),
		];
		$reads = [];
		$inventory = ( new OfflineSourceInventory() )->inventory( $pages,
			[ [ 'page_id' => 2, 'requested_title' => 'Held out', 'resolved_title' => 'Held out' ] ],
			static function ( $page ) use ( &$reads ) {
				$reads[] = $page['page_id'];
				return [ 'page_id' => $page['page_id'], 'revision_id' => $page['revision_id'],
					'kind' => 'species', 'crop' => 'Rice', 'wikitext' => 'synthetic' ];
			} );
		$this->assertSame( [ 3 ], $reads );
		$this->assertSame( 'heldout_target_or_alias', $inventory['pages'][0]['reason'] );
		$this->assertSame( 'heldout_target_or_alias', $inventory['pages'][1]['reason'] );
		$this->assertTrue( $inventory['pages'][2]['eligible'] );
	}

	public function testRedirectCyclesMissingTargetsAndExternalRedirectsStayIneligible(): void {
		$pages = [
			$this->page( 1, 'Cycle A', 'Cycle B' ),
			$this->page( 2, 'Cycle B', 'Cycle A' ),
			$this->page( 3, 'Missing', 'Nowhere' ),
			array_merge( $this->page( 4, 'External' ), [ 'redirect_target' => null, 'redirect_external' => true ] ),
		];
		$inventory = ( new OfflineSourceInventory() )->inventory( $pages, [], static function () {
			throw new RuntimeException( 'Content must not be read.' );
		} );
		$this->assertSame( [ 'redirect_cycle', 'redirect_cycle', 'unresolved_redirect', 'external_redirect' ],
			array_column( $inventory['pages'], 'reason' ) );
	}

	public function testHardLimitAndUnknownClassificationAreExplicit(): void {
		$this->expectException( RuntimeException::class );
		( new OfflineSourceInventory() )->inventory( [ $this->page( 1, 'One' ), $this->page( 2, 'Two' ) ], [], static function () {}, 1 );
	}

	public function testUnknownClassificationIsAccountedWithoutPublishingContent(): void {
		$inventory = ( new OfflineSourceInventory() )->inventory( [ $this->page( 1, 'Unclassified' ) ], [],
			static function () { return [ 'kind' => null, 'crop' => null ]; } );
		$this->assertFalse( $inventory['pages'][0]['eligible'] );
		$this->assertSame( 'unknown_classification', $inventory['pages'][0]['reason'] );
		$this->assertArrayNotHasKey( 'wikitext', $inventory['pages'][0] );
	}

	public function testNamespaceAndUnreadableSuppressedPagesHaveExplicitReasons(): void {
		$pages = [ $this->page( 1, 'Talk', null, 1 ), $this->page( 2, 'Unreadable' ), $this->page( 3, 'Suppressed' ) ];
		$inventory = ( new OfflineSourceInventory() )->inventory( $pages, [], static function ( $page ) {
			return $page['page_id'] === 2 ? [ 'unreadable' => true ] : [ 'suppressed' => true ];
		} );
		$this->assertSame( [ 'unsupported_namespace', 'unreadable', 'suppressed' ], array_column( $inventory['pages'], 'reason' ) );
	}

	public function testUnsupportedNamespaceCannotShadowMainNamespaceRedirectTarget(): void {
		$pages = [ $this->page( 1, 'Redirect', 'Shared title' ),
			$this->page( 2, 'Shared title', null, 1 ), $this->page( 3, 'Shared title' ) ];
		$inventory = ( new OfflineSourceInventory() )->inventory( $pages, [], static function ( $page ) {
			return [ 'page_id' => $page['page_id'], 'revision_id' => $page['revision_id'],
				'kind' => 'species', 'crop' => 'Rice', 'wikitext' => 'synthetic' ];
		} );
		$this->assertSame( 'redirect_alias', $inventory['pages'][0]['reason'] );
		$this->assertSame( 'unsupported_namespace', $inventory['pages'][1]['reason'] );
		$this->assertTrue( $inventory['pages'][2]['eligible'] );
	}

	public function testChangedReaderRevisionCannotBecomeEligible(): void {
		$inventory = ( new OfflineSourceInventory() )->inventory( [ $this->page( 1, 'Changed source' ) ], [],
			static function ( $page ) {
				return [ 'page_id' => $page['page_id'], 'revision_id' => $page['revision_id'] + 1,
					'kind' => 'species', 'crop' => 'Rice', 'wikitext' => 'synthetic' ];
			} );
		$this->assertFalse( $inventory['pages'][0]['eligible'] );
		$this->assertSame( 'stale_page_revision', $inventory['pages'][0]['reason'] );
	}

	public function testUnsupportedRedirectNamespaceWithNullTargetIsNeverRead(): void {
		$page = $this->page( 1, 'Inter-namespace redirect' );
		$page['redirect_namespace'] = 1;
		$inventory = ( new OfflineSourceInventory() )->inventory( [ $page ], [], static function () {
			throw new RuntimeException( 'Content must not be read.' );
		} );
		$this->assertFalse( $inventory['pages'][0]['eligible'] );
		$this->assertSame( 'unsupported_redirect_namespace', $inventory['pages'][0]['reason'] );
	}

	public function testNormalizedTitleCollisionAbortsBeforeAnyContentRead(): void {
		$pages = [ $this->page( 1, 'Rice Foo' ), $this->page( 2, 'rice foo' ) ];
		$this->expectException( RuntimeException::class );
		$this->expectExceptionMessage( 'Ambiguous normalized main-namespace title' );
		( new OfflineSourceInventory() )->inventory( $pages, [], static function () {
			throw new RuntimeException( 'Content must not be read.' );
		} );
	}

	public function testOutOfRangePageIdentityAbortsBeforeContentRead(): void {
		$this->expectException( RuntimeException::class );
		( new OfflineSourceInventory() )->inventory( [ $this->page( 4294967296, 'Out of range' ) ], [], static function () {
			throw new RuntimeException( 'Content must not be read.' );
		} );
	}

	public function testDuplicatePageIdentityAbortsBeforeContentRead(): void {
		$this->expectExceptionMessage( 'Duplicate page ID' );
		$this->expectException( RuntimeException::class );
		( new OfflineSourceInventory() )->inventory( [ $this->page( 1, 'First' ), $this->page( 1, 'Second' ) ], [], static function () {
			throw new RuntimeException( 'Content must not be read.' );
		} );
	}

	public function testMalformedRedirectMetadataAbortsBeforeContentRead(): void {
		$page = $this->page( 1, 'Malformed redirect' );
		$page['redirect_external'] = 'false';
		$this->expectException( RuntimeException::class );
		( new OfflineSourceInventory() )->inventory( [ $page ], [], static function () {
			throw new RuntimeException( 'Content must not be read.' );
		} );
	}

	public function testPairedRawAndCandidateArtifactsAreConsumableByClaimDocument(): void {
		$directory = sys_get_temp_dir() . '/wikikg-discovery-pair-' . bin2hex( random_bytes( 8 ) );
		$this->assertTrue( mkdir( $directory, 0700 ) );
		$this->artifactDirectories[] = $directory;
		$text = 'Synthetic source text.';
		$hash = hash( 'sha256', $text );
		$raw = [ 'schema_version' => '1.0', 'page_count' => 1, 'error_count' => 0,
			'pages' => [ [ 'title' => 'Synthetic crop', 'text' => $text, 'wikitext' => $text,
				'kind' => 'species', 'crop' => 'Synthetic crop', 'page_id' => 901,
				'revision_id' => 902, 'content_hash' => $hash ] ], 'errors' => [] ];
		$claims = [ 'schema_version' => '1.0',
			'metadata' => [ 'extractor_version' => '2.3.1', 'extraction_method' => 'rules',
				'review_state' => 'pending', 'source_pages' => [ [ 'title' => 'Synthetic crop',
					'page_id' => 901, 'revision_id' => 902, 'content_hash' => $hash ] ] ],
			'summary' => [ 'claim_count' => 0, 'traceability_complete_count' => 0,
				'claims_by_predicate' => (object)[] ],
			'claims' => [] ];
		$rawPath = $directory . '/raw_data.json';
		$claimsPath = $directory . '/candidate_claims.json';
		file_put_contents( $rawPath, json_encode( $raw, JSON_THROW_ON_ERROR ) );
		file_put_contents( $claimsPath, json_encode( $claims, JSON_THROW_ON_ERROR ) );
		chmod( $rawPath, 0600 );
		chmod( $claimsPath, 0600 );

		$loaded = ClaimDocument::load( $claimsPath, $rawPath );

		$this->assertCount( 0, $loaded['claims'] );
		$this->assertSame( 901, $loaded['metadata']['source_pages'][0]['page_id'] );
	}

	private function page( int $id, string $title, ?string $target = null, int $namespace = 0 ): array {
		return [ 'page_id' => $id, 'title' => $title, 'namespace' => $namespace,
			'revision_id' => 100 + $id, 'redirect_target' => $target, 'redirect_namespace' => 0 ];
	}
}
