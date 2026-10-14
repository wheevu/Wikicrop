<?php

namespace MediaWiki\Extension\WikiKGExtractor;

use MediaWiki\MediaWikiServices;
use MediaWiki\Permissions\Authority;
use MediaWiki\Permissions\PermissionManager;
use MediaWiki\Revision\RevisionRecord;
use MediaWiki\Revision\SlotRecord;
use Throwable;
use Title;
use Wikimedia\Rdbms\IDBAccessObject;

/** Offline-only fresh authorization boundary for projected claim identities. */
final class CurrentEvidenceGate {
	private const MAX_RESPONSE_BYTES = 1048576;
	private const MAX_EVIDENCE = 50;
	private const MAX_SOURCE_BYTES = 5242880;
	private ClaimReviewStore $store;
	private ?OfflineDevelopmentScope $scope;
	private ReviewedProjectionExport $projector;
	private string $failureReason = 'authorization_refused';

	/** Null scope is reserved for a disposable synthetic caller. */
	public function __construct( ClaimReviewStore $store, ?OfflineDevelopmentScope $scope = null ) {
		$this->store = $store;
		$this->scope = $scope;
		$this->projector = new ReviewedProjectionExport( $store );
	}

	/** Content-free diagnostic for the local reviewer bridge; not an authorization result. */
	public function getFailureReason(): string {
		return $this->failureReason;
	}

	/**
	 * Resolve bounded projected identities from the primary store, never from index data.
	 * @param array<int,array{snapshot_id:string,snapshot_hash:string,review_version:int}> $candidates
	 * @return array{status:string,claims:array}
	 */
	public function authorize( Authority $authority, array $candidates, ?string $expectedWatermark = null ): array {
		$this->failureReason = 'authorization_refused';
		if ( !$authority->getUser()->isRegistered()
			|| !$authority->isAllowed( 'wikikgextractor-review' ) || !array_is_list( $candidates )
			|| count( $candidates ) > 100
		) {
			return $this->failure();
		}
		try {
			if ( $expectedWatermark !== null
				&& ( !preg_match( '/\A[a-f0-9]{64}\z/D', $expectedWatermark )
					|| !hash_equals( $expectedWatermark, $this->projector->currentWatermark() ) )
			) {
				$this->failureReason = 'export_watermark_changed';
				return $this->failure();
			}
			$expected = [];
			foreach ( $candidates as $candidate ) {
				if ( !is_array( $candidate ) || !$this->hasKeys( $candidate, [
					'snapshot_id', 'snapshot_hash', 'review_version'
				] ) || !is_string( $candidate['snapshot_id'] )
					|| !preg_match( '/^[a-f0-9]{64}$/D', $candidate['snapshot_id'] )
					|| !is_string( $candidate['snapshot_hash'] )
					|| !preg_match( '/^[a-f0-9]{64}$/D', $candidate['snapshot_hash'] )
					|| !is_int( $candidate['review_version'] ) || $candidate['review_version'] < 1
					|| isset( $expected[$candidate['snapshot_id']] )
				) {
					$this->failureReason = 'invalid_authorization_request';
					return $this->failure();
				}
				$expected[$candidate['snapshot_id']] = $candidate;
			}
			$claims = [];
			$evidenceCount = 0;
			foreach ( $expected as $id => $identity ) {
				$review = $this->store->getReviewForProjection( $id );
				if ( !$this->isCurrent( $review, $identity, $authority ) ) {
					return $this->failure();
				}
				$claims[] = $this->claim( $review );
				$evidenceCount += count( $review['evidence'] );
				if ( $evidenceCount > self::MAX_EVIDENCE
					|| strlen( $this->encode( $claims ) ) > self::MAX_RESPONSE_BYTES
				) {
					$this->failureReason = 'authorization_response_limit';
					return $this->failure();
				}
			}
			// Re-read immediately before returning; any state or content change denies all.
			foreach ( $claims as $claim ) {
				$identity = $expected[$claim['snapshot_id']];
				$review = $this->store->getReviewForProjection( $identity['snapshot_id'] );
				if ( !$this->isCurrent( $review, $identity, $authority ) ) {
					return $this->failure();
				}
				if ( $this->encode( $this->claim( $review ) ) !== $this->encode( $claim ) ) {
					$this->failureReason = 'source_evidence_changed';
					return $this->failure();
				}
			}
			if ( $expectedWatermark !== null
				&& !hash_equals( $expectedWatermark, $this->projector->currentWatermark() )
			) {
				$this->failureReason = 'export_watermark_changed';
				return $this->failure();
			}
			return [ 'status' => 'ok', 'claims' => $claims ];
		} catch ( Throwable $exception ) {
			$this->failureReason = 'authorization_dependency_failure';
			return $this->failure();
		}
	}

	/**
	 * Return source text only after fresh current revision, queue, visibility and hash checks.
	 * @param array<int,array{page_id:int,revision_id:int,content_hash:string}> $candidates
	 * @return array{status:string,text:array}
	 */
	public function authorizeText( Authority $authority, array $candidates ): array {
		$this->failureReason = 'authorization_refused';
		if ( !$authority->getUser()->isRegistered()
			|| !$authority->isAllowed( 'wikikgextractor-review' ) || !array_is_list( $candidates )
			|| count( $candidates ) > 100
		) {
			return $this->textFailure();
		}
		try {
			$expected = [];
			foreach ( $candidates as $candidate ) {
				if ( !is_array( $candidate ) || !$this->hasKeys( $candidate, [
					'page_id', 'revision_id', 'content_hash'
				] ) || !is_int( $candidate['page_id'] ) || $candidate['page_id'] < 1
					|| $candidate['page_id'] > 4294967295
					|| !is_int( $candidate['revision_id'] ) || $candidate['revision_id'] < 1
					|| $candidate['revision_id'] > 4294967295
					|| !is_string( $candidate['content_hash'] )
					|| !preg_match( '/^[a-f0-9]{64}$/D', $candidate['content_hash'] )
					|| isset( $expected[$candidate['page_id']] )
				) {
					$this->failureReason = 'invalid_authorization_request';
					return $this->textFailure();
				}
				$expected[$candidate['page_id']] = $candidate;
			}
			$text = [];
			foreach ( $expected as $candidate ) {
				$resolved = $this->currentText( $authority, $candidate );
				if ( $resolved === null ) {
					return $this->textFailure();
				}
				$text[] = $resolved;
				if ( strlen( $this->encode( $text ) ) > self::MAX_RESPONSE_BYTES ) {
					$this->failureReason = 'authorization_response_limit';
					return $this->textFailure();
				}
			}
			foreach ( $text as $resolved ) {
				$current = $this->currentText( $authority, $expected[$resolved['page_id']] );
				if ( $current === null ) {
					return $this->textFailure();
				}
				if ( $current !== $resolved ) {
					$this->failureReason = 'source_evidence_changed';
					return $this->textFailure();
				}
			}
			return [ 'status' => 'ok', 'text' => $text ];
		} catch ( Throwable $exception ) {
			$this->failureReason = 'authorization_dependency_failure';
			return $this->textFailure();
		}
	}

	/** @return array<string,mixed>|null */
	private function currentText( Authority $authority, array $identity ): ?array {
		$db = MediaWikiServices::getInstance()->getConnectionProvider()->getPrimaryDatabase();
		$rows = $db->newSelectQueryBuilder()
			->select( [ 'aq_revision_id', 'aq_status', 'aq_is_latest' ] )
			->from( 'approve_queue' )
			->where( [ 'aq_page_id' => $identity['page_id'], 'aq_is_latest' => 1 ] )
			->limit( 2 )
			->caller( __METHOD__ )
			->fetchResultSet();
		$latest = [];
		foreach ( $rows as $row ) {
			$latest[] = $row;
		}
		if ( count( $latest ) !== 1
			|| (int)$latest[0]->aq_revision_id !== $identity['revision_id']
			|| (string)$latest[0]->aq_status !== 'approved'
		) {
			$this->failureReason = 'source_approval_changed';
			return null;
		}
		$resolved = $this->currentSource( $authority, $identity );
		if ( $resolved === null ) {
			return null;
		}
		return [
			'page_id' => $identity['page_id'], 'revision_id' => $identity['revision_id'],
			'content_hash' => $identity['content_hash'], 'text' => $resolved['text'],
		];
	}

	private function isCurrent( ?array $review, array $identity, Authority $authority ): bool {
		if ( !$review || ( $review['snapshot_id'] ?? null ) !== $identity['snapshot_id']
			|| ( $review['snapshot_hash'] ?? null ) !== $identity['snapshot_hash']
			|| ( $review['latest_review']['status'] ?? null ) !== 'approved'
			|| ( $review['latest_review']['version'] ?? null ) !== $identity['review_version']
			|| ( $review['traceability_complete'] ?? false ) !== true
		) {
			$this->failureReason = 'claim_review_changed';
			return false;
		}
		$evidence = $review['evidence'] ?? null;
		if ( !ClaimEvidenceAccess::canRead( $authority, $evidence ) ) {
			return false;
		}
		// currentSource performs fresh primary revision checks for each readable item.
		foreach ( $evidence as $item ) {
			$resolved = $this->currentSource( $authority, $item );
			$evidenceTitle = is_string( $item['page_title'] ?? null )
				? Title::newFromText( $item['page_title'] ) : null;
			if ( $resolved === null || !$evidenceTitle
				|| $evidenceTitle->getPrefixedDBkey() !== $resolved['dbkey']
			) {
				return false;
			}
			if ( ( $item['location_type'] ?? null ) === 'source_span' ) {
				$start = $item['source_offset_start'] ?? null;
				$end = $item['source_offset_end'] ?? null;
				$span = $item['supporting_span'] ?? null;
				if ( !is_int( $start ) || !is_int( $end ) || $start < 0 || $end <= $start
					|| $end > mb_strlen( $resolved['text'], 'UTF-8' )
					|| !is_string( $span ) || !is_string( $item['span_hash'] ?? null )
					|| !preg_match( '/\A[a-f0-9]{64}\z/D', $item['span_hash'] )
					|| mb_substr( $resolved['text'], $start, $end - $start, 'UTF-8' ) !== $span
					|| !hash_equals( $item['span_hash'], hash( 'sha256', $span ) )
				) {
					$this->failureReason = 'source_evidence_changed';
					return false;
				}
			} elseif ( ( $item['location_type'] ?? null ) === 'page_title' ) {
				if ( ( $item['location'] ?? null ) !== $item['page_title'] ) {
					return false;
				}
			} elseif ( ( $item['location_type'] ?? null ) !== 'page' ) {
				return false;
			}
		}
		return true;
	}

	/** Read the pinned source only after fresh primary metadata and secure permission checks. */
	private function currentSource( Authority $authority, array $identity ): ?array {
		foreach ( [ 'page_id', 'revision_id' ] as $key ) {
			if ( !is_int( $identity[$key] ?? null ) || $identity[$key] < 1 || $identity[$key] > 4294967295 ) {
				return null;
			}
		}
		if ( !is_string( $identity['content_hash'] ?? null )
			|| !preg_match( '/\A[a-f0-9]{64}\z/D', $identity['content_hash'] )
		) {
			return null;
		}
		$services = MediaWikiServices::getInstance();
		$db = $services->getConnectionProvider()->getPrimaryDatabase();
		$metadata = $db->newSelectQueryBuilder()
			->select( [ 'page_latest', 'rev_deleted' ] )->from( 'page' )
			->join( 'revision', null, 'rev_id = page_latest' )
			->where( [ 'page_id' => $identity['page_id'], 'rev_id' => $identity['revision_id'] ] )
			->caller( __METHOD__ )->fetchRow();
		if ( !$metadata || (int)$metadata->page_latest !== $identity['revision_id']
			|| (int)$metadata->rev_deleted !== 0
		) {
			$this->failureReason = 'source_revision_changed';
			return null;
		}
		$page = $services->getPageStore()->getPageById( $identity['page_id'], IDBAccessObject::READ_LATEST );
		$title = $page ? Title::newFromPageIdentity( $page ) : null;
		if ( !$page || $page->getLatest() !== $identity['revision_id'] || !$title
			|| !$services->getPermissionManager()->userCan(
				'read', $authority->getUser(), $title, PermissionManager::RIGOR_SECURE
			)
		) {
			return null;
		}
		if ( $this->scope !== null ) {
			$this->scope->assertSource( [ 'title' => $title->getPrefixedText(),
				'page_id' => $identity['page_id'], 'revision_id' => $identity['revision_id'],
				'content_hash' => $identity['content_hash'] ] );
		}
		$revision = $services->getRevisionLookup()->getRevisionById(
			$identity['revision_id'], IDBAccessObject::READ_LATEST, $page
		);
		if ( !$revision || $revision->getPageId() !== $identity['page_id']
			|| !$revision->audienceCan( RevisionRecord::DELETED_TEXT, RevisionRecord::FOR_THIS_USER, $authority )
		) {
			return null;
		}
		$content = $revision->getContent( SlotRecord::MAIN );
		if ( !$content || $content->getModel() !== CONTENT_MODEL_WIKITEXT || !method_exists( $content, 'getText' ) ) {
			return null;
		}
		$text = $content->getText();
		if ( !is_string( $text ) || strlen( $text ) > self::MAX_SOURCE_BYTES || preg_match( '//u', $text ) !== 1 ) {
			return null;
		}
		if ( !hash_equals( $identity['content_hash'], hash( 'sha256', $text ) ) ) {
			$this->failureReason = 'source_content_changed';
			return null;
		}
		return [ 'title' => $title->getPrefixedText(), 'dbkey' => $title->getPrefixedDBkey(), 'text' => $text ];
	}

	private function hasKeys( array $value, array $keys ): bool {
		$actual = array_keys( $value );
		sort( $actual );
		sort( $keys );
		return $actual === $keys;
	}

	private function claim( array $review ): array {
		return $this->projector->projectApprovedClaim( $review );
	}

	private function encode( array $value ): string {
		return json_encode( $value, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR );
	}

	private function failure(): array {
		return [ 'status' => 'dependency_failure', 'claims' => [] ];
	}

	private function textFailure(): array {
		return [ 'status' => 'dependency_failure', 'text' => [] ];
	}
}
