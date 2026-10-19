<?php

namespace MediaWiki\Extension\WikiKGExtractor;

use Html;
use MediaWiki\MediaWikiServices;
use SpecialPage;
use Throwable;
use Title;

/** Reviewer-only server-rendered connected synthetic question demo. */
class SpecialWikiKGSyntheticQuestions extends SpecialPage {
	public function __construct() {
		parent::__construct( 'WikiKGSyntheticQuestions', 'wikikgextractor-review' );
	}

	public function execute( $subPage ) {
		$this->setHeaders();
		if ( !$this->getUser()->isRegistered() ) { $this->displayRestrictionError(); }
		$this->checkPermissions();
		$this->getOutput()->addModuleStyles( 'ext.wikikg.questions.styles' );
		$runner = $this->newRunner();
		try {
			$questions = $runner->listQuestions();
		} catch ( Throwable $exception ) {
			$this->getOutput()->addHTML( $this->status( 'unavailable', 'wikikgquestions-unavailable' ) );
			return;
		}
		$result = null;
		$selected = '';
		if ( $this->getRequest()->wasPosted() ) {
			$selected = $this->getRequest()->getText( 'question', '' );
			if ( !$this->getUser()->matchEditToken( $this->getRequest()->getVal( 'wpEditToken' ) ) ) {
				$result = $this->failure( 'csrf' );
			} elseif ( !preg_match( '/\A[a-z][a-z0-9-]{0,63}\z/D', $selected ) ) {
				$result = $this->failure( 'invalid' );
			} else {
				try {
					$result = $runner->run( $selected, $this->getAuthority() );
				} catch ( Throwable $exception ) {
					$result = $this->failure( 'unavailable' );
				}
			}
		}
		$html = $this->renderForm( $questions, $selected );
		if ( $result !== null ) { $html .= $this->renderResult( $result ); }
		$this->getOutput()->addHTML( $html );
	}

	protected function newRunner(): SyntheticQuestionRunner {
		return new SyntheticQuestionRunner();
	}

	private function renderForm( array $questions, string $selected ): string {
		$options = '';
		foreach ( $questions as $question ) {
			$options .= Html::element( 'option', [ 'value' => $question['id'],
				'selected' => $question['id'] === $selected ], $question['label'] );
		}
		return Html::rawElement( 'form', [ 'method' => 'post', 'class' => 'wikikg-question-form' ],
			Html::rawElement( 'div', [], Html::element( 'label', [ 'for' => 'wikikg-question' ],
				$this->msg( 'wikikgquestions-select-label' )->text() )
				. Html::rawElement( 'select', [ 'id' => 'wikikg-question', 'name' => 'question', 'required' => true ], $options ) )
			. Html::hidden( 'wpEditToken', $this->getUser()->getEditToken() )
			. Html::element( 'button', [ 'type' => 'submit' ], $this->msg( 'wikikgquestions-run' )->text() ) );
	}

	private function renderResult( array $result ): string {
		if ( ( $result['status'] ?? '' ) === 'dependency_failure' ) {
			return $this->status( 'dependency_failure', 'wikikgquestions-blocked' );
		}
		if ( !in_array( $result['status'] ?? null, [ 'evidence', 'blocked' ], true )
			|| !is_array( $result['query'] ?? null )
		) {
			return $this->status( 'dependency_failure', 'wikikgquestions-blocked' );
		}
		$query = $result['query'];
		$canonical = [];
		foreach ( $result['_canonical_claims'] ?? [] as $claim ) {
			if ( is_array( $claim ) && is_string( $claim['snapshot_id'] ?? null ) ) {
				$canonical[$claim['snapshot_id']] = $claim;
			}
		}
		$state = $result['status'] === 'evidence'
			&& ( $query['complete'] ?? false ) === true && ( $query['partial'] ?? true ) === false
			? 'wikikgquestions-complete' : 'wikikgquestions-incomplete';
		$html = Html::element( 'p', [ 'class' => 'wikikg-question-state' ], $this->msg( $state )->text() )
			. Html::element( 'p', [ 'class' => 'wikikg-question-generation',
			'data-wikikg-generation' => $query['generation'] ?? '' ],
			$this->msg( 'wikikgquestions-generation', $query['generation'] ?? '' )->text() );
		$html .= $this->renderReasons( array_merge( $query['reasons'] ?? [], $result['reasons'] ?? [] ) );
		foreach ( array_slice( $query['results'] ?? [], 0, 100 ) as $match ) {
			if ( !is_array( $match ) ) { continue; }
			$root = $this->entityLabel( $match['subject'] ?? [] );
			$ids = [];
			foreach ( $match['claims'] ?? [] as $entry ) {
				$snapshot = $entry['citation']['snapshot_id'] ?? null;
				if ( is_string( $snapshot ) && isset( $canonical[$snapshot] )
					&& ( $canonical[$snapshot]['snapshot_hash'] ?? null ) === ( $entry['citation']['snapshot_hash'] ?? null )
					&& ( $canonical[$snapshot]['review_version'] ?? null ) === ( $entry['citation']['review_version'] ?? null )
				) { $ids[$snapshot] = true; }
			}
			$section = Html::element( 'h3', [ 'data-wikikg-root' => $root ], $root );
			foreach ( [ 'eligible_claim_ids' => 'eligible', 'diagnostic_claim_ids' => 'diagnostic',
				'context_claim_ids' => 'context' ] as $field => $role ) {
				$section .= $this->renderRole( $role, $match[$field] ?? [], $canonical, $ids );
			}
			$branches = $match['branch_claim_ids'] ?? [];
			if ( is_array( $branches ) ) {
				foreach ( array_slice( $branches, 0, 3 ) as $branch ) {
					$section .= $this->renderRole( 'branch', $branch, $canonical, $ids, true );
				}
			}
			$html .= Html::rawElement( 'section', [ 'class' => 'wikikg-question-match' ], $section );
		}
		return Html::rawElement( 'section', [ 'class' => 'wikikg-question-result',
			'data-wikikg-question-status' => $result['status'] === 'blocked' ? 'blocked' : ( $query['status'] ?? 'blocked' ) ], $html );
	}

	private function renderReasons( array $reasons ): string {
		$keys = [ 'invalid_request', 'unresolved_identity', 'unresolved_parent', 'relationship_missing',
			'property_missing', 'scope_unknown', 'opposing_assertions', 'scalar_alternatives',
			'numeric_uncertain', 'numeric_unsupported', 'numeric_not_satisfied', 'partial_manifest',
			'limits', 'empty_result', 'authorization_failed', 'graph_unavailable', 'graph_corrupt',
			'graph_generation_changed', 'graph_timeout', 'graph_limit' ];
		$items = '';
		$seen = [];
		foreach ( array_slice( $reasons, 0, 100 ) as $reason ) {
			$code = is_array( $reason ) && is_string( $reason['code'] ?? null ) ? $reason['code'] : '';
			if ( !in_array( $code, $keys, true ) || isset( $seen[$code] ) ) { continue; }
			$seen[$code] = true;
			$items .= Html::element( 'li', [], $this->msg( 'wikikgquestions-reason-' . $code )->text() );
		}
		return $items === '' ? '' : Html::rawElement( 'ul', [ 'class' => 'wikikg-question-reasons' ], $items );
	}

	private function renderRole( string $role, $ids, array $canonical, array $allowed, bool $branch = false ): string {
		if ( !is_array( $ids ) || !array_is_list( $ids ) ) { return ''; }
		$rows = '';
		foreach ( array_slice( $ids, 0, 100 ) as $id ) {
			if ( !is_string( $id ) || !isset( $allowed[$id], $canonical[$id] ) ) { continue; }
			$claim = $canonical[$id];
			$label = $this->claimLabel( $claim );
			$evidence = '';
			foreach ( array_slice( $claim['evidence'] ?? [], 0, 10 ) as $source ) {
				if ( !is_array( $source ) ) { continue; }
				$evidence .= Html::element( 'blockquote', [], (string)( $source['supporting_span'] ??
					( $source['location'] ?? $source['page_title'] ?? '' ) ) );
				$evidence .= $this->sourceLink( $source );
			}
			$rows .= Html::rawElement( 'li', [ 'data-wikikg-claim' => $id,
				...( $branch ? [ 'data-wikikg-branch' => $role ] : [] ) ],
				Html::element( 'strong', [], $label ) . $this->renderQualifiers( $claim['qualifiers'] ?? [] ) . $evidence );
		}
		if ( $rows === '' ) { return ''; }
		return Html::element( 'h4', [], $this->msg( 'wikikgquestions-role-' . $role )->text() )
			. Html::rawElement( 'ul', [ 'class' => 'wikikg-question-role wikikg-question-role-' . $role ], $rows );
	}

	private function sourceLink( array $source ): string {
		$titleText = $source['page_title'] ?? null;
		$revision = $source['revision_id'] ?? null;
		if ( !is_string( $titleText ) || !is_int( $revision ) || $revision < 1 ) { return ''; }
		$title = Title::newFromText( $titleText );
		if ( !$title ) { return ''; }
		return Html::element( 'a', [ 'class' => 'wikikg-question-source',
			'href' => $title->getLocalURL( [ 'oldid' => $revision ] ) ],
			$this->msg( 'wikikgquestions-source', $titleText, $revision )->text() );
	}

	private function claimLabel( array $claim ): string {
		$statement = $this->entityLabel( $claim['subject'] ?? [] ) . ' - ' . (string)( $claim['predicate'] ?? '' );
		return ( $claim['claim_type'] ?? '' ) === 'relationship'
			? $statement . ' - ' . $this->entityLabel( $claim['object'] ?? [] )
			: $statement . ': ' . (string)( $claim['value'] ?? '' );
	}

	private function renderQualifiers( $qualifiers ): string {
		if ( $qualifiers instanceof \stdClass ) { $qualifiers = get_object_vars( $qualifiers ); }
		if ( !is_array( $qualifiers ) || !$qualifiers ) { return ''; }
		$items = '';
		foreach ( array_slice( $qualifiers, 0, 20, true ) as $key => $value ) {
			if ( !is_scalar( $value ) ) { continue; }
			$items .= Html::rawElement( 'div', [], Html::element( 'dt', [], (string)$key )
				. Html::element( 'dd', [], (string)$value ) );
		}
		return Html::rawElement( 'dl', [ 'class' => 'wikikg-question-qualifiers' ], $items );
	}

	private function entityLabel( $entity ): string {
		if ( !is_array( $entity ) ) { return ''; }
		return trim( (string)( $entity['type'] ?? '' ) . ': ' . (string)( $entity['id'] ?? $entity['crop_id'] ?? '' ), ': ' );
	}

	private function status( string $status, string $message ): string {
		return Html::element( 'p', [ 'class' => 'wikikg-question-status', 'data-wikikg-question-status' => $status ],
			$this->msg( $message )->text() );
	}

	private function failure( string $reason ): array {
		return [ 'status' => 'dependency_failure', 'query' => null, 'reasons' => [ [ 'code' => $reason ] ] ];
	}

	protected function getGroupName() { return 'other'; }
}
