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

	public function testPrivateFilesStayOwnerOnlyUnderPermissiveUmask() {
		$oldUmask = umask( 0000 );
		try {
			$result = ( new ExportWriter( $this->outputDirectory, '' ) )->write(
				'local test', [ [ 'title' => 'Lúa', 'url' => '/wiki/Lúa', 'text' => 'Lúa.' ] ]
			);
		} finally {
			umask( $oldUmask );
		}

		$this->assertSame( 0700, fileperms( $this->outputDirectory ) & 0777 );
		$this->assertSame( 0700, fileperms( $result['directory'] ) & 0777 );
		$this->assertSame( 0600, fileperms( $result['json_path'] ) & 0777 );
		$this->assertSame( 0600, fileperms( $result['txt_path'] ) & 0777 );
	}

	public function testSecureExistingPrivateRootIsReused() {
		mkdir( $this->outputDirectory, 0700 );
		chmod( $this->outputDirectory, 0700 );
		$result = ( new ExportWriter( $this->outputDirectory, '' ) )->write( 'local test', [] );
		$this->assertDirectoryExists( $result['directory'] );
	}

	public function testInsecureExistingPrivateRootIsRejectedWithoutChangingIt() {
		mkdir( $this->outputDirectory, 0755 );
		chmod( $this->outputDirectory, 0755 );
		try {
			( new ExportWriter( $this->outputDirectory, '' ) )->write( 'local test', [] );
			$this->fail( 'Expected insecure destination rejection.' );
		} catch ( RuntimeException $e ) {
			$this->assertStringContainsString( 'quyền riêng tư', $e->getMessage() );
		}
		$this->assertSame( 0755, fileperms( $this->outputDirectory ) & 0777 );
		$this->assertSame( [ '.', '..' ], scandir( $this->outputDirectory ) );
	}

	public function testPrivateDestinationInsideWebRootIsRejectedBeforeWriting() {
		$oldDocumentRoot = $_SERVER['DOCUMENT_ROOT'] ?? null;
		mkdir( $this->outputDirectory, 0700 );
		$_SERVER['DOCUMENT_ROOT'] = $this->outputDirectory;
		try {
			$this->expectException( RuntimeException::class );
			$this->expectExceptionMessage( 'document root' );
			( new ExportWriter( $this->outputDirectory . '/exports', '' ) )->write( 'local test', [] );
		} finally {
			if ( $oldDocumentRoot === null ) {
				unset( $_SERVER['DOCUMENT_ROOT'] );
			} else {
				$_SERVER['DOCUMENT_ROOT'] = $oldDocumentRoot;
			}
		}
	}

	public function testPrivateSymlinkDestinationIntoWebRootIsRejected() {
		$webRoot = $this->outputDirectory . '-web';
		mkdir( $webRoot, 0700 );
		$link = $this->outputDirectory . '-link';
		symlink( $webRoot, $link );
		$oldDocumentRoot = $_SERVER['DOCUMENT_ROOT'] ?? null;
		$_SERVER['DOCUMENT_ROOT'] = $webRoot;
		try {
			$this->expectException( RuntimeException::class );
			( new ExportWriter( $link, '' ) )->write( 'local test', [] );
		} finally {
			if ( $oldDocumentRoot === null ) {
				unset( $_SERVER['DOCUMENT_ROOT'] );
			} else {
				$_SERVER['DOCUMENT_ROOT'] = $oldDocumentRoot;
			}
			@unlink( $link );
			$this->removeDirectory( $webRoot );
		}
	}

	public function testPrivateSymlinkAndParentTraversalIsRejectedBeforeWriting() {
		$webRoot = $this->outputDirectory . '-web';
		$base = $this->outputDirectory . '-base';
		mkdir( $webRoot . '/subdir', 0700, true );
		mkdir( $base, 0700 );
		symlink( $webRoot . '/subdir', $base . '/jump' );
		$this->assertSame( realpath( $webRoot ), realpath( $base . '/jump/..' ) );
		$oldDocumentRoot = $_SERVER['DOCUMENT_ROOT'] ?? null;
		$_SERVER['DOCUMENT_ROOT'] = $webRoot;
		$rejected = false;
		$payloadWritten = false;
		try {
			( new ExportWriter( $base . '/jump/../exports', '' ) )->write(
				'local test', [ [ 'title' => 'Private', 'url' => '/private', 'text' => 'secret' ] ]
			);
		} catch ( RuntimeException $e ) {
			$rejected = true;
			$this->assertStringContainsString( 'không được chứa thành phần ..', $e->getMessage() );
		} finally {
			$payloadWritten = is_dir( $webRoot . '/exports' );
			if ( $oldDocumentRoot === null ) {
				unset( $_SERVER['DOCUMENT_ROOT'] );
			} else {
				$_SERVER['DOCUMENT_ROOT'] = $oldDocumentRoot;
			}
			@unlink( $base . '/jump' );
			$this->removeDirectory( $base );
			$this->removeDirectory( $webRoot );
		}
		$this->assertTrue( $rejected );
		$this->assertFalse( $payloadWritten );
		$this->assertDirectoryDoesNotExist( $webRoot . '/exports' );
	}

	public function testPrivateRootWithSpecialPermissionBitsIsRejectedWithoutChangingIt() {
		mkdir( $this->outputDirectory, 0700 );
		chmod( $this->outputDirectory, 01700 );
		try {
			( new ExportWriter( $this->outputDirectory, '' ) )->write( 'local test', [] );
			$this->fail( 'Expected special-permission rejection.' );
		} catch ( RuntimeException $e ) {
			$this->assertStringContainsString( 'quyền riêng tư', $e->getMessage() );
		}
		$this->assertSame( 01700, fileperms( $this->outputDirectory ) & 07777 );
		$this->assertSame( [ '.', '..' ], scandir( $this->outputDirectory ) );
	}

	public function testPrivateDestinationThroughSafeSymlinkUsesCanonicalDirectory() {
		$target = $this->outputDirectory . '-safe-target';
		$link = $this->outputDirectory . '-safe-link';
		$webRoot = $this->outputDirectory . '-web';
		mkdir( $target, 0700 );
		mkdir( $webRoot, 0700 );
		symlink( $target, $link );
		$oldDocumentRoot = $_SERVER['DOCUMENT_ROOT'] ?? null;
		$_SERVER['DOCUMENT_ROOT'] = $webRoot;
		try {
			$result = ( new ExportWriter( $link, '' ) )->write( 'local test', [] );
			$this->assertSame( realpath( $target ), dirname( $result['directory'] ) );
			$this->assertSame( realpath( $result['directory'] ), $result['directory'] );
		} finally {
			if ( $oldDocumentRoot === null ) {
				unset( $_SERVER['DOCUMENT_ROOT'] );
			} else {
				$_SERVER['DOCUMENT_ROOT'] = $oldDocumentRoot;
			}
			@unlink( $link );
			$this->removeDirectory( $target );
			$this->removeDirectory( $webRoot );
		}
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
