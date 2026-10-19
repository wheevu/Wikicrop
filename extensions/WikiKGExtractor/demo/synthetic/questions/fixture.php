<?php
require '/var/www/html/w/maintenance/Maintenance.php';

use MediaWiki\Extension\WikiKGExtractor\ClaimReviewStore;
use MediaWiki\Maintenance\Maintenance;
use MediaWiki\Revision\SlotRecord;

/** Adds invented connected-question evidence after the frozen synthetic workflow. */
class SeedSyntheticQuestions extends Maintenance {
	private const ROOT = '/private/wikikg';
	private const EXTRACTOR = 'synthetic-direct-fixture.v1';

	private function guard( $services ): void {
		$root = self::ROOT;
		if ( getenv( 'WIKIKG_SYNTHETIC' ) !== '1'
			|| getenv( 'MW_CONFIG_FILE' ) !== $root . '/LocalSettings.synthetic.php'
			|| !is_file( $root . '/synthetic-marker' )
			|| trim( file_get_contents( $root . '/synthetic-marker' ) ) !== 'seeded' ) {
			throw new RuntimeException( 'Connected synthetic fixture guard refused.' );
		}
		$config = $services->getMainConfig();
		if ( $config->get( 'DBtype' ) !== 'sqlite' || $config->get( 'DBname' ) !== 'syntheticwiki'
			|| realpath( $config->get( 'SQLiteDataDir' ) ) !== $root . '/db'
			|| fileowner( $root ) !== posix_geteuid() || ( fileperms( $root ) & 07777 ) !== 0700 ) {
			throw new RuntimeException( 'Connected synthetic database identity refused.' );
		}
		$db = $services->getConnectionProvider()->getPrimaryDatabase();
		if ( $db->newSelectQueryBuilder()->select( '*' )->from( 'page' )->caller( __METHOD__ )->fetchRowCount() !== 8 ) {
			throw new RuntimeException( 'Connected fixture requires the known seven-page frozen seed.' );
		}
		foreach ( [ 'wikikg_snapshot', 'wikikg_claim', 'wikikg_evidence', 'wikikg_source_page', 'wikikg_review_event' ] as $table ) {
			if ( !$db->tableExists( $table, __METHOD__ ) ) {
				throw new RuntimeException( 'Claim store is not initialized.' );
			}
		}
		if ( $db->newSelectQueryBuilder()->select( '*' )->from( 'wikikg_snapshot' )->caller( __METHOD__ )->fetchRowCount() !== 9
			|| file_exists( $root . '/connected/fixture-ready' )
			|| file_exists( $root . '/connected/fixture-identities.json' )
			|| file_exists( $root . '/connected/fixture-provenance.json' )
			|| file_exists( $root . '/connected/questions.json' ) ) {
			throw new RuntimeException( 'Connected fixtures require fresh frozen claims and refuse reseeding.' );
		}
	}

	public function execute() {
		$services = $this->getServiceContainer();
		$this->guard( $services );
		$root = self::ROOT;
		$db = $services->getConnectionProvider()->getPrimaryDatabase();
		$connected = $root . '/connected';
		if ( !is_dir( $connected ) || is_link( $connected ) || fileowner( $connected ) !== posix_geteuid()
			|| ( fileperms( $connected ) & 07777 ) !== 0700 ) {
			throw new RuntimeException( 'Connected private data root refused.' );
		}
		$publicPath = __DIR__ . '/scenarios.json';
		$public = json_decode( file_get_contents( $publicPath ), true, 512, JSON_THROW_ON_ERROR );
		if ( !is_array( $public['fixtures']['articles'] ?? null )
			|| ( $public['fixtures']['counts']['direct_claims'] ?? null ) !== 37
			|| ( $public['fixtures']['counts']['unique_sources'] ?? null ) !== 12 ) {
			throw new RuntimeException( 'Public connected fixture definitions refused.' );
		}
		$user = $services->getUserFactory()->newFromName( 'Synthetic reviewer' );
		$sources = [];
		$claimLabels = [];
		$claims = [];
		foreach ( $public['fixtures']['articles'] as $article ) {
			$title = Title::newFromText( $article['title'] );
			$page = $services->getWikiPageFactory()->newFromTitle( $title );
			$text = $article['text'];
			$updater = $page->newPageUpdater( $user );
			$updater->setContent( SlotRecord::MAIN, ContentHandler::makeContent( $text, $title ) );
			$revision = $updater->saveRevision( CommentStoreComment::newUnsavedComment( 'Invented connected synthetic fixture' ), 0 );
			if ( !$revision || !$updater->getStatus()->isOK() ) {
				throw new RuntimeException( 'Connected synthetic page save failed.' );
			}
			$pageId = $revision->getPageId();
			$revisionId = $revision->getId();
			$contentHash = hash( 'sha256', $text );
			$source = [ 'title' => $title->getPrefixedText(), 'page_id' => $pageId,
				'revision_id' => $revisionId, 'content_hash' => $contentHash ];
			$sources[$article['key']] = $source;
			foreach ( $article['claims'] as $definition ) {
				$label = $definition['label'];
				$span = $definition['span'];
				$start = mb_strpos( $text, $span, 0, 'UTF-8' );
				if ( $start === false || mb_substr( $text, $start, mb_strlen( $span, 'UTF-8' ), 'UTF-8' ) !== $span ) {
					throw new RuntimeException( 'Connected fixture span is not present in source.' );
				}
				$claim = [ 'claim_id' => hash( 'sha256', 'connected-claim:' . $label ),
					'snapshot_id' => hash( 'sha256', 'connected-snapshot:' . $label ),
					'claim_type' => $definition['claim_type'], 'subject' => $definition['subject'],
					'predicate' => $definition['predicate'], 'qualifiers' => $definition['qualifiers'],
					'traceability_complete' => true, 'review' => [ 'status' => 'pending' ],
					'evidence' => [ [ 'evidence_id' => hash( 'sha256', 'connected-evidence:' . $label ),
						'page_id' => $pageId, 'revision_id' => $revisionId, 'content_hash' => $contentHash,
						'page_title' => $title->getPrefixedText(), 'extraction_method' => 'rules',
						'extractor_version' => self::EXTRACTOR, 'location_type' => 'source_span',
						'source_offset_start' => $start,
						'source_offset_end' => $start + mb_strlen( $span, 'UTF-8' ),
						'supporting_span' => $span, 'span_hash' => hash( 'sha256', $span ) ] ] ];
				if ( isset( $definition['object'] ) ) {
					$claim['object'] = $definition['object'];
				} else {
					$claim['value'] = $definition['value'];
				}
				$claims[] = $claim;
				$claimLabels[$label] = $claim['snapshot_id'];
				$db->newInsertQueryBuilder()->insertInto( 'approve_queue' )->row( [
					'aq_page_id' => $pageId, 'aq_revision_id' => $revisionId,
					'aq_status' => 'approved', 'aq_is_latest' => 1,
				] )->caller( __METHOD__ )->execute();
			}
		}
		$store = new ClaimReviewStore( $services->getConnectionProvider() );
		$doc = [ 'schema_version' => '1.0', 'metadata' => [ 'extractor_version' => self::EXTRACTOR,
			'extraction_method' => 'rules', 'review_state' => 'pending', 'source_pages' => array_values( $sources ) ],
			'claims' => $claims ];
		$ids = $store->importVerified( $doc );
		if ( count( $ids ) !== 37 ) {
			throw new RuntimeException( 'Connected claims did not import completely.' );
		}
		foreach ( $claims as $claim ) {
			$store->recordDecision( $claim['snapshot_id'], (int)$user->getId(), 'approved', 'Invented demonstration evidence only.', 0 );
		}
		$catalog = [ 'version' => 'wikikg.synthetic-questions.v1', 'questions' => [] ];
		$scenarioDoc = json_decode( file_get_contents( __DIR__ . '/scenarios.json' ), true, 512, JSON_THROW_ON_ERROR );
		foreach ( $scenarioDoc['scenarios'] as $scenario ) {
			if ( isset( $scenario['mutation'] ) ) {
				continue;
			}
			$catalog['questions'][] = [ 'id' => $scenario['id'], 'label' => $scenario['label'], 'query' => $scenario['query'] ];
		}
		$catalog['questions'][] = [ 'id' => 'supported-review-sensitive',
			'label' => 'Review-sensitive synthetic compound example', 'query' => $scenarioDoc['scenarios'][0]['query'] ];
		$identities = [ 'sources' => $sources, 'claim_labels' => $claimLabels,
			'rejected_snapshot_id' => $claimLabels['alpha-resistance'],
			'source_change_claim_label' => 'source-change-target',
			'source_change_snapshot_id' => $claimLabels['source-change-target'],
			'source_change_source_label' => 'changed' ];
		$frozenInput = $root . '/seed-identities.json';
		if ( !is_file( $frozenInput ) ) {
			throw new RuntimeException( 'Frozen workflow identity receipt is missing.' );
		}
		$provenance = [ 'frozen_worker' => [ 'claim_count' => 9,
			'input_sha256' => hash_file( 'sha256', $frozenInput ) ],
			'direct_claim_store_fixtures' => [ 'claim_count' => count( $claims ), 'source_count' => count( $sources ),
				'extractor_version' => self::EXTRACTOR, 'not_frozen_worker_output' => true,
				'input_sha256' => hash( 'sha256', json_encode( $claims, JSON_UNESCAPED_UNICODE | JSON_THROW_ON_ERROR ) ) ] ];
		foreach ( [ 'questions.json' => $catalog, 'fixture-identities.json' => $identities,
			'fixture-provenance.json' => $provenance ] as $file => $data ) {
			$encoded = json_encode( $data, JSON_UNESCAPED_UNICODE | JSON_THROW_ON_ERROR );
			$handle = fopen( $root . '/connected/' . $file, 'x' );
			if ( !$handle || fwrite( $handle, $encoded ) !== strlen( $encoded ) || !fflush( $handle ) ) {
				if ( $handle ) {
					fclose( $handle );
				}
				throw new RuntimeException( 'Connected fixture metadata write failed.' );
			}
			fclose( $handle );
			chmod( $root . '/connected/' . $file, 0600 );
		}
		if ( file_put_contents( $root . '/connected/fixture-ready', 'ready' ) !== 5 ) {
			throw new RuntimeException( 'Connected fixture readiness marker write failed.' );
		}
		chmod( $root . '/connected/fixture-ready', 0600 );
		$this->output( json_encode( [ 'direct_fixture_claims' => count( $claims ), 'invented_sources' => count( $sources ),
			'frozen_worker_claims' => 9, 'catalog_questions' => count( $catalog['questions'] ) ] ) . "\n" );
	}
}
$maintClass = SeedSyntheticQuestions::class;
require_once RUN_MAINTENANCE_IF_MAIN;
