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
	private const EDGE_LABELS = [
		'HAS_VARIETY' => 'Có giống',
		'RESISTANT_TO' => 'Kháng',
		'SUSCEPTIBLE_TO' => 'Nhiễm',
		'AFFECTED_BY' => 'Bị gây hại bởi'
	];

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
		if ( !self::hasValidGraph( $document['nodes'], $document['edges'] ) ) {
			return null;
		}

		return $document;
	}

	/**
	 * @param array<int,mixed> $nodes
	 * @param array<int,mixed> $edges
	 * @return bool
	 */
	private static function hasValidGraph( array $nodes, array $edges ) {
		$nodeKeys = [];
		foreach ( $nodes as $node ) {
			if ( !is_array( $node )
				|| trim( (string)( $node['id'] ?? '' ) ) === ''
				|| trim( (string)( $node['label'] ?? '' ) ) === ''
				|| trim( (string)( $node['type'] ?? '' ) ) === ''
				|| !is_array( $node['properties'] ?? null )
				|| ( isset( $node['extra_labels'] )
					&& !is_array( $node['extra_labels'] ) )
			) {
				return false;
			}
			$key = self::nodeKey( $node['type'], $node['id'] );
			if ( isset( $nodeKeys[$key] ) ) {
				return false;
			}
			$nodeKeys[$key] = true;
		}

		foreach ( $edges as $edge ) {
			if ( !is_array( $edge )
				|| trim( (string)( $edge['source'] ?? '' ) ) === ''
				|| trim( (string)( $edge['source_type'] ?? '' ) ) === ''
				|| trim( (string)( $edge['type'] ?? '' ) ) === ''
				|| trim( (string)( $edge['target'] ?? '' ) ) === ''
				|| trim( (string)( $edge['target_type'] ?? '' ) ) === ''
				|| !is_array( $edge['properties'] ?? null )
				|| !isset( $nodeKeys[self::nodeKey(
					$edge['source_type'],
					$edge['source']
				)] )
				|| !isset( $nodeKeys[self::nodeKey(
					$edge['target_type'],
					$edge['target']
				)] )
			) {
				return false;
			}
		}

		return true;
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
		$sourceRevisions = [];
		foreach ( $metadata['source_pages'] ?? [] as $source ) {
			if ( is_array( $source ) && !empty( $source['title'] ) ) {
				$sourceRevisions[(string)$source['title']] = (int)(
					$source['revision_id'] ?? 0
				);
			}
		}
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
		$nodeLinks = [];
		foreach ( $nodes as $node ) {
			$pageTitle = trim( (string)( $node['properties']['page_title'] ?? '' ) );
			$revisionId = $sourceRevisions[$pageTitle] ?? 0;
			$nodeLinks[self::nodeKey( $node['type'] ?? '', $node['id'] ?? '' )] = [
				'title' => $pageTitle,
				'revision_id' => $revisionId
			];
			$nodeCards .= self::renderNode( $node, $revisionId );
		}

		$edgeRows = '';
		foreach ( $edges as $edge ) {
			$edgeRows .= self::renderEdge( $edge, $nodeLinks );
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
				. Html::rawElement(
					'div',
					[ 'class' => 'wikikg-graph-visual', 'hidden' => true ],
					Html::element(
						'h5',
						[ 'class' => 'wikikg-graph-heading' ],
						'Sơ đồ quan hệ'
					)
						. Html::element( 'div', [
							'class' => 'wikikg-graph-canvas',
							'data-wikikg-graph' => '1',
							'aria-hidden' => 'true'
						] )
						. Html::element(
							'p',
							[ 'class' => 'wikikg-note' ],
							'Chọn một đường nối để xem tên quan hệ. '
								. 'Dùng danh sách node và bảng bên dưới để đọc bằng bàn phím.'
						)
				)
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
	 * Return the bounded, presentation-only data passed to ResourceLoader.
	 *
	 * @param array<string,mixed> $document
	 * @return array<string,mixed>
	 */
	public static function clientData( array $document ) {
		$nodes = array_slice( $document['nodes'] ?? [], 0, self::MAX_RENDERED_NODES );
		$allowed = [];
		$clientNodes = [];
		$sourceRevisions = [];
		foreach ( $document['metadata']['source_pages'] ?? [] as $source ) {
			if ( is_array( $source ) && !empty( $source['title'] ) ) {
				$sourceRevisions[(string)$source['title']] = (int)(
					$source['revision_id'] ?? 0
				);
			}
		}
		foreach ( $nodes as $node ) {
			if ( !is_array( $node ) ) {
				continue;
			}
			$key = self::nodeKey( $node['type'] ?? '', $node['id'] ?? '' );
			$allowed[$key] = true;
			$pageTitle = trim( (string)( $node['properties']['page_title'] ?? '' ) );
			$clientNodes[] = [
				'data' => [
					'id' => self::clientNodeId( $key ),
					'label' => (string)( $node['label'] ?? $node['id'] ?? '' ),
					'type' => (string)( $node['type'] ?? 'Node' ),
					'href' => self::pageUrl(
						$pageTitle,
						$sourceRevisions[$pageTitle] ?? 0
					)
				]
			];
		}

		$clientEdges = [];
		foreach ( $document['edges'] ?? [] as $index => $edge ) {
			if ( !is_array( $edge ) ) {
				continue;
			}
			$sourceKey = self::nodeKey(
				$edge['source_type'] ?? '',
				$edge['source'] ?? ''
			);
			$targetKey = self::nodeKey(
				$edge['target_type'] ?? '',
				$edge['target'] ?? ''
			);
			if ( !isset( $allowed[$sourceKey], $allowed[$targetKey] ) ) {
				continue;
			}
			$type = (string)( $edge['type'] ?? 'RELATED_TO' );
			$clientEdges[] = [
				'data' => [
					'id' => 'e-' . hash( 'sha256', $sourceKey . "\0" . $type
						. "\0" . $targetKey . "\0" . $index ),
					'source' => self::clientNodeId( $sourceKey ),
					'target' => self::clientNodeId( $targetKey ),
					'label' => self::edgeLabel( $type )
				]
			];
			if ( count( $clientEdges ) >= self::MAX_RENDERED_EDGES ) {
				break;
			}
		}

		return [
			'schema_version' => self::SCHEMA_VERSION,
			'nodes' => $clientNodes,
			'edges' => $clientEdges
		];
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
					self::renderPageLink(
						$source['title'],
						$source['title'],
						(int)( $source['revision_id'] ?? 0 )
					)
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
	private static function renderNode( array $node, $revisionId = 0 ) {
		$id = trim( (string)( $node['id'] ?? '' ) );
		$type = trim( (string)( $node['type'] ?? 'Node' ) );
		$properties = is_array( $node['properties'] ?? null )
			? $node['properties']
			: [];
		$label = self::renderPageLink(
			$properties['page_title'] ?? '',
			$id,
			$revisionId
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
	private static function renderEdge( array $edge, array $nodeLinks = [] ) {
		$properties = is_array( $edge['properties'] ?? null )
			? $edge['properties']
			: [];
		$rawType = trim( (string)( $edge['type'] ?? 'RELATED_TO' ) );
		$type = self::edgeLabel( $rawType );
		if ( $properties ) {
			$type .= ' (' . self::displayValue( $properties ) . ')';
		}

		return Html::rawElement(
			'tr',
			[],
			Html::rawElement(
				'td',
				[],
				self::renderEndpointLink( $edge, 'source', $nodeLinks )
			)
				. Html::element( 'td', [], $type )
				. Html::rawElement(
					'td',
					[],
					self::renderEndpointLink( $edge, 'target', $nodeLinks )
				)
		);
	}

	/**
	 * @param array<string,mixed> $edge
	 * @param string $side
	 * @param array<string,array{title:string,revision_id:int}> $nodeLinks
	 * @return string
	 */
	private static function renderEndpointLink( array $edge, $side, array $nodeLinks ) {
		$id = (string)( $edge[$side] ?? '' );
		$type = (string)( $edge[$side . '_type'] ?? '' );
		$link = $nodeLinks[self::nodeKey( $type, $id )] ?? [];
		return self::renderPageLink(
			$link['title'] ?? '',
			$id,
			$link['revision_id'] ?? 0
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
	private static function renderPageLink( $pageTitle, $fallback, $revisionId = 0 ) {
		$pageTitle = trim( (string)$pageTitle );
		$title = $pageTitle !== '' ? Title::newFromText( $pageTitle ) : false;
		if ( $title ) {
			return Html::element(
				'a',
				[ 'href' => $title->getLocalURL(
					(int)$revisionId > 0 ? [ 'oldid' => (int)$revisionId ] : []
				) ],
				$fallback
			);
		}
		return Html::element( 'span', [], $fallback );
	}

	/**
	 * @param mixed $type
	 * @param mixed $id
	 * @return string
	 */
	private static function nodeKey( $type, $id ) {
		return trim( (string)$type ) . "\0" . trim( (string)$id );
	}

	/**
	 * @param string $nodeKey
	 * @return string
	 */
	private static function clientNodeId( $nodeKey ) {
		return 'n-' . rtrim( strtr( base64_encode( $nodeKey ), '+/', '-_' ), '=' );
	}

	/**
	 * @param string $type
	 * @return string
	 */
	private static function edgeLabel( $type ) {
		return self::EDGE_LABELS[$type] ?? $type;
	}

	/**
	 * @param string $pageTitle
	 * @param int $revisionId
	 * @return string
	 */
	private static function pageUrl( $pageTitle, $revisionId ) {
		$pageTitle = trim( (string)$pageTitle );
		$title = $pageTitle !== '' ? Title::newFromText( $pageTitle ) : false;
		return $title
			? $title->getLocalURL(
				(int)$revisionId > 0 ? [ 'oldid' => (int)$revisionId ] : []
			)
			: '';
	}
}
