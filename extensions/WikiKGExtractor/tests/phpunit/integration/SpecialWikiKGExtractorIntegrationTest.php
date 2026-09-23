<?php

namespace MediaWiki\Extension\WikiKGExtractor\Tests\Integration;

use MediaWiki\Extension\WikiKGExtractor\ClaimEvidenceAccess;
use MediaWiki\Extension\WikiKGExtractor\SpecialWikiKGExtractor;
use MediaWiki\Permissions\SimpleAuthority;
use MediaWiki\Request\FauxRequest;
use MediaWiki\Revision\RevisionRecord;
use PermissionsError;
use SpecialPageTestBase;

/**
 * @group Database
 * @covers \MediaWiki\Extension\WikiKGExtractor\SpecialWikiKGExtractor
 */
class SpecialWikiKGExtractorIntegrationTest extends SpecialPageTestBase {
	/** @var string */
	private $outputDirectory;
	private ?string $candidateDirectory = null;
	private ?\Closure $afterKgWorker = null;
	private ?bool $helperCanReadAfterSuppression = null;
	private ?array $evidenceAfterSuppression = null;

	protected function setUp(): void {
		parent::setUp();
		$this->outputDirectory = sys_get_temp_dir() . DIRECTORY_SEPARATOR
			. 'wikikg-special-' . bin2hex( random_bytes( 6 ) );
	}

	protected function tearDown(): void {
		$this->removeDirectory( $this->outputDirectory );
		parent::tearDown();
	}

	protected function newSpecialPage(): SpecialWikiKGExtractor {
		if ( $this->afterKgWorker !== null ) {
			$callback = $this->afterKgWorker;
			return new class( $callback ) extends SpecialWikiKGExtractor {
				private \Closure $callback;

				public function __construct( \Closure $callback ) {
					$this->callback = $callback;
					parent::__construct();
				}

				protected function runKgWorker( $config, array $result ) {
					$kgResult = parent::runKgWorker( $config, $result );
					( $this->callback )( $result );
					return $kgResult;
				}
			};
		}

		return new SpecialWikiKGExtractor();
	}

	public function testUserWithoutRightCannotOpenPage() {
		$user = $this->getServiceContainer()->getUserFactory()->newAnonymous();
		$authority = new SimpleAuthority( $user, [] );
		$this->expectException( PermissionsError::class );
		$this->executeSpecialPage( '', null, 'vi', $authority );
	}

	public function testInvalidCsrfTokenRejectsRequestBeforeExtraction() {
		$user = $this->getTestSysop()->getUser();
		$request = new FauxRequest( [
			'source_pages' => 'Lúa',
			'wpEditToken' => 'invalid-token'
		], true );

		[ $html ] = $this->executeSpecialPage(
			'',
			$request,
			'vi',
			$this->getTestSysop()->getAuthority()
		);

		$this->assertStringContainsString( 'Token bảo mật không hợp lệ', $html );
	}

	public function testDisabledKgModeRejectsRequestBeforeExtraction() {
		$testUser = $this->getTestSysop();
		$user = $testUser->getUser();
		$this->overrideConfigValue( 'WikiKGExtractorEnableKG', false );
		$request = new FauxRequest( [
			'source_pages' => 'Lúa',
			'run_kg' => '1',
			'wpEditToken' => $user->getEditToken()
		], true );

		[ $html ] = $this->executeSpecialPage(
			'',
			$request,
			'vi',
			$testUser->getAuthority()
		);

		$this->assertStringContainsString( 'Tùy chọn tạo KG đang bị tắt', $html );
	}

	public function testPrivateExportDoesNotRenderDownloadUrl() {
		$cropTitle = 'Lúa kiểm thử special';
		$varietyTitle = 'Lúa kiểm thử special ST25';
		$this->editPage(
			$cropTitle,
			"{{InfoPlant1}}\n== Danh sách giống ==\n* [[{$varietyTitle}]]"
		);
		$this->editPage( $varietyTitle, 'Nội dung giống.' );
		$this->overrideConfigValues( [
			'WikiKGExtractorOutputDirectory' => $this->outputDirectory,
			'WikiKGExtractorOutputBaseUrl' => ''
		] );
		$testUser = $this->getTestSysop();
		$request = new FauxRequest( [
			'source_pages' => $cropTitle,
			'wpEditToken' => $testUser->getUser()->getEditToken()
		], true );

		[ $html ] = $this->executeSpecialPage(
			'',
			$request,
			'vi',
			$testUser->getAuthority()
		);

		$this->assertStringContainsString(
			'File đã được ghi trên server nhưng chưa có URL công khai',
			$html
		);
		$this->assertStringNotContainsString( 'download="raw_data.json"', $html );
	}

	public function testDoesNotExportWhenNoLinkedPageIsAnAcceptedVariety() {
		$cropTitle = 'Lúa kiểm thử special không có giống';
		$unrelatedTitle = 'Trang kiểm thử special không gắn loài';
		$this->editPage(
			$cropTitle,
			"{{InfoPlant1}}\n== Danh sách giống ==\n* [[{$unrelatedTitle}]]"
		);
		$this->editPage( $unrelatedTitle, 'Nội dung trang không liên quan.' );
		$this->overrideConfigValues( [
			'WikiKGExtractorOutputDirectory' => $this->outputDirectory,
			'WikiKGExtractorOutputBaseUrl' => ''
		] );
		$testUser = $this->getTestSysop();
		$request = new FauxRequest( [
			'source_pages' => $cropTitle,
			'wpEditToken' => $testUser->getUser()->getEditToken()
		], true );

		[ $html ] = $this->executeSpecialPage(
			'',
			$request,
			'vi',
			$testUser->getAuthority()
		);

		$this->assertStringContainsString(
			'Không tìm thấy trang giống hợp lệ để trích xuất',
			$html
		);
		$this->assertDirectoryDoesNotExist( $this->outputDirectory );
	}

	public function testRulesOnlyRunShowsAndStoresRevisionPinnedPendingClaims() {
		$cropTitle = 'Lúa kiểm thử bằng chứng';
		$varietyTitle = $cropTitle . ' ST25';
		$this->editPage(
			$cropTitle,
			"{{InfoPlant1}}\nLúa bị rầy nâu gây hại.\n== Danh sách giống ==\n* [[{$varietyTitle}]]"
		);
		$this->editPage(
			$varietyTitle,
			"{{InfoCropPlant}}\nGiống ST25 có thời gian sinh trưởng 100 ngày và kháng rầy nâu."
		);
		$this->overrideConfigValues( [
			'WikiKGExtractorOutputDirectory' => $this->outputDirectory,
			'WikiKGExtractorOutputBaseUrl' => '',
			'WikiKGExtractorEnableKG' => true,
			'WikiKGExtractorPythonCommand' => '/usr/local/bin/python3',
			'WikiKGExtractorGeminiApiKey' => '',
			'WikiKGExtractorPushToNeo4j' => false
		] );
		$testUser = $this->getTestSysop();
		$request = new FauxRequest( [
			'source_pages' => $cropTitle,
			'run_kg' => '1',
			'wpEditToken' => $testUser->getUser()->getEditToken()
		], true );

		[ $html ] = $this->executeSpecialPage(
			'', $request, 'vi', $testUser->getAuthority()
		);

		$this->assertStringContainsString( 'Claim và bằng chứng', $html );
		$this->assertStringContainsString( 'Chờ duyệt', $html );
		$this->assertStringContainsString( 'Xem và duyệt claim', $html );
		$this->assertStringNotContainsString( 'chưa lưu được lịch sử duyệt', $html );
		$this->assertStringNotContainsString( 'Không thể kiểm tra bằng chứng', $html );
		$this->assertStringNotContainsString( 'download="raw_data.json"', $html );

		$db = $this->getServiceContainer()->getConnectionProvider()->getPrimaryDatabase();
		$row = $db->newSelectQueryBuilder()
			->select( [ 'total' => 'COUNT(*)' ] )
			->from( 'wikikg_snapshot' )
			->fetchRow();
		$this->assertGreaterThan( 0, (int)$row->total );
	}

	public function testSuppressedEvidenceAfterWorkerIsNotShownOrImported() {
		$cropTitle = 'Lúa kiểm thử bằng chứng đã ẩn';
		$varietyTitle = $cropTitle . ' ST25';
		$cropRevision = $this->editPage(
			$cropTitle,
			"{{InfoPlant1}}\nLúa bị rầy nâu gây hại.\n== Danh sách giống ==\n* [[{$varietyTitle}]]"
		)->getNewRevision();
		$varietyRevision = $this->editPage(
			$varietyTitle,
			"{{InfoCropPlant}}\nGiống ST25 có thời gian sinh trưởng 100 ngày và kháng rầy nâu."
		)->getNewRevision();
		$this->afterKgWorker = function ( array $result ) use (
			$cropTitle,
			$varietyTitle,
			$cropRevision,
			$varietyRevision
		) {
			$authority = $this->getTestSysop()->getAuthority();
			foreach ( [
				[ $cropTitle, $cropRevision ],
				[ $varietyTitle, $varietyRevision ],
			] as [ $title, $revision ] ) {
				$this->editPage( $title, 'Later visible revision.' );
				$this->revisionDelete( $revision->getId(), [
					RevisionRecord::DELETED_TEXT => 1,
					RevisionRecord::DELETED_COMMENT => 1,
					RevisionRecord::DELETED_USER => 1,
					RevisionRecord::DELETED_RESTRICTED => 1,
				] );
			}
			$this->candidateDirectory = $result['directory'];
			$candidateJson = file_get_contents(
				$this->candidateDirectory . '/candidate_claims.json'
			);
			$candidateDocument = is_string( $candidateJson )
				? json_decode( $candidateJson, true )
				: null;
			if ( is_array( $candidateDocument )
				&& is_array( $candidateDocument['claims'] ?? null )
			) {
				$this->evidenceAfterSuppression = [];
				foreach ( $candidateDocument['claims'] as $claim ) {
					foreach ( $claim['evidence'] ?? [] as $evidence ) {
						$this->evidenceAfterSuppression[] = $evidence;
					}
				}
				$this->helperCanReadAfterSuppression = ClaimEvidenceAccess::canRead(
					$authority,
					$this->evidenceAfterSuppression
				);
			}
		};
		$this->overrideConfigValues( [
			'WikiKGExtractorOutputDirectory' => $this->outputDirectory,
			'WikiKGExtractorOutputBaseUrl' => '',
			'WikiKGExtractorEnableKG' => true,
			'WikiKGExtractorPythonCommand' => '/usr/local/bin/python3',
			'WikiKGExtractorGeminiApiKey' => '',
			'WikiKGExtractorPushToNeo4j' => false
		] );
		$testUser = $this->getTestSysop();
		$request = new FauxRequest( [
			'source_pages' => $cropTitle,
			'run_kg' => '1',
			'wpEditToken' => $testUser->getUser()->getEditToken()
		], true );
		$db = $this->getServiceContainer()->getConnectionProvider()->getPrimaryDatabase();
		$before = $db->newSelectQueryBuilder()
			->select( [ 'total' => 'COUNT(*)' ] )
			->from( 'wikikg_snapshot' )
			->fetchRow();

		[ $html ] = $this->executeSpecialPage(
			'', $request, 'vi', $testUser->getAuthority()
		);

		$this->assertFalse( $this->helperCanReadAfterSuppression );
		$this->assertStringContainsString( 'Không thể hiển thị hoặc lưu claim', $html );
		$this->assertStringContainsString( 'Graph trong Wiki', $html );
		$this->assertStringNotContainsString( 'class="wikikg-claim-document"', $html );
		$this->assertStringNotContainsString( 'class="wikikg-evidence"', $html );
		$this->assertNotNull( $this->candidateDirectory );
		$this->assertIsArray( $this->evidenceAfterSuppression );
		$this->assertNotEmpty( $this->evidenceAfterSuppression );
		$spans = [];
		foreach ( $this->evidenceAfterSuppression as $evidence ) {
			if ( is_string( $evidence['supporting_span'] ?? null ) ) {
				$spans[] = $evidence['supporting_span'];
			}
		}
		$this->assertNotEmpty( $spans );
		foreach ( $spans as $span ) {
			$this->assertStringNotContainsString( $span, $html );
		}
		$after = $db->newSelectQueryBuilder()
			->select( [ 'total' => 'COUNT(*)' ] )
			->from( 'wikikg_snapshot' )
			->fetchRow();
		$this->assertSame( (int)$before->total, (int)$after->total );
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
			is_dir( $path ) ? $this->removeDirectory( $path ) : unlink( $path );
		}
		rmdir( $directory );
	}
}
