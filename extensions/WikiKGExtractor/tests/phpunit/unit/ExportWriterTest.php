<?php

namespace MediaWiki\Extension\WikiKGExtractor\Tests;

use MediaWiki\Extension\WikiKGExtractor\ExportWriter;
use MediaWikiUnitTestCase;
use RuntimeException;

/**
 * @covers \MediaWiki\Extension\WikiKGExtractor\ExportWriter
 */
class ExportWriterTest extends MediaWikiUnitTestCase {
	/** @var string */
	private $outputDirectory;

	protected function setUp(): void {
		parent::setUp();
		$this->outputDirectory = sys_get_temp_dir() . DIRECTORY_SEPARATOR
			. 'wikikg-writer-' . bin2hex( random_bytes( 6 ) );
	}

	protected function tearDown(): void {
		$this->removeDirectory( $this->outputDirectory );
		parent::tearDown();
	}

	public function testWritesVersionedRawContractWithoutPublishingPrivateFiles() {
		$wikitext = "{{InfoCropPlant}}\nLúa.";
		$writer = new ExportWriter( $this->outputDirectory, '' );
		$result = $writer->write( 'local test', [
			[
				'title' => 'Lúa',
				'url' => '/wiki/Lúa',
				'wikitext' => $wikitext,
				'text' => 'Lúa.',
				'kind' => 'species',
				'crop' => 'Lúa',
				'source_pages' => [],
				'page_id' => 62,
				'revision_id' => 875,
				'content_hash' => hash( 'sha256', $wikitext )
			],
			[ 'title' => 'Missing', 'error' => 'Không tìm thấy.' ]
		] );

		$payload = json_decode( file_get_contents( $result['json_path'] ), true );
		$this->assertSame( '1.0', $payload['schema_version'] );
		$this->assertSame( 1, $payload['page_count'] );
		$this->assertSame( 1, $payload['error_count'] );
		$this->assertSame( $wikitext, $payload['pages'][0]['wikitext'] );
		$this->assertSame( 'Lúa.', $payload['pages'][0]['text'] );
		$this->assertSame( '', $result['json_url'] );
		$this->assertSame( '', $result['txt_url'] );
	}

	public function testPublicBaseUrlUsesOpaqueJobDirectory() {
		$writer = new ExportWriter( $this->outputDirectory, 'https://example.test/kg/' );
		$result = $writer->write( 'local test', [ [
			'title' => 'Lúa',
			'url' => '/wiki/Lúa',
			'text' => 'Lúa.'
		] ] );

		$this->assertSame(
			'https://example.test/kg/' . rawurlencode( $result['job_id'] )
				. '/raw_data.json',
			$result['json_url']
		);
	}

	public function testEmptyOutputDirectoryFailsClearly() {
		$this->expectException( RuntimeException::class );
		$this->expectExceptionMessage( 'Chưa cấu hình thư mục xuất dữ liệu.' );
		( new ExportWriter( '', '' ) )->write( 'local test', [] );
	}

	private function removeDirectory( $directory ) {
		if ( !is_dir( $directory ) ) {
			return;
		}
		$entries = scandir( $directory ) ?: [];
		foreach ( $entries as $entry ) {
			if ( $entry === '.' || $entry === '..' ) {
				continue;
			}
			$path = $directory . DIRECTORY_SEPARATOR . $entry;
			if ( is_dir( $path ) ) {
				$this->removeDirectory( $path );
			} else {
				unlink( $path );
			}
		}
		rmdir( $directory );
	}
}
