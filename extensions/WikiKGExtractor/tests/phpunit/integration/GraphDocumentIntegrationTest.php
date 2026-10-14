<?php

namespace MediaWiki\Extension\WikiKGExtractor\Tests\Integration;

use MediaWiki\Extension\WikiKGExtractor\GraphDocument;
use MediaWikiIntegrationTestCase;

/**
 * @group Database
 * @covers \MediaWiki\Extension\WikiKGExtractor\GraphDocument
 */
class GraphDocumentIntegrationTest extends MediaWikiIntegrationTestCase {
	public function testReviewedEdgeLinksKeepExactUniqueSnapshotAnchors(): void {
		$snapshot = str_repeat( 'a', 64 );
		$document = [ 'schema_version' => '1.0', 'metadata' => [],
			'nodes' => [ [ 'id' => 'Synthetic crop', 'type' => 'Crop', 'properties' => [] ],
				[ 'id' => 'Synthetic variety', 'type' => 'Variety', 'properties' => [] ] ],
			'edges' => [ [ 'source' => 'Synthetic crop', 'target' => 'Synthetic variety',
				'source_type' => 'Crop', 'target_type' => 'Variety', 'type' => 'HAS_VARIETY',
				'properties' => [ 'supporting_snapshot_ids' => [ $snapshot, $snapshot, '<script>' ] ] ] ] ];
		$html = GraphDocument::render( $document );
		$this->assertSame( 1, substr_count( $html, 'href="#wikikg-reviewed-claim-' . $snapshot . '"' ) );
		$this->assertStringContainsString( 'data-wikikg-edge-evidence', $html );
		$this->assertStringNotContainsString( '<script>', $html );
		$this->assertSame( [ $snapshot ], GraphDocument::clientData( $document )['edges'][0]['data']['supporting_snapshot_ids'] );
	}

	public function testRevisionLinksArePinnedInSourcesNodesAndEdges() {
		$document = [
			'schema_version' => '1.0',
			'metadata' => [
				'extractor_version' => '2.2.0',
				'extraction_method' => 'rules',
				'source_pages' => [ [
					'title' => 'Lúa',
					'kind' => 'species',
					'page_id' => 62,
					'revision_id' => 875,
					'content_hash' => str_repeat( 'a', 64 )
				] ]
			],
			'nodes' => [ [
				'id' => 'Lúa',
				'label' => 'Lúa',
				'type' => 'Crop',
				'properties' => [ 'page_title' => 'Lúa' ]
			] ],
			'edges' => []
		];

		$html = GraphDocument::render( $document );
		$clientData = GraphDocument::clientData( $document );

		$this->assertGreaterThanOrEqual( 2, substr_count( $html, 'oldid=875' ) );
		$this->assertStringContainsString(
			'oldid=875',
			$clientData['nodes'][0]['data']['href']
		);
	}
}
