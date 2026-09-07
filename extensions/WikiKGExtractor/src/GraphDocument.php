<?php

namespace MediaWiki\Extension\WikiKGExtractor;

use Html;
use Title;

/**
 * Reads and renders the versioned graph document produced by kg_worker.py.
 *
 * This is deliberately a read-only presentation boundary. It does not write
 * pages, create graph records, or connect to Neo4j.
 */
class GraphDocument {
	private const SCHEMA_VERSION = '1.0';
	private const MAX_DOCUMENT_BYTES = 5242880;
	private const MAX_RENDERED_NODES = 200;
	private const MAX_RENDERED_EDGES = 500;

	/**
	 * @param string $path
	 * @return array<string,mixed>|null
	 */
	public static function load( $path ) {
		if ( !is_file( $path ) || !is_readable( $path )
			|| filesize( $path ) > self::MAX_DOCUMENT_BYTES
		) {
			return null;
		}

		$raw = file_get_contents( $path );
		if ( $raw === false ) {
			return null;
		}

		$document = json_decode( $raw, true );
		if ( !is_array( $document )
			|| ( $document['schema_version'] ?? null ) !== self::SCHEMA_VERSION
			|| !is_array( $document['nodes'] ?? null )
			|| !is_array( $document['edges'] ?? null )
		) {
			return null;
		}

		if ( !self::hasValidMetadata( $document['metadata'] ?? null ) ) {
			return null;
		}

		return $document;
	}

	/**
	 * @param mixed $metadata
	 * @return bool
	 */
	private static function hasValidMetadata( $metadata ) {
		if ( !is_array( $metadata )
			|| trim( (string)( $metadata['extractor_version'] ?? '' ) ) === ''
			|| !in_array(
				$metadata['extraction_method'] ?? '',
				[ 'rules', 'ai' ],
				true
			)
			|| !is_array( $metadata['source_pages'] ?? null )
		) {
			return false;
		}

		foreach ( $metadata['source_pages'] as $source ) {
			if ( !is_array( $source )
				|| trim( (string)( $source['title'] ?? '' ) ) === ''
				|| !in_array( $source['kind'] ?? '', [ 'species', 'variety' ], true )
				|| (int)( $source['page_id'] ?? 0 ) < 1
				|| (int)( $source['revision_id'] ?? 0 ) < 1
				|| !preg_match(
					'/^[a-f0-9]{64}$/',
					(string)( $source['content_hash'] ?? '' )
				)
			) {
				return false;
			}
		}

		return true;
	}

	/**
	 * @param string $path
	 * @return string
	 */
	public static function renderFile( $path ) {
		$document = self::load( $path );
		if ( $document === null ) {
			return Html::element(
				'p',
				[ 'class' => 'wikikg-note wikikg-graph-error' ],
				'Không đọc được graph document để hiển thị trong Wiki.'
			);
		}

		return self::render( $document );
	}

	/**
	 * @param array<string,mixed> $document
	 * @return string
	 */
	public static function render( array $document ) {
		$nodes = array_values( array_filter(
			$document['nodes'],
			static function ( $node ) {
				return is_array( $node ) && trim( (string)( $node['id'] ?? '' ) ) !== '';
			}
		) );
		$edges = array_values( array_filter(
			$document['edges'],
			static function ( $edge ) {
				return is_array( $edge )
					&& trim( (string)( $edge['source'] ?? '' ) ) !== ''
					&& trim( (string)( $edge['target'] ?? '' ) ) !== '';
			}
		) );

		$totalNodes = count( $nodes );
		$totalEdges = count( $edges );
		$nodes = array_slice( $nodes, 0, self::MAX_RENDERED_NODES );
		$edges = array_slice( $edges, 0, self::MAX_RENDERED_EDGES );

		$metadata = $document['metadata'];
		$schemaVersion = (string)(
			$document['schema_version'] ?? self::SCHEMA_VERSION
		);
		$summaryText = 'Graph document v' . $schemaVersion . ': '
			. $totalNodes . ' node, ' . $totalEdges . ' quan hệ.';
		if ( $totalNodes > count( $nodes ) || $totalEdges > count( $edges ) ) {
			$summaryText .= ' Chỉ hiển thị tối đa '
				. self::MAX_RENDERED_NODES . ' node và '
				. self::MAX_RENDERED_EDGES . ' quan hệ trong trang này.';
		}

		$summary = Html::element(
			'p',
			[ 'class' => 'wikikg-note' ],
			$summaryText
		);

		if ( !empty( $metadata['extraction_method'] ) ) {
			$summary .= Html::element(
				'p',
				[ 'class' => 'wikikg-note' ],
				'Phương pháp: ' . (string)$metadata['extraction_method']
			);
		}

		$nodeCards = '';
		foreach ( $nodes as $node ) {
			$nodeCards .= self::renderNode( $node );
		}

		$edgeRows = '';
		foreach ( $edges as $edge ) {
			$edgeRows .= self::renderEdge( $edge );
		}

		$edgesHtml = Html::rawElement(
			'table',
			[ 'class' => 'wikikg-graph-edges' ],
			Html::rawElement(
				'thead',
				[],
				Html::rawElement(
					'tr',
					[],
					Html::element( 'th', [], 'Nguồn' )
						. Html::element( 'th', [], 'Quan hệ' )
						. Html::element( 'th', [], 'Đích' )
					)
				)
				. Html::rawElement( 'tbody', [], $edgeRows )
		);

		return Html::rawElement(
			'section',
			[ 'class' => 'wikikg-graph-document' ],
			Html::element( 'h4', [ 'class' => 'wikikg-subhead' ], 'Graph trong Wiki' )
				. $summary
				. self::renderSources( $metadata['source_pages'] ?? [] )
				. Html::element(
					'h5',
					[ 'class' => 'wikikg-graph-heading' ],
					'Nodes'
				)
				. Html::rawElement( 'div', [ 'class' => 'wikikg-graph-nodes' ], $nodeCards )
				. Html::element(
					'h5',
					[ 'class' => 'wikikg-graph-heading' ],
					'Quan hệ'
				)
				. $edgesHtml
		);
	}

	/**
	 * @param mixed $sources
	 * @return string
	 */
	private static function renderSources( $sources ) {
		if ( !is_array( $sources ) || !$sources ) {
			return '';
		}

		$rows = '';
		foreach ( $sources as $source ) {
			if ( !is_array( $source ) || empty( $source['title'] ) ) {
				continue;
			}
			$hash = trim( (string)( $source['content_hash'] ?? '' ) );
			$hash = $hash !== '' ? substr( $hash, 0, 12 ) . '…' : 'Chưa có';
			$rows .= Html::rawElement(
				'tr',
				[],
				Html::rawElement(
					'td',
					[],
					self::renderPageLink( $source['title'], $source['title'] )
				)
					. Html::element( 'td', [], (string)( $source['kind'] ?? '' ) )
					. Html::element( 'td', [], (string)( $source['revision_id'] ?? 'Chưa có' ) )
					. Html::element( 'td', [], $hash )
			);
		}

		if ( $rows === '' ) {
			return '';
		}

		return Html::element(
			'h5',
			[ 'class' => 'wikikg-graph-heading' ],
			'Nguồn và revision'
		) . Html::rawElement(
			'table',
			[ 'class' => 'wikikg-graph-edges wikikg-graph-sources' ],
			Html::rawElement(
				'thead',
				[],
				Html::rawElement(
					'tr',
					[],
					Html::element( 'th', [], 'Trang' )
						. Html::element( 'th', [], 'Loại' )
						. Html::element( 'th', [], 'Revision' )
						. Html::element( 'th', [], 'Hash' )
					)
				)
				. Html::rawElement( 'tbody', [], $rows )
		);
	}

	/**
	 * @param array<string,mixed> $node
	 * @return string
	 */
	private static function renderNode( array $node ) {
		$id = trim( (string)( $node['id'] ?? '' ) );
		$type = trim( (string)( $node['type'] ?? 'Node' ) );
		$properties = is_array( $node['properties'] ?? null )
			? $node['properties']
			: [];
		$label = self::renderPageLink(
			$properties['page_title'] ?? '',
			$id
		);
		$propertyItems = '';

		foreach ( array_slice( $properties, 0, 12, true ) as $key => $value ) {
			$propertyItems .= Html::rawElement(
				'li',
				[],
				Html::element( 'strong', [], (string)$key . ': ' )
					. Html::element( 'span', [], self::displayValue( $value ) )
			);
		}

		return Html::rawElement(
			'article',
			[ 'class' => 'wikikg-graph-node', 'data-node-type' => $type ],
			Html::element( 'span', [ 'class' => 'wikikg-graph-node-type' ], $type )
				. Html::rawElement( 'h6', [], $label )
				. ( $propertyItems !== ''
					? Html::rawElement( 'ul', [], $propertyItems )
					: '' )
		);
	}

	/**
	 * @param array<string,mixed> $edge
	 * @return string
	 */
	private static function renderEdge( array $edge ) {
		$properties = is_array( $edge['properties'] ?? null )
			? $edge['properties']
			: [];
		$type = trim( (string)( $edge['type'] ?? 'RELATED_TO' ) );
		if ( $properties ) {
			$type .= ' (' . self::displayValue( $properties ) . ')';
		}

		return Html::rawElement(
			'tr',
			[],
			Html::element( 'td', [], (string)$edge['source'] )
				. Html::element( 'td', [], $type )
				. Html::element( 'td', [], (string)$edge['target'] )
		);
	}

	/**
	 * @param mixed $value
	 * @return string
	 */
	private static function displayValue( $value ) {
		if ( is_array( $value ) || is_object( $value ) ) {
			$value = json_encode( $value, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES );
		}
		$value = trim( (string)$value );
		return function_exists( 'mb_substr' )
			? mb_substr( $value, 0, 240 )
			: substr( $value, 0, 240 );
	}

	/**
	 * @param mixed $pageTitle
	 * @param string $fallback
	 * @return string
	 */
	private static function renderPageLink( $pageTitle, $fallback ) {
		$pageTitle = trim( (string)$pageTitle );
		$title = $pageTitle !== '' ? Title::newFromText( $pageTitle ) : false;
		if ( $title ) {
			return Html::element(
				'a',
				[ 'href' => $title->getLocalURL() ],
				$fallback
			);
		}
		return Html::element( 'span', [], $fallback );
	}
}
