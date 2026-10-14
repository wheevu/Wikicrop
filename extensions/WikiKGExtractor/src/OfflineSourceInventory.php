<?php

namespace MediaWiki\Extension\WikiKGExtractor;

use RuntimeException;

/** Metadata-first inventory; content access is deferred until exclusion closure is known. */
class OfflineSourceInventory {
	public const MAX_PAGES = 1000;
	public const MANIFEST_SHA256 = 'e517e9d51f74439470a800a585b3accb9f15e8d5e2e5584bc03a8f8dbf586975';
	public const PROTOCOL_SHA256 = 'ad5457a097d1c702f97368ff91e403efd135d15eb7a458fe915f3d00edf8eb7e';

	/**
	 * @param array $pages Metadata rows keyed by page ID. Each has title, namespace, revision_id,
	 *  redirect_target (nullable title), and optional redirect_namespace.
	 * @param array $heldout Metadata identities from the hash-pinned manifest.
	 * @param callable $readContent Receives terminal metadata; its second argument requests classification-only access.
	 * @return array
	 */
	public function inventory( array $pages, array $heldout, callable $readContent, $limit = self::MAX_PAGES,
		$metadataOnly = false, array $selectedPages = [] ) {
		$limit = (int)$limit;
		if ( $limit < 1 || $limit > self::MAX_PAGES || count( $pages ) > $limit ) {
			throw new RuntimeException( 'Metadata page count exceeds the hard inventory limit.' );
		}
		$byTitle = [];
		$byId = [];
		$pageIds = [];
		$mainTitleKeys = [];
		foreach ( $pages as $page ) {
			if ( !is_array( $page ) || !isset( $page['page_id'], $page['title'], $page['namespace'], $page['revision_id'] )
				|| !is_int( $page['page_id'] ) || !is_string( $page['title'] )
				|| !is_int( $page['namespace'] ) || !is_int( $page['revision_id'] )
				|| $page['page_id'] < 1 || $page['page_id'] > 4294967295
				|| $page['revision_id'] < 1 || $page['revision_id'] > 4294967295
				|| $page['namespace'] < 0 || $page['namespace'] > 4294967295
				|| preg_match( '//u', $page['title'] ) !== 1 || trim( $page['title'] ) === ''
				|| ( array_key_exists( 'redirect_target', $page ) && $page['redirect_target'] !== null
					&& ( !is_string( $page['redirect_target'] ) || preg_match( '//u', $page['redirect_target'] ) !== 1 ) )
				|| ( array_key_exists( 'redirect_namespace', $page ) && ( !is_int( $page['redirect_namespace'] )
					|| $page['redirect_namespace'] < 0 || $page['redirect_namespace'] > 4294967295 ) )
				|| ( array_key_exists( 'redirect_external', $page ) && !is_bool( $page['redirect_external'] ) )
			) {
				throw new RuntimeException( 'Incomplete metadata identity; inventory aborted.' );
			}
			if ( isset( $pageIds[$page['page_id']] ) ) {
				throw new RuntimeException( 'Duplicate page ID; inventory aborted before content access.' );
			}
			$pageIds[$page['page_id']] = true;
			$byId[$page['page_id']] = $page;
			if ( $page['namespace'] === 0 ) {
				$titleKey = $this->key( $page['title'] );
				if ( $titleKey === '' || isset( $mainTitleKeys[$titleKey] ) ) {
					throw new RuntimeException( 'Ambiguous normalized main-namespace title; inventory aborted before content access.' );
				}
				$mainTitleKeys[$titleKey] = true;
				$byTitle[$titleKey] = $page;
			}
		}
		$excludedIds = [];
		$excludedTitles = [];
		foreach ( $heldout as $entry ) {
			if ( isset( $entry['page_id'] ) ) {
				$excludedIds[(int)$entry['page_id']] = true;
			}
			foreach ( [ 'title', 'requested_title', 'resolved_title' ] as $field ) {
				if ( isset( $entry[$field] ) && is_string( $entry[$field] ) ) {
					$excludedTitles[$this->key( $entry[$field] )] = true;
				}
			}
		}
		$heldRows = [];
		foreach ( $heldout as $entry ) {
			if ( isset( $entry['page_id'] ) && isset( $byId[(int)$entry['page_id']] ) ) {
				$heldRows[$byId[(int)$entry['page_id']]['page_id']] = $byId[(int)$entry['page_id']];
			}
			foreach ( [ 'title', 'requested_title', 'resolved_title' ] as $field ) {
				$titleKey = isset( $entry[$field] ) && is_string( $entry[$field] ) ? $this->key( $entry[$field] ) : '';
				if ( $titleKey !== '' && isset( $byTitle[$titleKey] ) ) {
					$heldRows[$byTitle[$titleKey]['page_id']] = $byTitle[$titleKey];
				}
			}
		}
		foreach ( $heldRows as $heldPage ) {
			if ( $heldPage['namespace'] === 0 && $this->terminalId( $heldPage, $byTitle ) === null ) {
				throw new RuntimeException( 'Held-out redirect closure is ambiguous; no source content may be read.' );
			}
		}

		$rows = [];
		$contentCache = [];
		foreach ( $pages as $page ) {
			$row = [
				'page_id' => $page['page_id'], 'title' => $page['title'],
				'namespace' => $page['namespace'],
				'revision_id' => $page['revision_id'], 'eligible' => false,
				'reason' => 'unclassified', 'kind' => null, 'crop' => null,
				'redirect_target' => $page['redirect_target'] ?? null,
				'redirect_namespace' => $page['redirect_namespace'] ?? 0,
				'redirect_external' => !empty( $page['redirect_external'] ),
			];
			if ( $page['namespace'] !== 0 ) {
				$row['reason'] = 'unsupported_namespace';
				$rows[] = $row;
				continue;
			}
			$chain = [];
			$current = $page;
			$failed = null;
			for ( $hop = 0; $hop <= count( $pages ); $hop++ ) {
				$id = $current['page_id'];
				$titleKey = $this->key( $current['title'] );
				if ( isset( $excludedIds[$id] ) || isset( $excludedTitles[$titleKey] ) ) {
					$failed = 'heldout_target_or_alias';
					break;
				}
				if ( isset( $chain[$id] ) ) {
					$failed = 'redirect_cycle';
					break;
				}
				$chain[$id] = true;
				$target = $current['redirect_target'] ?? null;
				if ( !empty( $current['redirect_external'] ) ) {
					$failed = 'external_redirect';
					break;
				}
				if ( ( $current['redirect_namespace'] ?? 0 ) !== 0 ) {
					$failed = 'unsupported_redirect_namespace';
					break;
				}
				if ( $target === null || $target === '' ) {
					break;
				}
				$next = $byTitle[$this->key( $target )] ?? null;
				if ( $next === null ) {
					$failed = 'unresolved_redirect';
					break;
				}
				$current = $next;
			}
			if ( $failed !== null ) {
				$row['reason'] = $failed;
				$row['redirect_chain'] = array_keys( $chain );
				$rows[] = $row;
				continue;
			}
			if ( isset( $excludedIds[$current['page_id']] )
				|| isset( $excludedTitles[$this->key( $current['title'] )] )
			) {
				$row['reason'] = 'heldout_target_or_alias';
				$rows[] = $row;
				continue;
			}
			// Check all aliases that terminate here, before invoking the reader.
			$aliases = [];
			foreach ( $pages as $candidate ) {
				if ( $this->terminalId( $candidate, $byTitle ) === $current['page_id'] ) {
					$aliases[] = $candidate;
				}
			}
			$heldAlias = false;
			foreach ( $aliases as $alias ) {
				if ( isset( $excludedIds[$alias['page_id']] ) || isset( $excludedTitles[$this->key( $alias['title'] )] ) ) {
					$heldAlias = true;
					break;
				}
			}
			if ( $heldAlias ) {
				$row['reason'] = 'heldout_target_or_alias';
				$rows[] = $row;
				continue;
			}
			// Stable terminal identity belongs to safe, non-excluded rows regardless of batch selection or classification.
			$row = array_replace( $row, [ 'resolved_title' => $current['title'],
				'resolved_page_id' => $current['page_id'], 'resolved_revision_id' => $current['revision_id'],
				'redirect_chain' => array_keys( $chain ) ] );
			if ( $page['page_id'] !== $current['page_id'] ) {
				$row['reason'] = 'redirect_alias';
				$rows[] = $row;
				continue;
			}
			if ( $metadataOnly ) {
				$classification = $readContent( $current, true );
				$kind = $classification['kind'] ?? null;
				$crop = $classification['crop'] ?? null;
				if ( !in_array( $kind, [ 'species', 'variety' ], true ) || !is_string( $crop ) || trim( $crop ) === '' ) {
					$row['reason'] = 'unknown_classification';
				} else {
					$row = array_replace( $row, [ 'eligible' => false, 'reason' => 'awaiting_collection',
						'kind' => $kind, 'crop' => $crop ] );
				}
				$rows[] = $row;
				continue;
			}
			if ( $selectedPages && !isset( $selectedPages[$current['page_id']] ) ) {
				$classification = $readContent( $current, true );
				$kind = is_array( $classification ) ? ( $classification['kind'] ?? null ) : null;
				$crop = is_array( $classification ) ? ( $classification['crop'] ?? null ) : null;
				$classified = in_array( $kind, [ 'species', 'variety' ], true )
					&& is_string( $crop ) && trim( $crop ) !== '';
				$row = array_replace( $row, [ 'reason' => $classified ? 'not_selected_in_batch' : 'unknown_classification',
					'kind' => $classified ? $kind : null, 'crop' => $classified ? $crop : null ] );
				$rows[] = $row;
				continue;
			}
			$data = $contentCache[$current['page_id']] ?? null;
			if ( $data === null ) {
				$data = $readContent( $current );
				$contentCache[$current['page_id']] = $data;
			}
			if ( isset( $data['reason'] ) && is_string( $data['reason'] ) ) {
				$row['reason'] = $data['reason'];
				$rows[] = $row;
				continue;
			}
			if ( !is_array( $data ) || !empty( $data['unreadable'] ) || !empty( $data['suppressed'] ) ) {
				$row['reason'] = !empty( $data['suppressed'] ) ? 'suppressed' : 'unreadable';
				$rows[] = $row;
				continue;
			}
			$kind = $data['kind'] ?? null;
			$crop = $data['crop'] ?? null;
			if ( !in_array( $kind, [ 'species', 'variety' ], true ) || !is_string( $crop ) || trim( $crop ) === '' ) {
				$row['reason'] = 'unknown_classification';
				$rows[] = $row;
				continue;
			}
			$text = $data['wikitext'] ?? null;
			if ( !is_string( $text ) || $text === '' ) {
				$row['reason'] = 'unreadable';
				$rows[] = $row;
				continue;
			}
			if ( ( $data['page_id'] ?? null ) !== $current['page_id']
				|| ( $data['revision_id'] ?? null ) !== $current['revision_id']
			) {
				$row['reason'] = 'stale_page_revision';
				$rows[] = $row;
				continue;
			}
			if ( $page['page_id'] !== $current['page_id'] ) {
				$row['reason'] = 'redirect_alias';
				$rows[] = $row;
				continue;
			}
			$row = array_replace( $row, [
				'eligible' => true, 'reason' => null, 'kind' => $kind,
				'resolved_title' => $current['title'], 'resolved_page_id' => $current['page_id'],
				'redirect_chain' => array_keys( $chain ),
				'crop' => $crop, 'content_hash' => hash( 'sha256', $text ),
				'wikitext' => $text, 'resolved_revision_id' => $current['revision_id'],
				'source_file' => $data['source_file'] ?? null,
			] );
			$rows[] = $row;
		}
		return [ 'schema_version' => 'wikikg.offline-source-inventory.v1', 'pages' => $rows ];
	}

	private function key( $title ) {
		$title = str_replace( '_', ' ', trim( (string)$title ) );
		$title = preg_replace( '/\s+/u', ' ', $title );
		if ( class_exists( '\\Normalizer' ) ) {
			$title = \Normalizer::normalize( $title, \Normalizer::FORM_C );
		}
		if ( function_exists( 'mb_convert_case' ) && defined( 'MB_CASE_FOLD' ) ) {
			return mb_convert_case( $title, MB_CASE_FOLD, 'UTF-8' );
		}
		return function_exists( 'mb_strtolower' ) ? mb_strtolower( $title, 'UTF-8' ) : strtolower( $title );
	}

	private function terminalId( array $page, array $byTitle ) {
		$seen = [];
		for ( $hop = 0; $hop <= count( $byTitle ); $hop++ ) {
			if ( isset( $seen[$page['page_id']] ) ) {
				return null;
			}
			$seen[$page['page_id']] = true;
			$target = $page['redirect_target'] ?? null;
			if ( !empty( $page['redirect_external'] ) || ( $page['redirect_namespace'] ?? 0 ) !== 0 ) {
				return null;
			}
			if ( !$target ) {
				return $target ? null : $page['page_id'];
			}
			$page = $byTitle[$this->key( $target )] ?? null;
			if ( $page === null ) {
				return null;
			}
		}
		return null;
	}
}
