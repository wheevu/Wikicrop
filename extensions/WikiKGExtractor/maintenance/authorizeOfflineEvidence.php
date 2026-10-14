<?php

$IP = getenv( 'MW_INSTALL_PATH' );
if ( $IP === false || $IP === '' ) {
	$IP = __DIR__ . '/../../..';
}
require_once "$IP/maintenance/Maintenance.php";

use MediaWiki\Extension\WikiKGExtractor\ClaimReviewStore;
use MediaWiki\Extension\WikiKGExtractor\CurrentEvidenceGate;
use MediaWiki\Extension\WikiKGExtractor\OfflineDevelopmentScope;
use MediaWiki\Maintenance\Maintenance;

/** Local reviewer-only bridge. No HTTP endpoint or reader audience is enabled. */
class AuthorizeOfflineEvidence extends Maintenance {
	public function __construct() {
		parent::__construct();
		$this->addDescription( 'Reauthorize offline graph/text identities and write a new private response' );
		foreach ( [ 'actor', 'request', 'output', 'document-root', 'audience' ] as $option ) {
			$this->addOption( $option, 'Required offline authorization input', true, true );
		}
		$this->addOption( 'offline-only', 'Confirm local offline reviewer-only operation', true, false );
		$this->addOption( 'synthetic-only', 'Select a disposable synthetic wiki explicitly', false, false );
		$this->addOption( 'corpus-manifest', 'Otherwise require the hash-pinned development metadata manifest', false, true );
	}

	public function execute() {
		$handle = null;
		$failureReason = 'offline_authorization_refused';
		try {
			$manifest = $this->getOption( 'corpus-manifest' );
			if ( !$this->hasOption( 'offline-only' ) || $this->getOption( 'audience' ) !== 'actor-only'
				|| !function_exists( 'posix_geteuid' ) || PHP_OS_FAMILY === 'Windows'
				|| $this->hasOption( 'synthetic-only' ) === ( is_string( $manifest ) && $manifest !== '' )
			) {
				throw new \RuntimeException( 'unsupported boundary' );
			}
			$repository = realpath( dirname( __DIR__, 3 ) );
			$web = realpath( (string)$this->getOption( 'document-root' ) );
			$runtime = $_SERVER['DOCUMENT_ROOT'] ?? getenv( 'DOCUMENT_ROOT' );
			if ( !is_string( $runtime ) || $runtime === '' ) {
				$runtime = getenv( 'DOCUMENT_ROOT' );
			}
			$webOption = (string)$this->getOption( 'document-root' );
			if ( !$repository || !$web || !is_dir( $web ) || $webOption === '' || $webOption[0] !== DIRECTORY_SEPARATOR
				|| ( is_string( $runtime ) && $runtime !== '' && realpath( $runtime ) !== $web )
			) {
				throw new \RuntimeException( 'unknown boundary' );
			}
			$request = (string)$this->getOption( 'request' );
			$output = (string)$this->getOption( 'output' );
			$this->assertDirectory( dirname( $request ), $repository, $web );
			$this->assertDirectory( dirname( $output ), $repository, $web );
			clearstatcache();
			if ( is_link( $request ) || !is_file( $request ) || fileowner( $request ) !== posix_geteuid()
				|| ( fileperms( $request ) & 07777 ) !== 0600 || filesize( $request ) > 65536
				|| is_link( $output ) || file_exists( $output )
				|| basename( $output ) === '.' || basename( $output ) === '..'
			) {
				throw new \RuntimeException( 'unsafe file' );
			}
			$identities = json_decode( file_get_contents( $request, false, null, 0, 65537 ), true, 32, JSON_THROW_ON_ERROR );
			if ( !is_array( $identities ) || array_diff( array_keys( $identities ), [ 'graph', 'text', 'source_watermark' ] )
				|| !isset( $identities['graph'], $identities['text'] )
				|| !is_array( $identities['graph'] ) || !is_array( $identities['text'] )
				|| ( $identities['graph'] && ( !is_string( $identities['source_watermark'] ?? null )
					|| !preg_match( '/\A[a-f0-9]{64}\z/D', $identities['source_watermark'] ) ) )
			) {
				$failureReason = 'invalid_authorization_request';
				throw new \RuntimeException( 'invalid request' );
			}
			$services = $this->getServiceContainer();
			$actor = $services->getUserFactory()->newFromName( (string)$this->getOption( 'actor' ) );
			if ( !$actor || !$actor->isRegistered()
				|| !$services->getPermissionManager()->userHasRight( $actor, 'wikikgextractor-review' )
			) {
				throw new \RuntimeException( 'actor denied' );
			}
			$authority = $actor;
			$scope = $manifest ? new OfflineDevelopmentScope( $manifest ) : null;
			$gate = new CurrentEvidenceGate( new ClaimReviewStore( $services->getConnectionProvider() ), $scope );
			$watermark = $identities['source_watermark'] ?? null;
			$authorizeGraph = static function () use ( $gate, $authority, $identities, $watermark, &$failureReason ) {
				$result = $gate->authorize( $authority, $identities['graph'], $watermark );
				if ( $result['status'] !== 'ok' ) {
					$failureReason = $gate->getFailureReason();
					throw new \RuntimeException( 'current graph eligibility unavailable' );
				}
				return $result;
			};
			$authorizeText = static function () use ( $gate, $authority, $identities, &$failureReason ) {
				$result = $gate->authorizeText( $authority, $identities['text'] );
				if ( $result['status'] !== 'ok' ) {
					$failureReason = $gate->getFailureReason();
					throw new \RuntimeException( 'current text eligibility unavailable' );
				}
				return $result;
			};
			$firstGraph = $authorizeGraph();
			$firstText = $authorizeText();
			$graph = $authorizeGraph();
			$text = $authorizeText();
			if ( json_encode( $graph, JSON_THROW_ON_ERROR ) !== json_encode( $firstGraph, JSON_THROW_ON_ERROR )
				|| json_encode( $text, JSON_THROW_ON_ERROR ) !== json_encode( $firstText, JSON_THROW_ON_ERROR )
			) {
				$failureReason = 'source_evidence_changed';
				throw new \RuntimeException( 'current eligibility changed' );
			}
			$document = [ 'schema_version' => 'wikikg.current-evidence.v1', 'status' => 'ok',
				'claims' => $graph['claims'], 'text' => $text['text'] ];
			$bytes = json_encode( $document, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR ) . "\n";
			if ( strlen( $bytes ) > 1048576 ) {
				$failureReason = 'authorization_response_limit';
				throw new \RuntimeException( 'response bound' );
			}
			$this->assertDirectory( dirname( $output ), $repository, $web );
			$output = realpath( dirname( $output ) ) . DIRECTORY_SEPARATOR . basename( $output );
			$previousUmask = umask( 0077 );
			try {
				$handle = @fopen( $output, 'x' );
			} finally {
				umask( $previousUmask );
			}
			if ( !$handle || !chmod( $output, 0600 ) || ( fileperms( $output ) & 07777 ) !== 0600
				|| fileowner( $output ) !== posix_geteuid()
				|| fwrite( $handle, $bytes ) !== strlen( $bytes ) || !fflush( $handle )
			) {
				throw new \RuntimeException( 'private write failed' );
			}
			fclose( $handle );
			$handle = null;
			$this->output( json_encode( [ 'status' => 'ok', 'claim_count' => count( $graph['claims'] ),
				'text_count' => count( $text['text'] ) ], JSON_THROW_ON_ERROR ) . "\n" );
		} catch ( \Throwable $exception ) {
			if ( is_resource( $handle ) ) {
				// Leave only a bounded, private, untrusted partial file. Never overwrite it on retry.
				fclose( $handle );
			}
			$this->fatalError( json_encode( [ 'status' => 'dependency_failure',
				'error' => 'offline_authorization_refused', 'reason' => $failureReason ], JSON_THROW_ON_ERROR ), 2 );
		}
	}

	private function assertDirectory( string $path, string $repository, string $web ): void {
		$real = realpath( $path );
		clearstatcache();
		for ( $part = $path; $part !== dirname( $part ); $part = dirname( $part ) ) {
			if ( is_link( $part ) ) {
				throw new \RuntimeException( 'path indirection' );
			}
		}
		if ( $path === '' || $path[0] !== DIRECTORY_SEPARATOR || !$real || !is_dir( $real )
			|| is_link( $path ) || fileowner( $real ) !== posix_geteuid() || ( fileperms( $real ) & 07777 ) !== 0700
		) {
			throw new \RuntimeException( 'unsafe directory' );
		}
		foreach ( [ $repository, $web ] as $root ) {
			if ( $real === $root || str_starts_with( $real, rtrim( $root, DIRECTORY_SEPARATOR ) . DIRECTORY_SEPARATOR ) ) {
				throw new \RuntimeException( 'public directory' );
			}
		}
	}
}

$maintClass = AuthorizeOfflineEvidence::class;
require_once RUN_MAINTENANCE_IF_MAIN;
