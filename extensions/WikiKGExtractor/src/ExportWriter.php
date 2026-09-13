<?php

namespace MediaWiki\Extension\WikiKGExtractor;

use RuntimeException;

class ExportWriter {
    /** @var string */
    private $outputDirectory;

    /** @var string */
    private $outputBaseUrl;

    public function __construct( $outputDirectory, $outputBaseUrl ) {
        $this->outputDirectory = rtrim( (string)$outputDirectory, DIRECTORY_SEPARATOR );
        $this->outputBaseUrl = rtrim( (string)$outputBaseUrl, '/' );
    }

    /**
     * @param array<int,array<string,mixed>> $pages
     * @return array<string,mixed>
     */
    public function write( $sourceUrl, array $pages ) {
        if ( $this->outputDirectory === '' ) {
            throw new RuntimeException( 'Chưa cấu hình thư mục xuất dữ liệu.' );
        }

        if ( !is_dir( $this->outputDirectory )
            && !mkdir( $this->outputDirectory, 0775, true )
            && !is_dir( $this->outputDirectory )
        ) {
            throw new RuntimeException( 'Không tạo được thư mục xuất dữ liệu.' );
        }

        $jobId = gmdate( 'Ymd-His' ) . '-' . bin2hex( random_bytes( 4 ) );
        $jobDir = $this->outputDirectory . DIRECTORY_SEPARATOR . $jobId;
        if ( !mkdir( $jobDir, 0775, true ) && !is_dir( $jobDir ) ) {
            throw new RuntimeException( 'Không tạo được thư mục phiên xuất.' );
        }

        $successful = array_values( array_filter( $pages, static function ( $page ) {
            return !empty( $page['text'] );
        } ) );
        $errors = array_values( array_filter( $pages, static function ( $page ) {
            return !empty( $page['error'] );
        } ) );

        $payload = [
            'schema_version' => '1.0',
            'source_url' => $sourceUrl,
            'generated_at' => gmdate( 'c' ),
            'page_count' => count( $successful ),
            'error_count' => count( $errors ),
            'pages' => $successful,
            'errors' => $errors
        ];

        $jsonPath = $jobDir . DIRECTORY_SEPARATOR . 'raw_data.json';
        $txtPath = $jobDir . DIRECTORY_SEPARATOR . 'raw_data.txt';

        $json = json_encode(
            $payload,
            JSON_PRETTY_PRINT | JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES
        );
        if ( $json === false || file_put_contents( $jsonPath, $json ) === false ) {
            throw new RuntimeException( 'Không ghi được raw_data.json.' );
        }

        $chunks = [];
        foreach ( $successful as $page ) {
            $chunks[] = '=== ' . $page['title'] . " ===\n"
                . 'URL: ' . $page['url'] . "\n\n"
                . $page['text'];
        }
        if ( file_put_contents( $txtPath, implode( "\n\n\n", $chunks ) ) === false ) {
            throw new RuntimeException( 'Không ghi được raw_data.txt.' );
        }

        $base = $this->outputBaseUrl !== ''
            ? $this->outputBaseUrl . '/' . rawurlencode( $jobId )
            : '';

        return [
            'job_id' => $jobId,
            'directory' => $jobDir,
            'json_path' => $jsonPath,
            'txt_path' => $txtPath,
            'json_url' => $base !== '' ? $base . '/raw_data.json' : '',
            'txt_url' => $base !== '' ? $base . '/raw_data.txt' : '',
            'page_count' => count( $successful ),
            'error_count' => count( $errors )
        ];
    }
}
