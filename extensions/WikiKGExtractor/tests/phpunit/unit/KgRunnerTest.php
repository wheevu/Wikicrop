<?php

namespace MediaWiki\Extension\WikiKGExtractor\Tests;

use MediaWiki\Extension\WikiKGExtractor\KgRunner;
use MediaWikiUnitTestCase;
use RuntimeException;

/**
 * @covers \MediaWiki\Extension\WikiKGExtractor\KgRunner
 */
class KgRunnerTest extends MediaWikiUnitTestCase {
	/** @var string */
	private $directory;

	protected function setUp(): void {
		parent::setUp();
		$this->directory = sys_get_temp_dir() . DIRECTORY_SEPARATOR
			. 'wikikg runner ' . bin2hex( random_bytes( 6 ) );
		mkdir( $this->directory, 0775, true );
	}

	protected function tearDown(): void {
		$this->removeDirectory( $this->directory );
		parent::tearDown();
	}

	public function testRunsWorkerWithPathsContainingSpacesAndKeepsSecretOutOfArguments() {
		$script = $this->writeScript( 'success.php', <<<'PHP'
<?php
$args = $argv;
$outputIndex = array_search( '--output-dir', $args, true );
$output = $args[$outputIndex + 1];
file_put_contents( $output . '/kg_summary.json', json_encode( [ 'ok' => true ] ) );
echo json_encode( $args );
PHP
		);
		$input = $this->directory . DIRECTORY_SEPARATOR . 'input data.json';
		file_put_contents( $input, '{}' );
		$runner = new KgRunner(
			PHP_BINARY,
			$script,
			'super-secret-key',
			'test-model',
			10
		);

		$result = $runner->run( $input, $this->directory );
		$arguments = json_decode( $result['stdout'], true );

		$this->assertSame( 0, $result['exit_code'] );
		$this->assertSame( [ 'ok' => true ], $result['summary'] );
		$this->assertContains( $input, $arguments );
		$this->assertStringNotContainsString( 'super-secret-key', $result['stdout'] );
		$this->assertTrue( $result['used_ai'] );
	}

	public function testNonzeroExitReportsCodeAndStderr() {
		$script = $this->writeScript( 'failure.php', <<<'PHP'
<?php
fwrite( STDERR, 'controlled failure' );
exit( 7 );
PHP
		);
		$runner = new KgRunner( PHP_BINARY, $script, '', 'test-model', 10 );

		$this->expectException( RuntimeException::class );
		$this->expectExceptionMessage( 'KG worker lỗi (mã 7): controlled failure' );
		$runner->run( $this->directory . '/input.json', $this->directory );
	}

	public function testMalformedSummaryDoesNotTurnSuccessfulRunIntoFailure() {
		$script = $this->writeScript( 'bad-summary.php', <<<'PHP'
<?php
$outputIndex = array_search( '--output-dir', $argv, true );
$output = $argv[$outputIndex + 1];
file_put_contents( $output . '/kg_summary.json', '{bad json' );
PHP
		);
		$runner = new KgRunner( PHP_BINARY, $script, '', 'test-model', 10 );

		$result = $runner->run( $this->directory . '/input.json', $this->directory );

		$this->assertSame( 0, $result['exit_code'] );
		$this->assertNull( $result['summary'] );
	}

	private function writeScript( $name, $contents ) {
		$path = $this->directory . DIRECTORY_SEPARATOR . $name;
		file_put_contents( $path, $contents );
		return $path;
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
