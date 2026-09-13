<?php

namespace MediaWiki\Extension\WikiKGExtractor\Tests\Integration;

use MediaWiki\Extension\WikiKGExtractor\SpecialWikiKGExtractor;
use MediaWiki\Permissions\SimpleAuthority;
use MediaWiki\Request\FauxRequest;
use PermissionsError;
use SpecialPageTestBase;

/**
 * @group Database
 * @covers \MediaWiki\Extension\WikiKGExtractor\SpecialWikiKGExtractor
 */
class SpecialWikiKGExtractorIntegrationTest extends SpecialPageTestBase {
	/** @var string */
	private $outputDirectory;

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
			"== Danh sách giống ==\n* [[{$varietyTitle}]]"
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
