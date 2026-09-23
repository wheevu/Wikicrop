<?php

namespace MediaWiki\Extension\WikiKGExtractor;

use JsonException;
use RuntimeException;
use stdClass;

/**
 * Loads candidate claims only when both private job artifacts agree on their
 * source revisions and every quoted span can be revalidated against raw text.
 */
class ClaimDocument {
	private const SCHEMA_VERSION = '1.0';
	private const MAX_ARTIFACT_BYTES = 5242880;
	private const MAX_CLAIMS = 500;
	private const MAX_EVIDENCE_PER_CLAIM = 20;
	private const MAX_SPAN_CHARS = 10000;

	/**
	 * @param string $claimsPath Path to candidate_claims.json
	 * @param string $rawPath Path to raw_data.json from the same job directory
	 * @return array<string,mixed>
	 * @throws RuntimeException If artifacts are missing, invalid, or disagree
	 */
	public static function load( $claimsPath, $rawPath ): array {
		if ( !function_exists( 'mb_substr' ) || !function_exists( 'mb_strlen' )
			|| !function_exists( 'mb_check_encoding' )
		) {
			throw new RuntimeException( 'Unicode evidence validation is unavailable.' );
		}

		$claimsArtifact = self::readArtifact( $claimsPath, 'candidate_claims.json' );
		$rawArtifact = self::readArtifact( $rawPath, 'raw_data.json' );
		if ( $claimsArtifact['directory'] !== $rawArtifact['directory'] ) {
			throw new RuntimeException( 'Claim and raw artifacts must share one job directory.' );
		}

		try {
			$documentObject = json_decode(
				$claimsArtifact['contents'],
				false,
				512,
				JSON_THROW_ON_ERROR | JSON_BIGINT_AS_STRING
			);
			$rawObject = json_decode(
				$rawArtifact['contents'],
				false,
				512,
				JSON_THROW_ON_ERROR | JSON_BIGINT_AS_STRING
			);
			$document = json_decode(
				$claimsArtifact['contents'],
				true,
				512,
				JSON_THROW_ON_ERROR | JSON_BIGINT_AS_STRING
			);
			$raw = json_decode(
				$rawArtifact['contents'],
				true,
				512,
				JSON_THROW_ON_ERROR | JSON_BIGINT_AS_STRING
			);
		} catch ( JsonException $exception ) {
			throw new RuntimeException( 'Claim or raw artifact contains invalid JSON.', 0, $exception );
		}

		if ( !( $documentObject instanceof stdClass )
			|| !( $rawObject instanceof stdClass )
			|| !is_array( $document ) || !is_array( $raw )
		) {
			throw new RuntimeException( 'Claim or raw artifact has an invalid root value.' );
		}

		self::validateJsonShapes( $documentObject, $rawObject );
		$rawPages = self::validateRawDocument( $raw );
		self::validateClaimDocument( $document, $rawPages );
		try {
			self::addSnapshotIds( $document, $documentObject );
		} catch ( JsonException $exception ) {
			throw new RuntimeException( 'Candidate claim snapshot data is invalid.', 0, $exception );
		}

		return $document;
	}

	/**
	 * @param string $path
	 * @param string $expectedName
	 * @return array{contents:string,directory:string}
	 */
	private static function readArtifact( $path, $expectedName ) {
		if ( !is_string( $path ) || $path === '' || str_contains( $path, "\0" )
			|| basename( $path ) !== $expectedName
			|| is_link( $path ) || !is_file( $path ) || !is_readable( $path )
		) {
			throw new RuntimeException( 'A required claim job artifact is missing or unsafe.' );
		}

		$realPath = realpath( $path );
		$directory = realpath( dirname( $path ) );
		if ( $realPath === false || $directory === false
			|| basename( $realPath ) !== $expectedName
			|| dirname( $realPath ) !== $directory
		) {
			throw new RuntimeException( 'A required claim job artifact has an invalid path.' );
		}

		$size = filesize( $realPath );
		if ( $size === false || $size > self::MAX_ARTIFACT_BYTES ) {
			throw new RuntimeException( 'A claim job artifact exceeds the size limit.' );
		}

		$contents = file_get_contents(
			$realPath,
			false,
			null,
			0,
			self::MAX_ARTIFACT_BYTES + 1
		);
		if ( $contents === false || strlen( $contents ) > self::MAX_ARTIFACT_BYTES ) {
			throw new RuntimeException( 'A claim job artifact could not be read safely.' );
		}

		return [ 'contents' => $contents, 'directory' => $directory ];
	}

	/**
	 * Preserve the distinction between JSON objects and arrays, including empty
	 * maps such as qualifiers: {}.
	 */
	private static function validateJsonShapes( stdClass $document, stdClass $raw ) {
		if ( !( ( $document->metadata ?? null ) instanceof stdClass )
			|| !( ( $document->summary ?? null ) instanceof stdClass )
			|| !isset( $document->claims ) || !is_array( $document->claims )
			|| !array_is_list( $document->claims )
			|| !property_exists( $document->metadata, 'source_pages' )
			|| !is_array( $document->metadata->source_pages )
			|| !array_is_list( $document->metadata->source_pages )
			|| !( ( $document->summary->claims_by_predicate ?? null ) instanceof stdClass )
		) {
			throw new RuntimeException( 'Candidate JSON object and array shapes are invalid.' );
		}
		foreach ( $document->metadata->source_pages as $source ) {
			if ( !( $source instanceof stdClass ) ) {
				throw new RuntimeException( 'Candidate source metadata is not an object.' );
			}
		}
		if ( property_exists( $document->summary, 'skipped_source_pages' )
			&& ( !is_array( $document->summary->skipped_source_pages )
				|| !array_is_list( $document->summary->skipped_source_pages ) )
		) {
			throw new RuntimeException( 'Candidate skipped page metadata is not an array.' );
		}
		foreach ( $document->claims as $claim ) {
			if ( !( $claim instanceof stdClass )
				|| !( ( $claim->subject ?? null ) instanceof stdClass )
				|| !( ( $claim->qualifiers ?? null ) instanceof stdClass )
				|| !( ( $claim->review ?? null ) instanceof stdClass )
				|| !isset( $claim->evidence ) || !is_array( $claim->evidence )
				|| !array_is_list( $claim->evidence )
			) {
				throw new RuntimeException( 'Candidate claim JSON object and array shapes are invalid.' );
			}
			if ( ( $claim->claim_type ?? null ) === 'relationship'
				&& property_exists( $claim, 'object' )
				&& !( $claim->object instanceof stdClass )
			) {
				throw new RuntimeException( 'Candidate relationship object is invalid.' );
			}
			foreach ( $claim->evidence as $evidence ) {
				if ( !( $evidence instanceof stdClass ) ) {
					throw new RuntimeException( 'Candidate evidence is not an object.' );
				}
			}
		}

		if ( !isset( $raw->pages ) || !is_array( $raw->pages ) || !array_is_list( $raw->pages ) ) {
			throw new RuntimeException( 'Raw page JSON array is invalid.' );
		}
		foreach ( $raw->pages as $page ) {
			if ( !( $page instanceof stdClass ) ) {
				throw new RuntimeException( 'A raw page is not an object.' );
			}
			if ( property_exists( $page, 'source_pages' )
				&& ( !is_array( $page->source_pages ) || !array_is_list( $page->source_pages ) )
			) {
				throw new RuntimeException( 'A raw page source list is invalid.' );
			}
		}
		if ( property_exists( $raw, 'errors' ) ) {
			if ( !is_array( $raw->errors ) || !array_is_list( $raw->errors ) ) {
				throw new RuntimeException( 'Raw error JSON array is invalid.' );
			}
			foreach ( $raw->errors as $error ) {
				if ( !( $error instanceof stdClass ) ) {
					throw new RuntimeException( 'A raw error is not an object.' );
				}
			}
		}
	}

	/**
	 * @param array<string,mixed> $raw
	 * @return array<string,array<string,mixed>> Indexed by exact page title
	 */
	private static function validateRawDocument( array $raw ) {
		if ( ( $raw['schema_version'] ?? null ) !== self::SCHEMA_VERSION
			|| !is_array( $raw['pages'] ?? null ) || !array_is_list( $raw['pages'] )
		) {
			throw new RuntimeException( 'Raw data does not match schema version 1.0.' );
		}
		foreach ( [ 'source_url', 'generated_at' ] as $field ) {
			if ( array_key_exists( $field, $raw ) && !is_string( $raw[$field] ) ) {
				throw new RuntimeException( 'Raw data metadata is invalid.' );
			}
		}
		foreach ( [ 'page_count', 'error_count' ] as $field ) {
			if ( array_key_exists( $field, $raw ) && ( !is_int( $raw[$field] ) || $raw[$field] < 0 ) ) {
				throw new RuntimeException( 'Raw data counts are invalid.' );
			}
		}
		if ( array_key_exists( 'page_count', $raw )
			&& $raw['page_count'] !== count( $raw['pages'] )
		) {
			throw new RuntimeException( 'Raw data page count does not match its pages.' );
		}
		if ( array_key_exists( 'errors', $raw ) && ( !is_array( $raw['errors'] )
			|| !array_is_list( $raw['errors'] ) )
		) {
			throw new RuntimeException( 'Raw data errors are invalid.' );
		}
		foreach ( $raw['errors'] ?? [] as $error ) {
			if ( !is_array( $error ) || ( $error && array_is_list( $error ) ) ) {
				throw new RuntimeException( 'Raw data errors are invalid.' );
			}
		}

		$pagesByTitle = [];
		$seenPageIds = [];
		foreach ( $raw['pages'] as $page ) {
			if ( !is_array( $page )
				|| !self::nonEmptyString( $page['title'] ?? null )
				|| !self::positiveInt( $page['page_id'] ?? null )
				|| !self::positiveInt( $page['revision_id'] ?? null )
				|| !self::isHash( $page['content_hash'] ?? null )
				|| !is_string( $page['wikitext'] ?? null )
				|| $page['wikitext'] === ''
				|| !is_string( $page['text'] ?? null )
			) {
				throw new RuntimeException( 'A raw page is missing required schema fields.' );
			}
			if ( array_key_exists( 'kind', $page )
				&& !in_array( $page['kind'], [ 'species', 'variety' ], true )
			) {
				throw new RuntimeException( 'A raw page has an invalid kind.' );
			}
			if ( array_key_exists( 'crop', $page ) && !is_string( $page['crop'] ) ) {
				throw new RuntimeException( 'A raw page has invalid crop metadata.' );
			}
			if ( array_key_exists( 'source_pages', $page ) && ( !is_array( $page['source_pages'] )
				|| !array_is_list( $page['source_pages'] ) )
			) {
				throw new RuntimeException( 'A raw page has invalid source metadata.' );
			}
			foreach ( $page['source_pages'] ?? [] as $sourceTitle ) {
				if ( !is_string( $sourceTitle ) ) {
					throw new RuntimeException( 'A raw page has invalid source metadata.' );
				}
			}
			if ( array_key_exists( 'url', $page ) && !is_string( $page['url'] ) ) {
				throw new RuntimeException( 'A raw page has invalid URL metadata.' );
			}
			if ( !mb_check_encoding( $page['wikitext'], 'UTF-8' )
				|| !hash_equals(
					$page['content_hash'],
					hash( 'sha256', $page['wikitext'] )
				)
			) {
				throw new RuntimeException( 'A raw page content hash does not match its wikitext.' );
			}

			$titleKey = self::pageKey( $page['title'] );
			if ( isset( $pagesByTitle[$titleKey] ) || isset( $seenPageIds[$page['page_id']] ) ) {
				throw new RuntimeException( 'Raw data contains duplicate page identities.' );
			}
			$pagesByTitle[$titleKey] = $page;
			$seenPageIds[$page['page_id']] = true;
		}

		return $pagesByTitle;
	}

	/**
	 * @param array<string,mixed> $document
	 * @param array<string,array<string,mixed>> $rawPages
	 */
	private static function validateClaimDocument( array $document, array $rawPages ) {
		if ( ( $document['schema_version'] ?? null ) !== self::SCHEMA_VERSION
			|| !is_array( $document['metadata'] ?? null )
			|| !is_array( $document['summary'] ?? null )
			|| !is_array( $document['claims'] ?? null )
			|| !array_is_list( $document['claims'] )
			|| count( $document['claims'] ) > self::MAX_CLAIMS
		) {
			throw new RuntimeException( 'Candidate claims do not match schema version 1.0.' );
		}

		$metadata = $document['metadata'];
		if ( !self::nonEmptyString( $metadata['extractor_version'] ?? null )
			|| !in_array( $metadata['extraction_method'] ?? null, [ 'rules', 'ai' ], true )
			|| ( $metadata['review_state'] ?? null ) !== 'pending'
			|| !is_array( $metadata['source_pages'] ?? null )
			|| !array_is_list( $metadata['source_pages'] )
		) {
			throw new RuntimeException( 'Candidate claims metadata is invalid or not pending.' );
		}
		self::validateSummary( $document['summary'] );

		$sourcePages = [];
		foreach ( $metadata['source_pages'] as $source ) {
			if ( !is_array( $source )
				|| !self::nonEmptyString( $source['title'] ?? null )
				|| !self::positiveInt( $source['page_id'] ?? null )
				|| !self::positiveInt( $source['revision_id'] ?? null )
				|| !self::isHash( $source['content_hash'] ?? null )
			) {
				throw new RuntimeException( 'Candidate source metadata is invalid.' );
			}
			$key = self::pageKey( $source['title'] );
			$rawPage = $rawPages[$key] ?? null;
			if ( $rawPage === null || !self::samePageIdentity( $source, $rawPage )
				|| isset( $sourcePages[$key] )
			) {
				throw new RuntimeException( 'Candidate source metadata does not match raw data.' );
			}
			$sourcePages[$key] = $source;
		}
		if ( count( $sourcePages ) !== count( $rawPages ) ) {
			throw new RuntimeException( 'Candidate and raw artifacts do not describe the same pages.' );
		}

		$seenClaimIds = [];
		foreach ( $document['claims'] as $claim ) {
			self::validateClaim( $claim, $metadata, $rawPages, $sourcePages );
			if ( isset( $seenClaimIds[$claim['claim_id']] ) ) {
				throw new RuntimeException( 'Candidate claims contain duplicate claim identities.' );
			}
			$seenClaimIds[$claim['claim_id']] = true;
		}
	}

	/**
	 * @param array<string,mixed> $summary
	 */
	private static function validateSummary( array $summary ) {
		if ( !self::nonNegativeInt( $summary['claim_count'] ?? null )
			|| !self::nonNegativeInt( $summary['traceability_complete_count'] ?? null )
			|| !is_array( $summary['claims_by_predicate'] ?? null )
		) {
			throw new RuntimeException( 'Candidate claims summary is invalid.' );
		}
		foreach ( $summary['claims_by_predicate'] as $count ) {
			if ( !self::nonNegativeInt( $count ) ) {
				throw new RuntimeException( 'Candidate predicate counts are invalid.' );
			}
		}
		if ( array_key_exists( 'skipped_source_pages', $summary ) ) {
			if ( !is_array( $summary['skipped_source_pages'] )
				|| !array_is_list( $summary['skipped_source_pages'] )
			) {
				throw new RuntimeException( 'Candidate skipped page metadata is invalid.' );
			}
			foreach ( $summary['skipped_source_pages'] as $title ) {
				if ( !is_string( $title ) ) {
					throw new RuntimeException( 'Candidate skipped page metadata is invalid.' );
				}
			}
		}
	}

	/**
	 * @param mixed $claim
	 * @param array<string,mixed> $metadata
	 * @param array<string,array<string,mixed>> $rawPages
	 * @param array<string,array<string,mixed>> $sourcePages
	 */
	private static function validateClaim( $claim, array $metadata, array $rawPages, array $sourcePages ) {
		if ( !is_array( $claim ) ) {
			throw new RuntimeException( 'A candidate claim is not an object.' );
		}
		self::onlyKeys( $claim, [
			'claim_id', 'claim_type', 'subject', 'predicate', 'object', 'value',
			'qualifiers', 'evidence', 'traceability_complete', 'review'
		] );
		if ( !self::isHash( $claim['claim_id'] ?? null )
			|| !in_array( $claim['claim_type'] ?? null, [ 'relationship', 'property' ], true )
			|| !self::validEntityRef( $claim['subject'] ?? null )
			|| !self::nonEmptyString( $claim['predicate'] ?? null )
			|| !is_array( $claim['qualifiers'] ?? null )
			|| !is_array( $claim['evidence'] ?? null )
			|| !array_is_list( $claim['evidence'] )
			|| count( $claim['evidence'] ) < 1
			|| count( $claim['evidence'] ) > self::MAX_EVIDENCE_PER_CLAIM
			|| !is_bool( $claim['traceability_complete'] ?? null )
			|| !is_array( $claim['review'] ?? null )
		) {
			throw new RuntimeException( 'A candidate claim is missing required schema fields.' );
		}
		if ( $claim['claim_type'] === 'relationship' ) {
			if ( !self::validEntityRef( $claim['object'] ?? null ) || array_key_exists( 'value', $claim ) ) {
				throw new RuntimeException( 'A relationship claim has invalid semantic fields.' );
			}
		} elseif ( !self::nonEmptyString( $claim['value'] ?? null )
			|| array_key_exists( 'object', $claim )
		) {
			throw new RuntimeException( 'A property claim has invalid semantic fields.' );
		}
		self::onlyKeys( $claim['review'], [ 'status' ] );
		if ( ( $claim['review']['status'] ?? null ) !== 'pending' ) {
			throw new RuntimeException( 'A candidate claim is not pending review.' );
		}

		$seenEvidenceIds = [];
		$hasPageOnlyEvidence = false;
		foreach ( $claim['evidence'] as $evidence ) {
			self::validateEvidence( $evidence, $metadata, $rawPages, $sourcePages );
			if ( isset( $seenEvidenceIds[$evidence['evidence_id']] ) ) {
				throw new RuntimeException( 'A claim contains duplicate evidence identities.' );
			}
			$seenEvidenceIds[$evidence['evidence_id']] = true;
			$hasPageOnlyEvidence = $hasPageOnlyEvidence
				|| $evidence['location_type'] === 'page';
		}
		if ( $claim['traceability_complete'] && $hasPageOnlyEvidence ) {
			throw new RuntimeException( 'Page-only evidence cannot be marked traceable.' );
		}
	}

	/**
	 * @param mixed $evidence
	 * @param array<string,mixed> $metadata
	 * @param array<string,array<string,mixed>> $rawPages
	 * @param array<string,array<string,mixed>> $sourcePages
	 */
	private static function validateEvidence( $evidence, array $metadata, array $rawPages, array $sourcePages ) {
		if ( !is_array( $evidence ) ) {
			throw new RuntimeException( 'Claim evidence is not an object.' );
		}
		self::onlyKeys( $evidence, [
			'evidence_id', 'page_id', 'revision_id', 'content_hash', 'page_title',
			'extraction_method', 'extractor_version', 'location_type', 'location',
			'source_offset_start', 'source_offset_end', 'supporting_span', 'span_hash'
		] );
		if ( !self::isHash( $evidence['evidence_id'] ?? null )
			|| !self::positiveInt( $evidence['page_id'] ?? null )
			|| !self::positiveInt( $evidence['revision_id'] ?? null )
			|| !self::isHash( $evidence['content_hash'] ?? null )
			|| !self::nonEmptyString( $evidence['page_title'] ?? null )
			|| !in_array( $evidence['extraction_method'] ?? null, [ 'rules', 'ai' ], true )
			|| $evidence['extraction_method'] !== $metadata['extraction_method']
			|| !self::nonEmptyString( $evidence['extractor_version'] ?? null )
			|| $evidence['extractor_version'] !== $metadata['extractor_version']
			|| !in_array( $evidence['location_type'] ?? null, [ 'source_span', 'page_title', 'page' ], true )
		) {
			throw new RuntimeException( 'Claim evidence is missing required schema fields.' );
		}

		$key = self::pageKey( $evidence['page_title'] );
		$rawPage = $rawPages[$key] ?? null;
		$source = $sourcePages[$key] ?? null;
		if ( $rawPage === null || $source === null
			|| !self::samePageIdentity( $evidence, $rawPage )
			|| !self::samePageIdentity( $evidence, $source )
		) {
			throw new RuntimeException( 'Claim evidence does not match a pinned raw page.' );
		}

		$locationType = $evidence['location_type'];
		if ( $locationType === 'source_span' ) {
			if ( !is_int( $evidence['source_offset_start'] ?? null )
				|| !is_int( $evidence['source_offset_end'] ?? null )
				|| !is_string( $evidence['supporting_span'] ?? null )
				|| $evidence['supporting_span'] === ''
				|| !self::isHash( $evidence['span_hash'] ?? null )
				|| ( array_key_exists( 'location', $evidence )
					&& !is_string( $evidence['location'] ) )
			) {
				throw new RuntimeException( 'Source-span evidence is missing its coordinates or quote.' );
			}
			$start = $evidence['source_offset_start'];
			$end = $evidence['source_offset_end'];
			$spanLength = mb_strlen( $evidence['supporting_span'], 'UTF-8' );
			$sourceLength = mb_strlen( $rawPage['wikitext'], 'UTF-8' );
			if ( $start < 0 || $end <= $start || $end > $sourceLength
				|| $spanLength > self::MAX_SPAN_CHARS || $spanLength !== $end - $start
				|| !mb_check_encoding( $evidence['supporting_span'], 'UTF-8' )
				|| mb_substr( $rawPage['wikitext'], $start, $end - $start, 'UTF-8' )
					!== $evidence['supporting_span']
				|| !hash_equals(
					$evidence['span_hash'],
					hash( 'sha256', $evidence['supporting_span'] )
				)
			) {
				throw new RuntimeException( 'Source-span evidence does not match raw wikitext.' );
			}
		} elseif ( $locationType === 'page_title' ) {
			if ( !is_string( $evidence['location'] ?? null )
				|| $evidence['location'] !== $evidence['page_title']
				|| self::hasSpanFields( $evidence )
			) {
				throw new RuntimeException( 'Page-title evidence does not match its pinned title.' );
			}
		} elseif ( array_key_exists( 'location', $evidence ) || self::hasSpanFields( $evidence ) ) {
			throw new RuntimeException( 'Page-only evidence cannot contain a fabricated quote.' );
		}
	}

	/**
	 * @param array<string,mixed> $document
	 * @param stdClass $documentObject
	 */
	private static function addSnapshotIds( array &$document, stdClass $documentObject ) {
		if ( !isset( $documentObject->claims ) || !is_array( $documentObject->claims ) ) {
			throw new RuntimeException( 'Candidate claims cannot be assigned stable snapshots.' );
		}
		foreach ( $document['claims'] as $index => &$claim ) {
			$claimObject = $documentObject->claims[$index] ?? null;
			if ( !( $claimObject instanceof stdClass )
				|| !isset( $claimObject->evidence ) || !is_array( $claimObject->evidence )
			) {
				throw new RuntimeException( 'Candidate claim semantic data is invalid.' );
			}

			$semantic = new stdClass();
			foreach ( [ 'claim_type', 'subject', 'predicate', 'qualifiers' ] as $field ) {
				$semantic->$field = $claimObject->$field;
			}
			if ( $claimObject->claim_type === 'relationship' ) {
				$semantic->object = $claimObject->object;
			} else {
				$semantic->value = $claimObject->value;
			}

			$evidence = $claimObject->evidence;
			usort( $evidence, static function ( $left, $right ) {
				return strcmp(
					self::canonicalJson( $left ),
					self::canonicalJson( $right )
				);
			} );
			$snapshot = new stdClass();
			$snapshot->semantic = $semantic;
			$snapshot->evidence = $evidence;
			$claim['snapshot_id'] = hash( 'sha256', self::canonicalJson( $snapshot ) );
		}
		unset( $claim );
	}

	/**
	 * Canonical JSON keeps object keys sorted recursively while preserving arrays.
	 *
	 * @param mixed $value
	 * @return string
	 */
	private static function canonicalJson( $value ) {
		$flags = JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_PRESERVE_ZERO_FRACTION;
		if ( $value instanceof stdClass ) {
			$properties = get_object_vars( $value );
			$keys = array_keys( $properties );
			sort( $keys, SORT_STRING );
			$parts = [];
			foreach ( $keys as $key ) {
				$encodedKey = json_encode( (string)$key, $flags | JSON_THROW_ON_ERROR );
				$parts[] = $encodedKey . ':' . self::canonicalJson( $properties[$key] );
			}
			return '{' . implode( ',', $parts ) . '}';
		}
		if ( is_array( $value ) ) {
			$parts = [];
			foreach ( $value as $item ) {
				$parts[] = self::canonicalJson( $item );
			}
			return '[' . implode( ',', $parts ) . ']';
		}
		return json_encode( $value, $flags | JSON_THROW_ON_ERROR );
	}

	/** @param mixed $value */
	private static function validEntityRef( $value ) {
		return is_array( $value )
			&& in_array( $value['type'] ?? null, [ 'Crop', 'Variety', 'Pest' ], true )
			&& self::nonEmptyString( $value['id'] ?? null )
			&& count( $value ) === 2;
	}

	/** @param array<string,mixed> $value */
	private static function samePageIdentity( array $value, array $page ) {
		return ( $value['title'] ?? $value['page_title'] ?? null ) === $page['title']
			&& ( $value['page_id'] ?? null ) === $page['page_id']
			&& ( $value['revision_id'] ?? null ) === $page['revision_id']
			&& ( $value['content_hash'] ?? null ) === $page['content_hash'];
	}

	/** @param array<string,mixed> $evidence */
	private static function hasSpanFields( array $evidence ) {
		foreach ( [
			'source_offset_start', 'source_offset_end', 'supporting_span', 'span_hash'
		] as $field ) {
			if ( array_key_exists( $field, $evidence ) ) {
				return true;
			}
		}
		return false;
	}

	/** @param array<string,mixed> $data @param string[] $allowed */
	private static function onlyKeys( array $data, array $allowed ) {
		foreach ( array_keys( $data ) as $key ) {
			if ( !in_array( $key, $allowed, true ) ) {
				throw new RuntimeException( 'Candidate claim contains an unsupported field.' );
			}
		}
	}

	/** @param mixed $value */
	private static function nonEmptyString( $value ) {
		return is_string( $value ) && trim( $value ) !== '';
	}

	/** @param mixed $value */
	private static function positiveInt( $value ) {
		return is_int( $value ) && $value > 0;
	}

	/** @param mixed $value */
	private static function nonNegativeInt( $value ) {
		return is_int( $value ) && $value >= 0;
	}

	/** @param mixed $value */
	private static function isHash( $value ) {
		return is_string( $value ) && preg_match( '/\A[a-f0-9]{64}\z/', $value ) === 1;
	}

	private static function pageKey( $title ) {
		return "\0" . $title;
	}
}
