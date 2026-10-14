<?php

namespace MediaWiki\Extension\WikiKGExtractor;

use RuntimeException;
use Title;

/** Hash-pinned allowlist of the protocol's source page and selected development pages. */
final class OfflineDevelopmentScope {
	private const PINNED_MANIFEST_HASH = 'e517e9d51f74439470a800a585b3accb9f15e8d5e2e5584bc03a8f8dbf586975';
	private const MAX_MANIFEST_BYTES = 5242880;
	/** @var array<string,array{page_id:int,revision_id:int,content_hash:string}> */
	private array $pages = [];
	/** @var array<int,string> */
	private array $pageIds = [];

	public function __construct( string $manifestPath, ?string $expectedHash = null ) {
		$expectedHash ??= self::PINNED_MANIFEST_HASH;
		if ( !preg_match( '/\A[a-f0-9]{64}\z/D', $expectedHash )
			|| is_link( $manifestPath ) || !is_file( $manifestPath )
		) {
			throw new RuntimeException( 'Development corpus manifest is missing or unsafe.' );
		}
		$real = realpath( $manifestPath );
		$size = $real === false ? false : filesize( $real );
		if ( $real === false || $size === false || $size > self::MAX_MANIFEST_BYTES ) {
			throw new RuntimeException( 'Development corpus manifest exceeds its size limit.' );
		}
		$bytes = file_get_contents( $real, false, null, 0, self::MAX_MANIFEST_BYTES + 1 );
		if ( $bytes === false || strlen( $bytes ) > self::MAX_MANIFEST_BYTES
			|| !hash_equals( $expectedHash, hash( 'sha256', $bytes ) )
		) {
			throw new RuntimeException( 'Development corpus manifest hash is not approved.' );
		}
		try {
			$manifest = json_decode( $bytes, true, 64, JSON_THROW_ON_ERROR );
		} catch ( \JsonException $exception ) {
			throw new RuntimeException( 'Development corpus manifest is invalid JSON.', 0, $exception );
		}
		if ( !is_array( $manifest ) || ( $manifest['schema_version'] ?? null ) !== '1.0'
			|| !is_string( $manifest['corpus'] ?? null ) || trim( $manifest['corpus'] ) === ''
			|| !is_array( $manifest['source'] ?? null )
			|| !is_array( $manifest['pages'] ?? null ) || !array_is_list( $manifest['pages'] )
			|| count( $manifest['pages'] ) > 20000
			|| !is_array( $manifest['selection'] ?? null )
			|| !is_array( $manifest['counts'] ?? null )
		) {
			throw new RuntimeException( 'Development corpus manifest shape is invalid.' );
		}
		$source = $manifest['source'];
		if ( !is_string( $source['discovery_heading_rule'] ?? null )
			|| trim( $source['discovery_heading_rule'] ) === ''
		) {
			throw new RuntimeException( 'Development corpus source metadata is invalid.' );
		}
		$selection = $manifest['selection'];
		$counts = $manifest['counts'];
		if ( !is_string( $selection['method'] ?? null ) || !is_string( $selection['strata'] ?? null )
			|| !is_string( $selection['seed'] ?? null )
			|| !is_string( $selection['heldout_exclusion_policy'] ?? null )
			|| !is_int( $selection['sample_size'] ?? null ) || $selection['sample_size'] < 2
			|| !is_int( $selection['dev_size'] ?? null ) || $selection['dev_size'] < 1
			|| !is_int( $selection['test_size'] ?? null ) || $selection['test_size'] < 1
			|| !is_array( $selection['previously_observed_titles'] ?? null )
			|| !array_is_list( $selection['previously_observed_titles'] )
			|| !is_int( $counts['discovered'] ?? null ) || $counts['discovered'] < 0
			|| !is_array( $counts['statuses'] ?? null )
			|| !is_int( $counts['selected'] ?? null ) || $counts['selected'] < 0
		) {
			throw new RuntimeException( 'Development corpus selection or count metadata is invalid.' );
		}
		foreach ( $selection['previously_observed_titles'] as $title ) {
			if ( !is_string( $title ) || trim( $title ) === '' ) {
				throw new RuntimeException( 'Development corpus title exclusions are invalid.' );
			}
		}
		foreach ( $counts['statuses'] as $count ) {
			if ( !is_int( $count ) || $count < 0 ) {
				throw new RuntimeException( 'Development corpus status counts are invalid.' );
			}
		}
		$this->addPage( $manifest['source'] );
		foreach ( $manifest['pages'] as $page ) {
			if ( !is_array( $page ) || !is_int( $page['discovery_index'] ?? null )
				|| $page['discovery_index'] < 0 || !is_string( $page['requested_title'] ?? null )
				|| trim( $page['requested_title'] ) === ''
				|| !in_array( $page['status'] ?? null, [ 'resolved', 'missing', 'redirect_unresolved', 'duplicate_target' ], true )
				|| !array_key_exists( 'resolved_title', $page )
				|| !( is_string( $page['resolved_title'] ) || $page['resolved_title'] === null )
				|| !is_bool( $page['selected'] ?? null )
				|| !array_key_exists( 'split', $page )
				|| !in_array( $page['split'], [ 'dev', 'test', null ], true )
				|| !is_bool( $page['eligible_for_development'] ?? null )
				|| !is_bool( $page['eligible_for_heldout'] ?? null )
				|| ( $page['selected'] && !in_array( $page['status'] ?? null, [ 'resolved' ], true ) )
				|| ( !$page['selected'] && $page['split'] !== null )
			) {
				throw new RuntimeException( 'Development corpus page metadata is invalid.' );
			}
			if ( $page['selected'] ) {
				self::validateIdentity( $page );
				if ( !is_string( $page['resolved_title'] ) || $page['status'] !== 'resolved'
					|| ( $page['split'] === 'dev' && !$page['eligible_for_development'] )
					|| ( $page['split'] === 'test'
						&& ( !$page['eligible_for_heldout'] || ( $page['heldout_exclusion_reason'] ?? null ) !== null ) )
				) {
					throw new RuntimeException( 'Selected page metadata is inconsistent with its split.' );
				}
			}
			if ( !$page['eligible_for_heldout']
				&& ( !is_string( $page['heldout_exclusion_reason'] ?? null )
					|| trim( $page['heldout_exclusion_reason'] ) === '' )
			) {
				throw new RuntimeException( 'Held-out exclusion metadata is invalid.' );
			}
			if ( $page['selected'] && $page['split'] === 'dev' ) {
				$this->addPage( $page );
			}
		}
	}

	/** @param array<string,mixed> $identity */
	public function assertSource( array $identity ): void {
		$title = $identity['title'] ?? $identity['page_title'] ?? null;
		if ( !is_string( $title ) || !is_int( $identity['page_id'] ?? null )
			|| !is_int( $identity['revision_id'] ?? null )
			|| !is_string( $identity['content_hash'] ?? null )
		) {
			throw new RuntimeException( 'Source identity is malformed or outside development scope.' );
		}
		$key = self::titleKey( $title );
		$page = $this->pages[$key] ?? null;
		if ( $page === null || $identity['page_id'] !== $page['page_id']
			|| $identity['revision_id'] !== $page['revision_id']
			|| !hash_equals( $page['content_hash'], $identity['content_hash'] )
		) {
			throw new RuntimeException( 'Source identity is not in the pinned development scope.' );
		}
	}

	/** @param array<string,mixed> $claim */
	public function assertProjectionClaim( array $claim ): void {
		$evidence = $claim['evidence'] ?? null;
		if ( !is_array( $evidence ) || !$evidence || !array_is_list( $evidence ) ) {
			throw new RuntimeException( 'Projection claim has no valid development evidence.' );
		}
		foreach ( $evidence as $source ) {
			if ( !is_array( $source ) ) {
				throw new RuntimeException( 'Projection evidence is malformed.' );
			}
			$this->assertSource( $source );
		}
	}

	/** @param array<string,mixed> $page */
	private function addPage( array $page ): void {
		$title = $page['title'] ?? $page['resolved_title'] ?? null;
		if ( !is_string( $title ) || !is_int( $page['page_id'] ?? null ) || $page['page_id'] < 1
			|| !is_int( $page['revision_id'] ?? null ) || $page['revision_id'] < 1
			|| !is_string( $page['content_hash'] ?? null )
			|| !preg_match( '/\A[a-f0-9]{64}\z/D', $page['content_hash'] )
		) {
			throw new RuntimeException( 'Development source identity is malformed.' );
		}
		$key = self::titleKey( $title );
		$value = [
			'page_id' => $page['page_id'],
			'revision_id' => $page['revision_id'],
			'content_hash' => $page['content_hash'],
		];
		if ( isset( $this->pages[$key] ) && $this->pages[$key] !== $value ) {
			throw new RuntimeException( 'Development manifest contains conflicting page identities.' );
		}
		if ( isset( $this->pageIds[$value['page_id']] ) && $this->pageIds[$value['page_id']] !== $key ) {
			throw new RuntimeException( 'Development manifest contains duplicate page IDs.' );
		}
		$this->pages[$key] = $value;
		$this->pageIds[$value['page_id']] = $key;
	}

	/** @param array<string,mixed> $page */
	private static function validateIdentity( array $page ): void {
		$title = $page['title'] ?? $page['resolved_title'] ?? null;
		if ( !is_string( $title ) || !is_int( $page['page_id'] ?? null ) || $page['page_id'] < 1
			|| !is_int( $page['revision_id'] ?? null ) || $page['revision_id'] < 1
			|| !is_string( $page['content_hash'] ?? null )
			|| !preg_match( '/\A[a-f0-9]{64}\z/D', $page['content_hash'] )
		) {
			throw new RuntimeException( 'Selected corpus page has a malformed identity.' );
		}
	}

	private static function titleKey( string $title ): string {
		$parsed = Title::newFromText( $title );
		if ( !$parsed || $parsed->getNamespace() !== NS_MAIN ) {
			throw new RuntimeException( 'Development title is invalid.' );
		}
		return $parsed->getPrefixedDBkey();
	}
}
