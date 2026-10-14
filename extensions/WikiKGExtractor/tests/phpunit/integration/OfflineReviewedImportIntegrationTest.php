<?php

namespace MediaWiki\Extension\WikiKGExtractor\Tests\Integration;

use MediaWiki\Extension\WikiKGExtractor\ClaimReviewStore;
use MediaWiki\Extension\WikiKGExtractor\OfflineReviewedImport;
use MediaWiki\Revision\SlotRecord;
use MediaWikiIntegrationTestCase;
use RuntimeException;
use Title;

/**
 * @group Database
 * @covers \MediaWiki\Extension\WikiKGExtractor\OfflineReviewedImport
 */
class OfflineReviewedImportIntegrationTest extends MediaWikiIntegrationTestCase {
	private ClaimReviewStore $store;
	private array $directories = [];

	protected function setUp(): void {
		parent::setUp();
		$this->store = new ClaimReviewStore( $this->getServiceContainer()->getConnectionProvider() );
	}

	protected function tearDown(): void {
		foreach ( $this->directories as $directory ) {
			foreach ( scandir( $directory ) ?: [] as $entry ) {
				if ( $entry !== '.' && $entry !== '..' ) {
					unlink( $directory . '/' . $entry );
				}
			}
			rmdir( $directory );
		}
		parent::tearDown();
	}

	public function testCurrentSyntheticImportIsIdempotentAndLaterRejectionRemainsAuthoritative(): void {
		$source = $this->liveSource( 'idempotent-import' );
		[ $claims, $raw ] = $this->artifacts( $source );
		[ $claimsPath, $rawPath ] = $this->writeArtifacts( $claims, $raw );
		$authority = $this->getTestUser( [ 'sysop' ] )->getAuthority();
		$first = OfflineReviewedImport::import( $this->store, $claimsPath, $rawPath, $authority, null );
		$second = OfflineReviewedImport::import( $this->store, $claimsPath, $rawPath, $authority, null );
		$this->assertSame( $first['snapshot_ids'], $second['snapshot_ids'] );
		$this->assertSame( 'pending', $this->store->getLatestReview( $first['snapshot_ids'][0] )['status'] );
		$this->assertNotNull( $this->store->recordDecision(
			$first['snapshot_ids'][0], $authority->getUser()->getId(), 'rejected', 'Synthetic test rejection.', 0
		) );
		$this->assertSame( 'rejected', $this->store->getLatestReview( $first['snapshot_ids'][0] )['status'] );
	}

	public function testMaintenanceDryRunUsesTheNamedUserAuthorityWithoutPersistingSnapshots(): void {
		require_once dirname( __DIR__, 3 ) . '/maintenance/importReviewedClaims.php';
		$source = $this->liveSource( 'named-cli-actor' );
		[ $claims, $raw ] = $this->artifacts( $source );
		[ $claimsPath, $rawPath ] = $this->writeArtifacts( $claims, $raw );
		$actor = $this->getTestUser( [ 'sysop' ] )->getUser();
		$documentRoot = $_SERVER['DOCUMENT_ROOT'] ?? getenv( 'DOCUMENT_ROOT' );
		if ( !is_string( $documentRoot ) || $documentRoot === '' ) {
			$documentRoot = getenv( 'DOCUMENT_ROOT' ) ?: dirname( __DIR__, 5 );
		}
		$command = $this->getMockBuilder( \ImportReviewedClaims::class )
			->onlyMethods( [ 'output', 'fatalError' ] )->getMock();
		$command->method( 'fatalError' )->willThrowException( new RuntimeException( 'Unexpected CLI refusal.' ) );
		$command->expects( $this->once() )->method( 'output' )
			->with( '{"status":"dry_run","claim_count":1}' . "\n" );
		foreach ( [ 'actor' => $actor->getName(), 'claims' => $claimsPath, 'raw-data' => $rawPath,
			'offline-only' => true, 'audience' => 'actor-only', 'synthetic-only' => true,
			'document-root' => realpath( $documentRoot ),
		] as $name => $value ) {
			$command->setOption( $name, $value );
		}
		$command->execute();
		$this->assertSame( [], $this->store->listSnapshotIdsAfter() );
	}

	public function testMaintenanceRefusesMismatchedDocumentRootBeforeOpeningArtifacts(): void {
		require_once dirname( __DIR__, 3 ) . '/maintenance/importReviewedClaims.php';
		$actor = $this->getTestUser( [ 'sysop' ] )->getUser();
		$wikiRoot = realpath( dirname( __DIR__, 5 ) );
		$previous = $_SERVER['DOCUMENT_ROOT'] ?? null;
		$_SERVER['DOCUMENT_ROOT'] = dirname( $wikiRoot );
		try {
			$command = $this->getMockBuilder( \ImportReviewedClaims::class )
				->onlyMethods( [ 'output', 'fatalError' ] )->getMock();
			$command->expects( $this->never() )->method( 'output' );
			$command->method( 'fatalError' )->willReturnCallback( static function ( $message ) {
				throw new RuntimeException( $message );
			} );
			foreach ( [ 'actor' => $actor->getName(), 'claims' => '/missing/candidate_claims.json',
				'raw-data' => '/missing/raw_data.json', 'offline-only' => true, 'audience' => 'actor-only',
				'synthetic-only' => true, 'document-root' => $wikiRoot,
			] as $name => $value ) {
				$command->setOption( $name, $value );
			}
			$this->expectException( RuntimeException::class );
			$this->expectExceptionMessage( '--document-root does not match DOCUMENT_ROOT.' );
			$command->execute();
		} finally {
			if ( $previous === null ) {
				unset( $_SERVER['DOCUMENT_ROOT'] );
			} else {
				$_SERVER['DOCUMENT_ROOT'] = $previous;
			}
		}
	}

	public function testRefusesFakePairedIdentityAndStaleCurrentRevisionBeforeImport(): void {
		$source = $this->liveSource( 'fake-import-identity' );
		[ $claims, $raw ] = $this->artifacts( $source );
		$fakeId = $source['page_id'] + 1000000;
		$claims['metadata']['source_pages'][0]['page_id'] = $fakeId;
		$claims['claims'][0]['evidence'][0]['page_id'] = $fakeId;
		$raw['pages'][0]['page_id'] = $fakeId;
		[ $claimsPath, $rawPath ] = $this->writeArtifacts( $claims, $raw );
		try {
			OfflineReviewedImport::import(
				$this->store,
				$claimsPath,
				$rawPath,
				$this->getTestUser( [ 'sysop' ] )->getAuthority(),
				null
			);
			$this->fail( 'A self-consistent paired artifact identity that is not a MediaWiki page must be rejected.' );
		} catch ( RuntimeException $exception ) {
			$this->assertStringContainsString( 'not the current MediaWiki page revision', $exception->getMessage() );
		}
		$this->assertSame( [], $this->store->listSnapshotIdsAfter() );

		$source = $this->liveSource( 'stale-import-source' );
		[ $claims, $raw ] = $this->artifacts( $source );
		[ $claimsPath, $rawPath ] = $this->writeArtifacts( $claims, $raw );
		$this->editPage( $source['title'], 'Newer synthetic revision.' );
		try {
			OfflineReviewedImport::import(
				$this->store,
				$claimsPath,
				$rawPath,
				$this->getTestUser( [ 'sysop' ] )->getAuthority(),
				null
			);
			$this->fail( 'A stale source revision must be rejected.' );
		} catch ( RuntimeException $exception ) {
			$this->assertStringContainsString( 'not the current MediaWiki page revision', $exception->getMessage() );
		}
		$this->assertSame( [], $this->store->listSnapshotIdsAfter() );
	}

	public function testBadActorAndNonPrivateArtifactsAreRejected(): void {
		$this->expectException( RuntimeException::class );
		$this->expectExceptionMessage( 'requires the review right' );
		OfflineReviewedImport::verifyArtifacts(
			'/missing/candidate_claims.json',
			'/missing/raw_data.json',
			$this->getTestUser()->getAuthority(),
			null
		);
	}

	public function testArtifactsMustBeOwnerOnlyBeforeTheirContentsAreRead(): void {
		$source = $this->liveSource( 'private-artifacts' );
		[ $claims, $raw ] = $this->artifacts( $source );
		[ $claimsPath, $rawPath ] = $this->writeArtifacts( $claims, $raw );
		chmod( $rawPath, 0644 );
		try {
			OfflineReviewedImport::verifyArtifacts(
				$claimsPath,
				$rawPath,
				$this->getTestUser( [ 'sysop' ] )->getAuthority(),
				null
			);
			$this->fail( 'Group-readable artifacts must be rejected.' );
		} catch ( RuntimeException $exception ) {
			$this->assertStringContainsString( 'owner-only', $exception->getMessage() );
		}
	}

	public function testLiveSourceRequiresValidHashAndWikitextMainSlot(): void {
		$source = $this->liveSource( 'wikitext-main-slot' );
		$authority = $this->getTestUser( [ 'sysop' ] )->getAuthority();
		$invalidHash = $source;
		$invalidHash['content_hash'] = 'not-a-sha256';
		try {
			OfflineReviewedImport::assertLiveCurrentSource( $invalidHash, $authority );
			$this->fail( 'Malformed source hashes must fail before querying page content.' );
		} catch ( RuntimeException $exception ) {
			$this->assertSame( 'Offline source identity is malformed.', $exception->getMessage() );
		}
		OfflineReviewedImport::assertLiveCurrentSource( $source, $authority );
		$evidenceIdentity = $source;
		$evidenceIdentity['page_title'] = $evidenceIdentity['title'];
		unset( $evidenceIdentity['title'] );
		OfflineReviewedImport::assertLiveCurrentSource( $evidenceIdentity, $authority );
		$mismatchedHash = $source;
		$mismatchedHash['content_hash'] = str_repeat( 'f', 64 );
		try {
			OfflineReviewedImport::assertLiveCurrentSource( $mismatchedHash, $authority );
			$this->fail( 'A well-formed but inaccurate content hash must be rejected.' );
		} catch ( RuntimeException $exception ) {
			$this->assertSame(
				'Offline source content does not match its live revision hash.',
				$exception->getMessage()
			);
		}

		$title = Title::newFromText( 'WikiKG offline import json model' );
		$title->setContentModel( CONTENT_MODEL_JSON );
		$json = '{"not":"wikitext"}';
		$page = $this->getServiceContainer()->getWikiPageFactory()->newFromTitle( $title );
		$status = $page->doUserEditContent(
			\ContentHandler::makeContent( $json, $title, CONTENT_MODEL_JSON ),
			$this->getTestUser( [ 'sysop' ] )->getAuthority(),
			'Synthetic JSON source'
		);
		$this->assertStatusGood( $status );
		$revision = $status->getNewRevision();
		$nonWikitext = [
			'title' => $title->getPrefixedText(),
			'page_id' => $revision->getPageId(),
			'revision_id' => $revision->getId(),
			'content_hash' => hash( 'sha256', $json ),
		];
		try {
			OfflineReviewedImport::assertLiveCurrentSource( $nonWikitext, $authority );
			$this->fail( 'A non-wikitext main slot must not be accepted as source evidence.' );
		} catch ( RuntimeException $exception ) {
			$this->assertSame( 'Offline source main slot is not UTF-8 wikitext.', $exception->getMessage() );
		}
	}

	public function testLiveSourceRequiresCurrentRevisionAndValidUtf8Wikitext(): void {
		$source = $this->liveSource( 'current-unicode-source' );
		$this->assertStringContainsString( 'lúa', $source['wikitext'] );
		OfflineReviewedImport::assertLiveCurrentSource(
			$source,
			$this->getTestUser( [ 'sysop' ] )->getAuthority()
		);
		$this->editPage( $source['title'], 'A new live source revision.' );
		try {
			OfflineReviewedImport::assertLiveCurrentSource(
				$source,
				$this->getTestUser( [ 'sysop' ] )->getAuthority()
			);
			$this->fail( 'A source identity pinned to an older revision must be refused.' );
		} catch ( RuntimeException $exception ) {
			$this->assertSame(
				'Offline source is not the current MediaWiki page revision.',
				$exception->getMessage()
			);
		}
	}

	private function liveSource( string $key ): array {
		$title = 'WikiKG offline import ' . $key;
		$revision = $this->editPage( $title, 'Synthetic evidence cho lúa: ' . $key . '.' )->getNewRevision();
		$text = $revision->getContent( SlotRecord::MAIN )->getText();
		return [
			'title' => $title,
			'page_id' => $revision->getPageId(),
			'revision_id' => $revision->getId(),
			'wikitext' => $text,
			'content_hash' => hash( 'sha256', $text ),
		];
	}

	private function artifacts( array $source ): array {
		$span = $source['wikitext'];
		$identity = [
			'title' => $source['title'], 'page_id' => $source['page_id'],
			'revision_id' => $source['revision_id'], 'content_hash' => $source['content_hash'],
		];
		return [
			[
				'schema_version' => '1.0',
				'metadata' => [
					'extractor_version' => 'test-1',
					'extraction_method' => 'rules',
					'review_state' => 'pending',
					'source_pages' => [ $identity ],
				],
				'summary' => [
					'claim_count' => 1,
					'traceability_complete_count' => 1,
					'claims_by_predicate' => [ 'GROWTH_DURATION' => 1 ],
				],
				'claims' => [ [
					'claim_id' => hash( 'sha256', 'claim:' . $source['title'] ),
					'claim_type' => 'property', 'subject' => [ 'type' => 'Crop', 'id' => 'Rice' ],
					'predicate' => 'GROWTH_DURATION', 'value' => '100 days', 'qualifiers' => (object)[],
					'evidence' => [ [
						'evidence_id' => hash( 'sha256', 'evidence:' . $source['title'] ),
						'page_id' => $source['page_id'], 'revision_id' => $source['revision_id'],
						'content_hash' => $source['content_hash'], 'page_title' => $source['title'],
						'extraction_method' => 'rules', 'extractor_version' => 'test-1',
						'location_type' => 'source_span', 'source_offset_start' => 0,
						'source_offset_end' => mb_strlen( $span, 'UTF-8' ), 'supporting_span' => $span,
						'span_hash' => hash( 'sha256', $span ),
					] ],
					'traceability_complete' => true, 'review' => [ 'status' => 'pending' ],
				] ],
			],
			[
				'schema_version' => '1.0', 'page_count' => 1, 'error_count' => 0,
				'pages' => [ [
					'title' => $source['title'], 'page_id' => $source['page_id'], 'revision_id' => $source['revision_id'],
					'content_hash' => $source['content_hash'], 'wikitext' => $span, 'text' => $span,
				] ], 'errors' => [],
			],
		];
	}

	private function writeArtifacts( array $claims, array $raw ): array {
		$directory = sys_get_temp_dir() . '/wikikg-offline-import-' . bin2hex( random_bytes( 6 ) );
		mkdir( $directory, 0700 );
		$this->directories[] = $directory;
		$claimsPath = $directory . '/candidate_claims.json';
		$rawPath = $directory . '/raw_data.json';
		file_put_contents( $claimsPath, json_encode(
			$claims,
			JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR
		) );
		file_put_contents( $rawPath, json_encode(
			$raw,
			JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR
		) );
		chmod( $claimsPath, 0600 );
		chmod( $rawPath, 0600 );
		return [ $claimsPath, $rawPath ];
	}
}
