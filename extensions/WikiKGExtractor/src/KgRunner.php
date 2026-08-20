<?php

namespace MediaWiki\Extension\WikiKGExtractor;

use RuntimeException;

/**
 * Chạy bin/kg_worker.py để dựng Knowledge Graph và (tùy chọn) ghi vào Neo4j.
 */
class KgRunner {
    /** @var string */
    private $pythonCommand;

    /** @var string */
    private $scriptPath;

    /** @var string */
    private $apiKey;

    /** @var string */
    private $model;

    /** @var int */
    private $timeout;

    /** @var array<string,mixed> */
    private $options;

    /**
     * @param string $pythonCommand
     * @param string $scriptPath
     * @param string $apiKey Gemini API key ('' = chạy chế độ không dùng AI)
     * @param string $model
     * @param int $timeout
     * @param array<string,mixed> $options batch_size, neo4j_*, push_neo4j
     */
    public function __construct(
        $pythonCommand,
        $scriptPath,
        $apiKey,
        $model,
        $timeout = 600,
        array $options = []
    ) {
        $this->pythonCommand = (string)$pythonCommand;
        $this->scriptPath = (string)$scriptPath;
        $this->apiKey = trim( (string)$apiKey );
        $this->model = (string)$model;
        $this->timeout = max( 10, (int)$timeout );
        $this->options = $options;
    }

    private function option( $key, $default = '' ) {
        return array_key_exists( $key, $this->options )
            ? $this->options[$key]
            : $default;
    }

    /**
     * @return array{stdout:string,stderr:string,exit_code:int,summary:array|null}
     */
    public function run( $inputJson, $outputDirectory ) {
        if ( !function_exists( 'proc_open' ) ) {
            throw new RuntimeException(
                'Server đã tắt hàm proc_open; không thể chạy KG tự động.'
            );
        }
        if ( !is_file( $this->scriptPath ) ) {
            throw new RuntimeException( 'Không tìm thấy bin/kg_worker.py.' );
        }

        $useAi = $this->apiKey !== '';
        $pushNeo4j = (bool)$this->option( 'push_neo4j', false );
        $notice = '';
        if ( $pushNeo4j
            && trim( (string)$this->option( 'neo4j_password', '' ) ) === ''
        ) {
            // Không dừng cả tiến trình: vẫn dựng KG và ghi file .cypher,
            // chỉ bỏ bước đẩy lên Neo4j và báo rõ lý do.
            $pushNeo4j = false;
            $notice = 'Đã bật ghi Neo4j nhưng mật khẩu đang rỗng nên bỏ qua '
                . "bước này. Nếu dùng getenv('NEO4J_PASSWORD'), hãy tắt hẳn "
                . 'Apache rồi bật lại sau khi đặt biến môi trường.';
        }
        if ( !$useAi && !$pushNeo4j ) {
            // Không có API key thì vẫn dựng được KG bằng bộ trích xuất theo
            // luật, nhưng báo cho người dùng biết chất lượng sẽ thấp hơn.
            $useAi = false;
        }

        $command = escapeshellarg( $this->pythonCommand )
            . ' ' . escapeshellarg( $this->scriptPath )
            . ' --input ' . escapeshellarg( $inputJson )
            . ' --output-dir ' . escapeshellarg( $outputDirectory )
            . ' --model ' . escapeshellarg( $this->model );

        $batchSize = (int)$this->option( 'batch_size', 6 );
        if ( $batchSize > 0 ) {
            $command .= ' --batch-size ' . escapeshellarg( (string)$batchSize );
        }
        if ( !$useAi ) {
            $command .= ' --no-ai';
        }
        if ( $pushNeo4j ) {
            $command .= ' --push-neo4j'
                . ' --neo4j-uri ' . escapeshellarg(
                    (string)$this->option( 'neo4j_uri', 'bolt://localhost:7687' )
                )
                . ' --neo4j-user ' . escapeshellarg(
                    (string)$this->option( 'neo4j_user', 'neo4j' )
                )
                . ' --neo4j-database ' . escapeshellarg(
                    (string)$this->option( 'neo4j_database', 'neo4j' )
                );
        }

        $descriptors = [
            0 => [ 'pipe', 'r' ],
            1 => [ 'pipe', 'w' ],
            2 => [ 'pipe', 'w' ]
        ];

        $environment = getenv();
        if ( !is_array( $environment ) ) {
            $environment = [];
        }
        $environment['GEMINI_API_KEY'] = $this->apiKey;
        $environment['PYTHONIOENCODING'] = 'utf-8';
        if ( $pushNeo4j ) {
            $environment['NEO4J_PASSWORD'] = (string)$this->option(
                'neo4j_password',
                ''
            );
        }

        $process = proc_open( $command, $descriptors, $pipes, null, $environment );
        if ( !is_resource( $process ) ) {
            throw new RuntimeException( 'Không khởi động được Python KG worker.' );
        }

        fclose( $pipes[0] );
        stream_set_blocking( $pipes[1], false );
        stream_set_blocking( $pipes[2], false );

        $stdout = '';
        $stderr = '';
        $started = microtime( true );
        $timedOut = false;
        $lastStatus = null;

        while ( true ) {
            $stdout .= stream_get_contents( $pipes[1] );
            $stderr .= stream_get_contents( $pipes[2] );

            $status = proc_get_status( $process );
            $lastStatus = $status;
            if ( !$status['running'] ) {
                break;
            }
            if ( microtime( true ) - $started > $this->timeout ) {
                $timedOut = true;
                proc_terminate( $process );
                break;
            }
            usleep( 100000 );
        }

        $stdout .= stream_get_contents( $pipes[1] );
        $stderr .= stream_get_contents( $pipes[2] );
        fclose( $pipes[1] );
        fclose( $pipes[2] );
        $closeCode = proc_close( $process );
        $exitCode = $closeCode;
        if ( $closeCode === -1 && is_array( $lastStatus )
            && isset( $lastStatus['exitcode'] ) && $lastStatus['exitcode'] >= 0
        ) {
            $exitCode = (int)$lastStatus['exitcode'];
        }

        if ( $timedOut ) {
            throw new RuntimeException(
                'KG worker vượt quá thời gian cho phép ('
                    . $this->timeout . 's). Hãy tăng '
                    . '$wgWikiKGExtractorKgTimeout.'
            );
        }
        if ( $exitCode !== 0 ) {
            throw new RuntimeException(
                'KG worker lỗi (mã ' . $exitCode . '): ' . trim( $stderr )
            );
        }

        return [
            'stdout' => trim( $stdout ),
            'stderr' => trim( $stderr ),
            'exit_code' => $exitCode,
            'summary' => $this->readSummary( $outputDirectory ),
            'used_ai' => $useAi,
            'notice' => $notice
        ];
    }

    /**
     * @return array<string,mixed>|null
     */
    private function readSummary( $outputDirectory ) {
        $path = rtrim( (string)$outputDirectory, DIRECTORY_SEPARATOR )
            . DIRECTORY_SEPARATOR . 'kg_summary.json';
        if ( !is_file( $path ) ) {
            return null;
        }
        $raw = file_get_contents( $path );
        if ( $raw === false ) {
            return null;
        }
        $data = json_decode( $raw, true );
        return is_array( $data ) ? $data : null;
    }
}