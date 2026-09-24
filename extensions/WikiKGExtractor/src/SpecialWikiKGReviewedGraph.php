<?php

namespace MediaWiki\Extension\WikiKGExtractor;

use Html;
use MediaWiki\MediaWikiServices;
use SpecialPage;
use Throwable;

/**
 * Sysop-only graph assembled from currently readable, approved claim snapshots.
 */
class SpecialWikiKGReviewedGraph extends SpecialPage {
	private const PAGE_SIZE = 50;
	private const MAX_OFFSET = 1000000;

	public function __construct() {
		parent::__construct( 'WikiKGReviewedGraph', 'wikikgextractor-review' );
	}

	/**
	 * @param string|null $subPage
	 */
	public function execute( $subPage ) {
		$this->setHeaders();
		$this->checkPermissions();
		$this->getOutput()->addModuleStyles( 'ext.wikikg.styles' );

		$offset = $this->readOffset();
		if ( $offset === null ) {
			$this->addMessage( 'wikikgreviewedgraph-invalid-offset', 'wikikgreviewedgraph-error' );
			return;
		}

		try {
			$store = $this->newReviewStore();
			$snapshots = $store->listSnapshots(
				self::PAGE_SIZE + 1,
				$offset
			);
			$hasMore = count( $snapshots ) > self::PAGE_SIZE;
			$snapshots = array_slice( $snapshots, 0, self::PAGE_SIZE );
			$visibleReviews = [];

			foreach ( $snapshots as $snapshot ) {
				$snapshotId = $snapshot['snapshot_id'] ?? null;
				if ( !is_string( $snapshotId )
					|| !preg_match( '/^[a-f0-9]{64}$/D', $snapshotId )
					|| ( $snapshot['latest_review']['status'] ?? null ) !== 'approved'
				) {
					continue;
				}

				$review = $store->getReview( $snapshotId );
				if ( $review === null
					|| ( $review['latest_review']['status'] ?? null ) !== 'approved'
					|| !ClaimEvidenceAccess::canReadCurrent(
						$this->getAuthority(),
						$review['evidence'] ?? []
					)
				) {
					continue;
				}
				$visibleReviews[] = $review;
			}

			$graph = ReviewedGraph::build( $visibleReviews );
		} catch ( Throwable $exception ) {
			$this->addMessage( 'wikikgreviewedgraph-load-error', 'wikikgreviewedgraph-error' );
			return;
		}

		$out = $this->getOutput();
		$html = Html::element(
			'p',
			[ 'class' => 'wikikg-note' ],
			$this->msg( 'wikikgreviewedgraph-intro' )->text()
		);
		if ( !$graph['property_claims'] && !$graph['relationship_claims'] ) {
			$html .= Html::element(
				'p',
				[ 'class' => 'wikikg-note' ],
				$this->msg( 'wikikgreviewedgraph-empty' )->text()
			);
		}

		if ( $graph['document']['nodes'] ) {
			$out->addJsConfigVars(
				'wgWikiKGGraph',
				GraphDocument::clientData( $graph['document'] )
			);
			$out->addModules( 'ext.wikikg.graph' );
			$html .= GraphDocument::render( $graph['document'] );
		}

		$html .= $this->renderPropertyClaims( $graph['property_claims'] );
		$html .= $this->renderRelationshipClaims( $graph['relationship_claims'] );
		$html .= $this->renderNavigation( $offset, $hasMore );
		$out->addHTML( $html );
	}

	/** @return ClaimReviewStore */
	protected function newReviewStore(): ClaimReviewStore {
		return new ClaimReviewStore(
			MediaWikiServices::getInstance()->getConnectionProvider()
		);
	}

	private function readOffset(): ?int {
		$text = $this->getRequest()->getText( 'offset', '0' );
		if ( !preg_match( '/^(0|[1-9][0-9]{0,6})$/D', $text )
			|| (int)$text > self::MAX_OFFSET
		) {
			return null;
		}
		return (int)$text;
	}

	/** @param array<int,array<string,string>> $claims */
	private function renderPropertyClaims( array $claims ): string {
		if ( !$claims ) {
			return '';
		}

		$rows = '';
		foreach ( $claims as $claim ) {
			$rows .= Html::rawElement(
				'tr',
				[],
				Html::element( 'td', [], $claim['subject_type'] . ': ' . $claim['subject'] )
					. Html::element( 'td', [], $claim['predicate'] )
					. Html::element( 'td', [], $claim['value'] )
					. Html::element( 'td', [], $claim['qualifiers'] )
					. Html::rawElement( 'td', [], $this->reviewLink( $claim['snapshot_id'] ) )
			);
		}

		return Html::element(
			'h2',
			[],
			$this->msg( 'wikikgreviewedgraph-properties-heading' )->text()
		) . $this->claimTable( [
			'wikikgreviewedgraph-subject',
			'wikikgreviewedgraph-property',
			'wikikgreviewedgraph-value',
			'wikikgreviewedgraph-qualifiers',
			'wikikgreviewedgraph-review',
		], $rows );
	}

	/** @param array<int,array<string,string>> $claims */
	private function renderRelationshipClaims( array $claims ): string {
		if ( !$claims ) {
			return '';
		}

		$rows = '';
		foreach ( $claims as $claim ) {
			$rows .= Html::rawElement(
				'tr',
				[],
				Html::element( 'td', [], $claim['subject_type'] . ': ' . $claim['subject'] )
					. Html::element( 'td', [], $claim['predicate'] )
					. Html::element( 'td', [], $claim['object_type'] . ': ' . $claim['object'] )
					. Html::element( 'td', [], $claim['qualifiers'] )
					. Html::rawElement( 'td', [], $this->reviewLink( $claim['snapshot_id'] ) )
			);
		}

		return Html::element(
			'h2',
			[],
			$this->msg( 'wikikgreviewedgraph-relationships-heading' )->text()
		) . $this->claimTable( [
			'wikikgreviewedgraph-subject',
			'wikikgreviewedgraph-relationship',
			'wikikgreviewedgraph-object',
			'wikikgreviewedgraph-qualifiers',
			'wikikgreviewedgraph-review',
		], $rows );
	}

	/** @param string[] $headerMessages */
	private function claimTable( array $headerMessages, string $rows ): string {
		$headers = '';
		foreach ( $headerMessages as $message ) {
			$headers .= Html::element(
				'th',
				[ 'scope' => 'col' ],
				$this->msg( $message )->text()
			);
		}

		return Html::rawElement(
			'table',
			[ 'class' => 'wikikgreview-list wikikg-reviewed-claims' ],
			Html::rawElement( 'thead', [], Html::rawElement( 'tr', [], $headers ) )
				. Html::rawElement( 'tbody', [], $rows )
		);
	}

	private function reviewLink( string $snapshotId ): string {
		return Html::element(
			'a',
			[ 'href' => SpecialPage::getTitleFor( 'WikiKGReview', $snapshotId )->getLocalURL() ],
			$this->msg( 'wikikgreviewedgraph-open-review' )->text()
		);
	}

	private function renderNavigation( int $offset, bool $hasMore ): string {
		$links = '';
		if ( $offset > 0 ) {
			$links .= Html::element(
				'a',
				[ 'href' => $this->pageUrl( max( 0, $offset - self::PAGE_SIZE ) ) ],
				$this->msg( 'wikikgreviewedgraph-previous' )->text()
			);
		}

		$canContinue = $hasMore && $offset <= self::MAX_OFFSET - self::PAGE_SIZE;
		if ( $canContinue ) {
			$links .= Html::element(
				'a',
				[ 'href' => $this->pageUrl( $offset + self::PAGE_SIZE ) ],
				$this->msg( 'wikikgreviewedgraph-next' )->text()
			);
		}

		$html = $links !== ''
			? Html::rawElement(
				'nav',
				[ 'aria-label' => $this->msg( 'wikikgreviewedgraph-navigation' )->text() ],
				$links
			)
			: '';
		if ( $hasMore && !$canContinue ) {
			$html .= Html::element(
				'p',
				[ 'class' => 'wikikg-note wikikgreviewedgraph-error' ],
				$this->msg( 'wikikgreviewedgraph-page-limit' )->text()
			);
		}
		return $html;
	}

	private function pageUrl( int $offset ): string {
		$title = SpecialPage::getTitleFor( 'WikiKGReviewedGraph' );
		return $title->getLocalURL( $offset > 0 ? [ 'offset' => $offset ] : [] );
	}

	private function addMessage( string $message, string $class ): void {
		$this->getOutput()->addHTML( Html::element(
			'p',
			[ 'class' => $class ],
			$this->msg( $message )->text()
		) );
	}

	/** @inheritDoc */
	protected function getGroupName() {
		return 'other';
	}
}
