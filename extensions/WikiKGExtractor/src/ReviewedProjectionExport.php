<?php

namespace MediaWiki\Extension\WikiKGExtractor;

use InvalidArgumentException;
use Closure;
use JsonException;
use MediaWiki\Permissions\Authority;
use RuntimeException;
use UnexpectedValueException;

/**
 * Builds a complete, current, authority-scoped export of reviewed claim snapshots.
 */
final class ReviewedProjectionExport {
	private const SNAPSHOT_PAGE_SIZE = 100;
	private const HASH_LENGTH = 64;
	private const MAX_DB_UINT = 4294967295;
	private const MAX_OFFSET = 2147483647;
	private const MAX_QUALIFIERS_BYTES = 16384;

	private ClaimReviewStore $store;
	private ?Closure $scopeAssertion;

	/**
	 * @param callable(array<string,mixed>):void|null $scopeAssertion Optional fail-closed assertion
	 */
	public function __construct( ClaimReviewStore $store, ?callable $scopeAssertion = null ) {
		$this->store = $store;
		$this->scopeAssertion = $scopeAssertion === null
			? null
			: Closure::fromCallable( $scopeAssertion );
	}

	/**
	 * Export approved, traceable snapshots whose cited revisions are current and readable.
	 *
	 * @return array<string,mixed>
	 * @throws JsonException
	 * @throws RuntimeException If the store changes or contains malformed data
	 */
	public function export( Authority $authority ): array {
		if ( !$authority->isAllowed( 'wikikgextractor-review' ) ) {
			throw new RuntimeException( 'Reviewed projection export requires the review right.' );
		}
		$stateTuples = [];
		$statesById = [];
		$readabilityById = [];
		$claimsById = [];
		$afterId = null;

		while ( true ) {
			$snapshotIds = $this->store->listSnapshotIdsAfter(
				$afterId,
				self::SNAPSHOT_PAGE_SIZE
			);
			if ( !$snapshotIds ) {
				break;
			}
			if ( count( $snapshotIds ) > self::SNAPSHOT_PAGE_SIZE ) {
				throw new UnexpectedValueException( 'Snapshot keyset page exceeds its requested size.' );
			}

			foreach ( $snapshotIds as $snapshotId ) {
				$this->assertHash( $snapshotId, 'Snapshot ID' );
				if ( $afterId !== null && strcmp( $snapshotId, $afterId ) <= 0 ) {
					throw new UnexpectedValueException( 'Snapshot keyset order is invalid.' );
				}
				$afterId = $snapshotId;

				$review = $this->store->getReviewForProjection( $snapshotId );
				if ( $review === null ) {
					throw new UnexpectedValueException( 'A listed snapshot disappeared during export.' );
				}
				[ $status, $version ] = $this->validateReviewRecord( $review, $snapshotId );
				$tuple = [ $snapshotId, $status, $version ];
				$stateTuples[] = $tuple;
				$statesById[$snapshotId] = $tuple;

				if ( $status !== 'approved' || !$review['traceability_complete'] ) {
					continue;
				}

				$canRead = ClaimEvidenceAccess::canReadCurrent(
					$authority,
					$review['evidence']
				);
				$readabilityById[$snapshotId] = $canRead;
				if ( $canRead ) {
					$claim = $this->projectionClaim( $review );
					$this->assertScope( $claim );
					$claimsById[$snapshotId] = $claim;
				}
			}

			if ( count( $snapshotIds ) < self::SNAPSHOT_PAGE_SIZE ) {
				break;
			}
		}

		foreach ( $readabilityById as $snapshotId => $wasReadable ) {
			$review = $this->store->getReviewForProjection( $snapshotId );
			if ( $review === null ) {
				throw new UnexpectedValueException( 'An eligible snapshot disappeared during export.' );
			}
			[ $status, $version ] = $this->validateReviewRecord( $review, $snapshotId );
			if ( [ $snapshotId, $status, $version ] !== $statesById[$snapshotId] ) {
				throw new RuntimeException( 'Review state changed during export.' );
			}

			$canRead = ClaimEvidenceAccess::canReadCurrent(
				$authority,
				$review['evidence']
			);
			if ( $canRead !== $wasReadable ) {
				throw new RuntimeException( 'Evidence access changed during export.' );
			}
			if ( $wasReadable ) {
				$claim = $this->projectionClaim( $review );
				$this->assertScope( $claim );
				if ( $this->encodeJson( $claim ) !== $this->encodeJson( $claimsById[$snapshotId] ) ) {
					throw new RuntimeException( 'An eligible claim changed during export.' );
				}
			}
		}

		$currentTuples = $this->readCurrentStateTuples();
		if ( $currentTuples !== $stateTuples ) {
			throw new RuntimeException( 'Snapshot state changed during export.' );
		}

		ksort( $claimsById, SORT_STRING );
		return [
			'schema_version' => 'wikikg.reviewed-projection.v1',
			'snapshot_count' => count( $stateTuples ),
			'source_watermark' => $this->watermark( $stateTuples ),
			'claims' => array_values( $claimsById ),
		];
	}

	/**
	 * Validate and project one approved record for a caller that separately checks authority
	 * and fresh source access. Shares the export's authoritative-record validation.
	 * @return array<string,mixed>
	 */
	public function projectApprovedClaim( array $review ): array {
		$id = $review['snapshot_id'] ?? null;
		$this->assertHash( $id, 'Snapshot ID' );
		[ $status ] = $this->validateReviewRecord( $review, $id );
		if ( $status !== 'approved' || !$review['traceability_complete'] ) {
			throw new RuntimeException( 'Claim is not approved and traceable.' );
		}
		$claim = $this->projectionClaim( $review );
		$this->assertScope( $claim );
		return $claim;
	}

	/** Current primary-store decision watermark, without reading any source content. */
	public function currentWatermark(): string {
		return $this->watermark( $this->readCurrentStateTuples() );
	}

	/**
	 * Write one complete export to a new owner-only file outside both public roots.
	 *
	 * @return array<string,mixed> The document written to disk
	 * @throws InvalidArgumentException If the destination is not private and safe
	 * @throws JsonException
	 * @throws RuntimeException If exporting or writing fails
	 */
	public function exportToFile(
		Authority $authority,
		string $outputPath,
		string $repositoryRoot,
		string $documentRoot
	): array {
		$destination = $this->assertPrivateDestination(
			$outputPath,
			$repositoryRoot,
			$documentRoot
		);

		$document = $this->export( $authority );
		$json = json_encode(
			$document,
			JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_PRETTY_PRINT | JSON_THROW_ON_ERROR
		) . "\n";
		if ( $this->assertPrivateDestination( $outputPath, $repositoryRoot, $documentRoot ) !== $destination ) {
			throw new RuntimeException( 'Output directory changed during export.' );
		}
		$this->writeAtomically( $destination, $json );

		return $document;
	}

	/**
	 * @param array<string,mixed> $review
	 * @return array{0:string,1:int}
	 */
	private function validateReviewRecord( array $review, string $snapshotId ): array {
		$requiredKeys = [
			'snapshot_id', 'snapshot_hash', 'claim_id', 'claim_type', 'subject', 'predicate',
			'qualifiers', 'traceability_complete', 'evidence', 'review', 'schema_version',
			'metadata', 'created_at', 'source_count', 'latest_review', 'review_events',
		];
		$allowedKeys = array_merge( $requiredKeys, [ 'object', 'value' ] );
		$this->assertExactKeys( $review, $requiredKeys, $allowedKeys, 'Stored claim' );

		if ( $review['snapshot_id'] !== $snapshotId ) {
			throw new UnexpectedValueException( 'Stored snapshot ID does not match its key.' );
		}
		$this->assertHash( $review['snapshot_hash'], 'Snapshot hash' );
		$this->assertHash( $review['claim_id'], 'Claim ID' );
		if ( $review['schema_version'] !== '1.0'
			|| !is_string( $review['created_at'] )
			|| !preg_match( '/^[0-9]{14}$/D', $review['created_at'] )
		) {
			throw new UnexpectedValueException( 'Stored snapshot metadata is invalid.' );
		}
		if ( !is_bool( $review['traceability_complete'] ) ) {
			throw new UnexpectedValueException( 'Stored traceability flag is invalid.' );
		}

		$this->assertEntity( $review['subject'], 'Claim subject' );
		$this->assertText( $review['predicate'], 255, 'Claim predicate' );
		if ( !( $review['qualifiers'] instanceof \stdClass )
			|| strlen( $this->encodeJson( $review['qualifiers'] ) ) > self::MAX_QUALIFIERS_BYTES
		) {
			throw new UnexpectedValueException( 'Stored claim qualifiers are invalid.' );
		}

		$claimType = $review['claim_type'];
		if ( $claimType === 'property' ) {
			if ( array_key_exists( 'object', $review ) || !array_key_exists( 'value', $review ) ) {
				throw new UnexpectedValueException( 'Stored property claim shape is invalid.' );
			}
			$this->assertText( $review['value'], 4096, 'Claim value' );
		} elseif ( $claimType === 'relationship' ) {
			if ( array_key_exists( 'value', $review ) || !array_key_exists( 'object', $review ) ) {
				throw new UnexpectedValueException( 'Stored relationship claim shape is invalid.' );
			}
			$this->assertEntity( $review['object'], 'Claim object' );
		} else {
			throw new UnexpectedValueException( 'Stored claim type is invalid.' );
		}

		if ( !is_array( $review['review'] )
			|| array_keys( $review['review'] ) !== [ 'status' ]
			|| $review['review']['status'] !== 'pending'
		) {
			throw new UnexpectedValueException( 'Stored original claim review state is invalid.' );
		}
		$this->validateMetadata( $review['metadata'], $review['source_count'] );
		$this->validateEvidence( $review['evidence'], $review['metadata'] );

		$events = $review['review_events'];
		if ( !is_array( $events ) || !array_is_list( $events ) ) {
			throw new UnexpectedValueException( 'Stored review history is invalid.' );
		}
		$expectedVersion = 0;
		foreach ( $events as $event ) {
			$this->assertExactKeys(
				$event,
				[ 'snapshot_id', 'version', 'reviewer_id', 'status', 'reason', 'timestamp' ],
				[ 'snapshot_id', 'version', 'reviewer_id', 'status', 'reason', 'timestamp' ],
				'Stored review event'
			);
			$expectedVersion++;
			if ( $event['snapshot_id'] !== $snapshotId
				|| $event['version'] !== $expectedVersion
				|| !is_int( $event['reviewer_id'] )
				|| $event['reviewer_id'] < 1 || $event['reviewer_id'] > self::MAX_DB_UINT
				|| !in_array( $event['status'], [ 'approved', 'rejected' ], true )
				|| !is_string( $event['reason'] )
				|| strlen( $event['reason'] ) > 4096
				|| preg_match( '//u', $event['reason'] ) !== 1
				|| !is_string( $event['timestamp'] )
				|| !preg_match( '/^[0-9]{14}$/D', $event['timestamp'] )
			) {
				throw new UnexpectedValueException( 'Stored review event is malformed.' );
			}
		}

		$latest = $review['latest_review'];
		$this->assertExactKeys(
			$latest,
			[ 'snapshot_id', 'version', 'reviewer_id', 'status', 'reason', 'timestamp' ],
			[ 'snapshot_id', 'version', 'reviewer_id', 'status', 'reason', 'timestamp' ],
			'Stored latest review state'
		);
		if ( $expectedVersion === 0 ) {
			$expectedLatest = [
				'snapshot_id' => $snapshotId,
				'version' => 0,
				'reviewer_id' => null,
				'status' => 'pending',
				'reason' => null,
				'timestamp' => null,
			];
		} else {
			$expectedLatest = $events[count( $events ) - 1];
		}
		if ( $latest !== $expectedLatest ) {
			throw new UnexpectedValueException( 'Stored latest review state is inconsistent.' );
		}
		[ $status, $version ] = $this->stateFromLatestReview( $latest, $snapshotId );
		if ( $version !== $expectedVersion ) {
			throw new UnexpectedValueException( 'Stored review versions are not consecutive.' );
		}

		return [ $status, $version ];
	}

	/**
	 * @param mixed $metadata
	 * @param mixed $sourceCount
	 */
	private function validateMetadata( $metadata, $sourceCount ): void {
		$this->assertExactKeys(
			$metadata,
			[ 'extractor_version', 'extraction_method', 'review_state', 'source_pages' ],
			[ 'extractor_version', 'extraction_method', 'review_state', 'source_pages' ],
			'Stored snapshot metadata'
		);
		$this->assertText( $metadata['extractor_version'], 64, 'Extractor version' );
		if ( !in_array( $metadata['extraction_method'], [ 'rules', 'ai' ], true )
			|| $metadata['review_state'] !== 'pending'
			|| !is_int( $sourceCount )
			|| $sourceCount < 1
			|| !is_array( $metadata['source_pages'] )
			|| !array_is_list( $metadata['source_pages'] )
			|| count( $metadata['source_pages'] ) > 100
			|| count( $metadata['source_pages'] ) !== $sourceCount
		) {
			throw new UnexpectedValueException( 'Stored source metadata is invalid.' );
		}

		$sourceKeys = [];
		foreach ( $metadata['source_pages'] as $source ) {
			$this->assertExactKeys(
				$source,
				[ 'title', 'page_id', 'revision_id', 'content_hash' ],
				[ 'title', 'page_id', 'revision_id', 'content_hash', 'kind' ],
				'Stored source page'
			);
			$this->assertText( $source['title'], 512, 'Source page title' );
			$this->assertDatabaseId( $source['page_id'], 'Source page ID' );
			$this->assertDatabaseId( $source['revision_id'], 'Source revision ID' );
			$this->assertHash( $source['content_hash'], 'Source content hash' );
			if ( isset( $source['kind'] )
				&& !in_array( $source['kind'], [ 'species', 'variety' ], true )
			) {
				throw new UnexpectedValueException( 'Stored source page kind is invalid.' );
			}
			$sourceKey = $source['page_id'] . ':' . $source['revision_id'];
			if ( isset( $sourceKeys[$sourceKey] ) ) {
				throw new UnexpectedValueException( 'Stored source page is duplicated.' );
			}
			$sourceKeys[$sourceKey] = $source;
		}
	}

	/**
	 * @param mixed $evidenceItems
	 * @param array<string,mixed> $metadata
	 */
	private function validateEvidence( $evidenceItems, array $metadata ): void {
		if ( !is_array( $evidenceItems ) || !$evidenceItems || !array_is_list( $evidenceItems )
			|| count( $evidenceItems ) > 20
		) {
			throw new UnexpectedValueException( 'Stored evidence list is invalid.' );
		}
		$sources = [];
		foreach ( $metadata['source_pages'] as $source ) {
			$sources[$source['page_id'] . ':' . $source['revision_id']] = $source;
		}

		$evidenceIds = [];
		foreach ( $evidenceItems as $evidence ) {
			$this->assertExactKeys(
				$evidence,
				[
					'evidence_id', 'page_id', 'revision_id', 'content_hash', 'page_title',
					'extraction_method', 'extractor_version', 'location_type',
				],
				[
					'evidence_id', 'page_id', 'revision_id', 'content_hash', 'page_title',
					'extraction_method', 'extractor_version', 'location_type', 'location',
					'source_offset_start', 'source_offset_end', 'supporting_span', 'span_hash',
				],
				'Stored evidence'
			);
			$this->assertHash( $evidence['evidence_id'], 'Evidence ID' );
			if ( isset( $evidenceIds[$evidence['evidence_id']] ) ) {
				throw new UnexpectedValueException( 'Stored evidence ID is duplicated.' );
			}
			$evidenceIds[$evidence['evidence_id']] = true;
			$this->assertDatabaseId( $evidence['page_id'], 'Evidence page ID' );
			$this->assertDatabaseId( $evidence['revision_id'], 'Evidence revision ID' );
			$this->assertHash( $evidence['content_hash'], 'Evidence content hash' );
			$this->assertText( $evidence['page_title'], 512, 'Evidence page title' );
			if ( $evidence['extraction_method'] !== $metadata['extraction_method']
				|| $evidence['extractor_version'] !== $metadata['extractor_version']
			) {
				throw new UnexpectedValueException( 'Stored evidence extraction metadata is inconsistent.' );
			}

			$sourceKey = $evidence['page_id'] . ':' . $evidence['revision_id'];
			$source = $sources[$sourceKey] ?? null;
			if ( $source === null
				|| $evidence['content_hash'] !== $source['content_hash']
				|| $evidence['page_title'] !== $source['title']
			) {
				throw new UnexpectedValueException( 'Stored evidence does not match its source page.' );
			}

			$locationType = $evidence['location_type'];
			if ( $locationType === 'source_span' ) {
				foreach ( [ 'source_offset_start', 'source_offset_end', 'supporting_span', 'span_hash' ] as $key ) {
					if ( !array_key_exists( $key, $evidence ) ) {
						throw new UnexpectedValueException( 'Stored source-span evidence is incomplete.' );
					}
				}
				$start = $evidence['source_offset_start'];
				$end = $evidence['source_offset_end'];
				$span = $evidence['supporting_span'];
				$this->assertOffset( $start, 'Evidence offset start' );
				$this->assertOffset( $end, 'Evidence offset end' );
				$this->assertText( $span, 40000, 'Evidence span' );
				$this->assertHash( $evidence['span_hash'], 'Evidence span hash' );
				if ( $end <= $start
					|| !function_exists( 'mb_strlen' )
					|| $end - $start !== mb_strlen( $span, 'UTF-8' )
					|| !hash_equals( $evidence['span_hash'], hash( 'sha256', $span ) )
				) {
					throw new UnexpectedValueException( 'Stored source-span evidence is invalid.' );
				}
			} elseif ( $locationType === 'page_title' ) {
				if ( !array_key_exists( 'location', $evidence )
					|| $evidence['location'] !== $evidence['page_title']
				) {
					throw new UnexpectedValueException( 'Stored page-title evidence is invalid.' );
				}
				$this->assertNoSpanFields( $evidence );
			} elseif ( $locationType === 'page' ) {
				if ( array_key_exists( 'location', $evidence ) ) {
					throw new UnexpectedValueException( 'Stored page evidence contains a location.' );
				}
				$this->assertNoSpanFields( $evidence );
			} else {
				throw new UnexpectedValueException( 'Stored evidence location type is invalid.' );
			}

			if ( array_key_exists( 'location', $evidence ) ) {
				$this->assertText( $evidence['location'], 512, 'Evidence location', true );
			}
		}
	}

	/** @param array<string,mixed> $evidence */
	private function assertNoSpanFields( array $evidence ): void {
		foreach ( [ 'source_offset_start', 'source_offset_end', 'supporting_span', 'span_hash' ] as $key ) {
			if ( array_key_exists( $key, $evidence ) ) {
				throw new UnexpectedValueException( 'Stored non-span evidence contains span fields.' );
			}
		}
	}

	/** @param array<string,mixed> $review @return array<string,mixed> */
	private function projectionClaim( array $review ): array {
		$claim = [
			'snapshot_id' => $review['snapshot_id'],
			'snapshot_hash' => $review['snapshot_hash'],
			'claim_id' => $review['claim_id'],
			'review_version' => $review['latest_review']['version'],
			'claim_type' => $review['claim_type'],
			'subject' => $review['subject'],
			'predicate' => $review['predicate'],
			'qualifiers' => $review['qualifiers'],
			'evidence' => $review['evidence'],
		];
		if ( $review['claim_type'] === 'property' ) {
			$claim['value'] = $review['value'];
		} else {
			$claim['object'] = $review['object'];
		}
		return $claim;
	}

	/** @param array<string,mixed> $claim */
	private function assertScope( array $claim ): void {
		if ( $this->scopeAssertion !== null ) {
			( $this->scopeAssertion )( $claim );
		}
	}

	/** @return array<int,array{0:string,1:string,2:int}> */
	private function readCurrentStateTuples(): array {
		$tuples = [];
		$afterId = null;
		while ( true ) {
			$snapshotIds = $this->store->listSnapshotIdsAfter(
				$afterId,
				self::SNAPSHOT_PAGE_SIZE
			);
			if ( !$snapshotIds ) {
				break;
			}
			if ( count( $snapshotIds ) > self::SNAPSHOT_PAGE_SIZE ) {
				throw new UnexpectedValueException( 'Snapshot keyset page exceeds its requested size.' );
			}
			foreach ( $snapshotIds as $snapshotId ) {
				$this->assertHash( $snapshotId, 'Snapshot ID' );
				if ( $afterId !== null && strcmp( $snapshotId, $afterId ) <= 0 ) {
					throw new UnexpectedValueException( 'Snapshot keyset order is invalid.' );
				}
				$afterId = $snapshotId;
				$latest = $this->store->getLatestReview( $snapshotId );
				if ( $latest === null ) {
					throw new UnexpectedValueException( 'A listed snapshot disappeared during export.' );
				}
				[ $status, $version ] = $this->stateFromLatestReview( $latest, $snapshotId );
				$tuples[] = [ $snapshotId, $status, $version ];
			}
			if ( count( $snapshotIds ) < self::SNAPSHOT_PAGE_SIZE ) {
				break;
			}
		}
		return $tuples;
	}

	/** @param array<string,mixed> $latest @return array{0:string,1:int} */
	private function stateFromLatestReview( array $latest, string $snapshotId ): array {
		if ( ( $latest['snapshot_id'] ?? null ) !== $snapshotId
			|| !is_int( $latest['version'] ?? null )
			|| $latest['version'] < 0 || $latest['version'] > self::MAX_OFFSET
			|| !in_array( $latest['status'] ?? null, [ 'pending', 'approved', 'rejected' ], true )
		) {
			throw new UnexpectedValueException( 'Stored snapshot review state is malformed.' );
		}
		if ( ( $latest['version'] === 0 ) !== ( $latest['status'] === 'pending' ) ) {
			throw new UnexpectedValueException( 'Stored review status and version are inconsistent.' );
		}
		return [ $latest['status'], $latest['version'] ];
	}

	/**
	 * @param mixed $entity
	 * @param string $label
	 */
	private function assertEntity( $entity, string $label ): void {
		if ( $entity instanceof \stdClass ) {
			$entity = get_object_vars( $entity );
		}
		$this->assertExactKeys( $entity, [ 'type', 'id' ], [ 'type', 'id' ], $label );
		if ( !in_array( $entity['type'], [ 'Crop', 'Variety', 'Pest' ], true ) ) {
			throw new UnexpectedValueException( "$label type is invalid." );
		}
		$this->assertText( $entity['id'], 512, "$label ID" );
	}

	/**
	 * @param mixed $value
	 * @param string $label
	 */
	private function assertHash( $value, string $label ): void {
		if ( !is_string( $value ) || !preg_match( '/^[a-f0-9]{' . self::HASH_LENGTH . '}$/D', $value ) ) {
			throw new UnexpectedValueException( "$label is not a lowercase SHA-256 hash." );
		}
	}

	/**
	 * @param mixed $value
	 * @param string $label
	 */
	private function assertDatabaseId( $value, string $label ): void {
		if ( !is_int( $value ) || $value < 1 || $value > self::MAX_DB_UINT ) {
			throw new UnexpectedValueException( "$label is out of bounds." );
		}
	}

	/**
	 * @param mixed $value
	 * @param string $label
	 */
	private function assertOffset( $value, string $label ): void {
		if ( !is_int( $value ) || $value < 0 || $value > self::MAX_OFFSET ) {
			throw new UnexpectedValueException( "$label is out of bounds." );
		}
	}

	/**
	 * @param mixed $value
	 * @param int $maxBytes
	 * @param string $label
	 * @param bool $allowEmpty
	 */
	private function assertText( $value, int $maxBytes, string $label, bool $allowEmpty = false ): void {
		if ( !is_string( $value )
			|| ( !$allowEmpty && trim( $value ) === '' )
			|| strlen( $value ) > $maxBytes
			|| preg_match( '//u', $value ) !== 1
		) {
			throw new UnexpectedValueException( "$label is invalid." );
		}
	}

	/**
	 * @param mixed $value
	 * @param string[] $required
	 * @param string[] $allowed
	 * @param string $label
	 */
	private function assertExactKeys( $value, array $required, array $allowed, string $label ): void {
		if ( !is_array( $value )
			|| array_diff( $required, array_keys( $value ) )
			|| array_diff( array_keys( $value ), $allowed )
		) {
			throw new UnexpectedValueException( "$label fields are invalid." );
		}
	}

	/** @param array<int,array{0:string,1:string,2:int}> $tuples */
	private function watermark( array $tuples ): string {
		return hash( 'sha256', $this->encodeJson( $tuples ) );
	}

	/**
	 * @param mixed $value
	 * @throws JsonException
	 */
	private function encodeJson( $value ): string {
		return json_encode(
			$value,
			JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR
		);
	}

	private function assertPrivateDestination(
		string $outputPath,
		string $repositoryRoot,
		string $documentRoot
	): string {
		if ( PHP_OS_FAMILY === 'Windows' || !function_exists( 'posix_geteuid' ) ) {
			throw new InvalidArgumentException( 'Cannot verify private export ownership on this platform.' );
		}
		if ( $outputPath === '' || $outputPath[0] !== DIRECTORY_SEPARATOR ) {
			throw new InvalidArgumentException( 'Output path must be absolute.' );
		}
		$directory = realpath( dirname( $outputPath ) );
		$repo = realpath( $repositoryRoot );
		$docRoot = realpath( $documentRoot );
		if ( $directory === false || !is_dir( $directory )
			|| $repo === false || $docRoot === false
		) {
			throw new InvalidArgumentException( 'Output, repository, or document root path is invalid.' );
		}

		$destination = $directory . DIRECTORY_SEPARATOR . basename( $outputPath );
		if ( $destination === $directory
			|| $this->isWithinDirectory( $destination, $repo )
			|| $this->isWithinDirectory( $destination, $docRoot )
		) {
			throw new InvalidArgumentException( 'Output path must be outside the repository and document root.' );
		}
		if ( file_exists( $destination ) || is_link( $destination ) ) {
			throw new InvalidArgumentException( 'Output file already exists.' );
		}

		clearstatcache( true, $directory );
		$directoryMode = fileperms( $directory );
		$directoryOwner = fileowner( $directory );
		if ( $directoryMode === false || ( $directoryMode & 0077 ) !== 0
			|| $directoryOwner === false || $directoryOwner !== posix_geteuid()
		) {
			throw new InvalidArgumentException( 'Output directory must be private and owned by the current process.' );
		}
		return $destination;
	}

	private function isWithinDirectory( string $path, string $directory ): bool {
		if ( $directory === DIRECTORY_SEPARATOR ) {
			return true;
		}
		return $path === $directory
			|| str_starts_with( $path, rtrim( $directory, DIRECTORY_SEPARATOR ) . DIRECTORY_SEPARATOR );
	}

	private function writeAtomically( string $destination, string $json ): void {
		$directory = dirname( $destination );
		$temporaryPath = tempnam( $directory, '.wikikg-reviewed-' );
		if ( $temporaryPath === false ) {
			throw new RuntimeException( 'Could not create a private temporary export file.' );
		}

		try {
			if ( realpath( dirname( $temporaryPath ) ) !== $directory || !chmod( $temporaryPath, 0600 ) ) {
				throw new RuntimeException( 'Could not secure the temporary export file.' );
			}
			clearstatcache( true, $temporaryPath );
			$fileMode = fileperms( $temporaryPath );
			if ( $fileMode === false || ( $fileMode & 07777 ) !== 0600
				|| fileowner( $temporaryPath ) !== posix_geteuid()
			) {
				throw new RuntimeException( 'Could not verify private temporary export ownership and mode.' );
			}
			$handle = fopen( $temporaryPath, 'wb' );
			if ( $handle === false ) {
				throw new RuntimeException( 'Could not open the temporary export file.' );
			}
			try {
				$length = strlen( $json );
				$written = 0;
				while ( $written < $length ) {
					$count = fwrite( $handle, substr( $json, $written ) );
					if ( $count === false || $count === 0 ) {
						throw new RuntimeException( 'Could not write the complete export file.' );
					}
					$written += $count;
				}
				if ( !fflush( $handle ) || !fsync( $handle ) ) {
					throw new RuntimeException( 'Could not flush the complete export file.' );
				}
				if ( !fclose( $handle ) ) {
					$handle = false;
					throw new RuntimeException( 'Could not close the complete export file.' );
				}
				$handle = false;
			} finally {
				if ( $handle !== false ) {
					fclose( $handle );
				}
			}

			set_error_handler( static function () {
				throw new RuntimeException( 'Could not install the export without replacing a file.' );
			} );
			try {
				$linked = link( $temporaryPath, $destination );
			} finally {
				restore_error_handler();
			}
			if ( !$linked ) {
				throw new RuntimeException( 'Could not install the export without replacing a file.' );
			}
		} finally {
			if ( file_exists( $temporaryPath ) ) {
				unlink( $temporaryPath );
			}
		}
	}
}
