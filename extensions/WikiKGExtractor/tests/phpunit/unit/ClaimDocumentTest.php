<?php

namespace MediaWiki\Extension\WikiKGExtractor\Tests;

use MediaWiki\Extension\WikiKGExtractor\ClaimDocument;
use MediaWikiUnitTestCase;
use RuntimeException;

/**
 * @covers \MediaWiki\Extension\WikiKGExtractor\ClaimDocument
 */
class ClaimDocumentTest extends MediaWikiUnitTestCase {
	/** @var string */
	private $directory;

	protected function setUp(): void {
		parent::setUp();
		$this->directory = sys_get_temp_dir() . DIRECTORY_SEPARATOR
			. 'wikikg-claims-' . bin2hex( random_bytes( 6 ) );
		mkdir( $this->directory, 0775, true );
	}

	protected function tearDown(): void {
		$this->removeDirectory( $this->directory );
		parent::tearDown();
	}

	public function testLoadsVerifiedVietnameseCodepointSpanAndStableSnapshot() {
		[ $claims, $raw ] = self::validArtifacts();
		$this->writeArtifacts( $claims, $raw );

		$first = ClaimDocument::load( $this->claimsPath(), $this->rawPath() );
		$second = ClaimDocument::load( $this->claimsPath(), $this->rawPath() );
		$this->assertSame( 'Lúa Việt Nam', $first['metadata']['source_pages'][0]['title'] );
		$this->assertSame( 'giống này chịu mặn.', $first['claims'][0]['evidence'][0]['supporting_span'] );
		$this->assertMatchesRegularExpression( '/\A[a-f0-9]{64}\z/', $first['claims'][0]['snapshot_id'] );
		$this->assertSame( $first['claims'][0]['snapshot_id'], $second['claims'][0]['snapshot_id'] );

		$claims['metadata']['source_pages'][0]['revision_id'] = 93;
		$claims['claims'][0]['evidence'][0]['revision_id'] = 93;
		$raw['pages'][0]['revision_id'] = 93;
		$this->writeArtifacts( $claims, $raw );
		$changed = ClaimDocument::load( $this->claimsPath(), $this->rawPath() );
		$this->assertNotSame( $first['claims'][0]['snapshot_id'], $changed['claims'][0]['snapshot_id'] );
	}

	public function testSnapshotDoesNotDependOnEvidenceArrayOrder() {
		[ $claims, $raw ] = self::validArtifacts();
		$secondEvidence = $claims['claims'][0]['evidence'][0];
		$secondEvidence['evidence_id'] = str_repeat( 'c', 64 );
		$secondEvidence['location'] = 'same verified span';
		$claims['claims'][0]['evidence'][] = $secondEvidence;
		$this->writeArtifacts( $claims, $raw );
		$firstOrder = ClaimDocument::load( $this->claimsPath(), $this->rawPath() );

		$claims['claims'][0]['evidence'] = array_reverse( $claims['claims'][0]['evidence'] );
		$this->writeArtifacts( $claims, $raw );
		$reverseOrder = ClaimDocument::load( $this->claimsPath(), $this->rawPath() );
		$this->assertSame(
			$firstOrder['claims'][0]['snapshot_id'],
			$reverseOrder['claims'][0]['snapshot_id']
		);
	}

	public function testRejectsEvidenceUsingByteOffsetsInsteadOfUnicodeCodepoints() {
		[ $claims, $raw ] = self::validArtifacts();
		$claims['claims'][0]['evidence'][0]['source_offset_start'] = strlen( 'Ở Việt Nam: ' );
		$claims['claims'][0]['evidence'][0]['source_offset_end'] = strlen( 'Ở Việt Nam: giống này chịu mặn.' );
		$this->writeArtifacts( $claims, $raw );

		$this->expectException( RuntimeException::class );
		ClaimDocument::load( $this->claimsPath(), $this->rawPath() );
	}

	public function testRejectsTamperedSpanText() {
		[ $claims, $raw ] = self::validArtifacts();
		$claims['claims'][0]['evidence'][0]['supporting_span'] = 'giống này kháng mặn.';
		$this->writeArtifacts( $claims, $raw );

		$this->expectException( RuntimeException::class );
		ClaimDocument::load( $this->claimsPath(), $this->rawPath() );
	}

	public function testRejectsSpanHashMismatchAfterExactSpanMatches() {
		[ $claims, $raw ] = self::validArtifacts();
		$claims['claims'][0]['evidence'][0]['span_hash'] = str_repeat( 'f', 64 );
		$this->writeArtifacts( $claims, $raw );

		$this->expectException( RuntimeException::class );
		ClaimDocument::load( $this->claimsPath(), $this->rawPath() );
	}

	public function testRejectsRawWikitextHashMismatch() {
		[ $claims, $raw ] = self::validArtifacts();
		$raw['pages'][0]['content_hash'] = str_repeat( 'f', 64 );
		$this->writeArtifacts( $claims, $raw );

		$this->expectException( RuntimeException::class );
		ClaimDocument::load( $this->claimsPath(), $this->rawPath() );
	}

	public function testRejectsEvidenceForUnpinnedPageIdentity() {
		[ $claims, $raw ] = self::validArtifacts();
		$claims['claims'][0]['evidence'][0]['page_id'] = 18;
		$this->writeArtifacts( $claims, $raw );

		$this->expectException( RuntimeException::class );
		ClaimDocument::load( $this->claimsPath(), $this->rawPath() );
	}

	public function testEvidenceMustMatchEveryPinnedPageIdentityField() {
		$changes = [
			'page_title' => 'Lúa khác',
			'page_id' => 18,
			'revision_id' => 93,
			'content_hash' => str_repeat( 'f', 64 )
		];
		foreach ( $changes as $field => $value ) {
			[ $claims, $raw ] = self::validArtifacts();
			$claims['claims'][0]['evidence'][0][$field] = $value;
			$this->writeArtifacts( $claims, $raw );
			try {
				ClaimDocument::load( $this->claimsPath(), $this->rawPath() );
				$this->fail( "Mismatched evidence {$field} should be rejected." );
			} catch ( RuntimeException $exception ) {
				$this->assertStringContainsString( 'pinned raw page', $exception->getMessage() );
			}
		}
	}

	public function testPageTitleEvidenceMustNameThePinnedPage() {
		[ $claims, $raw ] = self::validArtifacts();
		$evidence = $claims['claims'][0]['evidence'][0];
		unset(
			$evidence['source_offset_start'],
			$evidence['source_offset_end'],
			$evidence['supporting_span'],
			$evidence['span_hash']
		);
		$evidence['location_type'] = 'page_title';
		$evidence['location'] = 'Lúa khác';
		$claims['claims'][0]['evidence'] = [ $evidence ];
		$this->writeArtifacts( $claims, $raw );

		$this->expectException( RuntimeException::class );
		ClaimDocument::load( $this->claimsPath(), $this->rawPath() );
	}

	public function testRejectsMissingEvidenceInsteadOfShowingAnUnsupportedClaim() {
		[ $claims, $raw ] = self::validArtifacts();
		$claims['claims'][0]['evidence'] = [];
		$this->writeArtifacts( $claims, $raw );

		$this->expectException( RuntimeException::class );
		ClaimDocument::load( $this->claimsPath(), $this->rawPath() );
	}

	public function testThrowsWhenARequiredJobArtifactIsMissing() {
		$this->expectException( RuntimeException::class );
		ClaimDocument::load( $this->claimsPath(), $this->rawPath() );
	}

	public function testAcceptsPageOnlyEvidenceAsIncompleteWithoutQuoteFields() {
		[ $claims, $raw ] = self::validArtifacts();
		$evidence = $claims['claims'][0]['evidence'][0];
		unset(
			$evidence['source_offset_start'],
			$evidence['source_offset_end'],
			$evidence['supporting_span'],
			$evidence['span_hash']
		);
		$evidence['location_type'] = 'page';
		$claims['claims'][0]['evidence'] = [ $evidence ];
		$claims['claims'][0]['traceability_complete'] = false;
		$claims['summary']['traceability_complete_count'] = 0;
		$this->writeArtifacts( $claims, $raw );

		$document = ClaimDocument::load( $this->claimsPath(), $this->rawPath() );
		$this->assertFalse( $document['claims'][0]['traceability_complete'] );
		$this->assertSame( 'page', $document['claims'][0]['evidence'][0]['location_type'] );
		$this->assertArrayNotHasKey( 'supporting_span', $document['claims'][0]['evidence'][0] );
	}

	public function testRejectsPageOnlyEvidenceContainingQuote() {
		[ $claims, $raw ] = self::validArtifacts();
		$claims['claims'][0]['evidence'][0]['location_type'] = 'page';
		$claims['claims'][0]['traceability_complete'] = false;
		$this->writeArtifacts( $claims, $raw );

		$this->expectException( RuntimeException::class );
		ClaimDocument::load( $this->claimsPath(), $this->rawPath() );
	}

	public function testRejectsPageOnlyEvidenceMarkedComplete() {
		[ $claims, $raw ] = self::validArtifacts();
		$evidence = $claims['claims'][0]['evidence'][0];
		unset(
			$evidence['source_offset_start'],
			$evidence['source_offset_end'],
			$evidence['supporting_span'],
			$evidence['span_hash']
		);
		$evidence['location_type'] = 'page';
		$claims['claims'][0]['evidence'] = [ $evidence ];
		$claims['claims'][0]['traceability_complete'] = true;
		$this->writeArtifacts( $claims, $raw );

		$this->expectException( RuntimeException::class );
		ClaimDocument::load( $this->claimsPath(), $this->rawPath() );
	}

	public function testRejectsDuplicateClaimAndEvidenceIdentities() {
		[ $claims, $raw ] = self::validArtifacts();
		$claims['claims'][] = $claims['claims'][0];
		$claims['summary']['claim_count'] = 2;
		$this->writeArtifacts( $claims, $raw );
		try {
			ClaimDocument::load( $this->claimsPath(), $this->rawPath() );
			$this->fail( 'Duplicate claim identities should be rejected.' );
		} catch ( RuntimeException $exception ) {
			$this->assertStringContainsString( 'duplicate claim identities', $exception->getMessage() );
		}

		[ $claims, $raw ] = self::validArtifacts();
		$claims['claims'][0]['evidence'][] = $claims['claims'][0]['evidence'][0];
		$this->writeArtifacts( $claims, $raw );
		$this->expectException( RuntimeException::class );
		ClaimDocument::load( $this->claimsPath(), $this->rawPath() );
	}

	public function testRejectsClaimsEvidenceAndSpanOverConfiguredBounds() {
		[ $claims, $raw ] = self::validArtifacts();
		$claims['claims'] = array_fill( 0, 501, $claims['claims'][0] );
		$this->writeArtifacts( $claims, $raw );
		try {
			ClaimDocument::load( $this->claimsPath(), $this->rawPath() );
			$this->fail( 'More than 500 claims should be rejected.' );
		} catch ( RuntimeException $exception ) {
			$this->assertStringContainsString( 'schema version 1.0', $exception->getMessage() );
		}

		[ $claims, $raw ] = self::validArtifacts();
		$claims['claims'][0]['evidence'] = array_fill( 0, 21, $claims['claims'][0]['evidence'][0] );
		$this->writeArtifacts( $claims, $raw );
		try {
			ClaimDocument::load( $this->claimsPath(), $this->rawPath() );
			$this->fail( 'More than 20 evidence items should be rejected.' );
		} catch ( RuntimeException $exception ) {
			$this->assertStringContainsString( 'schema fields', $exception->getMessage() );
		}

		[ $claims, $raw ] = self::validArtifacts();
		$span = str_repeat( 'x', 10001 );
		$raw['pages'][0]['wikitext'] = $span;
		$raw['pages'][0]['text'] = $span;
		$raw['pages'][0]['content_hash'] = hash( 'sha256', $span );
		$claims['metadata']['source_pages'][0]['content_hash'] = $raw['pages'][0]['content_hash'];
		$evidence = &$claims['claims'][0]['evidence'][0];
		$evidence['content_hash'] = $raw['pages'][0]['content_hash'];
		$evidence['source_offset_start'] = 0;
		$evidence['source_offset_end'] = 10001;
		$evidence['supporting_span'] = $span;
		$evidence['span_hash'] = hash( 'sha256', $span );
		unset( $evidence );
		$this->writeArtifacts( $claims, $raw );
		$this->expectException( RuntimeException::class );
		ClaimDocument::load( $this->claimsPath(), $this->rawPath() );
	}

	public function testRejectsOversizeArtifactAndArtifactsFromDifferentJobDirectories() {
		[ $claims, $raw ] = self::validArtifacts();
		$this->writeArtifacts( $claims, $raw );
		file_put_contents( $this->claimsPath(), str_repeat( ' ', 5242881 ) );
		try {
			ClaimDocument::load( $this->claimsPath(), $this->rawPath() );
			$this->fail( 'An artifact over five MiB should be rejected.' );
		} catch ( RuntimeException $exception ) {
			$this->assertStringContainsString( 'size limit', $exception->getMessage() );
		}

		$this->writeArtifacts( $claims, $raw );
		$otherDirectory = $this->directory . DIRECTORY_SEPARATOR . 'other-job';
		mkdir( $otherDirectory );
		file_put_contents(
			$otherDirectory . DIRECTORY_SEPARATOR . 'raw_data.json',
			json_encode( $raw, JSON_UNESCAPED_UNICODE | JSON_THROW_ON_ERROR )
		);
		$this->expectException( RuntimeException::class );
		ClaimDocument::load(
			$this->claimsPath(),
			$otherDirectory . DIRECTORY_SEPARATOR . 'raw_data.json'
		);
	}

	public function testRejectsNonPendingMetadataAndUnsupportedSchemaVersion() {
		[ $claims, $raw ] = self::validArtifacts();
		$claims['metadata']['review_state'] = 'approved';
		$this->writeArtifacts( $claims, $raw );
		try {
			ClaimDocument::load( $this->claimsPath(), $this->rawPath() );
			$this->fail( 'Non-pending metadata should be rejected.' );
		} catch ( RuntimeException $exception ) {
			$this->assertStringContainsString( 'metadata', $exception->getMessage() );
		}

		[ $claims, $raw ] = self::validArtifacts();
		$claims['schema_version'] = '2.0';
		$this->writeArtifacts( $claims, $raw );
		$this->expectException( RuntimeException::class );
		ClaimDocument::load( $this->claimsPath(), $this->rawPath() );
	}

	/** @return array{array<string,mixed>,array<string,mixed>} */
	private static function validArtifacts() {
		$wikitext = 'Ở Việt Nam: giống này chịu mặn.';
		$contentHash = hash( 'sha256', $wikitext );
		$raw = [
			'schema_version' => '1.0',
			'page_count' => 1,
			'error_count' => 0,
			'pages' => [ [
				'title' => 'Lúa Việt Nam',
				'text' => $wikitext,
				'wikitext' => $wikitext,
				'kind' => 'species',
				'crop' => 'Lúa',
				'source_pages' => [],
				'page_id' => 17,
				'revision_id' => 92,
				'content_hash' => $contentHash
			] ],
			'errors' => []
		];
		$source = [
			'title' => 'Lúa Việt Nam',
			'page_id' => 17,
			'revision_id' => 92,
			'content_hash' => $contentHash
		];
		$evidence = [
			'evidence_id' => str_repeat( 'b', 64 ),
			'page_id' => 17,
			'revision_id' => 92,
			'content_hash' => $contentHash,
			'page_title' => 'Lúa Việt Nam',
			'extraction_method' => 'rules',
			'extractor_version' => 'test-1',
			'location_type' => 'source_span',
			'source_offset_start' => 12,
			'source_offset_end' => 31,
			'supporting_span' => 'giống này chịu mặn.',
			'span_hash' => hash( 'sha256', 'giống này chịu mặn.' )
		];
		$claims = [
			'schema_version' => '1.0',
			'metadata' => [
				'extractor_version' => 'test-1',
				'extraction_method' => 'rules',
				'review_state' => 'pending',
				'source_pages' => [ $source ]
			],
			'summary' => [
				'claim_count' => 1,
				'traceability_complete_count' => 1,
				'claims_by_predicate' => [ 'salt_tolerance' => 1 ]
			],
			'claims' => [ [
				'claim_id' => str_repeat( 'a', 64 ),
				'claim_type' => 'property',
				'subject' => [ 'type' => 'Variety', 'id' => 'ST25' ],
				'predicate' => 'salt_tolerance',
				'value' => 'chịu mặn',
				'qualifiers' => [ 'season' => 'mùa khô' ],
				'evidence' => [ $evidence ],
				'traceability_complete' => true,
				'review' => [ 'status' => 'pending' ]
			] ]
		];

		return [ $claims, $raw ];
	}

	/** @param array<string,mixed> $claims @param array<string,mixed> $raw */
	private function writeArtifacts( array $claims, array $raw ) {
		file_put_contents(
			$this->claimsPath(),
			json_encode( $claims, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR )
		);
		file_put_contents(
			$this->rawPath(),
			json_encode( $raw, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR )
		);
	}

	private function claimsPath() {
		return $this->directory . DIRECTORY_SEPARATOR . 'candidate_claims.json';
	}

	private function rawPath() {
		return $this->directory . DIRECTORY_SEPARATOR . 'raw_data.json';
	}

	private function removeDirectory( $directory ) {
		if ( !is_dir( $directory ) ) {
			return;
		}
		foreach ( scandir( $directory ) ?: [] as $entry ) {
			if ( $entry === '.' || $entry === '..' ) {
				continue;
			}
			$path = $directory . DIRECTORY_SEPARATOR . $entry;
			if ( is_dir( $path ) && !is_link( $path ) ) {
				$this->removeDirectory( $path );
			} else {
				unlink( $path );
			}
		}
		rmdir( $directory );
	}
}
