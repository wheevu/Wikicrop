<?php

namespace MediaWiki\Extension\WikiKGExtractor\Tests\Integration;

use MediaWiki\Extension\WikiKGExtractor\OfflineDevelopmentScope;
use MediaWikiIntegrationTestCase;
use RuntimeException;

/**
 * @group Database
 * @covers \MediaWiki\Extension\WikiKGExtractor\OfflineDevelopmentScope
 */
class OfflineDevelopmentScopeIntegrationTest extends MediaWikiIntegrationTestCase {
	private string $directory;

	protected function setUp(): void {
		parent::setUp();
		$this->directory = sys_get_temp_dir() . '/wikikg-offline-scope-' . bin2hex( random_bytes( 6 ) );
		mkdir( $this->directory, 0700 );
	}

	protected function tearDown(): void {
		foreach ( glob( $this->directory . '/*' ) ?: [] as $path ) {
			unlink( $path );
		}
		rmdir( $this->directory );
		parent::tearDown();
	}

	public function testAllowsOnlyManifestSourceAndSelectedDevelopmentIdentities(): void {
		$this->expectNotToPerformAssertions();
		$manifest = $this->manifest();
		$scope = $this->scope( $manifest );
		$scope->assertSource( $manifest['source'] );
		$scope->assertSource( [
			'title' => 'Dev crop', 'page_id' => 2, 'revision_id' => 22, 'content_hash' => str_repeat( 'b', 64 ),
		] );
		$scope->assertProjectionClaim( [ 'evidence' => [ [
			'page_title' => 'Dev crop', 'page_id' => 2, 'revision_id' => 22, 'content_hash' => str_repeat( 'b', 64 ),
		] ] ] );
	}

	public function testRejectsHeldoutUnselectedAndIdentitySubstitution(): void {
		$manifest = $this->manifest();
		$scope = $this->scope( $manifest );
		foreach ( [
			[ 'title' => 'Held out', 'page_id' => 3, 'revision_id' => 33, 'content_hash' => str_repeat( 'c', 64 ) ],
			[ 'title' => 'Unselected', 'page_id' => 4, 'revision_id' => 44, 'content_hash' => str_repeat( 'd', 64 ) ],
			[ 'title' => 'Dev crop', 'page_id' => 2, 'revision_id' => 23, 'content_hash' => str_repeat( 'b', 64 ) ],
		] as $identity ) {
			try {
				$scope->assertSource( $identity );
				$this->fail( 'Out-of-scope source identity was accepted.' );
			} catch ( RuntimeException $exception ) {
				$this->assertStringContainsString( 'not in the pinned development scope', $exception->getMessage() );
			}
		}
	}

	public function testRejectsManifestHashTamperingAndMalformedShape(): void {
		$path = $this->write( '{"schema_version":"1.0"}' );
		$this->expectException( RuntimeException::class );
		new OfflineDevelopmentScope( $path, str_repeat( '0', 64 ) );
	}

	public function testUnselectedMissingPageDoesNotInvalidateThePinnedDevelopmentAllowlist(): void {
		$manifest = $this->manifest();
		$manifest['pages'][2] = array_replace( $manifest['pages'][2], [
			'status' => 'missing', 'resolved_title' => null, 'page_id' => null,
			'revision_id' => null, 'content_hash' => null, 'eligible_for_development' => false,
			'heldout_exclusion_reason' => 'missing page',
		] );
		$scope = $this->scope( $manifest );
		$scope->assertSource( $manifest['source'] );
		$scope->assertSource( [ 'title' => $manifest['pages'][0]['resolved_title'] ] + $manifest['pages'][0] );
		$this->assertInstanceOf( OfflineDevelopmentScope::class, $scope );
	}

	private function scope( array $manifest ): OfflineDevelopmentScope {
		$bytes = json_encode( $manifest, JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR );
		return new OfflineDevelopmentScope( $this->write( $bytes ), hash( 'sha256', $bytes ) );
	}

	private function write( string $bytes ): string {
		$path = $this->directory . '/manifest-' . bin2hex( random_bytes( 4 ) ) . '.json';
		file_put_contents( $path, $bytes );
		return $path;
	}

	private function manifest(): array {
		return [
			'schema_version' => '1.0',
			'corpus' => 'synthetic test corpus',
			'source' => [ 'title' => 'Manifest source', 'page_id' => 1, 'revision_id' => 11, 'content_hash' => str_repeat( 'a', 64 ), 'discovery_heading_rule' => 'test heading' ],
			'selection' => [ 'method' => 'test', 'strata' => 'test', 'seed' => 'fixture', 'sample_size' => 2, 'dev_size' => 1, 'test_size' => 1, 'heldout_exclusion_policy' => 'test only', 'previously_observed_titles' => [] ],
			'counts' => [ 'discovered' => 3, 'statuses' => [], 'selected' => 2 ],
			'pages' => [
				[ 'discovery_index' => 0, 'requested_title' => 'Dev crop', 'status' => 'resolved', 'resolved_title' => 'Dev crop', 'page_id' => 2, 'revision_id' => 22, 'content_hash' => str_repeat( 'b', 64 ), 'selected' => true, 'split' => 'dev', 'eligible_for_development' => true, 'eligible_for_heldout' => true, 'heldout_exclusion_reason' => null ],
				[ 'discovery_index' => 1, 'requested_title' => 'Held out', 'status' => 'resolved', 'resolved_title' => 'Held out', 'page_id' => 3, 'revision_id' => 33, 'content_hash' => str_repeat( 'c', 64 ), 'selected' => true, 'split' => 'test', 'eligible_for_development' => false, 'eligible_for_heldout' => true, 'heldout_exclusion_reason' => null ],
				[ 'discovery_index' => 2, 'requested_title' => 'Unselected', 'status' => 'resolved', 'resolved_title' => 'Unselected', 'page_id' => 4, 'revision_id' => 44, 'content_hash' => str_repeat( 'd', 64 ), 'selected' => false, 'split' => null, 'eligible_for_development' => true, 'eligible_for_heldout' => false, 'heldout_exclusion_reason' => 'outside sample' ],
			],
		];
	}
}
