<?php

namespace MediaWiki\Extension\WikiKGExtractor\Tests\Unit;

use MediaWiki\Extension\WikiKGExtractor\ClaimReviewSchemaHooks;
use MediaWiki\Installer\DatabaseUpdater;
use MediaWikiUnitTestCase;
use Wikimedia\Rdbms\IMaintainableDatabase;

/**
 * @covers \MediaWiki\Extension\WikiKGExtractor\ClaimReviewSchemaHooks
 */
class ClaimReviewSchemaHooksTest extends MediaWikiUnitTestCase {
	public function testMysqlTablesHaveIndependentUpdaterPatches(): void {
		$this->assertRegistersIndependentPatches( 'mysql' );
	}

	public function testSqliteTablesHaveIndependentUpdaterPatches(): void {
		$this->assertRegistersIndependentPatches( 'sqlite' );
	}

	private function assertRegistersIndependentPatches( string $dbType ): void {
		$database = $this->createMock( IMaintainableDatabase::class );
		$database->method( 'getType' )->willReturn( $dbType );

		$updater = $this->createMock( DatabaseUpdater::class );
		$updater->method( 'getDB' )->willReturn( $database );
		$registeredTables = [];
		$updater->expects( $this->exactly( 5 ) )
			->method( 'addExtensionTable' )
			->willReturnCallback( static function ( string $table, string $path ) use ( &$registeredTables ): void {
				$registeredTables[] = [ $table, $path ];
			} );
		$registeredIndexes = [];
		$expectedIndexes = $dbType === 'sqlite' ? [
			[ 'wikikg_snapshot', 'wikikg_snapshot_created' ],
			[ 'wikikg_evidence', 'wikikg_evidence_revision' ],
			[ 'wikikg_review_event', 'wikikg_review_reviewer_timestamp' ],
		] : [];
		$updater->expects( $this->exactly( count( $expectedIndexes ) ) )
			->method( 'addExtensionIndex' )
			->willReturnCallback(
				static function ( string $table, string $index, string $path ) use ( &$registeredIndexes ): void {
					$registeredIndexes[] = [ $table, $index, $path ];
				}
			);

		( new ClaimReviewSchemaHooks() )->onLoadExtensionSchemaUpdates( $updater );

		$schemaDir = dirname( __DIR__, 3 ) . "/sql/$dbType";
		$tableNames = [
			'wikikg_snapshot',
			'wikikg_source_page',
			'wikikg_claim',
			'wikikg_evidence',
			'wikikg_review_event',
		];
		$expectedTables = array_map(
			static fn ( string $table ): array => [ $table, "$schemaDir/$table-generated.sql" ],
			$tableNames
		);

		$this->assertSame( $expectedTables, $registeredTables );
		foreach ( $expectedTables as [ , $path ] ) {
			$this->assertFileExists( $path );
		}

		$expectedIndexesWithPaths = array_map(
			static fn ( array $index ): array => [ ...$index, "$schemaDir/patch-{$index[1]}.sql" ],
			$expectedIndexes
		);
		$this->assertSame( $expectedIndexesWithPaths, $registeredIndexes );
		foreach ( $registeredIndexes as [ , , $path ] ) {
			$this->assertFileExists( $path );
		}
	}
}
