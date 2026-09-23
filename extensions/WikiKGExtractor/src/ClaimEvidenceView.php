<?php

namespace MediaWiki\Extension\WikiKGExtractor;

use Html;
use SpecialPage;
use Title;

/**
 * Read-only, bounded server-side view of an already verified claim snapshot.
 */
class ClaimEvidenceView {
	private const MAX_RENDERED_CLAIMS = 100;
	private const MAX_RENDERED_EVIDENCE = 10;
	private const MAX_RENDERED_SPAN_CHARS = 2000;
	private const MAX_DISPLAY_CHARS = 240;
	private const MAX_LATEST_REVIEWS = 500;

	/**
	 * @param array<string,mixed> $verifiedDocument Result from ClaimDocument::load
	 * @param array<string,mixed> $latestReviews Latest review row, list of rows,
	 *		or map from snapshot_id to the latest persisted review
	 * @return string Safe HTML for a same-request, read-only review view
	 */
	public static function render( array $verifiedDocument, array $latestReviews = [], bool $canReview = false ): string {
		$allClaims = is_array( $verifiedDocument['claims'] ?? null )
			? array_values( array_filter( $verifiedDocument['claims'], 'is_array' ) )
			: [];
		$conflictGroups = self::findConflictGroups( $allClaims );
		$visibleIndexes = self::selectClaims( count( $allClaims ), $conflictGroups );
		$items = '';
		foreach ( $visibleIndexes as $index ) {
			$items .= self::renderClaim(
				$allClaims[$index],
				self::conflictForIndex( $index, $conflictGroups ),
				$latestReviews,
				$canReview
			);
		}

		$notice = 'Các claim được trích xuất ở trạng thái chờ duyệt; mỗi bằng chứng trỏ đến đúng trang '
			. 'và revision nguồn.';
		$html = Html::element( 'h4', [ 'class' => 'wikikg-subhead' ], 'Claim và bằng chứng' )
			. Html::element( 'p', [ 'class' => 'wikikg-note' ], $notice );
		if ( $allClaims ) {
			$html .= Html::rawElement(
				'div',
				[ 'class' => 'wikikg-claim-list' ],
				$items
			);
		} else {
			$html .= Html::element(
				'p',
				[ 'class' => 'wikikg-note' ],
				'Lần chạy này không tạo claim nào.'
			);
		}

		if ( count( $visibleIndexes ) < count( $allClaims ) ) {
			$html .= Html::element(
				'p',
				[ 'class' => 'wikikg-note' ],
				'Đang hiển thị ' . count( $visibleIndexes ) . ' trên '
					. count( $allClaims ) . ' claim.'
			);
		}

		return Html::rawElement( 'section', [ 'class' => 'wikikg-claim-document' ], $html );
	}

	/** @param array<string,mixed> $claim @param array<string,mixed>|null $conflict */
	private static function renderClaim( array $claim, $conflict, array $latestReviews, bool $canReview ) {
		$claimType = $claim['claim_type'] ?? '';
		$subject = self::entityText( $claim['subject'] ?? [] );
		$predicate = self::displayText( $claim['predicate'] ?? '', self::MAX_DISPLAY_CHARS );
		if ( $claimType === 'relationship' ) {
			$statement = $subject . ' - ' . $predicate . ' - '
				. self::entityText( $claim['object'] ?? [] );
		} else {
			$statement = $subject . ' - ' . $predicate . ': '
				. self::displayText( $claim['value'] ?? '', self::MAX_DISPLAY_CHARS );
		}

		$latestReview = self::latestReviewFor( $claim['snapshot_id'] ?? '', $latestReviews );
		$reviewStatus = $latestReview['status'] ?? 'pending';
		$statusText = [
			'approved' => 'Đã duyệt',
			'rejected' => 'Đã từ chối',
			'pending' => 'Chờ duyệt'
		][$reviewStatus] ?? 'Chờ duyệt';
		$body = Html::element( 'p', [ 'class' => 'wikikg-claim-statement' ], $statement )
			. self::renderQualifiers( $claim['qualifiers'] ?? [] )
			. Html::element( 'p', [ 'class' => 'wikikg-claim-status' ], $statusText );
		$snapshotId = $claim['snapshot_id'] ?? '';
		if ( $canReview && is_string( $snapshotId )
			&& preg_match( '/^[a-f0-9]{64}$/D', $snapshotId )
		) {
			$body .= Html::rawElement(
				'p',
				[ 'class' => 'wikikg-claim-review-link' ],
				Html::element( 'a', [
					'href' => SpecialPage::getTitleFor( 'WikiKGReview', $snapshotId )->getLocalURL()
				], 'Xem và duyệt claim' )
			);
		}
		if ( $conflict !== null ) {
			$body .= Html::element(
				'p',
				[ 'class' => 'wikikg-claim-conflict' ],
				'Mâu thuẫn, phía ' . $conflict['side'] . ' / ' . $conflict['count']
					. '. Hãy đọc cả hai phía trước khi duyệt.'
			);
		}

		if ( $latestReview !== null && $reviewStatus !== 'pending' ) {
			$reviewText = 'Quyết định đã lưu cho bằng chứng này.';
			$reason = $latestReview['reason'] ?? $latestReview['note'] ?? null;
			if ( is_string( $reason ) && $reason !== '' ) {
				$reviewText .= ' Lý do: ' . self::displayText(
					$reason,
					self::MAX_DISPLAY_CHARS
				);
			}
			$body .= Html::element( 'p', [ 'class' => 'wikikg-claim-latest-review' ], $reviewText );
		}

		$evidence = is_array( $claim['evidence'] ?? null ) ? $claim['evidence'] : [];
		$evidenceCount = count( $evidence );
		foreach ( array_slice( $evidence, 0, self::MAX_RENDERED_EVIDENCE ) as $index => $item ) {
			if ( is_array( $item ) ) {
				$body .= self::renderEvidence( $item, $index + 1 );
			}
		}
		if ( $evidenceCount > self::MAX_RENDERED_EVIDENCE ) {
			$body .= Html::element(
				'p',
				[ 'class' => 'wikikg-note' ],
				'Chỉ hiển thị ' . self::MAX_RENDERED_EVIDENCE . ' trên '
					. $evidenceCount . ' bằng chứng của claim này.'
			);
		}

		return Html::rawElement(
			'article',
			[ 'class' => 'wikikg-claim' ],
			Html::rawElement( 'div', [ 'class' => 'wikikg-claim-content' ], $body )
		);
	}

	/** @param array<string,mixed> $evidence */
	private static function renderEvidence( array $evidence, $number ) {
		$title = self::displayText( $evidence['page_title'] ?? '', self::MAX_DISPLAY_CHARS );
		$revisionId = is_int( $evidence['revision_id'] ?? null )
			? $evidence['revision_id']
			: 0;
		$locationType = $evidence['location_type'] ?? '';
		$heading = 'Bằng chứng ' . $number . ': ' . $title
			. ( $revisionId > 0 ? ' (revision ' . $revisionId . ')' : '' );
		$details = Html::element( 'summary', [], $heading );

		if ( $locationType === 'source_span'
			&& is_string( $evidence['supporting_span'] ?? null )
		) {
			$span = self::displayText(
				$evidence['supporting_span'],
				self::MAX_RENDERED_SPAN_CHARS
			);
			$details .= Html::rawElement(
				'blockquote',
				[ 'class' => 'wikikg-evidence-span' ],
				Html::element( 'p', [], $span )
			);
		} elseif ( $locationType === 'page_title' ) {
			$details .= Html::element(
				'p',
				[ 'class' => 'wikikg-note' ],
				'Chỉ dựa vào tiêu đề trang, không có trích dẫn nội dung.'
			);
		} else {
			$details .= Html::element(
				'p',
				[ 'class' => 'wikikg-note' ],
				'Chỉ có bằng chứng cấp trang; không có trích dẫn hỗ trợ.'
			);
		}

		$link = self::renderRevisionLink( $evidence['page_title'] ?? '', $revisionId );
		if ( $link !== '' ) {
			$details .= Html::rawElement(
				'p',
				[ 'class' => 'wikikg-evidence-source' ],
				'Nguồn: ' . $link
			);
		}
		return Html::rawElement(
			'details',
			[ 'class' => 'wikikg-evidence' ],
			$details
		);
	}

	/** @param mixed $qualifiers */
	private static function renderQualifiers( $qualifiers ) {
		if ( !is_array( $qualifiers ) || !$qualifiers ) {
			return '';
		}
		$items = '';
		foreach ( array_slice( $qualifiers, 0, 20, true ) as $key => $value ) {
			$items .= Html::rawElement(
				'div',
				[],
				Html::element( 'dt', [], self::displayText( $key, self::MAX_DISPLAY_CHARS ) )
					. Html::element( 'dd', [], self::displayText( $value, self::MAX_DISPLAY_CHARS ) )
			);
		}
		return Html::rawElement(
			'dl',
			[ 'class' => 'wikikg-claim-qualifiers' ],
			$items
		);
	}

	private static function renderRevisionLink( $pageTitle, $revisionId ) {
		$pageTitle = is_string( $pageTitle ) ? $pageTitle : '';
		if ( $pageTitle === '' || strlen( $pageTitle ) > 1024
			|| !is_int( $revisionId ) || $revisionId < 1
		) {
			return '';
		}
		$title = Title::newFromText( $pageTitle );
		if ( !$title ) {
			return '';
		}
		return Html::element(
			'a',
			[ 'href' => $title->getLocalURL( [ 'oldid' => $revisionId ] ) ],
			$pageTitle
		);
	}

	/** @param mixed $entity */
	private static function entityText( $entity ) {
		if ( !is_array( $entity ) ) {
			return '';
		}
		$type = self::displayText( $entity['type'] ?? '', self::MAX_DISPLAY_CHARS );
		$id = self::displayText( $entity['id'] ?? '', self::MAX_DISPLAY_CHARS );
		return $type !== '' ? $type . ': ' . $id : $id;
	}

	/** @param mixed $value */
	private static function displayText( $value, $maxChars ) {
		if ( is_array( $value ) || is_object( $value ) ) {
			$encoded = json_encode( $value, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES );
			$value = $encoded === false ? '' : $encoded;
		} elseif ( !is_string( $value ) && !is_numeric( $value ) && !is_bool( $value ) ) {
			$value = '';
		}
		$value = (string)$value;
		if ( function_exists( 'mb_substr' ) && function_exists( 'mb_strlen' ) ) {
			if ( mb_strlen( $value, 'UTF-8' ) > $maxChars ) {
				return mb_substr( $value, 0, $maxChars, 'UTF-8' ) . '…';
			}
			return $value;
		}
		return strlen( $value ) > $maxChars ? substr( $value, 0, $maxChars ) . '…' : $value;
	}

	/**
	 * @param array<int,array<string,mixed>> $claims
	 * @return array<int,array<int,int>>
	 */
	private static function findConflictGroups( array $claims ) {
		$propertyBuckets = [];
		$opposingBuckets = [];
		foreach ( $claims as $index => $claim ) {
			$subject = self::entityKey( $claim['subject'] ?? null );
			if ( $subject === null ) {
				continue;
			}
			if ( ( $claim['claim_type'] ?? '' ) === 'property'
				&& is_string( $claim['predicate'] ?? null )
				&& array_key_exists( 'value', $claim )
			) {
				$qualifiers = self::stableValue( $claim['qualifiers'] ?? [] );
				$value = self::stableValue( $claim['value'] );
				$key = hash( 'sha256', $subject . "\0" . $claim['predicate'] . "\0" . $qualifiers );
				$propertyBuckets[$key][$value][] = $index;
			}

			$predicate = $claim['predicate'] ?? '';
			if ( ( $claim['claim_type'] ?? '' ) === 'relationship'
				&& in_array( $predicate, [ 'RESISTANT_TO', 'SUSCEPTIBLE_TO' ], true )
			) {
				$object = self::entityKey( $claim['object'] ?? null );
				if ( $object !== null ) {
					$key = hash( 'sha256', $subject . "\0" . $object );
					$opposingBuckets[$key][$predicate][] = $index;
				}
			}
		}

		$groups = [];
		foreach ( $propertyBuckets as $values ) {
			if ( count( $values ) > 1 ) {
				$groups[] = array_merge( ...array_values( $values ) );
			}
		}
		foreach ( $opposingBuckets as $predicates ) {
			if ( !empty( $predicates['RESISTANT_TO'] ) && !empty( $predicates['SUSCEPTIBLE_TO'] ) ) {
				$groups[] = array_merge(
					$predicates['RESISTANT_TO'],
					$predicates['SUSCEPTIBLE_TO']
				);
			}
		}
		foreach ( $groups as &$group ) {
			$group = array_values( array_unique( $group ) );
			sort( $group, SORT_NUMERIC );
		}
		unset( $group );
		usort( $groups, static function ( $left, $right ) {
			return ( $left[0] ?? 0 ) <=> ( $right[0] ?? 0 );
		} );
		return $groups;
	}

	/** @param mixed $entity @return string|null */
	private static function entityKey( $entity ) {
		if ( !is_array( $entity ) || !is_string( $entity['type'] ?? null )
			|| !is_string( $entity['id'] ?? null )
		) {
			return null;
		}
		return self::stableValue( [ $entity['type'], $entity['id'] ] );
	}

	/** @param mixed $value */
	private static function stableValue( $value ) {
		if ( is_array( $value ) ) {
			if ( !array_is_list( $value ) ) {
				ksort( $value, SORT_STRING );
			}
			foreach ( $value as &$item ) {
				$item = self::sortNestedArrays( $item );
			}
			unset( $item );
		}
		$encoded = json_encode( $value, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES );
		return $encoded === false ? '' : $encoded;
	}

	/** @param mixed $value */
	private static function sortNestedArrays( $value ) {
		if ( !is_array( $value ) ) {
			return $value;
		}
		if ( !array_is_list( $value ) ) {
			ksort( $value, SORT_STRING );
		}
		foreach ( $value as &$item ) {
			$item = self::sortNestedArrays( $item );
		}
		unset( $item );
		return $value;
	}

	/** @param int $count @param array<int,array<int,int>> $groups @return int[] */
	private static function selectClaims( $count, array $groups ) {
		$selected = [];
		$indexes = [];
		foreach ( $groups as $group ) {
			$newIndexes = array_diff( $group, array_keys( $selected ) );
			if ( count( $selected ) + count( $newIndexes ) <= self::MAX_RENDERED_CLAIMS ) {
				foreach ( $group as $index ) {
					if ( !isset( $selected[$index] ) ) {
						$selected[$index] = true;
						$indexes[] = $index;
					}
				}
			}
		}
		for ( $index = 0; $index < $count
			&& count( $selected ) < self::MAX_RENDERED_CLAIMS; $index++
		) {
			if ( !isset( $selected[$index] ) ) {
				$selected[$index] = true;
				$indexes[] = $index;
			}
		}
		return $indexes;
	}

	/** @param int $index @param array<int,array<int,int>> $groups @return array<string,int>|null */
	private static function conflictForIndex( $index, array $groups ) {
		foreach ( $groups as $group ) {
			$side = array_search( $index, $group, true );
			if ( $side !== false ) {
				return [ 'side' => $side + 1, 'count' => count( $group ) ];
			}
		}
		return null;
	}

	/** @param mixed $snapshotId @param array<string,mixed> $latestReviews */
	private static function latestReviewFor( $snapshotId, array $latestReviews ) {
		if ( !is_string( $snapshotId ) || $snapshotId === '' ) {
			return null;
		}
		if ( ( $latestReviews['snapshot_id'] ?? null ) === $snapshotId ) {
			return $latestReviews;
		}
		if ( isset( $latestReviews[$snapshotId] ) && is_array( $latestReviews[$snapshotId] ) ) {
			return $latestReviews[$snapshotId];
		}
		$scanned = 0;
		foreach ( $latestReviews as $review ) {
			if ( ++$scanned > self::MAX_LATEST_REVIEWS ) {
				break;
			}
			if ( is_array( $review ) && ( $review['snapshot_id'] ?? null ) === $snapshotId ) {
				return $review;
			}
		}
		return null;
	}
}
