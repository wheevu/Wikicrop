<?php

namespace MediaWiki\Extension\WikiKGExtractor\Tests;

use MediaWiki\Extension\WikiKGExtractor\GraphDocument;
use MediaWikiUnitTestCase;

/**
 * Unit tests for GraphDocument (read-only presentation boundary).
 *
 * NOTE: The "Nguồn và revision" table (renderSources -> renderPageLink) calls
 * Title::newFromText(), which requires MediaWikiServices. MediaWikiUnitTestCase
 * strips services, so that rendering path is verified at integration level
 * (Slice 4, MediaWiki runtime), not here. All load()/validation and node/edge
 * rendering paths below are service-free.
 */
class GraphDocumentTest extends MediaWikiUnitTestCase {
	public function testLoadsVersionedDocument() {
		$path = dirname( __DIR__ ) . '/fixtures/graph-document.v1.json';
		$document = GraphDocument::load( $path );

		$this->assertIsArray( $document );
		$this->assertSame( '1.0', $document['schema_version'] );
		$this->assertCount( 2, $document['nodes'] );
		$this->assertCount( 1, $document['edges'] );
	}

	public function testInvalidDocumentIsRejected() {
		$path = tempnam( sys_get_temp_dir(), 'wikikg-' );
		file_put_contents( $path, '{"nodes":"not-an-array"}' );

		$this->assertNull( GraphDocument::load( $path ) );
		unlink( $path );
	}

	public function testRenderingEscapesNodeValues() {
		$html = GraphDocument::render( [
			'schema_version' => '1.0',
			'metadata' => [],
			'nodes' => [
				[
					'id' => '<script>alert(1)</script>',
					'type' => 'Variety',
					'properties' => []
				]
			],
			'edges' => []
		] );

		$this->assertStringNotContainsString( '<script>alert(1)</script>', $html );
		$this->assertStringContainsString( '&lt;script&gt;', $html );
	}

	// --- Slice 2: schema_version and provenance boundaries ------------------

	public function testRejectsMissingSchemaVersion() {
		$document = self::validDocument();
		unset( $document['schema_version'] );

		$path = self::writeTempDocument( $document );
		$this->assertNull( GraphDocument::load( $path ) );
		unlink( $path );
	}

	public function testRejectsUnknownSchemaVersion() {
		$document = self::validDocument();
		$document['schema_version'] = '0.9';

		$path = self::writeTempDocument( $document );
		$this->assertNull( GraphDocument::load( $path ) );
		unlink( $path );
	}

	public function testRejectsNumericSchemaVersion() {
		// JSON number 1.0 must not be accepted as the string "1.0".
		$path = tempnam( sys_get_temp_dir(), 'wikikg-' );
		$document = self::validDocument();
		$document['schema_version'] = 1.0;
		file_put_contents( $path, json_encode( $document ) );

		$this->assertNull( GraphDocument::load( $path ) );
		unlink( $path );
	}

	public function testRejectsMissingMetadata() {
		$document = self::validDocument();
		unset( $document['metadata'] );

		$path = self::writeTempDocument( $document );
		$this->assertNull( GraphDocument::load( $path ) );
		unlink( $path );
	}

	public function testRejectsMissingSourcePages() {
		$document = self::validDocument();
		unset( $document['metadata']['source_pages'] );

		$path = self::writeTempDocument( $document );
		$this->assertNull( GraphDocument::load( $path ) );
		unlink( $path );
	}

	public function testRejectsSourcePageWithoutProvenance() {
		$document = self::validDocument();
		unset( $document['metadata']['source_pages'][0]['content_hash'] );

		$path = self::writeTempDocument( $document );
		$this->assertNull( GraphDocument::load( $path ) );
		unlink( $path );
	}

	public function testRejectsSourcePageWithInvalidHashFormat() {
		$document = self::validDocument();
		$document['metadata']['source_pages'][0]['content_hash'] = 'xyz';

		$path = self::writeTempDocument( $document );
		$this->assertNull( GraphDocument::load( $path ) );
		unlink( $path );
	}

	public function testRejectsSourcePageWithInvalidIds() {
		$document = self::validDocument();
		$document['metadata']['source_pages'][0]['page_id'] = 0;

		$path = self::writeTempDocument( $document );
		$this->assertNull( GraphDocument::load( $path ) );
		unlink( $path );
	}

	public function testRejectsSourcePageWithUnknownKind() {
		$document = self::validDocument();
		$document['metadata']['source_pages'][0]['kind'] = 'technique';

		$path = self::writeTempDocument( $document );
		$this->assertNull( GraphDocument::load( $path ) );
		unlink( $path );
	}

	public function testRejectsUnknownExtractionMethod() {
		$document = self::validDocument();
		$document['metadata']['extraction_method'] = 'gemini';

		$path = self::writeTempDocument( $document );
		$this->assertNull( GraphDocument::load( $path ) );
		unlink( $path );
	}

	public function testRejectsOversizeDocument() {
		// MAX_DOCUMENT_BYTES = 5242880; the size check happens before parsing.
		$path = tempnam( sys_get_temp_dir(), 'wikikg-' );
		file_put_contents( $path, str_repeat( ' ', 5242881 ) );

		$this->assertNull( GraphDocument::load( $path ) );
		unlink( $path );
	}

	public function testAcceptsEmptySourcePages() {
		// Schema permits an empty provenance list; loader must not reject it.
		$document = self::validDocument();
		$document['metadata']['source_pages'] = [];

		$path = self::writeTempDocument( $document );
		$this->assertIsArray( GraphDocument::load( $path ) );
		unlink( $path );
	}

	// --- Slice 2: rendering boundaries --------------------------------------

	public function testRenderingEscapesEdgeSourceAndTarget() {
		$html = GraphDocument::render( [
			'schema_version' => '1.0',
			'metadata' => [],
			'nodes' => [],
			'edges' => [
				[
					'source' => '<img src=x onerror=alert(1)>',
					'type' => 'HAS_VARIETY',
					'target' => '&"quoted"',
					'properties' => []
				]
			]
		] );

		$this->assertStringNotContainsString( '<img src=x', $html );
		$this->assertStringContainsString( '&lt;img', $html );
		$this->assertStringContainsString( '&amp;&quot;quoted&quot;', $html );
	}

	public function testRenderingEscapesPropertyValues() {
		$html = GraphDocument::render( [
			'schema_version' => '1.0',
			'metadata' => [],
			'nodes' => [
				[
					'id' => 'ST24',
					'type' => 'Variety',
					'properties' => [
						'growth_duration' => '<script>alert(2)</script>',
						'note' => 'a & b'
					]
				]
			],
			'edges' => []
		] );

		$this->assertStringNotContainsString( '<script>alert(2)</script>', $html );
		$this->assertStringContainsString( '&lt;script&gt;', $html );
		$this->assertStringContainsString( 'a &amp; b', $html );
	}

	public function testRenderCapsNodesAndEdges() {
		$nodes = [];
		for ( $i = 0; $i < 201; $i++ ) {
			$nodes[] = [
				'id' => 'N' . $i,
				'type' => 'Variety',
				'properties' => []
			];
		}
		$edges = [];
		for ( $i = 0; $i < 501; $i++ ) {
			$edges[] = [
				'source' => 'Crop',
				'type' => 'HAS_VARIETY',
				'target' => 'N' . $i,
				'properties' => []
			];
		}
		$html = GraphDocument::render( [
			'schema_version' => '1.0',
			'metadata' => [],
			'nodes' => $nodes,
			'edges' => $edges
		] );

		$this->assertStringContainsString( 'Chỉ hiển thị tối đa', $html );
		$this->assertSame( 200, substr_count( $html, 'class="wikikg-graph-node"' ) );
		// 500 edge rows x 3 cells; no sources table in this document.
		$this->assertSame( 1500, substr_count( $html, '<td>' ) );
	}

	public function testRenderAtCapHasNoTruncationNotice() {
		$nodes = [];
		for ( $i = 0; $i < 200; $i++ ) {
			$nodes[] = [
				'id' => 'N' . $i,
				'type' => 'Variety',
				'properties' => []
			];
		}
		$edges = [];
		for ( $i = 0; $i < 500; $i++ ) {
			$edges[] = [
				'source' => 'Crop',
				'type' => 'HAS_VARIETY',
				'target' => 'N' . $i,
				'properties' => []
			];
		}
		$html = GraphDocument::render( [
			'schema_version' => '1.0',
			'metadata' => [],
			'nodes' => $nodes,
			'edges' => $edges
		] );

		$this->assertStringNotContainsString( 'Chỉ hiển thị tối đa', $html );
		$this->assertSame( 200, substr_count( $html, 'class="wikikg-graph-node"' ) );
	}

	// --- helpers ------------------------------------------------------------

	/**
	 * @return array<string,mixed>
	 */
	private static function validDocument() {
		return [
			'schema_version' => '1.0',
			'metadata' => [
				'extractor_version' => '2.1.1',
				'extraction_method' => 'rules',
				'source_pages' => [
					[
						'title' => 'Lúa',
						'kind' => 'species',
						'page_id' => 62,
						'revision_id' => 875,
						'content_hash' =>
							'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
					]
				]
			],
			'nodes' => [],
			'edges' => []
		];
	}

	/**
	 * @param array<string,mixed> $document
	 * @return string
	 */
	private static function writeTempDocument( array $document ) {
		$path = tempnam( sys_get_temp_dir(), 'wikikg-' );
		file_put_contents( $path, json_encode( $document ) );
		return $path;
	}
}
