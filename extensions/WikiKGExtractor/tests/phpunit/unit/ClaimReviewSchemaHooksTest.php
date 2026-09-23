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
		$registered = [];
		$updater->expects( $this->exactly( 5 ) )
			->method( 'addExtensionTable' )
			->willReturnCallback( static function ( string $table, string $path ) use ( &$registered ): void {
				$registered[] = [ $table, $path ];
			} );

		( new ClaimReviewSchemaHooks() )->onLoadExtensionSchemaUpdates( $updater );

		$schemaDir = dirname( __DIR__, 3 ) . "/sql/$dbType";
		$expectedTables = [
			'wikikg_snapshot',
			'wikikg_source_page',
			'wikikg_claim',
			'wikikg_evidence',
			'wikikg_review_event',
		];
		$expected = array_map(
			static fn ( string $table ): array => [ $table, "$schemaDir/$table-generated.sql" ],
			$expectedTables
		);

		$this->assertSame( $expected, $registered );
		foreach ( $expected as [ , $path ] ) {
			$this->assertFileExists( $path );
		}
	}
}
