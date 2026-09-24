<?php

namespace MediaWiki\Extension\WikiKGExtractor;

use MediaWiki\MediaWikiServices;
use MediaWiki\Permissions\Authority;
use MediaWiki\Permissions\PermissionManager;
use MediaWiki\Revision\RevisionRecord;
use Throwable;
use Title;
use Wikimedia\Rdbms\IDBAccessObject as RdbmsIDBAccessObject;

/**
 * Checks whether an authority can still read every pinned claim source.
 */
final class ClaimEvidenceAccess {
	/**
	 * @param Authority $authority
	 * @param mixed $evidenceItems Evidence list from verified or persisted claims
	 * @return bool
	 */
	public static function canRead( Authority $authority, $evidenceItems ): bool {
		if ( !is_array( $evidenceItems ) || !$evidenceItems
			|| !array_is_list( $evidenceItems )
		) {
			return false;
		}

		try {
			$services = MediaWikiServices::getInstance();
			foreach ( $evidenceItems as $evidence ) {
				if ( !is_array( $evidence )
					|| !is_int( $evidence['page_id'] ?? null )
					|| $evidence['page_id'] < 1
					|| !is_int( $evidence['revision_id'] ?? null )
					|| $evidence['revision_id'] < 1
				) {
					return false;
				}

				$pageId = $evidence['page_id'];
				$page = $services->getPageStore()->getPageById(
					$pageId,
					RdbmsIDBAccessObject::READ_LATEST
				);
				if ( !$page || $page->getId() !== $pageId ) {
					return false;
				}
				$title = Title::newFromPageIdentity( $page );
				if ( !$services->getPermissionManager()->userCan(
					'read',
					$authority->getUser(),
					$title,
					PermissionManager::RIGOR_SECURE
				) ) {
					return false;
				}

				$revision = $services->getRevisionLookup()->getRevisionById(
					$evidence['revision_id'],
					RdbmsIDBAccessObject::READ_LATEST,
					$page
				);
				if ( !$revision || $revision->getPageId() !== $pageId
					|| !$revision->audienceCan(
						RevisionRecord::DELETED_TEXT,
						RevisionRecord::FOR_THIS_USER,
						$authority
					)
				) {
					return false;
				}
			}
		} catch ( Throwable $exception ) {
			return false;
		}

		return true;
	}

	/**
	 * Check whether every cited source is readable and still at its cited revision.
	 *
	 * @param Authority $authority
	 * @param mixed $evidenceItems Evidence list from verified or persisted claims
	 * @return bool
	 */
	public static function canReadCurrent( Authority $authority, $evidenceItems ): bool {
		if ( !self::canRead( $authority, $evidenceItems ) ) {
			return false;
		}

		try {
			$pageStore = MediaWikiServices::getInstance()->getPageStore();
			foreach ( $evidenceItems as $evidence ) {
				$page = $pageStore->getPageById(
					$evidence['page_id'],
					RdbmsIDBAccessObject::READ_LATEST
				);
				if ( !$page || $page->getId() !== $evidence['page_id']
					|| $page->getLatest() !== $evidence['revision_id']
				) {
					return false;
				}
			}
		} catch ( Throwable $exception ) {
			return false;
		}

		return true;
	}
}
