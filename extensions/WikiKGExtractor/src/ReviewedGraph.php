<?php

namespace MediaWiki\Extension\WikiKGExtractor;

/**
 * Builds an in-Wiki graph view from individually reviewed claim snapshots.
 *
 * Property claims stay as separate rows so contradictory values are preserved.
 */
final class ReviewedGraph {
	/**
	 * @param array<int,array<string,mixed>> $reviews Full, access-checked snapshots
	 * @return array{
	 *   document:array<string,mixed>,
	 *   property_claims:array<int,array<string,mixed>>,
	 *   relationship_claims:array<int,array<string,mixed>>
	 * }
	 */
	public static function build( array $reviews ): array {
		$nodes = [];
		$edges = [];
		$propertyClaims = [];
		$relationshipClaims = [];

		foreach ( $reviews as $review ) {
			if ( ( $review['latest_review']['status'] ?? null ) !== 'approved' ) {
				continue;
			}

			$snapshotId = $review['snapshot_id'] ?? null;
			$claim = $review;
			if ( !is_string( $snapshotId )
				|| !preg_match( '/^[a-f0-9]{64}$/D', $snapshotId )
				|| !is_string( $claim['predicate'] ?? null )
				|| trim( $claim['predicate'] ) === ''
			) {
				continue;
			}

			$subject = self::entity( $claim['subject'] ?? null );
			if ( $subject === null ) {
				continue;
			}

			if ( ( $claim['claim_type'] ?? null ) === 'property'
				&& is_string( $claim['value'] ?? null )
			) {
				$key = $subject['type'] . "\0" . $subject['id'];
				$nodes[$key] ??= [
					'id' => $subject['id'],
					'label' => $subject['id'],
					'type' => $subject['type'],
					'properties' => [],
				];
				$propertyClaims[] = [
					'subject' => $subject['id'],
					'subject_type' => $subject['type'],
					'predicate' => $claim['predicate'],
					'value' => $claim['value'],
					'qualifiers' => self::qualifiersText( $claim['qualifiers'] ?? [] ),
					'snapshot_id' => $snapshotId,
					'source_evidence' => self::sourceEvidence( $claim['evidence'] ?? [] ),
				];
				continue;
			}

			if ( ( $claim['claim_type'] ?? null ) !== 'relationship' ) {
				continue;
			}
			$object = self::entity( $claim['object'] ?? null );
			if ( $object === null ) {
				continue;
			}

			foreach ( [ $subject, $object ] as $entity ) {
				$key = $entity['type'] . "\0" . $entity['id'];
				$nodes[$key] ??= [
					'id' => $entity['id'],
					'label' => $entity['id'],
					'type' => $entity['type'],
					'properties' => [],
				];
			}

			$edges[] = [
				'source' => $subject['id'],
				'source_type' => $subject['type'],
				'type' => $claim['predicate'],
				'target' => $object['id'],
				'target_type' => $object['type'],
				'properties' => [ 'supporting_snapshot_ids' => [ $snapshotId ] ],
			];
			$relationshipClaims[] = [
				'subject' => $subject['id'],
				'subject_type' => $subject['type'],
				'predicate' => $claim['predicate'],
				'object' => $object['id'],
				'object_type' => $object['type'],
				'qualifiers' => self::qualifiersText( $claim['qualifiers'] ?? [] ),
				'snapshot_id' => $snapshotId,
				'source_evidence' => self::sourceEvidence( $claim['evidence'] ?? [] ),
			];
		}

		return [
			'document' => [
				'schema_version' => '1.0',
				'metadata' => [],
				'nodes' => array_values( $nodes ),
				'edges' => $edges,
			],
			'property_claims' => $propertyClaims,
			'relationship_claims' => $relationshipClaims,
		];
	}

	/**
	 * @param mixed $value
	 * @return array{type:string,id:string}|null
	 */
	private static function entity( $value ): ?array {
		if ( !is_array( $value )
			|| !in_array( $value['type'] ?? null, [ 'Crop', 'Variety', 'Pest' ], true )
			|| !is_string( $value['id'] ?? null )
			|| trim( $value['id'] ) === ''
		) {
			return null;
		}

		return [
			'type' => $value['type'],
			'id' => $value['id'],
		];
	}

	/** @param mixed $value */
	private static function qualifiersText( $value ): string {
		if ( is_object( $value ) ) {
			$value = get_object_vars( $value );
		}
		if ( !is_array( $value ) || !$value ) {
			return '';
		}

		$parts = [];
		foreach ( $value as $key => $qualifier ) {
			if ( is_array( $qualifier ) || is_object( $qualifier ) ) {
				$displayValue = json_encode(
					$qualifier,
					JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR
				);
			} elseif ( is_bool( $qualifier ) ) {
				$displayValue = $qualifier ? 'true' : 'false';
			} elseif ( $qualifier === null ) {
				$displayValue = 'null';
			} elseif ( is_scalar( $qualifier ) ) {
				$displayValue = (string)$qualifier;
			} else {
				continue;
			}
			$parts[] = (string)$key . ': ' . $displayValue;
		}

		return implode( '; ', $parts );
	}

	/** @param mixed $value @return array<int,array{page_title:string,revision_id:int}> */
	private static function sourceEvidence( $value ): array {
		if ( !is_array( $value ) ) {
			return [];
		}

		$sources = [];
		foreach ( array_slice( $value, 0, 20 ) as $evidence ) {
			if ( !is_array( $evidence )
				|| !is_string( $evidence['page_title'] ?? null )
				|| trim( $evidence['page_title'] ) === ''
				|| !is_numeric( $evidence['revision_id'] ?? null )
				|| (int)$evidence['revision_id'] < 1
			) {
				continue;
			}
			$sources[] = [
				'page_title' => $evidence['page_title'],
				'revision_id' => (int)$evidence['revision_id'],
			];
		}
		return $sources;
	}
}
