<?php

namespace MediaWiki\Extension\WikiKGExtractor\Tests\Integration;

use MediaWiki\Extension\WikiKGExtractor\GraphDocument;
use MediaWikiIntegrationTestCase;

/**
 * @group Database
 * @covers \MediaWiki\Extension\WikiKGExtractor\GraphDocument
 */
class GraphDocumentIntegrationTest extends MediaWikiIntegrationTestCase {
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
