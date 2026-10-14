<?php

namespace MediaWiki\Extension\WikiKGExtractor\Tests;

use MediaWiki\Extension\WikiKGExtractor\OfflineSourceInventory;
use MediaWikiUnitTestCase;
use RuntimeException;

/**
 * @covers \MediaWiki\Extension\WikiKGExtractor\OfflineSourceInventory
 */
class OfflineSourceInventoryTest extends MediaWikiUnitTestCase {
	public function testPreparedAndCollectedRowsKeepStableMetadataAcrossBatches() {
		$pages = [
			$this->page( 1, 'Unclassified' ),
			$this->page( 2, 'Alias', 'Unclassified' ),
			$this->page( 3, 'Classified elsewhere' ),
			$this->page( 4, 'Selected page' ),
		];
		$preparedReads = [];
		$prepared = ( new OfflineSourceInventory() )->inventory( $pages, [], static function ( $page, $classificationOnly ) use ( &$preparedReads ) {
			$preparedReads[] = [ $page['page_id'], $classificationOnly ];
			return $page['page_id'] === 1 ? [] : [ 'kind' => 'species', 'crop' => 'Rice' ];
		}, 1000, true );

		$collectedReads = [];
		$collected = ( new OfflineSourceInventory() )->inventory( $pages, [], static function ( $page, $classificationOnly = false ) use ( &$collectedReads ) {
			$collectedReads[] = [ $page['page_id'], $classificationOnly ];
			if ( $classificationOnly ) {
				return $page['page_id'] === 1 ? [] : [ 'kind' => 'species', 'crop' => 'Rice' ];
			}
			return [ 'page_id' => $page['page_id'], 'revision_id' => $page['revision_id'],
				'kind' => 'species', 'crop' => 'Rice', 'wikitext' => 'synthetic content' ];
		}, 1000, false, [ 4 => true ] );

		$stableFields = [ 'page_id', 'title', 'namespace', 'revision_id', 'redirect_target',
			'redirect_namespace', 'redirect_external', 'resolved_page_id', 'resolved_title',
			'resolved_revision_id', 'redirect_chain', 'kind', 'crop' ];
		foreach ( $prepared['pages'] as $index => $row ) {
			$this->assertSame( array_intersect_key( $row, array_flip( $stableFields ) ),
				array_intersect_key( $collected['pages'][$index], array_flip( $stableFields ) ) );
		}
		$this->assertSame( [ [ 1, true ], [ 3, true ], [ 4, true ] ], $preparedReads );
		$this->assertSame( [ [ 1, true ], [ 3, true ], [ 4, false ] ], $collectedReads );
		$this->assertSame( 'unknown_classification', $prepared['pages'][0]['reason'] );
		$this->assertSame( 'unknown_classification', $collected['pages'][0]['reason'] );
		$this->assertSame( 'redirect_alias', $collected['pages'][1]['reason'] );
		$this->assertSame( 'awaiting_collection', $prepared['pages'][2]['reason'] );
		$this->assertSame( 'not_selected_in_batch', $collected['pages'][2]['reason'] );
		$this->assertFalse( $collected['pages'][2]['eligible'] );
		$this->assertArrayNotHasKey( 'wikitext', $collected['pages'][2] );
		$this->assertTrue( $collected['pages'][3]['eligible'] );
		$this->assertArrayNotHasKey( 'wikitext', $collected['pages'][0] );
	}

	public function testExcludedIdentityAndAliasNeverInvokeReaderOrPublishResolvedIdentity() {
		$pages = [ $this->page( 1, 'Held-out alias', 'Held out' ), $this->page( 2, 'Held out' ) ];
		$inventory = ( new OfflineSourceInventory() )->inventory( $pages,
			[ [ 'page_id' => 2, 'title' => 'Held out' ] ], static function () {
				throw new RuntimeException( 'Excluded pages must not be read.' );
			}, 1000, true );
		foreach ( $inventory['pages'] as $row ) {
			$this->assertSame( 'heldout_target_or_alias', $row['reason'] );
			$this->assertArrayNotHasKey( 'resolved_page_id', $row );
		}
	}

	private function page( int $id, string $title, ?string $target = null ): array {
		return [ 'page_id' => $id, 'title' => $title, 'namespace' => 0,
			'revision_id' => 100 + $id, 'redirect_target' => $target, 'redirect_namespace' => 0 ];
	}
}
