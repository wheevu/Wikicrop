<?php

namespace MediaWiki\Extension\WikiKGExtractor\Tests\Integration;

use MediaWiki\Extension\WikiKGExtractor\ClaimReviewSchemaHooks;
use MediaWiki\HookContainer\HookContainer;
use MediaWiki\HookContainer\StaticHookRegistry;
use MediaWiki\Installer\DatabaseUpdater;
use MediaWiki\MediaWikiServices;
use PHPUnit\Framework\TestCase;
use Wikimedia\Rdbms\DatabaseSqlite;

/**
 * @covers \MediaWiki\Extension\WikiKGExtractor\ClaimReviewSchemaHooks
 */
class ClaimReviewSchemaRecoveryIntegrationTest extends TestCase {
	public function testSqliteFirstInstallAndSecondRunLeaveExpectedIndexes(): void {
		$db = DatabaseSqlite::newStandaloneInstance( ':memory:' );
		$schema = $this->readSchemaSource();

		$this->runExtensionSchemaUpdates( $db );
		$this->assertExpectedSchema( $db, $schema );

		$this->runExtensionSchemaUpdates( $db );
		$this->assertExpectedSchema( $db, $schema );
	}

	public function testSqliteRerunRepairsAnInstallInterruptedAfterTableCreation(): void {
		$db = DatabaseSqlite::newStandaloneInstance( ':memory:' );
		$schema = $this->readSchemaSource();
		$this->createTablesWithoutIndexes( $db, $schema );
		$this->assertTablesExistWithoutIndexes( $db, $schema );

		$this->runExtensionSchemaUpdates( $db );
		$this->assertExpectedSchema( $db, $schema );

		$this->runExtensionSchemaUpdates( $db );
		$this->assertExpectedSchema( $db, $schema );
	}

	private function runExtensionSchemaUpdates( DatabaseSqlite $db ): void {
		$maintenance = new \FakeMaintenance();
		$maintenance->loadParamsAndArgs( null, [ 'quiet' => 1 ] );
		$updater = DatabaseUpdater::newForDB( $db, false, $maintenance );
		$hooks = new ClaimReviewSchemaHooks();
		$hookContainer = new HookContainer(
			new StaticHookRegistry( [
				'LoadExtensionSchemaUpdates' => [ [ $hooks, 'onLoadExtensionSchemaUpdates' ] ],
			] ),
			MediaWikiServices::getInstance()->getObjectFactory()
		);
		$updater->setAutoExtensionHookContainer( $hookContainer );
		$updater->doUpdates( [ 'extensions' ] );
	}

	private function readSchemaSource(): array {
		$schemaPath = dirname( __DIR__, 3 ) . '/sql/tables.json';
		$schema = json_decode( (string)file_get_contents( $schemaPath ), true, 512, JSON_THROW_ON_ERROR );
		$this->assertIsArray( $schema );

		return $schema;
	}

	private function createTablesWithoutIndexes( DatabaseSqlite $db, array $schema ): void {
		$schemaDir = dirname( __DIR__, 3 ) . '/sql/sqlite';
		foreach ( $schema as $tableDefinition ) {
			$table = $tableDefinition['name'];
			$patch = file_get_contents( "$schemaDir/$table-generated.sql" );
			$this->assertIsString( $patch );
			$indexStart = strpos( $patch, "\nCREATE INDEX " );
			$tableOnlyPatch = $indexStart === false ? $patch : substr( $patch, 0, $indexStart );
			$tempPatchPath = tempnam( sys_get_temp_dir(), 'wikikg-schema-' );
			$this->assertIsString( $tempPatchPath );
			try {
				$this->assertNotFalse( file_put_contents( $tempPatchPath, $tableOnlyPatch ) );
				$db->sourceFile( $tempPatchPath );
			} finally {
				unlink( $tempPatchPath );
			}
		}
	}

	private function assertTablesExistWithoutIndexes( DatabaseSqlite $db, array $schema ): void {
		foreach ( $schema as $tableDefinition ) {
			$table = $tableDefinition['name'];
			$this->assertTrue( $db->tableExists( $table, __METHOD__ ), "$table must exist." );
			foreach ( $tableDefinition['indexes'] as $index ) {
				$this->assertFalse(
					$db->indexExists( $table, $index['name'], __METHOD__ ),
					"{$index['name']} must not exist in the interrupted state."
				);
			}
		}
	}

	private function assertExpectedSchema( DatabaseSqlite $db, array $schema ): void {
		foreach ( $schema as $tableDefinition ) {
			$table = $tableDefinition['name'];
			$this->assertTrue( $db->tableExists( $table, __METHOD__ ), "$table must exist." );
			foreach ( $tableDefinition['indexes'] as $index ) {
				$indexName = $index['name'];
				$this->assertTrue( $db->indexExists( $table, $indexName, __METHOD__ ), "$indexName must exist." );
				$rows = $db->query(
					'PRAGMA index_info(' . $db->addIdentifierQuotes( $indexName ) . ')',
					__METHOD__
				);
				$columns = [];
				foreach ( $rows as $row ) {
					$columns[] = $row->name;
				}
				$this->assertSame( $index['columns'], $columns, "$indexName columns must match sql/tables.json." );
				$this->assertSame(
					$index['unique'],
					$db->indexInfo( $table, $indexName, __METHOD__ )['unique'],
					"$indexName uniqueness must match sql/tables.json."
				);
			}
		}
	}
}
