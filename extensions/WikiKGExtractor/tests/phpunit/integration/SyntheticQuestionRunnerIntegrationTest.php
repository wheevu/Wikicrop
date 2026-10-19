<?php

namespace MediaWiki\Extension\WikiKGExtractor\Tests\Integration;

use MediaWiki\Extension\WikiKGExtractor\ClaimReviewStore;
use MediaWiki\Extension\WikiKGExtractor\ReviewedProjectionExport;
use MediaWiki\Extension\WikiKGExtractor\SyntheticQuestionRunner;
use MediaWiki\Permissions\Authority;
use MediaWikiIntegrationTestCase;
use Wikimedia\Rdbms\IDatabase;

/** @group Database */
class SyntheticQuestionRunnerIntegrationTest extends MediaWikiIntegrationTestCase {
	private ClaimReviewStore $store;
	private Authority $authority;
	private IDatabase $db;
	private string $root;
	private string $sourceTitle;
	private string $snapshotId;
	private bool $queueCreated = false;
	private ?string $previousDocumentRoot = null;
	private ?string $previousHttpPort = null;

	protected function setUp(): void {
		parent::setUp();
		$this->store = new ClaimReviewStore( $this->getServiceContainer()->getConnectionProvider() );
		$this->authority = $this->getTestSysop()->getAuthority();
		$this->db = $this->getServiceContainer()->getConnectionProvider()->getPrimaryDatabase();
		$this->db->query( 'CREATE TEMPORARY TABLE ' . $this->db->tableName( 'approve_queue' )
			. ' (aq_page_id INT NOT NULL, aq_revision_id INT NOT NULL, aq_status VARCHAR(16) NOT NULL, aq_is_latest INT NOT NULL)', __METHOD__ );
		$this->queueCreated = true;
		$this->root = sys_get_temp_dir() . '/wikikg-question-runner-' . bin2hex( random_bytes( 8 ) );
		mkdir( $this->root, 0700 );
		$this->previousDocumentRoot = $_SERVER['DOCUMENT_ROOT'] ?? null;
		$_SERVER['DOCUMENT_ROOT'] = realpath( dirname( __DIR__, 4 ) );
		$this->previousHttpPort = getenv( 'WIKIKG_HTTP_PORT' ) ?: null;
		putenv( 'WIKIKG_HTTP_PORT=8787' );
		$this->sourceTitle = 'Synthetic question runner source ' . bin2hex( random_bytes( 4 ) );
		$this->snapshotId = $this->createApprovedClaim();
		$this->writeCatalog();
		$manifest = ( new ReviewedProjectionExport( $this->store ) )->export( $this->authority );
		$this->writePrivate( $this->root . '/reviewed.json', json_encode( $manifest,
			JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR ) );
	}

	protected function tearDown(): void {
		if ( $this->queueCreated ) {
			$this->db->query( 'DROP TABLE ' . $this->db->tableName( 'approve_queue' ), __METHOD__ );
		}
		if ( $this->previousDocumentRoot === null ) { unset( $_SERVER['DOCUMENT_ROOT'] ); }
		else { $_SERVER['DOCUMENT_ROOT'] = $this->previousDocumentRoot; }
		if ( $this->previousHttpPort === null ) { putenv( 'WIKIKG_HTTP_PORT' ); }
		else { putenv( 'WIKIKG_HTTP_PORT=' . $this->previousHttpPort ); }
		$this->removeTestDirectory( $this->root );
		parent::tearDown();
	}

	public function testRunnerUsesCatalogQuestionAndAuthorityActorWithFixedGraphArguments(): void {
		$runner = $this->runner();
		$result = $runner->run( 'supported-compound', $this->authority );
		$this->assertSame( 'evidence', $result['status'] );
		$this->assertSame( $this->snapshotId, $result['_canonical_claims'][0]['snapshot_id'] );
		$argv = TestSyntheticQuestionRunner::$lastArgv;
		$this->assertSame( '/usr/bin/timeout', $argv[0] );
		$this->assertSame( '--signal=TERM', $argv[1] );
		$this->assertSame( '--kill-after=2s', $argv[2] );
		$this->assertSame( '30s', $argv[3] );
		$this->assertSame( '/usr/local/bin/python3', $argv[4] );
		$this->assertSame( $this->authority->getUser()->getName(), $argv[array_search( '--actor', $argv, true ) + 1] );
		$this->assertSame( 'bolt://127.0.0.1:7687', $argv[array_search( '--graph-uri', $argv, true ) + 1] );
		$this->assertSame( $this->root . '/graph-password', $argv[array_search( '--graph-password-file', $argv, true ) + 1] );
		$this->assertNotContains( '/tmp/forged', $argv );
		$this->assertNotContains( 'forged-actor', $argv );
		$this->assertNotContains( 'MATCH (n) RETURN n', $argv );
	}

	public function testDuplicateCatalogIdsAndUnknownCatalogFieldsAreRejected(): void {
		$this->writeCatalog( true );
		try {
			$this->runner()->listQuestions();
			$this->fail( 'Duplicate question IDs must be refused.' );
		} catch ( \RuntimeException $exception ) {
			$this->assertSame( 'Synthetic question catalog invalid.', $exception->getMessage() );
		}
		$this->writePrivate( $this->root . '/questions.json', json_encode( [
			'version' => 'wikikg.synthetic-questions.v1', 'questions' => [], 'actor' => 'forged',
		], JSON_THROW_ON_ERROR ) );
		$this->expectException( \RuntimeException::class );
		$this->runner()->listQuestions();
	}

	public function testSingleForbiddenQueryFieldInvalidatesCatalogQuestion(): void {
		$this->writePrivate( $this->root . '/questions.json', json_encode( [
			'version' => 'wikikg.synthetic-questions.v1', 'questions' => [ [
				'id' => 'supported-compound', 'label' => 'Bad lookup',
				'query' => [ 'intent' => 'lookup', 'entities' => [ [
					'type' => 'Crop', 'id' => 'Rice', 'crop_id' => 'Rice',
			] ], 'path' => [ 'HAS_PARENT' ] ],
			] ],
		], JSON_THROW_ON_ERROR ) );
		$this->expectException( \RuntimeException::class );
		$this->runner()->listQuestions();
	}

	public function testUnlistedQuestionIdIsRefusedBeforeGateOrProcess(): void {
		$runner = $this->runner();
		TestSyntheticQuestionRunner::$lastArgv = [];
		$result = $runner->run( 'not-in-catalog', $this->authority );
		$this->assertSame( 'dependency_failure', $result['status'] );
		$this->assertSame( 'invalid_question', $result['reasons'][0]['code'] );
		$this->assertSame( [], TestSyntheticQuestionRunner::$lastArgv );
	}

	public function testChildEnvironmentIsStrictAndCarriesOnlyValidatedSyntheticSettings(): void {
		$runner = new TestSyntheticQuestionRunner();
		$environment = $runner->getProcessEnvironment( $_SERVER['DOCUMENT_ROOT'] );
		$this->assertSame( '1', $environment['WIKIKG_SYNTHETIC'] );
		$this->assertSame( '/private/wikikg/LocalSettings.synthetic.php', $environment['MW_CONFIG_FILE'] );
		$this->assertSame( '8787', $environment['WIKIKG_HTTP_PORT'] );
		$this->assertSame( $_SERVER['DOCUMENT_ROOT'], $environment['DOCUMENT_ROOT'] );
		$this->assertArrayNotHasKey( 'GEMINI_API_KEY', $environment );
		$this->assertArrayNotHasKey( 'NEO4J_PASSWORD', $environment );
		$this->assertSame( [ 'PATH', 'PYTHONIOENCODING', 'WIKIKG_SYNTHETIC', 'MW_CONFIG_FILE',
			'WIKIKG_HTTP_PORT', 'DOCUMENT_ROOT' ], array_keys( $environment ) );
		putenv( 'WIKIKG_HTTP_PORT=99999' );
		try {
			$runner->getProcessEnvironment( $_SERVER['DOCUMENT_ROOT'] );
			$this->fail( 'Out-of-range synthetic HTTP ports must be refused.' );
		} catch ( \RuntimeException $exception ) {
			$this->assertSame( 'Synthetic child environment refused.', $exception->getMessage() );
		} finally {
			putenv( 'WIKIKG_HTTP_PORT=8787' );
		}
	}

	public function testFirstQuestionCreatesOwnerPrivateAttemptsParent(): void {
		$this->assertFalse( is_dir( $this->root . '/attempts' ) );
		$runner = $this->runner();
		$result = $runner->run( 'supported-compound', $this->authority );
		$this->assertSame( 'evidence', $result['status'] );
		$this->assertDirectoryExists( $this->root . '/attempts' );
		$this->assertSame( 0700, fileperms( $this->root . '/attempts' ) & 07777 );
		$this->assertSame( posix_geteuid(), fileowner( $this->root . '/attempts' ) );
	}

	public function testTimeoutAndInvalidChildOutputReturnSanitizedFailure(): void {
		$runner = $this->runner();
		TestSyntheticQuestionRunner::$exitCode = 124;
		TestSyntheticQuestionRunner::$response = null;
		$timeout = $runner->run( 'supported-compound', $this->authority );
		$this->assertSame( 'dependency_failure', $timeout['status'] );
		$this->assertSame( 'question_unavailable', $timeout['reasons'][0]['code'] );
		$this->assertArrayNotHasKey( '_canonical_claims', $timeout );
		$this->assertStringNotContainsString( 'private child diagnostic', json_encode( $timeout ) );

		$runner = $this->runner();
		TestSyntheticQuestionRunner::$exitCode = 0;
		TestSyntheticQuestionRunner::$response = [ 'password' => 'private-child-secret' ];
		$invalid = $runner->run( 'supported-compound', $this->authority );
		$this->assertSame( 'dependency_failure', $invalid['status'] );
		$this->assertSame( 'invalid_result', $invalid['reasons'][0]['code'] );
		$this->assertStringNotContainsString( 'private-child-secret', json_encode( $invalid ) );
	}

	public function testProductionProcessCaptureCapsBothChildLogs(): void {
		$runner = new class extends SyntheticQuestionRunner {
			public function callProcess( array $argv, string $out, string $err ): int {
				return $this->invokeProcess( $argv, $out, $err );
			}
		};
		$out = $this->root . '/bounded-out.log';
		$err = $this->root . '/bounded-err.log';
		$script = 'fwrite(STDOUT, str_repeat("x", 100000)); fwrite(STDERR, str_repeat("y", 100000));';
		$this->assertSame( 0, $runner->callProcess( [ PHP_BINARY, '-r', $script ], $out, $err ) );
		$this->assertLessThanOrEqual( 65536, filesize( $out ) );
		$this->assertLessThanOrEqual( 65536, filesize( $err ) );
	}

	public function testBlockedAuthorizedQueryRetainsSanitizedDiagnosticAndCanonicalClaims(): void {
		$runner = $this->runner();
		$response = $this->validResponse();
		$response['status'] = 'blocked';
		$response['query']['status'] = 'unsupported';
		$response['query']['complete'] = false;
		$response['query']['partial'] = true;
		$response['query']['results'][0]['eligible_claim_ids'] = [];
		$response['query']['results'][0]['diagnostic_claim_ids'] = [ $this->snapshotId ];
		$response['query']['reasons'] = [ [ 'code' => 'unresolved_identity', 'message' => '<script>unsafe</script>' ] ];
		$response['reasons'] = [ [ 'code' => 'unresolved_identity', 'message' => 'raw child text' ] ];
		$response['citations'] = [ [ 'url' => 'https://attacker.invalid/' ] ];
		TestSyntheticQuestionRunner::$response = $response;
		TestSyntheticQuestionRunner::$exitCode = 3;
		$result = $runner->run( 'supported-compound', $this->authority );
		$this->assertSame( 'blocked', $result['status'] );
		$this->assertSame( 'unsupported', $result['query']['status'] );
		$this->assertSame( [ 'code' => 'unresolved_identity' ], $result['query']['reasons'][0] );
		$this->assertSame( [ 'code' => 'unresolved_identity' ], $result['reasons'][0] );
		$this->assertSame( [], $result['citations'] );
		$this->assertSame( $this->snapshotId, $result['_canonical_claims'][0]['snapshot_id'] );
	}

	public function testExitStatusAndGraphStampMismatchNeverReturnPositiveEvidence(): void {
		$runner = $this->runner();
		TestSyntheticQuestionRunner::$exitCode = 3;
		$wrongExit = $runner->run( 'supported-compound', $this->authority );
		$this->assertSame( 'invalid_result', $wrongExit['reasons'][0]['code'] );

		$runner = $this->runner();
		$response = $this->validResponse();
		$response['execution']['graph']['generation'] = str_repeat( 'd', 64 );
		TestSyntheticQuestionRunner::$response = $response;
		$wrongGraph = $runner->run( 'supported-compound', $this->authority );
		$this->assertSame( 'invalid_result', $wrongGraph['reasons'][0]['code'] );
		$this->assertArrayNotHasKey( '_canonical_claims', $wrongGraph );

		$runner = $this->runner();
		$response = $this->validResponse();
		$response['execution']['graph']['epoch'] = 0;
		TestSyntheticQuestionRunner::$response = $response;
		$zeroEpoch = $runner->run( 'supported-compound', $this->authority );
		$this->assertSame( 'invalid_result', $zeroEpoch['reasons'][0]['code'] );

		$runner = $this->runner();
		$response = $this->validResponse();
		$response['status'] = 'blocked';
		$response['query']['status'] = 'unsupported';
		$response['execution']['graph']['source'] = 'manifest';
		TestSyntheticQuestionRunner::$response = $response;
		TestSyntheticQuestionRunner::$exitCode = 3;
		$wrongSource = $runner->run( 'supported-compound', $this->authority );
		$this->assertSame( 'invalid_result', $wrongSource['reasons'][0]['code'] );
	}

	public function testReviewRevocationDuringChildRunRemovesAllCanonicalEvidence(): void {
		$runner = $this->runner();
		$callbackRan = false;
		TestSyntheticQuestionRunner::$afterProcess = function () use ( &$callbackRan ): void {
			$callbackRan = true;
			$this->store->recordDecision( $this->snapshotId, (int)$this->getTestSysop()->getUser()->getId(),
				'rejected', 'Revoked during synthetic run.', 1 );
		};
		$result = $runner->run( 'supported-compound', $this->authority );
		$this->assertTrue( $callbackRan );
		$this->assertSame( 'dependency_failure', $result['status'] );
		$this->assertNull( $result['query'] );
		$this->assertSame( [], $result['citations'] );
		$this->assertArrayNotHasKey( '_canonical_claims', $result );
	}

	public function testReadPermissionRevocationDuringChildRunRemovesAllCanonicalEvidence(): void {
		$runner = $this->runner();
		$callbackRan = false;
		TestSyntheticQuestionRunner::$afterProcess = function () use ( &$callbackRan ): void {
			$callbackRan = true;
			$this->setGroupPermissions( '*', 'read', false );
			$this->setGroupPermissions( 'user', 'read', false );
			$this->setGroupPermissions( 'sysop', 'read', false );
		};
		$result = $runner->run( 'supported-compound', $this->authority );
		$this->assertTrue( $callbackRan );
		$this->assertSame( 'dependency_failure', $result['status'] );
		$this->assertNull( $result['query'] );
		$this->assertSame( [], $result['citations'] );
		$this->assertArrayNotHasKey( '_canonical_claims', $result );
	}

	private function runner(): TestSyntheticQuestionRunner {
		TestSyntheticQuestionRunner::$exitCode = 0;
		TestSyntheticQuestionRunner::$response = $this->validResponse();
		TestSyntheticQuestionRunner::$afterProcess = null;
		TestSyntheticQuestionRunner::$directory = $this->root;
		TestSyntheticQuestionRunner::$lastArgv = [];
		return new TestSyntheticQuestionRunner();
	}

	private function validResponse(): array {
		$manifest = json_decode( file_get_contents( $this->root . '/reviewed.json' ), true, 64, JSON_THROW_ON_ERROR );
		$claim = null;
		foreach ( $manifest['claims'] as $candidate ) {
			if ( $candidate['snapshot_id'] === $this->snapshotId ) { $claim = $candidate; break; }
		}
		$this->assertNotNull( $claim );
		$generation = str_repeat( 'a', 64 );
		$publicClaim = $claim;
		unset( $publicClaim['evidence'] );
		return [
			'version' => 'wikikg.reviewed-answer.v1', 'offline_only' => true, 'status' => 'evidence',
			'query' => [ 'generation' => $generation, 'semantic_version' => 'wikikg.reviewed-query.v2',
				'offline_only' => true, 'completeness_scope' => 'manifest', 'scope_policy_version' => 'test-v1',
				'status' => 'ok', 'complete' => true, 'partial' => false, 'reasons' => [],
				'partial' => false, 'results' => [ [ 'subject' => [ 'type' => 'Crop', 'id' => 'Rice', 'crop_id' => 'Rice' ],
					'claims' => [ [ 'claim' => $publicClaim, 'citation' => [
						'snapshot_id' => $claim['snapshot_id'], 'snapshot_hash' => $claim['snapshot_hash'],
						'review_version' => $claim['review_version'], ], 'evidence' => $claim['evidence'] ] ],
					'eligible_claim_ids' => [ $claim['snapshot_id'] ] ] ] ],
			'answer' => null, 'citations' => [], 'reasons' => [],
			'execution' => [ 'graph' => [ 'source' => 'neo4j', 'generation' => $generation, 'epoch' => 1 ] ],
		];
	}

	private function createApprovedClaim(): string {
		$sourceText = 'Source span for synthetic runner.';
		$revision = $this->editPage( $this->sourceTitle, $sourceText )->getNewRevision();
		$claim = [
			'claim_id' => hash( 'sha256', 'runner-claim' ), 'snapshot_id' => hash( 'sha256', 'runner-snapshot' ),
			'claim_type' => 'property', 'subject' => [ 'type' => 'Crop', 'id' => 'Rice' ],
			'predicate' => 'GROWTH_DURATION', 'value' => '100 days', 'qualifiers' => [ 'season' => 'dry' ],
			'evidence' => [ [ 'evidence_id' => hash( 'sha256', 'runner-evidence' ), 'page_id' => $revision->getPageId(),
				'revision_id' => $revision->getId(), 'content_hash' => hash( 'sha256', $sourceText ),
				'page_title' => $this->sourceTitle, 'extraction_method' => 'rules', 'extractor_version' => 'runner-test',
				'location_type' => 'source_span', 'source_offset_start' => 0,
				'source_offset_end' => mb_strlen( $sourceText, 'UTF-8' ), 'supporting_span' => $sourceText,
				'span_hash' => hash( 'sha256', $sourceText ) ] ],
			'traceability_complete' => true, 'review' => [ 'status' => 'pending' ],
		];
		$this->store->importVerified( [ 'schema_version' => '1.0', 'metadata' => [
			'extractor_version' => 'runner-test', 'extraction_method' => 'rules', 'review_state' => 'pending',
			'source_pages' => [ [ 'title' => $this->sourceTitle, 'page_id' => $revision->getPageId(),
				'revision_id' => $revision->getId(), 'content_hash' => hash( 'sha256', $sourceText ) ] ],
		], 'claims' => [ $claim ] ] );
		$this->store->recordDecision( $claim['snapshot_id'], (int)$this->getTestSysop()->getUser()->getId(), 'approved', 'Approved test claim.', 0 );
		$this->db->newInsertQueryBuilder()->insertInto( 'approve_queue' )->row( [
			'aq_page_id' => $revision->getPageId(), 'aq_revision_id' => $revision->getId(),
			'aq_status' => 'approved', 'aq_is_latest' => 1,
		] )->caller( __METHOD__ )->execute();
		return $claim['snapshot_id'];
	}

	private function writeCatalog( bool $duplicate = false ): void {
		$question = [ 'id' => 'supported-compound', 'label' => 'Duration and parent',
			'query' => [ 'intent' => 'lookup', 'entities' => [ [ 'type' => 'Crop', 'id' => 'Rice', 'crop_id' => 'Rice' ] ] ] ];
		$this->writePrivate( $this->root . '/questions.json', json_encode( [
			'version' => 'wikikg.synthetic-questions.v1', 'questions' => $duplicate ? [ $question, $question ] : [ $question ],
		], JSON_THROW_ON_ERROR ) );
	}

	private function writePrivate( string $path, string $contents ): void {
		file_put_contents( $path, $contents );
		chmod( $path, 0600 );
	}

	private function removeTestDirectory( string $path ): void {
		if ( !is_dir( $path ) || is_link( $path ) ) { return; }
		foreach ( scandir( $path ) ?: [] as $name ) {
			if ( $name === '.' || $name === '..' ) { continue; }
			$child = $path . '/' . $name;
			if ( is_dir( $child ) && !is_link( $child ) ) { $this->removeTestDirectory( $child ); }
			else { unlink( $child ); }
		}
		rmdir( $path );
	}
}

/** Isolated test adapter; production continues through the fixed proc_open path. */
class TestSyntheticQuestionRunner extends SyntheticQuestionRunner {
	public static string $directory;
	public static int $exitCode = 0;
	public static ?array $response = null;
	public static $afterProcess = null;
	public static array $lastArgv = [];

	protected function assertEnabledRuntime(): void {}
	protected function directory(): string { return self::$directory; }
	public function getProcessEnvironment( string $documentRoot ): array {
		return $this->processEnvironment( $documentRoot );
	}
	protected function invokeProcess( array $argv, string $stdoutPath, string $stderrPath ): int {
		self::$lastArgv = $argv;
		file_put_contents( $stdoutPath, 'child stdout ' . str_repeat( 'x', 100000 ) );
		file_put_contents( $stderrPath, 'private child diagnostic' );
		chmod( $stdoutPath, 0600 );
		chmod( $stderrPath, 0600 );
		if ( self::$response !== null ) {
			$output = $argv[array_search( '--output', $argv, true ) + 1];
			file_put_contents( $output, json_encode( self::$response, JSON_THROW_ON_ERROR ) );
			chmod( $output, 0600 );
		}
		if ( is_callable( self::$afterProcess ) ) { ( self::$afterProcess )(); }
		return self::$exitCode;
	}
}
