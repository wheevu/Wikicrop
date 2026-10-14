<?php

namespace MediaWiki\Extension\WikiKGExtractor;

use MediaWiki\MediaWikiServices;
use MediaWiki\Permissions\Authority;
use MediaWiki\Revision\RevisionRecord;
use MediaWiki\Revision\SlotRecord;
use RuntimeException;
use Title;
use Wikimedia\Rdbms\IDBAccessObject as RdbmsIDBAccessObject;

/** Validates offline artifacts against live MediaWiki source identities before persistence. */
final class OfflineReviewedImport {
	private const MAX_ARTIFACT_BYTES = 5242880;

	/** @return array{document:array<string,mixed>,snapshot_ids:string[]} */
	public static function import(
		ClaimReviewStore $store,
		string $claimsPath,
		string $rawPath,
		Authority $authority,
		?OfflineDevelopmentScope $scope
	): array {
		$document = self::verifyArtifacts( $claimsPath, $rawPath, $authority, $scope );
		$snapshotIds = $store->importVerified( $document );
		return [ 'document' => $document, 'snapshot_ids' => $snapshotIds ];
	}

	/** @return array<string,mixed> */
	public static function verifyArtifacts(
		string $claimsPath,
		string $rawPath,
		Authority $authority,
		?OfflineDevelopmentScope $scope
	): array {
		if ( !$authority->isAllowed( 'wikikgextractor-review' ) ) {
			throw new RuntimeException( 'Offline import requires the review right.' );
		}
		self::assertPrivateArtifacts( $claimsPath, $rawPath );
		$document = ClaimDocument::load( $claimsPath, $rawPath );
		foreach ( $document['metadata']['source_pages'] as $source ) {
			if ( $scope !== null ) {
				$scope->assertSource( $source );
			}
			self::assertLiveCurrentSource( $source, $authority );
		}
		return $document;
	}

	/** @param array<string,mixed> $source */
	public static function assertLiveCurrentSource( array $source, Authority $authority ): void {
		$titleText = $source['title'] ?? $source['page_title'] ?? null;
		if ( !is_string( $titleText ) || $titleText === '' || strlen( $titleText ) > 512
			|| preg_match( '//u', $titleText ) !== 1
			|| !is_int( $source['page_id'] ?? null ) || $source['page_id'] < 1 || $source['page_id'] > 4294967295
			|| !is_int( $source['revision_id'] ?? null ) || $source['revision_id'] < 1
			|| $source['revision_id'] > 4294967295
			|| !is_string( $source['content_hash'] ?? null )
			|| !preg_match( '/\A[a-f0-9]{64}\z/D', $source['content_hash'] )
		) {
			throw new RuntimeException( 'Offline source identity is malformed.' );
		}
		$services = MediaWikiServices::getInstance();
		$page = $services->getPageStore()->getPageById(
			$source['page_id'],
			RdbmsIDBAccessObject::READ_LATEST
		);
		$title = $page ? Title::newFromPageIdentity( $page ) : null;
		$inputTitle = Title::newFromText( $titleText );
		if ( !$page || $page->getId() !== $source['page_id']
			|| $page->getLatest() !== $source['revision_id']
			|| !$title || !$inputTitle
			|| $title->getPrefixedDBkey() !== $inputTitle->getPrefixedDBkey()
		) {
			throw new RuntimeException( 'Offline source is not the current MediaWiki page revision.' );
		}
		$revision = $services->getRevisionLookup()->getRevisionById(
			$source['revision_id'],
			RdbmsIDBAccessObject::READ_LATEST,
			$page
		);
		if ( !$revision || $revision->getPageId() !== $source['page_id']
			|| !self::canReadRevisionMetadata( $revision, $authority )
			|| !ClaimEvidenceAccess::canReadCurrent( $authority, [ [
				'page_id' => $source['page_id'],
				'revision_id' => $source['revision_id'],
			] ] )
		) {
			throw new RuntimeException( 'Offline source revision is suppressed, unreadable, or stale.' );
		}
		$content = $revision->getContentOrThrow( SlotRecord::MAIN, RevisionRecord::FOR_THIS_USER, $authority );
		if ( $content->getModel() !== CONTENT_MODEL_WIKITEXT || !( $content instanceof \TextContent ) ) {
			throw new RuntimeException( 'Offline source main slot is not UTF-8 wikitext.' );
		}
		$text = $content->getText();
		if ( preg_match( '//u', $text ) !== 1
			|| !hash_equals( $source['content_hash'], hash( 'sha256', $text ) )
		) {
			throw new RuntimeException( 'Offline source content does not match its live revision hash.' );
		}
		$currentPage = $services->getPageStore()->getPageById(
			$source['page_id'],
			RdbmsIDBAccessObject::READ_LATEST
		);
		$currentTitle = $currentPage ? Title::newFromPageIdentity( $currentPage ) : null;
		$stillCurrent = $currentPage !== null
			&& $currentPage->getId() === $source['page_id']
			&& $currentPage->getLatest() === $source['revision_id']
			&& $currentTitle !== null
			&& $currentTitle->getPrefixedDBkey() === $inputTitle->getPrefixedDBkey();
		$textStillVisible = self::canReadRevisionMetadata( $revision, $authority );
		$pageStillReadable = ClaimEvidenceAccess::canReadCurrent( $authority, [ [
				'page_id' => $source['page_id'],
				'revision_id' => $source['revision_id'],
			] ] );
		if ( !$stillCurrent || !$textStillVisible || !$pageStillReadable ) {
			throw new RuntimeException( 'Offline source changed or lost visibility during verification.' );
		}
	}

	private static function canReadRevisionMetadata( RevisionRecord $revision, Authority $authority ): bool {
		$flags = RevisionRecord::DELETED_TEXT | RevisionRecord::DELETED_USER
			| RevisionRecord::DELETED_COMMENT | RevisionRecord::DELETED_RESTRICTED;
		$metadata = MediaWikiServices::getInstance()->getConnectionProvider()->getPrimaryDatabase()
			->newSelectQueryBuilder()->select( 'rev_deleted' )->from( 'revision' )
			->where( [ 'rev_id' => $revision->getId() ] )->caller( __METHOD__ )->fetchRow();
		if ( !$metadata || (int)$metadata->rev_deleted !== 0 || $revision->isDeleted( $flags ) ) {
			return false;
		}
		foreach ( [
			RevisionRecord::DELETED_TEXT,
			RevisionRecord::DELETED_USER,
			RevisionRecord::DELETED_COMMENT,
		] as $field ) {
			if ( !$revision->audienceCan( $field, RevisionRecord::FOR_THIS_USER, $authority ) ) {
				return false;
			}
		}
		return true;
	}

	private static function assertPrivateArtifacts( string $claimsPath, string $rawPath ): void {
		if ( PHP_OS_FAMILY === 'Windows' || !function_exists( 'posix_geteuid' ) ) {
			throw new RuntimeException( 'Cannot verify offline artifact ownership on this platform.' );
		}
		if ( basename( $claimsPath ) !== 'candidate_claims.json'
			|| basename( $rawPath ) !== 'raw_data.json'
		) {
			throw new RuntimeException( 'Offline claim artifact basenames are invalid.' );
		}
		$claimsDirectoryPath = dirname( $claimsPath );
		if ( is_link( $claimsDirectoryPath ) || dirname( $rawPath ) !== $claimsDirectoryPath ) {
			throw new RuntimeException( 'Offline artifact parents must be one non-symlink directory.' );
		}
		clearstatcache( true );
		$claimsReal = realpath( $claimsPath );
		$rawReal = realpath( $rawPath );
		$directory = realpath( dirname( $claimsPath ) );
		if ( !$claimsReal || !$rawReal || !$directory || dirname( $rawReal ) !== $directory
			|| dirname( $claimsReal ) !== $directory || is_link( $claimsPath ) || is_link( $rawPath )
			|| basename( $claimsReal ) !== 'candidate_claims.json'
			|| basename( $rawReal ) !== 'raw_data.json'
			|| !is_file( $claimsReal ) || !is_file( $rawReal )
		) {
			throw new RuntimeException( 'Offline claim artifacts must be regular files in one safe job directory.' );
		}
		clearstatcache( true, $directory );
		$directoryMode = fileperms( $directory );
		if ( fileowner( $directory ) !== posix_geteuid() || $directoryMode === false
			|| ( $directoryMode & 07777 ) !== 0700
		) {
			throw new RuntimeException( 'Offline job directory must be private and owned by the current process.' );
		}
		foreach ( [ $claimsReal, $rawReal ] as $path ) {
			clearstatcache( true, $path );
			$stat = lstat( $path );
			$size = filesize( $path );
			$mode = fileperms( $path );
			if ( $stat === false || ( $stat['mode'] & 0170000 ) !== 0100000 || $stat['nlink'] !== 1
				|| fileowner( $path ) !== posix_geteuid() || $mode === false || ( $mode & 07777 ) !== 0600
				|| $size === false || $size > self::MAX_ARTIFACT_BYTES
			) {
				throw new RuntimeException( 'Offline job artifacts must be owner-only, owned, and bounded.' );
			}
		}
	}
}
