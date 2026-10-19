<?php

namespace MediaWiki\Extension\WikiKGExtractor\Tests\Integration;

use MediaWiki\Extension\WikiKGExtractor\SpecialWikiKGSyntheticQuestions;
use MediaWiki\Extension\WikiKGExtractor\SyntheticQuestionRunner;
use MediaWiki\Permissions\SimpleAuthority;
use MediaWiki\Request\FauxRequest;
use PermissionsError;
use SpecialPageTestBase;

/** @group Database */
class SpecialWikiKGSyntheticQuestionsIntegrationTest extends SpecialPageTestBase {
	private bool $useFormRunner = false;
	public static int $runCalls = 0;
	public static ?string $lastQuestionId = null;
	public static ?string $lastActor = null;
	public static ?array $runResult = null;

	protected function newSpecialPage() {
		$useFormRunner = $this->useFormRunner;
		return new class( $useFormRunner ) extends SpecialWikiKGSyntheticQuestions {
			private bool $useFormRunner;
			public function __construct( bool $useFormRunner ) {
				$this->useFormRunner = $useFormRunner;
				parent::__construct();
			}
			protected function newRunner(): SyntheticQuestionRunner {
				if ( !$this->useFormRunner ) { return parent::newRunner(); }
				return new class extends SyntheticQuestionRunner {
					public function listQuestions(): array {
						return [ [ 'id' => 'supported-compound', 'label' => 'Duration and parent',
							'query' => [ 'intent' => 'lookup', 'entities' => [] ] ] ];
					}
					public function run( string $questionId, \MediaWiki\Permissions\Authority $authority ): array {
						if ( $questionId !== 'supported-compound' ) {
							return [ 'status' => 'dependency_failure', 'query' => null ];
						}
						SpecialWikiKGSyntheticQuestionsIntegrationTest::$runCalls++;
						SpecialWikiKGSyntheticQuestionsIntegrationTest::$lastQuestionId = $questionId;
						SpecialWikiKGSyntheticQuestionsIntegrationTest::$lastActor = $authority->getUser()->getName();
						return SpecialWikiKGSyntheticQuestionsIntegrationTest::$runResult
							?? [ 'status' => 'dependency_failure', 'query' => null ];
					}
				};
			}
		};
	}

	protected function setUp(): void {
		parent::setUp();
		self::$runCalls = 0;
		self::$lastQuestionId = null;
		self::$lastActor = null;
		self::$runResult = null;
	}

	public function testDisabledRuntimeRendersOnlyUnavailableMessage(): void {
		$user = $this->getTestSysop();
		[ $html ] = $this->executeSpecialPage( '', null, 'en', $user->getAuthority() );
		$this->assertStringContainsString( 'data-wikikg-question-status="unavailable"', $html );
		$this->assertStringNotContainsString( 'questions.json', $html );
		$this->assertStringNotContainsString( 'RuntimeException', $html );
	}

	public function testAnonymousUserIsDeniedBeforeRunnerIsConstructed(): void {
		$user = $this->getServiceContainer()->getUserFactory()->newAnonymous();
		$this->expectException( PermissionsError::class );
		$this->executeSpecialPage( '', new FauxRequest(), 'en', new SimpleAuthority( $user, [] ) );
	}

	public function testRegularUserIsDeniedBeforeRunnerIsConstructed(): void {
		$this->expectException( PermissionsError::class );
		$this->executeSpecialPage( '', null, 'en', $this->getTestUser()->getAuthority() );
	}

	public function testGetRendersNoScriptFormAndNeverRunsAQuestion(): void {
		$this->useFormRunner = true;
		[ $html ] = $this->executeSpecialPage( '', null, 'en', $this->getTestSysop()->getAuthority() );
		$this->assertStringContainsString( 'name="question"', $html );
		$this->assertStringContainsString( 'Example question', $html );
		$this->assertStringContainsString( 'Run question', $html );
		$this->assertStringContainsString( 'method="post"', $html );
		$this->assertSame( 0, self::$runCalls );
	}

	public function testCsrfRefusalDoesNotRunQuestion(): void {
		$this->useFormRunner = true;
		$sysop = $this->getTestSysop();
		[ $html ] = $this->executeSpecialPage( '', new FauxRequest( [
			'question' => 'supported-compound', 'wpEditToken' => 'forged-token',
		], true ), 'en', $sysop->getAuthority() );
		$this->assertStringContainsString( 'data-wikikg-question-status="dependency_failure"', $html );
		$this->assertSame( 0, self::$runCalls );
	}

	public function testForgedActorAndPathFieldsDoNotReachRunnerContract(): void {
		$this->useFormRunner = true;
		$sysop = $this->getTestSysop();
		[ $html ] = $this->executeSpecialPage( '', new FauxRequest( [
			'question' => 'supported-compound', 'wpEditToken' => $sysop->getUser()->getEditToken(),
			'actor' => 'forged-actor', 'path' => '/tmp/forged', 'query' => 'MATCH (n) RETURN n',
		], true ), 'en', $sysop->getAuthority() );
		$this->assertSame( 1, self::$runCalls );
		$this->assertSame( 'supported-compound', self::$lastQuestionId );
		$this->assertSame( $sysop->getUser()->getName(), self::$lastActor );
		$this->assertStringNotContainsString( 'forged-actor', $html );
		$this->assertStringNotContainsString( '/tmp/forged', $html );
	}

	public function testUnknownQuestionIdNeverRunsQuestion(): void {
		$this->useFormRunner = true;
		$sysop = $this->getTestSysop();
		[ $html ] = $this->executeSpecialPage( '', new FauxRequest( [
			'question' => 'unknown-question', 'wpEditToken' => $sysop->getUser()->getEditToken(),
		], true ), 'en', $sysop->getAuthority() );
		$this->assertSame( 0, self::$runCalls );
		$this->assertStringNotContainsString( 'query', $html );
	}

	public function testCanonicalEvidenceIsEscapedAndSourceLinkIsLocalRevisionLink(): void {
		$this->useFormRunner = true;
		$sysop = $this->getTestSysop();
		$sourceText = 'Evidence <img src=x onerror=alert(1)> span';
		$page = $this->editPage( 'Synthetic question XSS source', $sourceText );
		$revision = $page->getNewRevision();
		$snapshot = str_repeat( 'a', 64 );
		$hash = str_repeat( 'b', 64 );
		$generation = str_repeat( 'c', 64 );
		$title = $page->getTitle();
		self::$runResult = [
			'status' => 'evidence',
			'query' => [ 'generation' => $generation, 'status' => 'ok', 'complete' => true,
				'partial' => false, 'results' => [ [ 'subject' => [ 'type' => 'Crop', 'id' => 'Rice', 'crop_id' => 'Rice' ],
					'claims' => [ [ 'citation' => [ 'snapshot_id' => $snapshot, 'snapshot_hash' => $hash,
						'review_version' => 1 ], 'evidence' => [ [ 'url' => 'https://attacker.invalid/' ] ] ] ],
					'eligible_claim_ids' => [ $snapshot ] ] ] ],
			'_canonical_claims' => [ [ 'snapshot_id' => $snapshot, 'snapshot_hash' => $hash, 'review_version' => 1,
				'claim_type' => 'property', 'subject' => [ 'type' => 'Crop', 'id' => 'Rice' ],
				'predicate' => 'GROWTH_DURATION', 'value' => '<script>alert(2)</script>',
				'qualifiers' => (object)[ 'season' => '<img src=x onerror=alert(3)>' ],
				'evidence' => [ [ 'page_title' => $title->getPrefixedText(), 'revision_id' => $revision->getId(),
					'location_type' => 'source_span', 'supporting_span' => $sourceText ] ] ] ],
		];
		[ $html ] = $this->executeSpecialPage( '', new FauxRequest( [
			'question' => 'supported-compound', 'wpEditToken' => $sysop->getUser()->getEditToken(),
		], true ), 'en', $sysop->getAuthority() );
		$this->assertStringNotContainsString( '<script>alert(2)</script>', $html );
		$this->assertStringNotContainsString( '<img src=x onerror=alert(3)>', $html );
		$this->assertStringNotContainsString( 'https://attacker.invalid/', $html );
		$this->assertStringContainsString( '&lt;script&gt;', $html );
		$this->assertStringContainsString( '&lt;img', $html );
		$this->assertStringContainsString( $title->getLocalURL( [ 'oldid' => $revision->getId() ] ), $html );
		$this->assertStringContainsString( 'data-wikikg-claim="' . $snapshot . '"', $html );
	}

	public function testBlockedAuthorizedDiagnosticsRenderWithFixedReasonCopy(): void {
		$this->useFormRunner = true;
		$sysop = $this->getTestSysop();
		$page = $this->editPage( 'Synthetic question blocked diagnostic source', 'Current diagnostic source span.' );
		$revision = $page->getNewRevision();
		$snapshot = str_repeat( 'e', 64 );
		$hash = str_repeat( 'f', 64 );
		self::$runResult = [
			'status' => 'blocked',
			'query' => [ 'generation' => str_repeat( 'a', 64 ), 'status' => 'unsupported',
				'complete' => false, 'partial' => true,
				'reasons' => [ [ 'code' => 'numeric_uncertain' ] ],
				'results' => [ [ 'subject' => [ 'type' => 'Crop', 'id' => 'Rice', 'crop_id' => 'Rice' ],
					'claims' => [ [ 'citation' => [ 'snapshot_id' => $snapshot, 'snapshot_hash' => $hash,
						'review_version' => 1 ] ] ],
					'diagnostic_claim_ids' => [ $snapshot ] ] ] ],
			'reasons' => [ [ 'code' => 'numeric_uncertain', 'message' => 'must not render raw child text' ] ],
			'_canonical_claims' => [ [ 'snapshot_id' => $snapshot, 'snapshot_hash' => $hash,
				'review_version' => 1, 'claim_type' => 'property',
				'subject' => [ 'type' => 'Crop', 'id' => 'Rice' ], 'predicate' => 'GROWTH_DURATION',
				'value' => '95-105 days', 'qualifiers' => (object)[],
				'evidence' => [ [ 'page_title' => $page->getTitle()->getPrefixedText(),
					'revision_id' => $revision->getId(), 'location_type' => 'source_span',
					'supporting_span' => 'Current diagnostic source span.' ] ] ] ],
		];
		[ $html ] = $this->executeSpecialPage( '', new FauxRequest( [
			'question' => 'supported-compound', 'wpEditToken' => $sysop->getUser()->getEditToken(),
		], true ), 'en', $sysop->getAuthority() );
		$this->assertStringContainsString( 'data-wikikg-question-status="blocked"', $html );
		$this->assertStringContainsString( 'The result is incomplete or restricted', $html );
		$this->assertStringContainsString( 'The reported range crosses the requested threshold.', $html );
		$this->assertStringContainsString( 'Current diagnostic source span.', $html );
		$this->assertStringContainsString( 'data-wikikg-claim="' . $snapshot . '"', $html );
		$this->assertStringNotContainsString( 'must not render raw child text', $html );
	}
}
