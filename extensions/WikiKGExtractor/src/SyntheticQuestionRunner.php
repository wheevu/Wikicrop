<?php

namespace MediaWiki\Extension\WikiKGExtractor;

use MediaWiki\MediaWikiServices;
use MediaWiki\Permissions\Authority;
use RuntimeException;
use Throwable;

/** Runs only server-owned questions against the disposable connected demo. */
class SyntheticQuestionRunner {
	private const MAX_FILE_BYTES = 1048576;
	private const MAX_CATALOG_BYTES = 65536;
	private const TIMEOUT_SECONDS = 30;
	private const MAX_SUPERVISION_SECONDS = 34;
	private const MAX_LOG_BYTES = 65536;

	/** @return array<int,array{id:string,label:string,query:array}> */
	public function listQuestions(): array {
		$this->assertEnabledRuntime();
		return $this->readCatalog()['questions'];
	}

	/** @return array<string,mixed> */
	public function run( string $questionId, Authority $authority ): array {
		if ( !$authority->getUser()->isRegistered() || !$authority->isAllowed( 'wikikgextractor-review' ) ) {
			return $this->failure( 'authorization_refused' );
		}
		$this->assertEnabledRuntime();
		$question = null;
		foreach ( $this->readCatalog()['questions'] as $item ) {
			if ( $item['id'] === $questionId ) {
				$question = $item;
				break;
			}
		}
		if ( $question === null ) {
			return $this->failure( 'invalid_question' );
		}
		$directory = $this->directory();
		$manifestPath = $directory . '/reviewed.json';
		try {
			$manifest = $this->readJsonFile( $manifestPath, self::MAX_FILE_BYTES, 0600 );
			$identities = $this->manifestIdentities( $manifest );
			$watermark = $manifest['source_watermark'];
			$gate = $this->newGate();
			$before = $gate->authorize( $authority, $identities, $watermark );
			if ( $before['status'] !== 'ok' ) {
				return $this->failure( $gate->getFailureReason() );
			}
			$attempt = $this->newAttemptDirectory( $directory . '/attempts' );
			$requestPath = $attempt . '/request.json';
			$resultPath = $attempt . '/result.json';
			$stdoutPath = $attempt . '/stdout.log';
			$stderrPath = $attempt . '/stderr.log';
			$this->writeNewFile( $requestPath, json_encode( [
				'version' => 'wikikg.offline-question.v1', 'query' => $question['query'],
			], JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR ) . "\n" );
			$repo = realpath( dirname( __DIR__, 3 ) );
			$web = realpath( (string)( $_SERVER['DOCUMENT_ROOT'] ?? '' ) );
			if ( !$repo || !$web ) {
				return $this->failure( 'runtime_unavailable' );
			}
			$argv = [ '/usr/bin/timeout', '--signal=TERM', '--kill-after=2s', '30s',
				'/usr/local/bin/python3', $repo . '/extensions/WikiKGExtractor/tools/offline_answer.py',
				'--manifest', $manifestPath, '--request', $requestPath, '--output', $resultPath,
				'--actor', $authority->getUser()->getName(), '--wiki-root', $repo,
				'--document-root', $web, '--offline-only', '--synthetic-only', '--audience', 'actor-only',
				'--graph-uri', 'bolt://127.0.0.1:7687', '--graph-user', 'neo4j',
				'--graph-password-file', $directory . '/graph-password', '--php', '/usr/local/bin/php' ];
			$exitCode = $this->invokeProcess( $argv, $stdoutPath, $stderrPath );
			$after = $gate->authorize( $authority, $identities, $watermark );
			if ( $after['status'] !== 'ok' || $this->canonical( $before['claims'] ) !== $this->canonical( $after['claims'] ) ) {
				return $this->failure( $gate->getFailureReason() );
			}
			if ( !in_array( $exitCode, [ 0, 3 ], true ) || !is_file( $resultPath ) ) {
				return $this->failure( 'question_unavailable' );
			}
			$result = $this->readJsonFile( $resultPath, self::MAX_FILE_BYTES, 0600 );
			if ( !$this->validResult( $result, $exitCode ) ) {
				return $this->failure( 'invalid_result' );
			}
			if ( ( $result['query']['status'] ?? null ) === 'dependency_failure' ) {
				return $this->failure( 'question_unavailable' );
			}
			$result['reasons'] = $this->safeReasons( $result['reasons'] ?? [] );
			$result['query']['reasons'] = $this->safeReasons( $result['query']['reasons'] ?? [] );
			$result['citations'] = [];
			$result['_canonical_claims'] = $after['claims'];
			return $result;
		} catch ( Throwable $exception ) {
			return $this->failure( 'question_unavailable' );
		}
	}

	/** Test seam: production always uses proc_open with an argv array and no shell. */
	protected function invokeProcess( array $argv, string $stdoutPath, string $stderrPath ): int {
		if ( !function_exists( 'proc_open' ) ) {
			throw new RuntimeException( 'process unavailable' );
		}
		$process = null;
		$pipes = [];
		$handles = [];
		$lastStatus = null;
		$started = microtime( true );
		$stored = [ 0, 0 ];
		try {
			$stdout = fopen( $stdoutPath, 'x' );
			if ( is_resource( $stdout ) ) { $handles[] = $stdout; }
			$stderr = fopen( $stderrPath, 'x' );
			if ( is_resource( $stderr ) ) { $handles[] = $stderr; }
			if ( !$stdout || !$stderr ) { throw new RuntimeException( 'log unavailable' ); }
			if ( !chmod( $stdoutPath, 0600 ) || !chmod( $stderrPath, 0600 ) ) {
				throw new RuntimeException( 'log mode refused' );
			}
			$environment = $this->processEnvironment( (string)( $_SERVER['DOCUMENT_ROOT'] ?? '' ) );
			$process = proc_open( $argv, [ 0 => [ 'pipe', 'r' ], 1 => [ 'pipe', 'w' ], 2 => [ 'pipe', 'w' ] ], $pipes,
				null, $environment, [ 'bypass_shell' => true ] );
			if ( !is_resource( $process ) ) { throw new RuntimeException( 'process unavailable' ); }
			fclose( $pipes[0] );
			unset( $pipes[0] );
			stream_set_blocking( $pipes[1], false );
			stream_set_blocking( $pipes[2], false );
			while ( true ) {
				foreach ( [ 1, 2 ] as $index ) {
					$chunk = fread( $pipes[$index], 8192 );
					if ( $chunk !== false && $chunk !== '' && $stored[$index - 1] < self::MAX_LOG_BYTES ) {
						$piece = substr( $chunk, 0, self::MAX_LOG_BYTES - $stored[$index - 1] );
						if ( fwrite( $handles[$index - 1], $piece ) !== strlen( $piece ) ) {
							throw new RuntimeException( 'log write failed' );
						}
						$stored[$index - 1] += strlen( $piece );
					}
				}
				$lastStatus = proc_get_status( $process );
				if ( !$lastStatus['running'] ) { break; }
				if ( microtime( true ) - $started >= self::MAX_SUPERVISION_SECONDS ) {
					throw new RuntimeException( 'process supervision deadline' );
				}
				usleep( 20000 );
			}
			return $this->closeAndReap( $process, $pipes, $handles, $lastStatus );
		} catch ( Throwable $exception ) {
			$this->terminateAndReap( $process, $pipes, $handles );
			throw new RuntimeException( 'bounded question process failed' );
		}
	}

	/** Return only known synthetic child variables, never inherited credentials. */
	protected function processEnvironment( string $documentRoot ): array {
		$web = realpath( $documentRoot );
		$port = getenv( 'WIKIKG_HTTP_PORT' );
		if ( !$web || $web !== $documentRoot || !is_dir( $web )
			|| !is_string( $port ) || !preg_match( '/\A[0-9]{1,5}\z/D', $port )
			|| (int)$port < 1024 || (int)$port > 65535
		) {
			throw new RuntimeException( 'Synthetic child environment refused.' );
		}
		return [
			'PATH' => '/usr/local/bin:/usr/bin:/bin',
			'PYTHONIOENCODING' => 'utf-8',
			'WIKIKG_SYNTHETIC' => '1',
			'MW_CONFIG_FILE' => '/private/wikikg/LocalSettings.synthetic.php',
			'WIKIKG_HTTP_PORT' => $port,
			'DOCUMENT_ROOT' => $web,
		];
	}

	private function closeAndReap( $process, array &$pipes, array &$handles, ?array $lastStatus ): int {
		$this->closePipes( $pipes );
		$this->closeHandles( $handles );
		$code = proc_close( $process );
		if ( $code === -1 && is_array( $lastStatus ) && $lastStatus['exitcode'] >= 0 ) {
			$code = (int)$lastStatus['exitcode'];
		}
		return $code;
	}

	private function terminateAndReap( $process, array &$pipes, array &$handles ): void {
		if ( is_resource( $process ) ) {
			$status = proc_get_status( $process );
			if ( $status['running'] ) {
				@proc_terminate( $process, 15 );
				$deadline = microtime( true ) + 0.5;
				while ( microtime( true ) < $deadline ) {
					$status = proc_get_status( $process );
					if ( !$status['running'] ) { break; }
					usleep( 10000 );
				}
				if ( $status['running'] ) { @proc_terminate( $process, 9 ); }
			}
		}
		$this->closePipes( $pipes );
		$this->closeHandles( $handles );
		if ( is_resource( $process ) ) { @proc_close( $process ); }
	}

	private function closePipes( array &$pipes ): void {
		foreach ( $pipes as $pipe ) { if ( is_resource( $pipe ) ) { fclose( $pipe ); } }
		$pipes = [];
	}

	private function closeHandles( array &$handles ): void {
		foreach ( $handles as $handle ) { if ( is_resource( $handle ) ) { fclose( $handle ); } }
		$handles = [];
	}

	protected function assertEnabledRuntime(): void {
		$services = MediaWikiServices::getInstance();
		$config = $services->getMainConfig();
		global $wgDBtype, $wgDBname, $wgSQLiteDataDir, $wgWikiKGSyntheticVerificationRoot;
		if ( !$config->get( 'WikiKGSyntheticQuestionsEnabled' ) || getenv( 'WIKIKG_SYNTHETIC' ) !== '1'
			|| $wgWikiKGSyntheticVerificationRoot !== '/private/wikikg'
			|| $wgDBtype !== 'sqlite' || $wgDBname !== 'syntheticwiki'
			|| realpath( (string)$wgSQLiteDataDir ) !== '/private/wikikg/db'
			|| $config->get( 'DBtype' ) !== 'sqlite' || $config->get( 'DBname' ) !== 'syntheticwiki'
			|| realpath( (string)$config->get( 'SQLiteDataDir' ) ) !== '/private/wikikg/db'
		) {
			throw new RuntimeException( 'Synthetic question runtime refused.' );
		}
		$this->directory();
	}

	protected function directory(): string {
		$path = MediaWikiServices::getInstance()->getMainConfig()->get( 'WikiKGSyntheticQuestionsDirectory' );
		$root = realpath( '/private/wikikg' );
		$real = is_string( $path ) && $path !== '' ? realpath( $path ) : false;
		$repo = realpath( dirname( __DIR__, 3 ) );
		$web = realpath( (string)( $_SERVER['DOCUMENT_ROOT'] ?? '' ) );
		if ( !function_exists( 'posix_geteuid' ) || !$root || $root !== '/private/wikikg'
			|| fileowner( $root ) !== posix_geteuid() || ( fileperms( $root ) & 07777 ) !== 0700
			|| !is_string( $path ) || !$real || $real !== '/private/wikikg/connected' || $path !== $real || is_link( $path )
			|| dirname( $real ) !== $root || fileowner( $real ) !== posix_geteuid()
			|| ( fileperms( $real ) & 07777 ) !== 0700 || !$repo || !$web
			|| $this->within( $real, $repo ) || $this->within( $real, $web )
			|| getenv( 'WIKIKG_SYNTHETIC' ) !== '1'
		) {
			throw new RuntimeException( 'Synthetic question directory refused.' );
		}
		$this->assertPrivateFile( $root . '/synthetic-marker', 0600 );
		if ( trim( (string)file_get_contents( $root . '/synthetic-marker' ) ) !== 'seeded' ) {
			throw new RuntimeException( 'Synthetic question marker refused.' );
		}
		return $real;
	}

	private function readCatalog(): array {
		$catalog = $this->readJsonFile( $this->directory() . '/questions.json', self::MAX_CATALOG_BYTES, 0600 );
		if ( !$this->exactKeys( $catalog, [ 'version', 'questions' ] )
			|| $catalog['version'] !== 'wikikg.synthetic-questions.v1'
			|| !is_array( $catalog['questions'] ) || !array_is_list( $catalog['questions'] )
			|| count( $catalog['questions'] ) < 1 || count( $catalog['questions'] ) > 20
		) {
			throw new RuntimeException( 'Synthetic question catalog invalid.' );
		}
		$seen = [];
		foreach ( $catalog['questions'] as &$item ) {
			if ( !is_array( $item ) || !$this->exactKeys( $item, [ 'id', 'label', 'query' ] )
				|| !is_string( $item['id'] ) || !preg_match( '/\A[a-z][a-z0-9-]{0,63}\z/D', $item['id'] )
				|| isset( $seen[$item['id']] ) || !is_string( $item['label'] ) || trim( $item['label'] ) === ''
				|| mb_strlen( $item['label'], 'UTF-8' ) > 160 || !is_array( $item['query'] )
				|| !$this->validRequest( $item['query'] )
			) {
				throw new RuntimeException( 'Synthetic question catalog invalid.' );
			}
			$seen[$item['id']] = true;
		}
		unset( $item );
		return $catalog;
	}

	private function validRequest( array $query ): bool {
		$allowed = [ 'intent', 'purpose', 'entities', 'max_depth', 'path', 'target', 'branches', 'filters', 'qualifiers' ];
		if ( array_diff( array_keys( $query ), $allowed ) || !in_array( $query['intent'] ?? null,
			[ 'lookup', 'path', 'filter', 'conjunction' ], true ) || !is_array( $query['entities'] ?? null )
			|| !array_is_list( $query['entities'] ) || count( $query['entities'] ) < 1
			|| count( $query['entities'] ) > 20 || isset( $query['purpose'] )
			&& !in_array( $query['purpose'], [ 'evidence', 'answer' ], true )
		) {
			return false;
		}
		foreach ( $query['entities'] as $entity ) {
			if ( !$this->validEntity( $entity ) || !isset( $entity['crop_id'] ) ) { return false; }
		}
		$intent = $query['intent'];
		if ( isset( $query['max_depth'] ) && ( !is_int( $query['max_depth'] ) || $query['max_depth'] < 1 || $query['max_depth'] > 3 ) ) { return false; }
		if ( isset( $query['path'] ) && !$this->validPath( $query['path'] ) ) { return false; }
		if ( isset( $query['target'] ) && !$this->validTarget( $query['target'] ) ) { return false; }
		if ( isset( $query['branches'] ) ) {
			if ( !is_array( $query['branches'] ) || !array_is_list( $query['branches'] ) || count( $query['branches'] ) < 1 || count( $query['branches'] ) > 3 ) { return false; }
			foreach ( $query['branches'] as $branch ) {
				if ( !is_array( $branch ) || array_diff( array_keys( $branch ), [ 'path', 'target' ] )
					|| !$this->validPath( $branch['path'] ?? null )
					|| isset( $branch['target'] ) && !$this->validTarget( $branch['target'] ) ) { return false; }
			}
		}
		if ( isset( $query['filters'] ) ) {
			$predicates = [ 'height', 'plant_height', 'maturity', 'duration', 'growth_duration', 'yield', 'yield_amount' ];
			if ( !is_array( $query['filters'] ) || !array_is_list( $query['filters'] ) || count( $query['filters'] ) > 20 ) { return false; }
			foreach ( $query['filters'] as $filter ) {
				if ( !is_array( $filter ) || array_diff( array_keys( $filter ), [ 'predicate', 'op', 'value', 'on' ] )
					|| !in_array( $filter['predicate'] ?? null, $predicates, true )
					|| !in_array( $filter['op'] ?? null, [ '<', '<=', '>', '>=', '=' ], true )
					|| !is_string( $filter['value'] ?? null ) || $filter['value'] === '' || strlen( $filter['value'] ) > 128
					|| isset( $filter['on'] ) && !in_array( $filter['on'], [ 'root', 'target' ], true ) ) { return false; }
			}
		}
		if ( isset( $query['qualifiers'] ) ) {
			if ( !is_array( $query['qualifiers'] ) || array_diff( array_keys( $query['qualifiers'] ), [ 'season', 'condition' ] ) ) { return false; }
			foreach ( $query['qualifiers'] as $value ) { if ( !is_string( $value ) || mb_strlen( $value, 'UTF-8' ) > 512 ) { return false; } }
		}
		return match ( $intent ) {
			'lookup' => $this->noneSet( $query, [ 'path', 'target', 'branches', 'max_depth' ] ),
			'filter' => $this->noneSet( $query, [ 'path', 'target', 'branches', 'max_depth' ] ),
			'path' => isset( $query['path'] ) && !isset( $query['branches'] )
				&& ( !$this->hasTargetFilter( $query ) || isset( $query['target'] ) ),
			'conjunction' => isset( $query['branches'] ) && $this->noneSet( $query, [ 'path', 'target', 'max_depth' ] )
				&& !$this->hasTargetFilter( $query ),
			default => false,
		};
	}

	private function validEntity( $entity ): bool {
		return is_array( $entity ) && !array_diff( array_keys( $entity ), [ 'type', 'id', 'name', 'crop_id' ] )
			&& in_array( $entity['type'] ?? null, [ 'Crop', 'Variety', 'Pest' ], true )
			&& ( isset( $entity['id'] ) xor isset( $entity['name'] ) )
			&& ( !isset( $entity['id'] ) || is_string( $entity['id'] ) && $entity['id'] !== '' && mb_strlen( $entity['id'], 'UTF-8' ) <= 512 )
			&& ( !isset( $entity['name'] ) || is_string( $entity['name'] ) && $entity['name'] !== '' && mb_strlen( $entity['name'], 'UTF-8' ) <= 512 )
			&& ( !isset( $entity['crop_id'] ) || is_string( $entity['crop_id'] ) && $entity['crop_id'] !== '' && mb_strlen( $entity['crop_id'], 'UTF-8' ) <= 512 );
	}

	private function validTarget( $target ): bool {
		return is_array( $target ) && $this->exactKeys( $target, [ 'type', 'id' ] )
			&& in_array( $target['type'], [ 'Crop', 'Variety', 'Pest' ], true )
			&& is_string( $target['id'] ) && $target['id'] !== '' && mb_strlen( $target['id'], 'UTF-8' ) <= 512;
	}

	private function validPath( $path ): bool {
		return is_array( $path ) && array_is_list( $path ) && count( $path ) >= 1 && count( $path ) <= 3
			&& !array_diff( $path, [ 'HAS_VARIETY', 'HAS_PARENT', 'PARENT_OF', 'RESISTANT_TO', 'SUSCEPTIBLE_TO', 'AFFECTED_BY' ] );
	}

	private function hasTargetFilter( array $query ): bool {
		foreach ( $query['filters'] ?? [] as $filter ) { if ( ( $filter['on'] ?? null ) === 'target' ) { return true; } }
		return false;
	}

	private function noneSet( array $value, array $keys ): bool {
		return array_intersect( $keys, array_keys( $value ) ) === [];
	}

	private function exactKeys( array $value, array $keys ): bool {
		$actual = array_keys( $value );
		sort( $actual );
		sort( $keys );
		return $actual === $keys;
	}

	private function manifestIdentities( array $manifest ): array {
		if ( ( $manifest['schema_version'] ?? null ) !== 'wikikg.reviewed-projection.v1'
			|| !is_string( $manifest['source_watermark'] ?? null )
			|| !preg_match( '/\A[a-f0-9]{64}\z/D', $manifest['source_watermark'] )
			|| !is_array( $manifest['claims'] ?? null ) || !array_is_list( $manifest['claims'] )
			|| count( $manifest['claims'] ) > 100
		) { throw new RuntimeException( 'Synthetic question manifest invalid.' ); }
		$out = [];
		foreach ( $manifest['claims'] as $claim ) {
			if ( !is_array( $claim ) || !is_string( $claim['snapshot_id'] ?? null )
				|| !is_string( $claim['snapshot_hash'] ?? null ) || !is_int( $claim['review_version'] ?? null )
			) { throw new RuntimeException( 'Synthetic question manifest invalid.' ); }
			$out[] = [ 'snapshot_id' => $claim['snapshot_id'], 'snapshot_hash' => $claim['snapshot_hash'],
				'review_version' => $claim['review_version'] ];
		}
		return $out;
	}

	private function validResult( array $result, int $exitCode ): bool {
		if ( ( $result['version'] ?? null ) !== 'wikikg.reviewed-answer.v1'
			|| ( $result['offline_only'] ?? null ) !== true || !in_array( $result['status'] ?? null,
				[ 'evidence', 'blocked' ], true )
			|| ( $result['answer'] ?? null ) !== null || !is_array( $result['reasons'] ?? null )
			|| !$this->validReasons( $result['reasons'] )
		) { return false; }
		if ( ( $result['status'] === 'evidence' && $exitCode !== 0 )
			|| ( $result['status'] === 'blocked' && $exitCode !== 3 )
			|| !is_array( $result['citations'] ?? null ) || count( $result['citations'] ) > 50
		) { return false; }
		$q = $result['query'] ?? null;
		$qKeys = [ 'generation', 'semantic_version', 'offline_only', 'completeness_scope',
			'scope_policy_version', 'status', 'complete', 'partial', 'reasons', 'claims_scanned', 'reason', 'results' ];
		$qRequired = [ 'generation', 'semantic_version', 'offline_only', 'completeness_scope',
			'scope_policy_version', 'status', 'complete', 'results' ];
		if ( !is_array( $q ) || array_diff( array_keys( $q ), $qKeys )
			|| array_diff( $qRequired, array_keys( $q ) )
			|| !is_string( $q['generation'] ?? null )
			|| !preg_match( '/\A[a-f0-9]{64}\z/D', $q['generation'] )
			|| ( $q['semantic_version'] ?? null ) !== 'wikikg.reviewed-query.v2'
			|| ( $q['offline_only'] ?? null ) !== true || ( $q['completeness_scope'] ?? null ) !== 'manifest'
			|| !is_string( $q['scope_policy_version'] ?? null ) || $q['scope_policy_version'] === ''
			|| !in_array( $q['status'] ?? null, [ 'ok', 'empty', 'unsupported', 'truncated' ], true )
			|| $result['status'] === 'evidence' && !in_array( $q['status'], [ 'ok', 'empty' ], true )
			|| !is_bool( $q['complete'] ?? null )
			|| isset( $q['partial'] ) && !is_bool( $q['partial'] )
			|| isset( $q['reason'] ) && ( !is_string( $q['reason'] ) || strlen( $q['reason'] ) > 128 )
			|| isset( $q['claims_scanned'] ) && ( !is_int( $q['claims_scanned'] ) || $q['claims_scanned'] < 0 || $q['claims_scanned'] > 100 )
			|| !is_array( $q['results'] ?? null ) || !array_is_list( $q['results'] )
			|| count( $q['results'] ) > 100 || !$this->validQueryResults( $q['results'] )
			|| !is_array( $q['reasons'] ?? [] ) || !$this->validReasons( $q['reasons'] ?? [] )
		) { return false; }
		$graph = $result['execution']['graph'] ?? null;
		return is_array( $graph ) && $this->exactKeys( $graph, [ 'source', 'generation', 'epoch' ] )
			&& ( $graph['source'] ?? null ) === 'neo4j'
			&& is_string( $graph['generation'] ?? null )
			&& preg_match( '/\A[a-f0-9]{64}\z/D', $graph['generation'] )
			&& hash_equals( $q['generation'], $graph['generation'] )
			&& is_int( $graph['epoch'] ?? null ) && $graph['epoch'] > 0;
	}

	private function validReasons( array $reasons ): bool {
		$known = [ 'invalid_request', 'unresolved_identity', 'unresolved_parent', 'relationship_missing',
			'property_missing', 'scope_unknown', 'opposing_assertions', 'scalar_alternatives',
			'numeric_uncertain', 'numeric_unsupported', 'numeric_not_satisfied', 'partial_manifest',
			'limits', 'empty_result', 'authorizer_missing', 'text_authorizer_missing',
			'authorization_failed', 'authorization_refused', 'authorization_dependency_failure',
			'invalid_authorization_request', 'authorization_response_limit', 'source_revision_changed',
			'source_content_changed', 'source_evidence_changed', 'source_approval_changed',
			'claim_review_changed', 'export_watermark_changed', 'authorization_timeout',
			'malformed_authorization_response', 'authorization_output_limit', 'result_output_limit',
			'private_write_failed', 'interrupted', 'graph_unavailable', 'graph_corrupt',
			'graph_generation_changed', 'graph_timeout', 'graph_limit', 'runtime_failure',
			'offline_authorization_refused' ];
		if ( !array_is_list( $reasons ) || count( $reasons ) > 100 ) { return false; }
		foreach ( $reasons as $reason ) {
			if ( !is_array( $reason ) || !$this->exactKeys( $reason, [ 'code', 'message' ] )
				|| !is_string( $reason['message'] ) || strlen( $reason['message'] ) > 512
				|| !is_string( $reason['code'] ?? null )
				|| !in_array( $reason['code'], $known, true )
			) { return false; }
		}
		return true;
	}

	private function safeReasons( array $reasons ): array {
		return array_map( static fn ( array $reason ) => [ 'code' => $reason['code'] ], $reasons );
	}

	private function validQueryResults( array $results ): bool {
		$totalClaims = 0;
		$totalEvidence = 0;
		foreach ( $results as $match ) {
			if ( !is_array( $match ) || !$this->validResultSubject( $match['subject'] ?? null )
				|| array_diff( array_keys( $match ), [ 'subject', 'claims', 'eligible_claim_ids',
					'diagnostic_claim_ids', 'context_claim_ids', 'branch_claim_ids' ] )
				|| !is_array( $match['claims'] ?? null ) || !array_is_list( $match['claims'] )
				|| count( $match['claims'] ) > 50
			) { return false; }
			foreach ( $match['claims'] as $entry ) {
				$totalClaims++;
				if ( !is_array( $entry ) ) { return false; }
				$citation = $entry['citation'] ?? null;
				if ( !$this->exactKeys( $entry, [ 'claim', 'citation', 'evidence' ] )
					|| !is_array( $citation ) || !$this->exactKeys( $citation, [ 'snapshot_id', 'snapshot_hash', 'review_version' ] )
					|| !preg_match( '/\A[a-f0-9]{64}\z/D', (string)( $citation['snapshot_id'] ?? '' ) )
					|| !preg_match( '/\A[a-f0-9]{64}\z/D', (string)( $citation['snapshot_hash'] ?? '' ) )
					|| !is_int( $citation['review_version'] ?? null ) || $citation['review_version'] < 1
					|| !is_array( $entry['claim'] ?? null ) || !is_array( $entry['evidence'] ?? null )
					|| !array_is_list( $entry['evidence'] ) || count( $entry['evidence'] ) > 20
				) { return false; }
				foreach ( $entry['evidence'] as $evidence ) { if ( !is_array( $evidence ) ) { return false; } }
				$totalEvidence += count( $entry['evidence'] );
				if ( $totalClaims > 100 || $totalEvidence > 50 ) { return false; }
			}
			foreach ( [ 'eligible_claim_ids', 'diagnostic_claim_ids', 'context_claim_ids' ] as $field ) {
				if ( isset( $match[$field] ) && !$this->validIdList( $match[$field] ) ) { return false; }
			}
			if ( isset( $match['branch_claim_ids'] ) ) {
				if ( !is_array( $match['branch_claim_ids'] ) || !array_is_list( $match['branch_claim_ids'] ) || count( $match['branch_claim_ids'] ) > 3 ) { return false; }
				foreach ( $match['branch_claim_ids'] as $branch ) { if ( !$this->validIdList( $branch ) ) { return false; } }
			}
		}
		return true;
	}

	private function validIdList( $ids ): bool {
		if ( !is_array( $ids ) || !array_is_list( $ids ) || count( $ids ) > 100 ) { return false; }
		foreach ( $ids as $id ) { if ( !is_string( $id ) || !preg_match( '/\A[a-f0-9]{64}\z/D', $id ) ) { return false; } }
		return true;
	}

	private function validResultSubject( $subject ): bool {
		return is_array( $subject ) && !array_diff( array_keys( $subject ), [ 'type', 'id', 'crop_id' ] )
			&& in_array( $subject['type'] ?? null, [ 'Crop', 'Variety', 'Pest' ], true )
			&& is_string( $subject['id'] ?? null ) && $subject['id'] !== ''
			&& is_string( $subject['crop_id'] ?? null ) && $subject['crop_id'] !== '';
	}

	private function newGate(): CurrentEvidenceGate {
		return new CurrentEvidenceGate( new ClaimReviewStore( MediaWikiServices::getInstance()->getConnectionProvider() ) );
	}

	private function newAttemptDirectory( string $parent ): string {
		if ( !file_exists( $parent ) && !is_link( $parent ) ) {
			$ownerDirectory = dirname( $parent );
			$this->assertDirectory( $ownerDirectory, 0700 );
			if ( !@mkdir( $parent, 0700 ) && !is_dir( $parent ) ) {
				throw new RuntimeException( 'Attempt parent unavailable.' );
			}
			if ( is_dir( $parent ) && !is_link( $parent ) ) { chmod( $parent, 0700 ); }
		}
		$this->assertDirectory( $parent, 0700 );
		for ( $i = 0; $i < 4; $i++ ) {
			$path = $parent . '/' . bin2hex( random_bytes( 16 ) );
			if ( @mkdir( $path, 0700 ) ) { chmod( $path, 0700 ); $this->assertDirectory( $path, 0700 ); return $path; }
		}
		throw new RuntimeException( 'Attempt directory unavailable.' );
	}

	private function readJsonFile( string $path, int $limit, int $mode ): array {
		$this->assertPrivateFile( $path, $mode );
		$bytes = file_get_contents( $path, false, null, 0, $limit + 1 );
		if ( $bytes === false || strlen( $bytes ) > $limit ) { throw new RuntimeException( 'Private file invalid.' ); }
		$value = json_decode( $bytes, true, 64, JSON_THROW_ON_ERROR );
		if ( !is_array( $value ) ) { throw new RuntimeException( 'Private JSON invalid.' ); }
		return $value;
	}

	private function assertPrivateFile( string $path, int $mode ): void {
		clearstatcache( true, $path );
		if ( is_link( $path ) || !is_file( $path ) || fileowner( $path ) !== posix_geteuid()
			|| ( fileperms( $path ) & 07777 ) !== $mode
		) { throw new RuntimeException( 'Private file refused.' ); }
	}

	private function assertDirectory( string $path, int $mode ): void {
		if ( is_link( $path ) || !is_dir( $path ) || realpath( $path ) !== $path
			|| fileowner( $path ) !== posix_geteuid() || ( fileperms( $path ) & 07777 ) !== $mode
		) { throw new RuntimeException( 'Private directory refused.' ); }
	}

	private function writeNewFile( string $path, string $contents ): void {
		$handle = @fopen( $path, 'x' );
		if ( !$handle || !chmod( $path, 0600 ) || fwrite( $handle, $contents ) !== strlen( $contents ) || !fflush( $handle ) ) {
			if ( is_resource( $handle ) ) { fclose( $handle ); }
			throw new RuntimeException( 'Private write refused.' );
		}
		fclose( $handle );
		$this->assertPrivateFile( $path, 0600 );
	}

	private function failure( string $reason ): array {
		return [ 'version' => 'wikikg.reviewed-answer.v1', 'offline_only' => true,
			'status' => 'dependency_failure', 'query' => null, 'answer' => null, 'citations' => [],
			'reasons' => [ [ 'code' => preg_match( '/\A[a-z_]{1,64}\z/D', $reason ) ? $reason : 'question_unavailable',
				'message' => 'The current evidence is unavailable.' ] ] ];
	}

	private function canonical( array $value ): string {
		return json_encode( $value, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR );
	}

	private function within( string $path, string $root ): bool {
		return $path === $root || str_starts_with( $path, rtrim( $root, '/' ) . '/' );
	}
}
