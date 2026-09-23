<?php

namespace MediaWiki\Extension\WikiKGExtractor;

use InvalidArgumentException;
use JsonException;
use UnexpectedValueException;
use Wikimedia\Rdbms\IDatabase;
use Wikimedia\Rdbms\IConnectionProvider;

/**
 * Stores immutable, revision-pinned candidate snapshots and append-only decisions.
 *
 * This store does not publish approved claims to a graph or write to Neo4j.
 */
final class ClaimReviewStore {
	private const MAX_SOURCE_PAGES = 100;
	private const MAX_CLAIMS = 500;
	private const MAX_EVIDENCE_PER_CLAIM = 20;
	private const MAX_TOTAL_EVIDENCE = 10000;
	private const MAX_SNAPSHOT_METADATA_BYTES = 65530;
	private const MAX_SEMANTIC_BYTES = 65530;
	private const MAX_SPAN_BYTES = 40000;
	private const MAX_REASON_BYTES = 4096;
	private const MAX_TEXT_BYTES = 4096;
	private const MAX_TITLE_BYTES = 512;
	private const MAX_SNAPSHOT_PAYLOAD_BYTES = 5242880;
	private const MAX_OFFSET = 2147483647;
	private const MAX_DB_UINT = 4294967295;

	private IConnectionProvider $connectionProvider;

	public function __construct( IConnectionProvider $connectionProvider ) {
		$this->connectionProvider = $connectionProvider;
	}

	/**
	 * Import every verified claim as its own immutable, revision-pinned snapshot.
	 * Re-importing the same IDs and data is a no-op; reusing an ID for changed data
	 * is rejected. The complete document is one transaction.
	 *
	 * @param array<string,mixed> $verifiedDocument
	 * @return string[] Snapshot IDs in input claim order
	 * @throws InvalidArgumentException If the document is invalid or out of bounds
	 * @throws UnexpectedValueException If the snapshot ID is already bound to other data
	 */
	public function importVerified( array $verifiedDocument ): array {
		$snapshots = $this->validateDocument( $verifiedDocument );
		$dbw = $this->connectionProvider->getPrimaryDatabase();

		return $dbw->doAtomicSection(
			__METHOD__,
			function ( IDatabase $db ) use ( $snapshots ) {
				$ids = [];
				foreach ( $snapshots as $snapshot ) {
					$existing = $db->newSelectQueryBuilder()
						->select( [ 'wikikg_snapshot_hash' ] )
						->from( 'wikikg_snapshot' )
						->where( [ 'wikikg_snapshot_id' => $snapshot['id'] ] )
						->fetchRow();

					if ( $existing ) {
						$this->assertSameSnapshot( $existing, $snapshot );
						$ids[] = $snapshot['id'];
						continue;
					}

					$db->newInsertQueryBuilder()
						->insertInto( 'wikikg_snapshot' )
						->row( $snapshot['row'] )
						->ignore()
						->caller( __METHOD__ )
						->execute();

					if ( $db->affectedRows() !== 1 ) {
						// A parallel import may have inserted this snapshot after our read.
						$existing = $db->newSelectQueryBuilder()
							->select( [ 'wikikg_snapshot_hash' ] )
							->from( 'wikikg_snapshot' )
							->where( [ 'wikikg_snapshot_id' => $snapshot['id'] ] )
							->forUpdate()
							->fetchRow();
						if ( !$existing ) {
							throw new UnexpectedValueException(
								'Could not create or load the claim snapshot.'
							);
						}
						$this->assertSameSnapshot( $existing, $snapshot );
						$ids[] = $snapshot['id'];
						continue;
					}

					$this->insertRows( $db, 'wikikg_source_page', $snapshot['sources'] );
					$this->insertRows( $db, 'wikikg_claim', [ $snapshot['claim'] ] );
					$this->insertRows( $db, 'wikikg_evidence', $snapshot['evidence'] );
					$ids[] = $snapshot['id'];
				}

				return $ids;
			},
			IDatabase::ATOMIC_CANCELABLE
		);
	}

	/**
	 * Append a decision for one claim snapshot if its current version matches.
	 *
	 * @param string $snapshotId
	 * @param int $reviewerId Positive MediaWiki user ID
	 * @param string $status approved or rejected
	 * @param string $reason
	 * @param int $expectedVersion Current version expected by the caller; initially 0
	 * @return array<string,mixed>|null New event, or null if the snapshot is missing or stale
	 */
	public function recordDecision(
		string $snapshotId,
		int $reviewerId,
		string $status,
		string $reason,
		int $expectedVersion
	): ?array {
		$this->assertSnapshotId( $snapshotId );
		if ( $reviewerId < 1 || $reviewerId > self::MAX_DB_UINT ) {
			throw new InvalidArgumentException( 'Reviewer ID is out of bounds.' );
		}
		if ( !in_array( $status, [ 'approved', 'rejected' ], true ) ) {
			throw new InvalidArgumentException( 'Decision status must be approved or rejected.' );
		}
		$this->assertText( $reason, 'Decision reason', self::MAX_REASON_BYTES, true );
		if ( $expectedVersion < 0 || $expectedVersion >= self::MAX_OFFSET ) {
			throw new InvalidArgumentException( 'Expected review version is out of bounds.' );
		}

		$dbw = $this->connectionProvider->getPrimaryDatabase();
		return $dbw->doAtomicSection(
			__METHOD__,
			function ( IDatabase $db ) use (
				$snapshotId,
				$reviewerId,
				$status,
				$reason,
				$expectedVersion
			) {
				$snapshotExists = $db->newSelectQueryBuilder()
					->select( [ 'wikikg_snapshot_id' ] )
					->from( 'wikikg_snapshot' )
					->where( [ 'wikikg_snapshot_id' => $snapshotId ] )
					->fetchRow();
				if ( !$snapshotExists ) {
					return null;
				}

				$latest = $db->newSelectQueryBuilder()
					->select( [ 'wikikg_version' ] )
					->from( 'wikikg_review_event' )
					->where( [ 'wikikg_snapshot_id' => $snapshotId ] )
					->orderBy( 'wikikg_version', 'DESC' )
					->limit( 1 )
					->fetchRow();
				$currentVersion = $latest ? (int)$latest->wikikg_version : 0;
				if ( $currentVersion !== $expectedVersion ) {
					return null;
				}

				$event = [
					'wikikg_snapshot_id' => $snapshotId,
					'wikikg_version' => $expectedVersion + 1,
					'wikikg_reviewer_id' => $reviewerId,
					'wikikg_status' => $status,
					'wikikg_reason' => $reason,
					'wikikg_timestamp' => wfTimestampNow(),
				];
				$db->newInsertQueryBuilder()
					->insertInto( 'wikikg_review_event' )
					->row( $event )
					->ignore()
					->caller( __METHOD__ )
					->execute();

				// The unique (snapshot, version) key also arbitrates concurrent requests.
				if ( $db->affectedRows() !== 1 ) {
					return null;
				}

				return $this->formatEvent( (object)$event );
			},
			IDatabase::ATOMIC_CANCELABLE
		);
	}

	/**
	 * Read one immutable claim snapshot and its decision history.
	 * The returned fields are the original claim, plus snapshot_id, source metadata,
	 * latest_review, and review_events.
	 * The latest review state is separate from the original pending claim.
	 *
	 * @return array<string,mixed>|null
	 */
	public function getReview( string $snapshotId ): ?array {
		$this->assertSnapshotId( $snapshotId );
		// Review reads must reflect a write made earlier in the same request.
		$dbr = $this->connectionProvider->getPrimaryDatabase();
		$snapshot = $dbr->newSelectQueryBuilder()
			->select( [
				'wikikg_snapshot_id',
				'wikikg_snapshot_hash',
				'wikikg_schema_version',
				'wikikg_metadata',
				'wikikg_created',
				'wikikg_source_count',
			] )
			->from( 'wikikg_snapshot' )
			->where( [ 'wikikg_snapshot_id' => $snapshotId ] )
			->fetchRow();
		if ( !$snapshot ) {
			return null;
		}

		$metadata = $this->decodeJson( $snapshot->wikikg_metadata );
		$sourcePages = [];
		foreach ( $dbr->newSelectQueryBuilder()
			->select( [
				'wikikg_page_id',
				'wikikg_revision_id',
				'wikikg_content_hash',
				'wikikg_page_title',
				'wikikg_kind',
			] )
			->from( 'wikikg_source_page' )
			->where( [ 'wikikg_snapshot_id' => $snapshotId ] )
			->orderBy( [ 'wikikg_page_id ASC', 'wikikg_revision_id ASC' ] )
			->fetchResultSet() as $row
		) {
			$source = [
				'title' => (string)$row->wikikg_page_title,
				'page_id' => (int)$row->wikikg_page_id,
				'revision_id' => (int)$row->wikikg_revision_id,
				'content_hash' => (string)$row->wikikg_content_hash,
			];
			if ( (string)$row->wikikg_kind !== '' ) {
				$source['kind'] = (string)$row->wikikg_kind;
			}
			$sourcePages[] = $source;
		}
		$claimRow = $dbr->newSelectQueryBuilder()
			->select( [
				'wikikg_claim_id',
				'wikikg_semantic_data',
				'wikikg_traceability_complete',
			] )
			->from( 'wikikg_claim' )
			->where( [ 'wikikg_snapshot_id' => $snapshotId ] )
			->fetchRow();
		if ( !$claimRow ) {
			throw new UnexpectedValueException( 'Stored snapshot has no claim.' );
		}
		$claimId = (string)$claimRow->wikikg_claim_id;
		$claim = $this->decodeJson( $claimRow->wikikg_semantic_data );
		$claim['claim_id'] = $claimId;
		$claim['traceability_complete'] = (bool)$claimRow->wikikg_traceability_complete;
		$claim['evidence'] = [];
		$claim['review'] = [ 'status' => 'pending' ];

		foreach ( $dbr->newSelectQueryBuilder()
			->select( [
				'wikikg_claim_id',
				'wikikg_evidence_id',
				'wikikg_page_id',
				'wikikg_revision_id',
				'wikikg_content_hash',
				'wikikg_page_title',
				'wikikg_extraction_method',
				'wikikg_extractor_version',
				'wikikg_location_type',
				'wikikg_location',
				'wikikg_offset_start',
				'wikikg_offset_end',
				'wikikg_supporting_span',
				'wikikg_span_hash',
			] )
			->from( 'wikikg_evidence' )
			->where( [ 'wikikg_snapshot_id' => $snapshotId ] )
			->orderBy( [ 'wikikg_claim_id ASC', 'wikikg_evidence_id ASC' ] )
			->fetchResultSet() as $row
		) {
			$claimId = (string)$row->wikikg_claim_id;
			if ( $claimId !== (string)$claimRow->wikikg_claim_id ) {
				throw new UnexpectedValueException( 'Stored evidence has no parent claim.' );
			}
			$evidence = [
				'evidence_id' => (string)$row->wikikg_evidence_id,
				'page_id' => (int)$row->wikikg_page_id,
				'revision_id' => (int)$row->wikikg_revision_id,
				'content_hash' => (string)$row->wikikg_content_hash,
				'page_title' => (string)$row->wikikg_page_title,
				'extraction_method' => (string)$row->wikikg_extraction_method,
				'extractor_version' => (string)$row->wikikg_extractor_version,
				'location_type' => (string)$row->wikikg_location_type,
			];
			if ( $row->wikikg_location !== null ) {
				$evidence['location'] = (string)$row->wikikg_location;
			}
			if ( $row->wikikg_offset_start !== null ) {
				$evidence['source_offset_start'] = (int)$row->wikikg_offset_start;
			}
			if ( $row->wikikg_offset_end !== null ) {
				$evidence['source_offset_end'] = (int)$row->wikikg_offset_end;
			}
			if ( $row->wikikg_supporting_span !== null ) {
				$evidence['supporting_span'] = (string)$row->wikikg_supporting_span;
			}
			if ( $row->wikikg_span_hash !== null ) {
				$evidence['span_hash'] = (string)$row->wikikg_span_hash;
			}
			$claim['evidence'][] = $evidence;
		}

		$events = [];
		foreach ( $dbr->newSelectQueryBuilder()
			->select( [
				'wikikg_version',
				'wikikg_reviewer_id',
				'wikikg_status',
				'wikikg_reason',
				'wikikg_timestamp',
			] )
			->from( 'wikikg_review_event' )
			->where( [ 'wikikg_snapshot_id' => $snapshotId ] )
			->orderBy( 'wikikg_version', 'ASC' )
			->fetchResultSet() as $row
		) {
			$events[] = $this->formatEvent( $row, $snapshotId );
		}

		$metadata['source_pages'] = $sourcePages;
		return array_merge( $claim, [
			'snapshot_id' => $snapshotId,
			'snapshot_hash' => (string)$snapshot->wikikg_snapshot_hash,
			'schema_version' => (string)$snapshot->wikikg_schema_version,
			'metadata' => $metadata,
			'created_at' => (string)$snapshot->wikikg_created,
			'source_count' => (int)$snapshot->wikikg_source_count,
			'latest_review' => $this->latestReviewFromEvents( $snapshotId, $events ),
			'review_events' => $events,
		] );
	}

	/**
	 * Return current status and version for one snapshot; a new snapshot is pending at version 0.
	 *
	 * @return array<string,mixed>|null
	 */
	public function getLatestReview( string $snapshotId ): ?array {
		$this->assertSnapshotId( $snapshotId );
		$dbr = $this->connectionProvider->getPrimaryDatabase();
		$snapshot = $dbr->newSelectQueryBuilder()
			->select( [ 'wikikg_snapshot_id' ] )
			->from( 'wikikg_snapshot' )
			->where( [ 'wikikg_snapshot_id' => $snapshotId ] )
			->fetchRow();
		if ( !$snapshot ) {
			return null;
		}

		$event = $dbr->newSelectQueryBuilder()
			->select( [
				'wikikg_version',
				'wikikg_reviewer_id',
				'wikikg_status',
				'wikikg_reason',
				'wikikg_timestamp',
			] )
			->from( 'wikikg_review_event' )
			->where( [ 'wikikg_snapshot_id' => $snapshotId ] )
			->orderBy( 'wikikg_version', 'DESC' )
			->limit( 1 )
			->fetchRow();

		return $event
			? $this->formatEvent( $event, $snapshotId )
			: [
				'snapshot_id' => $snapshotId,
				'version' => 0,
				'reviewer_id' => null,
				'status' => 'pending',
				'reason' => null,
				'timestamp' => null,
			];
	}

	/**
	 * List per-claim snapshots newest-first, with each claim and its current review.
	 *
	 * @return array<int,array<string,mixed>>
	 */
	public function listSnapshots( int $limit = 50, int $offset = 0 ): array {
		if ( $limit < 1 || $limit > 100 || $offset < 0 || $offset > 1000000 ) {
			throw new InvalidArgumentException( 'Snapshot list bounds are invalid.' );
		}
		$dbr = $this->connectionProvider->getPrimaryDatabase();
		$rows = $dbr->newSelectQueryBuilder()
			->select( [
				'wikikg_snapshot_id',
				'wikikg_snapshot_hash',
				'wikikg_schema_version',
				'wikikg_metadata',
				'wikikg_created',
				'wikikg_source_count',
			] )
			->from( 'wikikg_snapshot' )
			->orderBy( [ 'wikikg_created DESC', 'wikikg_snapshot_id DESC' ] )
			->limit( $limit )
			->offset( $offset )
			->fetchResultSet();

		$snapshots = [];
		$snapshotIds = [];
		foreach ( $rows as $row ) {
			$id = (string)$row->wikikg_snapshot_id;
			$snapshotIds[] = $id;
			$snapshots[$id] = [
				'snapshot_id' => $id,
				'snapshot_hash' => (string)$row->wikikg_snapshot_hash,
				'schema_version' => (string)$row->wikikg_schema_version,
				'metadata' => $this->decodeJson( $row->wikikg_metadata ),
				'created_at' => (string)$row->wikikg_created,
				'claim_count' => 1,
				'source_count' => (int)$row->wikikg_source_count,
			];
		}
		if ( !$snapshotIds ) {
			return [];
		}

		foreach ( $dbr->newSelectQueryBuilder()
			->select( [
				'wikikg_snapshot_id',
				'wikikg_claim_id',
				'wikikg_semantic_data',
				'wikikg_traceability_complete',
			] )
			->from( 'wikikg_claim' )
			->where( [ 'wikikg_snapshot_id' => $snapshotIds ] )
			->fetchResultSet() as $row
		) {
			$id = (string)$row->wikikg_snapshot_id;
			if ( !isset( $snapshots[$id] ) ) {
				continue;
			}
			$claim = $this->decodeJson( $row->wikikg_semantic_data );
			$claim['claim_id'] = (string)$row->wikikg_claim_id;
			$claim['traceability_complete'] = (bool)$row->wikikg_traceability_complete;
			$claim['review'] = [ 'status' => 'pending' ];
			$snapshots[$id]['claim_id'] = (string)$row->wikikg_claim_id;
			$snapshots[$id]['claim'] = $claim;
		}

		foreach ( $dbr->newSelectQueryBuilder()
			->select( [
				'wikikg_snapshot_id',
				'wikikg_version',
				'wikikg_reviewer_id',
				'wikikg_status',
				'wikikg_reason',
				'wikikg_timestamp',
			] )
			->from( 'wikikg_review_event' )
			->where( [ 'wikikg_snapshot_id' => $snapshotIds ] )
			->orderBy( [ 'wikikg_snapshot_id ASC', 'wikikg_version DESC' ] )
			->fetchResultSet() as $row
		) {
			$id = (string)$row->wikikg_snapshot_id;
			if ( $snapshots[$id]['latest_review'] ?? false ) {
				continue;
			}
			$snapshots[$id]['latest_review'] = $this->formatEvent( $row, $id );
		}

		foreach ( $snapshots as &$snapshot ) {
			$snapshot['latest_review'] ??= [
				'snapshot_id' => $snapshot['snapshot_id'],
				'version' => 0,
				'reviewer_id' => null,
				'status' => 'pending',
				'reason' => null,
				'timestamp' => null,
			];
		}
		unset( $snapshot );

		return array_values( $snapshots );
	}

	/** @param IDatabase $db @param array<int,array<string,mixed>> $rows */
	private function insertRows( IDatabase $db, string $table, array $rows ): void {
		foreach ( $rows as $row ) {
			$db->newInsertQueryBuilder()
				->insertInto( $table )
				->row( $row )
				->caller( __METHOD__ )
				->execute();
		}
	}

	/** @param object $existing @param array<string,mixed> $snapshot */
	private function assertSameSnapshot( $existing, array $snapshot ): void {
		if ( !hash_equals( (string)$existing->wikikg_snapshot_hash, $snapshot['hash'] ) ) {
			throw new UnexpectedValueException(
				'Snapshot ID collision: the same ID was submitted with different data.'
			);
		}
	}

	/** @return array<int,array<string,mixed>> */
	private function validateDocument( array $document ): array {
		if ( array_key_exists( 'snapshot_id', $document ) ) {
			throw new InvalidArgumentException( 'Snapshot IDs belong to individual claims.' );
		}
		if ( strlen( $this->encodeJson( $document ) ) > self::MAX_SNAPSHOT_PAYLOAD_BYTES ) {
			throw new InvalidArgumentException( 'Verified document exceeds the payload limit.' );
		}
		if ( ( $document['schema_version'] ?? null ) !== '1.0' ) {
			throw new InvalidArgumentException( 'Unsupported candidate-claims schema version.' );
		}

		$metadata = $document['metadata'] ?? null;
		if ( !is_array( $metadata )
			|| !in_array( $metadata['extraction_method'] ?? null, [ 'rules', 'ai' ], true )
			|| ( $metadata['review_state'] ?? null ) !== 'pending'
		) {
			throw new InvalidArgumentException( 'Candidate metadata is invalid or not pending.' );
		}
		$metadataForStorage = [
			'extractor_version' => $this->assertText(
				$metadata['extractor_version'] ?? null,
				'Extractor version',
				64
			),
			'extraction_method' => $metadata['extraction_method'],
			'review_state' => 'pending',
		];
		$metadataJson = $this->encodeJson( $metadataForStorage );
		if ( strlen( $metadataJson ) > self::MAX_SNAPSHOT_METADATA_BYTES ) {
			throw new InvalidArgumentException( 'Snapshot metadata is too large.' );
		}

		$sourcePages = $metadata['source_pages'] ?? null;
		if ( !is_array( $sourcePages ) || !array_is_list( $sourcePages )
			|| count( $sourcePages ) > self::MAX_SOURCE_PAGES
		) {
			throw new InvalidArgumentException( 'Source page list is invalid or too large.' );
		}
		$sourceIndex = [];
		foreach ( $sourcePages as $source ) {
			if ( !is_array( $source ) ) {
				throw new InvalidArgumentException( 'Source page must be an object.' );
			}
			$title = $this->assertText(
				$source['title'] ?? null,
				'Source page title',
				self::MAX_TITLE_BYTES
			);
			$pageId = $this->assertPositiveInteger( $source['page_id'] ?? null, 'Page ID' );
			$revisionId = $this->assertPositiveInteger(
				$source['revision_id'] ?? null,
				'Revision ID'
			);
			$contentHash = $this->assertHash( $source['content_hash'] ?? null, 'Content hash' );
			$kind = '';
			if ( array_key_exists( 'kind', $source ) ) {
				if ( !in_array( $source['kind'], [ 'species', 'variety' ], true ) ) {
					throw new InvalidArgumentException( 'Source page kind is invalid.' );
				}
				$kind = $source['kind'];
			}
			$sourceKey = $pageId . ':' . $revisionId;
			if ( isset( $sourceIndex[$sourceKey] ) ) {
				throw new InvalidArgumentException( 'Source page revision is duplicated.' );
			}
			$sourceData = [
				'title' => $title,
				'page_id' => $pageId,
				'revision_id' => $revisionId,
				'content_hash' => $contentHash,
			];
			if ( $kind !== '' ) {
				$sourceData['kind'] = $kind;
			}
			$sourceIndex[$sourceKey] = $sourceData;
		}

		$claims = $document['claims'] ?? null;
		if ( !is_array( $claims ) || !array_is_list( $claims ) || count( $claims ) > self::MAX_CLAIMS ) {
			throw new InvalidArgumentException( 'Claim list is invalid or too large.' );
		}
		$snapshots = [];
		$claimIds = [];
		$snapshotIds = [];
		$totalEvidence = 0;
		$totalPayloadBytes = strlen( $metadataJson );
		foreach ( $claims as $claim ) {
			if ( !is_array( $claim ) ) {
				throw new InvalidArgumentException( 'Claim must be an object.' );
			}
			$this->assertAllowedKeys(
				$claim,
				[
					'claim_id', 'snapshot_id', 'claim_type', 'subject', 'predicate',
					'object', 'value', 'qualifiers', 'evidence',
					'traceability_complete', 'review',
				],
				'Claim'
			);
			$snapshotId = $this->assertHash( $claim['snapshot_id'] ?? null, 'Snapshot ID' );
			$claimId = $this->assertHash( $claim['claim_id'] ?? null, 'Claim ID' );
			if ( isset( $claimIds[$claimId] ) || isset( $snapshotIds[$snapshotId] ) ) {
				throw new InvalidArgumentException( 'Candidate claim or snapshot ID is duplicated.' );
			}
			$claimIds[$claimId] = true;
			$snapshotIds[$snapshotId] = true;
			$snapshots[] = $this->validateClaimSnapshot(
				$claim,
				$snapshotId,
				$metadataForStorage,
				$sourceIndex,
				$totalEvidence,
				$totalPayloadBytes
			);
		}

		if ( $totalEvidence > self::MAX_TOTAL_EVIDENCE
			|| $totalPayloadBytes > self::MAX_SNAPSHOT_PAYLOAD_BYTES
		) {
			throw new InvalidArgumentException( 'Verified claim payload is too large.' );
		}
		return $snapshots;
	}

	/** @return array<string,mixed> */
	private function validateClaimSnapshot(
		array $claim,
		string $snapshotId,
		array $metadata,
		array $sourceIndex,
		int &$totalEvidence,
		int &$totalPayloadBytes
	): array {
		$claimId = $this->assertHash( $claim['claim_id'] ?? null, 'Claim ID' );
		$claimType = $claim['claim_type'] ?? null;
		if ( !in_array( $claimType, [ 'relationship', 'property' ], true ) ) {
			throw new InvalidArgumentException( 'Claim type is invalid.' );
		}
		$semantic = [
			'claim_type' => $claimType,
			'subject' => $this->validateEntity( $claim['subject'] ?? null, 'Claim subject' ),
			'predicate' => $this->assertText(
				$claim['predicate'] ?? null,
				'Claim predicate',
				255
			),
		];
		$qualifiers = $claim['qualifiers'] ?? null;
		if ( !is_array( $qualifiers ) || ( $qualifiers && array_is_list( $qualifiers ) ) ) {
			throw new InvalidArgumentException( 'Claim qualifiers must be an object.' );
		}
		$semantic['qualifiers'] = $qualifiers ?: (object)[];
		if ( strlen( $this->encodeJson( $semantic['qualifiers'] ) ) > 16384 ) {
			throw new InvalidArgumentException( 'Claim qualifiers are too large.' );
		}

		$hasObject = array_key_exists( 'object', $claim );
		$hasValue = array_key_exists( 'value', $claim );
		if ( $hasObject === $hasValue || ( $claimType === 'relationship' ) !== $hasObject ) {
			throw new InvalidArgumentException( 'Claim type does not match its object/value shape.' );
		}
		if ( $hasObject ) {
			$semantic['object'] = $this->validateEntity( $claim['object'], 'Claim object' );
		} else {
			$semantic['value'] = $this->assertText(
				$claim['value'],
				'Claim value',
				self::MAX_TEXT_BYTES
			);
		}
		if ( !is_bool( $claim['traceability_complete'] ?? null ) ) {
			throw new InvalidArgumentException( 'Claim traceability flag is invalid.' );
		}
		if ( !is_array( $claim['review'] ?? null )
			|| array_keys( $claim['review'] ) !== [ 'status' ]
			|| $claim['review']['status'] !== 'pending'
		) {
			throw new InvalidArgumentException( 'Imported claims must be pending.' );
		}
		$semanticJson = $this->encodeJson( $semantic );
		if ( strlen( $semanticJson ) > self::MAX_SEMANTIC_BYTES ) {
			throw new InvalidArgumentException( 'Claim semantic data is too large.' );
		}
		$totalPayloadBytes += strlen( $semanticJson );

		$evidenceList = $claim['evidence'] ?? null;
		if ( !is_array( $evidenceList ) || !array_is_list( $evidenceList )
			|| !$evidenceList || count( $evidenceList ) > self::MAX_EVIDENCE_PER_CLAIM
		) {
			throw new InvalidArgumentException( 'Claim evidence list is invalid or too large.' );
		}

		$evidenceIds = [];
		$claimSources = [];
		$evidenceRows = [];
		$evidenceIdentity = [];
		foreach ( $evidenceList as $evidence ) {
			if ( !is_array( $evidence ) ) {
				throw new InvalidArgumentException( 'Evidence must be an object.' );
			}
			$this->assertAllowedKeys(
				$evidence,
				[
					'evidence_id', 'page_id', 'revision_id', 'content_hash', 'page_title',
					'extraction_method', 'extractor_version', 'location_type', 'location',
					'source_offset_start', 'source_offset_end', 'supporting_span', 'span_hash',
				],
				'Evidence'
			);
			$evidenceId = $this->assertHash( $evidence['evidence_id'] ?? null, 'Evidence ID' );
			if ( isset( $evidenceIds[$evidenceId] ) ) {
				throw new InvalidArgumentException( 'Evidence ID is duplicated for a claim.' );
			}
			$evidenceIds[$evidenceId] = true;
			$pageId = $this->assertPositiveInteger( $evidence['page_id'] ?? null, 'Evidence page ID' );
			$revisionId = $this->assertPositiveInteger(
				$evidence['revision_id'] ?? null,
				'Evidence revision ID'
			);
			$contentHash = $this->assertHash( $evidence['content_hash'] ?? null, 'Evidence content hash' );
			$pageTitle = $this->assertText(
				$evidence['page_title'] ?? null,
				'Evidence page title',
				self::MAX_TITLE_BYTES
			);
			$sourceKey = $pageId . ':' . $revisionId;
			$source = $sourceIndex[$sourceKey] ?? null;
			if ( $source === null || $source['content_hash'] !== $contentHash
				|| $source['title'] !== $pageTitle
			) {
				throw new InvalidArgumentException( 'Evidence must match a source page in this document.' );
			}
			$method = $evidence['extraction_method'] ?? null;
			if ( $method !== $metadata['extraction_method'] ) {
				throw new InvalidArgumentException( 'Evidence extraction method does not match metadata.' );
			}
			$evidenceVersion = $this->assertText(
				$evidence['extractor_version'] ?? null,
				'Evidence extractor version',
				64
			);
			if ( $evidenceVersion !== $metadata['extractor_version'] ) {
				throw new InvalidArgumentException( 'Evidence extractor version does not match metadata.' );
			}
			$locationType = $evidence['location_type'] ?? null;
			if ( !in_array( $locationType, [ 'source_span', 'page_title', 'page' ], true ) ) {
				throw new InvalidArgumentException( 'Evidence location type is invalid.' );
			}
			$location = null;
			if ( array_key_exists( 'location', $evidence ) ) {
				$location = $this->assertText(
					$evidence['location'],
					'Evidence location',
					self::MAX_TITLE_BYTES,
					true
				);
			}
			$offsetStart = null;
			$offsetEnd = null;
			$span = null;
			$spanHash = null;
			if ( $locationType === 'source_span' ) {
				$offsetStart = $this->assertOffset(
					$evidence['source_offset_start'] ?? null,
					'Source offset start'
				);
				$offsetEnd = $this->assertOffset(
					$evidence['source_offset_end'] ?? null,
					'Source offset end'
				);
				$span = $this->assertText(
					$evidence['supporting_span'] ?? null,
					'Supporting span',
					self::MAX_SPAN_BYTES
				);
				$spanHash = $this->assertHash( $evidence['span_hash'] ?? null, 'Span hash' );
				if ( !function_exists( 'mb_strlen' )
					|| $offsetEnd <= $offsetStart
					|| $offsetEnd - $offsetStart !== mb_strlen( $span, 'UTF-8' )
					|| !hash_equals( $spanHash, hash( 'sha256', $span ) )
				) {
					throw new InvalidArgumentException( 'Source span coordinates or hash are invalid.' );
				}
			} elseif ( $locationType === 'page_title' ) {
				if ( $location !== $pageTitle || $this->hasSpanFields( $evidence ) ) {
					throw new InvalidArgumentException( 'Page-title evidence is invalid.' );
				}
			} elseif ( $location !== null || $this->hasSpanFields( $evidence ) ) {
				throw new InvalidArgumentException( 'Page-only evidence cannot contain a fabricated quote.' );
			}

			$claimSources[$sourceKey] = $source;
			$evidenceRows[] = [
				'wikikg_snapshot_id' => $snapshotId,
				'wikikg_claim_id' => $claimId,
				'wikikg_evidence_id' => $evidenceId,
				'wikikg_page_id' => $pageId,
				'wikikg_revision_id' => $revisionId,
				'wikikg_content_hash' => $contentHash,
				'wikikg_page_title' => $pageTitle,
				'wikikg_extraction_method' => $method,
				'wikikg_extractor_version' => $evidenceVersion,
				'wikikg_location_type' => $locationType,
				'wikikg_location' => $location,
				'wikikg_offset_start' => $offsetStart,
				'wikikg_offset_end' => $offsetEnd,
				'wikikg_supporting_span' => $span,
				'wikikg_span_hash' => $spanHash,
			];
			$evidenceIdentity[] = $evidence;
			$totalEvidence++;
			$totalPayloadBytes += strlen( $this->encodeJson( $evidence ) );
		}

		usort( $evidenceIdentity, function ( $left, $right ) {
			return strcmp( $this->canonicalJson( $left ), $this->canonicalJson( $right ) );
		} );
		ksort( $claimSources, SORT_STRING );
		$sourceRows = [];
		$sourceIdentity = [];
		foreach ( $claimSources as $source ) {
			$sourceIdentity[] = $source;
			$sourceRows[] = [
				'wikikg_snapshot_id' => $snapshotId,
				'wikikg_page_id' => $source['page_id'],
				'wikikg_revision_id' => $source['revision_id'],
				'wikikg_content_hash' => $source['content_hash'],
				'wikikg_page_title' => $source['title'],
				'wikikg_kind' => $source['kind'] ?? '',
			];
			$totalPayloadBytes += strlen( $source['title'] );
		}

		$identity = [
			'schema_version' => '1.0',
			'metadata' => $metadata,
			'claim_id' => $claimId,
			'semantic' => $semantic,
			'traceability_complete' => $claim['traceability_complete'],
			'evidence' => $evidenceIdentity,
			'source_pages' => $sourceIdentity,
		];
		$snapshotHash = hash( 'sha256', $this->canonicalJson( $identity ) );
		$totalPayloadBytes += strlen( $semanticJson );
		if ( $totalPayloadBytes > self::MAX_SNAPSHOT_PAYLOAD_BYTES ) {
			throw new InvalidArgumentException( 'Verified claim payload is too large.' );
		}

		return [
			'id' => $snapshotId,
			'hash' => $snapshotHash,
			'row' => [
				'wikikg_snapshot_id' => $snapshotId,
				'wikikg_snapshot_hash' => $snapshotHash,
				'wikikg_schema_version' => '1.0',
				'wikikg_metadata' => $this->encodeJson( $metadata ),
				'wikikg_created' => wfTimestampNow(),
				'wikikg_claim_count' => 1,
				'wikikg_source_count' => count( $sourceRows ),
			],
			'sources' => $sourceRows,
			'claim' => [
				'wikikg_snapshot_id' => $snapshotId,
				'wikikg_claim_id' => $claimId,
				'wikikg_semantic_data' => $semanticJson,
				'wikikg_traceability_complete' => (int)$claim['traceability_complete'],
			],
			'evidence' => $evidenceRows,
		];
	}

	private function validateEntity( $entity, string $label ): array {
		if ( !is_array( $entity )
			|| !in_array( $entity['type'] ?? null, [ 'Crop', 'Variety', 'Pest' ], true )
		) {
			throw new InvalidArgumentException( "$label is invalid." );
		}
		$this->assertAllowedKeys( $entity, [ 'type', 'id' ], $label );
		return [
			'type' => $entity['type'],
			'id' => $this->assertText( $entity['id'] ?? null, "$label ID", 512 ),
		];
	}

	private function assertAllowedKeys( array $value, array $allowed, string $label ): void {
		if ( array_diff( array_keys( $value ), $allowed ) ) {
			throw new InvalidArgumentException( "$label contains unsupported fields." );
		}
	}

	/** @param array<string,mixed> $evidence */
	private function hasSpanFields( array $evidence ): bool {
		foreach ( [
			'source_offset_start', 'source_offset_end', 'supporting_span', 'span_hash',
		] as $field ) {
			if ( array_key_exists( $field, $evidence ) ) {
				return true;
			}
		}
		return false;
	}

	private function assertSnapshotId( string $snapshotId ): void {
		$this->assertHash( $snapshotId, 'Snapshot ID' );
	}

	private function assertHash( $value, string $label ): string {
		if ( !is_string( $value ) || !preg_match( '/^[a-f0-9]{64}$/D', $value ) ) {
			throw new InvalidArgumentException( "$label must be a lowercase SHA-256 hex string." );
		}
		return $value;
	}

	private function assertPositiveInteger( $value, string $label ): int {
		if ( !is_int( $value ) || $value < 1 || $value > self::MAX_DB_UINT ) {
			throw new InvalidArgumentException( "$label is out of bounds." );
		}
		return $value;
	}

	private function assertOffset( $value, string $label ): int {
		if ( !is_int( $value ) || $value < 0 || $value > self::MAX_OFFSET ) {
			throw new InvalidArgumentException( "$label is out of bounds." );
		}
		return $value;
	}

	private function assertText(
		$value,
		string $label,
		int $maxBytes,
		bool $allowEmpty = false
	): string {
		if ( !is_string( $value ) || ( !$allowEmpty && trim( $value ) === '' )
			|| strlen( $value ) > $maxBytes
			|| preg_match( '//u', $value ) !== 1
		) {
			throw new InvalidArgumentException( "$label is empty or out of bounds." );
		}
		return $value;
	}

	/** @throws JsonException */
	private function encodeJson( $value ): string {
		return json_encode(
			$value,
			JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR
		);
	}

	/** @throws JsonException */
	private function decodeJson( $json ): array {
		$value = json_decode( (string)$json, true, 512, JSON_THROW_ON_ERROR );
		if ( !is_array( $value ) ) {
			throw new UnexpectedValueException( 'Stored JSON value is not an object.' );
		}
		return $value;
	}

	/**
	 * Stable JSON used only for same-ID collision detection, not as the upstream snapshot-ID scheme.
	 *
	 * @throws JsonException
	 */
	private function canonicalJson( $value ): string {
		if ( $value instanceof \stdClass ) {
			$properties = get_object_vars( $value );
			ksort( $properties, SORT_STRING );
			return '{' . implode( ', ', array_map( function ( $key ) use ( $properties ) {
				return $this->encodeJson( (string)$key ) . ': '
					. $this->canonicalJson( $properties[$key] );
			}, array_keys( $properties ) ) ) . '}';
		}
		if ( is_array( $value ) ) {
			if ( array_is_list( $value ) ) {
				return '[' . implode( ', ', array_map( function ( $item ) {
					return $this->canonicalJson( $item );
				}, $value ) ) . ']';
			}
			ksort( $value, SORT_STRING );
			$entries = [];
			foreach ( $value as $key => $item ) {
				$entries[] = $this->encodeJson( (string)$key ) . ': '
					. $this->canonicalJson( $item );
			}
			return '{' . implode( ', ', $entries ) . '}';
		}
		return $this->encodeJson( $value );
	}

	private function formatEvent( $row, ?string $snapshotId = null ): array {
		return [
			'snapshot_id' => $snapshotId ?? (string)$row->wikikg_snapshot_id,
			'version' => (int)$row->wikikg_version,
			'reviewer_id' => (int)$row->wikikg_reviewer_id,
			'status' => (string)$row->wikikg_status,
			'reason' => (string)$row->wikikg_reason,
			'timestamp' => (string)$row->wikikg_timestamp,
		];
	}

	/** @param array<int,array<string,mixed>> $events */
	private function latestReviewFromEvents( string $snapshotId, array $events ): array {
		if ( $events ) {
			return $events[count( $events ) - 1];
		}
		return [
			'snapshot_id' => $snapshotId,
			'version' => 0,
			'reviewer_id' => null,
			'status' => 'pending',
			'reason' => null,
			'timestamp' => null,
		];
	}
}
