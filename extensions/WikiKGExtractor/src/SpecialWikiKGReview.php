<?php

namespace MediaWiki\Extension\WikiKGExtractor;

use Html;
use MediaWiki\MediaWikiServices;
use SpecialPage;
use Throwable;
use Title;

/**
 * Sysop-only review UI for immutable WikiKG claim snapshots.
 *
 * Decisions are appended to the audit history; they never publish graph data.
 */
class SpecialWikiKGReview extends SpecialPage {
	private const PAGE_SIZE = 50;
	private const MAX_REASON_BYTES = 4096;
	private const MAX_HISTORY_EVENTS = 100;

	public function __construct() {
		parent::__construct( 'WikiKGReview', 'wikikgextractor-review' );
	}

	/**
	 * @param string|null $subPage
	 */
	public function execute( $subPage ) {
		$this->setHeaders();
		if ( !$this->getUser()->isRegistered() ) {
			$this->displayRestrictionError();
		}
		$this->checkPermissions();

		$out = $this->getOutput();
		$out->addModuleStyles( 'ext.wikikg.styles' );

		$store = new ClaimReviewStore(
			MediaWikiServices::getInstance()->getConnectionProvider()
		);
		$snapshotId = is_string( $subPage ) ? $subPage : '';
		if ( $snapshotId !== '' ) {
			$this->executeReview( $store, $snapshotId );
			return;
		}

		$this->showSnapshotList( $store );
	}

	private function executeReview( ClaimReviewStore $store, string $snapshotId ): void {
		if ( !preg_match( '/^[a-f0-9]{64}$/D', $snapshotId ) ) {
			$this->showUnavailable();
			return;
		}

		$request = $this->getRequest();
		$error = '';
		$notice = '';
		$reasonDraft = '';

		if ( $request->wasPosted() ) {
			$reasonDraft = $request->getText( 'reason', '' );
			$reasonTooLong = strlen( $reasonDraft ) > self::MAX_REASON_BYTES;
			if ( $reasonTooLong ) {
				$reasonDraft = '';
			}
			if ( !$this->getUser()->matchEditToken( $request->getVal( 'wpEditToken' ) ) ) {
				$error = 'Token bảo mật không hợp lệ. Hãy tải lại trang và thử lại.';
			} else {
				$review = $this->loadReview( $store, $snapshotId );
				if ( $review === null || !ClaimEvidenceAccess::canRead(
					$this->getAuthority(),
					$review['evidence'] ?? []
				) ) {
					$this->showUnavailable();
					return;
				}

				$status = $request->getText( 'decision', '' );
				$versionText = $request->getText( 'expected_version', '' );
				$expectedVersion = null;
				if ( preg_match( '/^(0|[1-9][0-9]{0,9})$/D', $versionText ) ) {
					$parsedVersion = (int)$versionText;
					if ( $parsedVersion < 2147483647 ) {
						$expectedVersion = $parsedVersion;
					}
				}

				$trimmedReason = trim( $reasonDraft );
				if ( !in_array( $status, [ 'approved', 'rejected' ], true )
					|| $expectedVersion === null
					|| $reasonTooLong
					|| $trimmedReason === ''
					|| strlen( $trimmedReason ) > self::MAX_REASON_BYTES
					|| preg_match( '//u', $trimmedReason ) !== 1
				) {
					$error = 'Chọn quyết định hợp lệ và nhập lý do không quá 4.096 byte.';
				} else {
					try {
						$event = $store->recordDecision(
							$snapshotId,
							(int)$this->getUser()->getId(),
							$status,
							$trimmedReason,
							$expectedVersion
						);
					} catch ( Throwable $exception ) {
						$event = null;
						$error = 'Không thể lưu quyết định. Hãy tải lại trang rồi thử lại.';
					}

					if ( $event !== null ) {
						$reasonDraft = '';
						$notice = 'Đã lưu quyết định vào lịch sử duyệt.';
					} elseif ( $error === '' ) {
						$reasonDraft = '';
						$error = 'Claim đã được cập nhật. Hãy tải lại trang trước khi duyệt tiếp.';
					}
				}
			}
		}

		$review = $this->loadReview( $store, $snapshotId );
		if ( $review === null || !ClaimEvidenceAccess::canRead(
			$this->getAuthority(),
			$review['evidence'] ?? []
		) ) {
			$this->showUnavailable();
			return;
		}

		if ( $error !== '' ) {
			$this->addMessage( $error, 'wikikgreview-error' );
		}
		if ( $notice !== '' ) {
			$this->addMessage( $notice, 'wikikgreview-notice' );
		}
		$this->renderReview( $review, $reasonDraft );
	}

	private function showSnapshotList( ClaimReviewStore $store ): void {
		$offsetText = $this->getRequest()->getText( 'offset', '0' );
		if ( !preg_match( '/^(0|[1-9][0-9]{0,6})$/D', $offsetText )
			|| (int)$offsetText > 1000000
		) {
			$this->addMessage( 'Trang danh sách không hợp lệ.', 'wikikgreview-error' );
			return;
		}

		$offset = (int)$offsetText;
		try {
			$snapshots = $store->listSnapshots( self::PAGE_SIZE, $offset );
		} catch ( Throwable $exception ) {
			$this->addMessage( 'Không thể tải danh sách claim.', 'wikikgreview-error' );
			return;
		}

		$this->getOutput()->addHTML(
			Html::element( 'p', [], 'Danh sách snapshot claim mới nhất. Bằng chứng chỉ hiển thị khi mở từng claim.' )
		);
		$this->getOutput()->addHTML( Html::rawElement(
			'p',
			[ 'class' => 'wikikg-note' ],
			Html::element(
				'a',
				[ 'href' => SpecialPage::getTitleFor( 'WikiKGReviewedGraph' )->getLocalURL() ],
				$this->msg( 'wikikgreviewedgraph-link' )->text()
			)
		) );
		if ( !$snapshots ) {
			$this->getOutput()->addHTML(
				Html::element( 'p', [], 'Chưa có claim nào cần xem.' )
			);
			return;
		}

		$rows = '';
		foreach ( $snapshots as $snapshot ) {
			$id = is_string( $snapshot['snapshot_id'] ?? null )
				? $snapshot['snapshot_id']
				: '';
			$review = $id !== '' ? $this->loadReview( $store, $id ) : null;
			if ( $review === null || !ClaimEvidenceAccess::canRead(
				$this->getAuthority(),
				$review['evidence'] ?? []
			) ) {
				continue;
			}
			$claim = is_array( $snapshot['claim'] ?? null ) ? $snapshot['claim'] : [];
			$latest = is_array( $snapshot['latest_review'] ?? null )
				? $snapshot['latest_review']
				: [];
			$link = preg_match( '/^[a-f0-9]{64}$/D', $id )
				? Html::element(
					'a',
					[ 'href' => $this->reviewUrl( $id ) ],
					substr( $id, 0, 12 )
				)
				: '';
			$row = Html::rawElement( 'th', [ 'scope' => 'row' ], $link )
				. Html::element( 'td', [], self::claimStatement( $claim ) )
				. Html::element( 'td', [], self::statusLabel( $latest['status'] ?? 'pending' ) )
				. Html::element( 'td', [], (string)(int)( $snapshot['source_count'] ?? 0 ) )
				. Html::element( 'td', [], (string)( $snapshot['created_at'] ?? '' ) );
			$rows .= Html::rawElement( 'tr', [], $row );
		}
		if ( $rows === '' ) {
			$this->getOutput()->addHTML( Html::element( 'p', [], 'Chưa có claim nào có nguồn bạn có thể đọc.' ) );
		} else {
			$headers = '';
			foreach ( [ 'Snapshot', 'Claim', 'Trạng thái', 'Số nguồn', 'Tạo lúc' ] as $label ) {
				$headers .= Html::element( 'th', [ 'scope' => 'col' ], $label );
			}
			$table = Html::rawElement(
				'table',
				[ 'class' => 'wikikgreview-list' ],
				Html::rawElement( 'thead', [], Html::rawElement( 'tr', [], $headers ) )
					. Html::rawElement( 'tbody', [], $rows )
			);
			$this->getOutput()->addHTML( $table );
		}

		$navigation = '';
		if ( $offset > 0 ) {
			$navigation .= Html::element(
				'a',
				[ 'href' => $this->listUrl( max( 0, $offset - self::PAGE_SIZE ) ) ],
				'Trang trước'
			);
		}
		if ( count( $snapshots ) === self::PAGE_SIZE && $offset <= 1000000 - self::PAGE_SIZE ) {
			$navigation .= Html::element(
				'a',
				[ 'href' => $this->listUrl( $offset + self::PAGE_SIZE ) ],
				'Trang sau'
			);
		}
		if ( $navigation !== '' ) {
			$this->getOutput()->addHTML(
				Html::rawElement(
					'nav',
					[ 'aria-label' => 'Điều hướng danh sách claim' ],
					$navigation
				)
			);
		}
	}

	/** @param array<string,mixed> $review */
	private function renderReview( array $review, string $reasonDraft ): void {
		$claim = $review;
		$snapshotId = (string)$review['snapshot_id'];
		$latest = is_array( $review['latest_review'] ?? null )
			? $review['latest_review']
			: [ 'status' => 'pending', 'version' => 0 ];
		$status = self::statusLabel( $latest['status'] ?? 'pending' );
		$version = (int)( $latest['version'] ?? 0 );

		$state = Html::element( 'h2', [], 'Trạng thái đánh giá hiện tại' )
			. Html::element( 'p', [], $status . ' - phiên bản ' . $version );
		if ( is_string( $latest['reason'] ?? null ) && $latest['reason'] !== '' ) {
			$state .= Html::element( 'p', [], 'Lý do gần nhất: ' . $latest['reason'] );
		}
		if ( is_string( $latest['timestamp'] ?? null ) && $latest['timestamp'] !== '' ) {
			$state .= Html::element( 'p', [], 'Cập nhật lúc: ' . $latest['timestamp'] );
		}
		$this->getOutput()->addHTML( Html::rawElement( 'section', [], $state ) );

		$this->getOutput()->addHTML(
			ClaimEvidenceView::render(
				[ 'claims' => [ $claim ] ],
				[ $snapshotId => $latest ]
			)
		);
		$this->renderHistory( $review['review_events'] ?? [] );
		$this->renderDecisionForm( $snapshotId, $version, $reasonDraft );
	}

	/** @param mixed $events */
	private function renderHistory( $events ): void {
		$events = is_array( $events ) ? $events : [];
		$html = Html::element( 'h2', [], 'Lịch sử duyệt' );
		if ( !$events ) {
			$html .= Html::element( 'p', [], 'Chưa có quyết định nào được lưu.' );
			$this->getOutput()->addHTML( Html::rawElement( 'section', [], $html ) );
			return;
		}

		$visibleEvents = array_slice( $events, -self::MAX_HISTORY_EVENTS );
		if ( count( $visibleEvents ) < count( $events ) ) {
			$html .= Html::element(
				'p',
				[],
				'Đang hiển thị ' . count( $visibleEvents ) . ' quyết định gần nhất.'
			);
		}
		$items = '';
		foreach ( $visibleEvents as $event ) {
			if ( !is_array( $event ) ) {
				continue;
			}
			$line = 'Phiên bản ' . (int)( $event['version'] ?? 0 ) . ': '
				. self::statusLabel( $event['status'] ?? '' )
				. ' - người duyệt ID ' . (int)( $event['reviewer_id'] ?? 0 )
				. ' - ' . (string)( $event['timestamp'] ?? '' );
			$items .= Html::rawElement(
				'li',
				[],
				Html::element( 'p', [], $line )
					. Html::element( 'p', [], (string)( $event['reason'] ?? '' ) )
			);
		}
		$html .= Html::rawElement( 'ol', [], $items );
		$this->getOutput()->addHTML( Html::rawElement( 'section', [], $html ) );
	}

	private function renderDecisionForm(
		string $snapshotId,
		int $version,
		string $reasonDraft
	): void {
		$form = Html::openElement( 'form', [
			'method' => 'post',
			'action' => $this->reviewUrl( $snapshotId ),
			'class' => 'wikikgreview-form',
		] );
		$form .= Html::openElement( 'fieldset' );
		$form .= Html::element( 'legend', [], 'Ghi nhận quyết định' );
		$form .= Html::element( 'p', [], 'Quyết định này cập nhật đồ thị đã duyệt, không sửa dữ liệu trích xuất hay Neo4j.' );
		$form .= Html::openElement( 'label', [ 'for' => 'wikikgreview-approved' ] );
		$form .= Html::element( 'input', [
			'id' => 'wikikgreview-approved',
			'name' => 'decision',
			'type' => 'radio',
			'value' => 'approved',
			'required' => true,
		] ) . 'Duyệt';
		$form .= Html::closeElement( 'label' );
		$form .= Html::openElement( 'label', [ 'for' => 'wikikgreview-rejected' ] );
		$form .= Html::element( 'input', [
			'id' => 'wikikgreview-rejected',
			'name' => 'decision',
			'type' => 'radio',
			'value' => 'rejected',
			'required' => true,
		] ) . 'Từ chối';
		$form .= Html::closeElement( 'label' );
		$form .= Html::openElement( 'div' );
		$form .= Html::element( 'label', [ 'for' => 'wikikgreview-reason' ], 'Lý do' );
		$form .= Html::element( 'textarea', [
			'id' => 'wikikgreview-reason',
			'name' => 'reason',
			'rows' => 4,
			'maxlength' => self::MAX_REASON_BYTES,
			'required' => true,
		], $reasonDraft );
		$form .= Html::closeElement( 'div' );
		$form .= Html::hidden( 'expected_version', (string)$version );
		$form .= Html::hidden( 'wpEditToken', $this->getUser()->getEditToken() );
		$form .= Html::element( 'button', [ 'type' => 'submit' ], 'Lưu quyết định' );
		$form .= Html::closeElement( 'fieldset' ) . Html::closeElement( 'form' );
		$this->getOutput()->addHTML( $form );
	}

	private function loadReview( ClaimReviewStore $store, string $snapshotId ): ?array {
		try {
			return $store->getReview( $snapshotId );
		} catch ( Throwable $exception ) {
			return null;
		}
	}

	private function showUnavailable(): void {
		$this->addMessage(
			'Không thể mở claim này. Snapshot không tồn tại hoặc nguồn không còn khả dụng.',
			'wikikgreview-error'
		);
	}

	private function addMessage( string $message, string $class ): void {
		$this->getOutput()->addHTML( Html::element( 'p', [ 'class' => $class ], $message ) );
	}

	private function reviewUrl( string $snapshotId ): string {
		return Title::makeTitle( NS_SPECIAL, 'WikiKGReview/' . $snapshotId )->getLocalURL();
	}

	private function listUrl( int $offset ): string {
		$title = Title::makeTitle( NS_SPECIAL, 'WikiKGReview' );
		return $title->getLocalURL( $offset > 0 ? [ 'offset' => $offset ] : [] );
	}

	/** @param array<string,mixed> $claim */
	private static function claimStatement( array $claim ): string {
		$subject = self::entityText( $claim['subject'] ?? null );
		$predicate = is_scalar( $claim['predicate'] ?? null )
			? (string)$claim['predicate']
			: '';
		if ( ( $claim['claim_type'] ?? '' ) === 'relationship' ) {
			return $subject . ' - ' . $predicate . ' - '
				. self::entityText( $claim['object'] ?? null );
		}
		$value = is_scalar( $claim['value'] ?? null ) ? (string)$claim['value'] : '';
		return $subject . ' - ' . $predicate . ': ' . $value;
	}

	/** @param mixed $entity */
	private static function entityText( $entity ): string {
		if ( !is_array( $entity ) ) {
			return '';
		}
		$type = is_scalar( $entity['type'] ?? null ) ? (string)$entity['type'] : '';
		$id = is_scalar( $entity['id'] ?? null ) ? (string)$entity['id'] : '';
		return $type === '' ? $id : $type . ': ' . $id;
	}

	/** @param mixed $status */
	private static function statusLabel( $status ): string {
		switch ( $status ) {
			case 'approved':
				return 'Đã duyệt';
			case 'rejected':
				return 'Đã từ chối';
			case 'pending':
			default:
				return 'Chờ duyệt';
		}
	}

	/** @inheritDoc */
	protected function getGroupName() {
		return 'other';
	}
}
