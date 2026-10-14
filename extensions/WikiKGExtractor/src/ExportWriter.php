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

        $private = $this->outputBaseUrl === '';
        if ( $private && PHP_OS_FAMILY === 'Windows' ) {
            throw new RuntimeException( 'Không thể bảo đảm quyền riêng tư của thư mục xuất trên Windows.' );
        }
        if ( $private ) {
            $this->outputDirectory = $this->resolvePath( $this->outputDirectory );
            $this->assertPrivateDestination( $this->outputDirectory );
        }
        if ( !is_dir( $this->outputDirectory ) ) {
            if ( !@mkdir( $this->outputDirectory, $private ? 0700 : 0775, true )
                && !is_dir( $this->outputDirectory )
            ) {
                throw new RuntimeException( 'Không tạo được thư mục xuất dữ liệu.' );
            }
        }
        if ( $private ) {
            $this->assertPrivateDestination( $this->outputDirectory );
            $this->assertPrivateMode( $this->outputDirectory, 0700, 'thư mục xuất' );
        }

        $jobId = gmdate( 'Ymd-His' ) . '-' . bin2hex( random_bytes( 4 ) );
        $jobDir = $this->outputDirectory . DIRECTORY_SEPARATOR . $jobId;
        if ( !@mkdir( $jobDir, $private ? 0700 : 0775 ) ) {
            throw new RuntimeException( 'Không tạo được thư mục phiên xuất.' );
        }
        if ( $private ) {
            $this->assertPrivateDestination( $jobDir );
            $this->assertPrivateMode( $jobDir, 0700, 'thư mục phiên xuất' );
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
        if ( $json === false || !$this->writeFile( $jsonPath, $json, $private ) ) {
            throw new RuntimeException( 'Không ghi được raw_data.json.' );
        }

        $chunks = [];
        foreach ( $successful as $page ) {
            $chunks[] = '=== ' . $page['title'] . " ===\n"
                . 'URL: ' . $page['url'] . "\n\n"
                . $page['text'];
        }
        if ( !$this->writeFile( $txtPath, implode( "\n\n\n", $chunks ), $private ) ) {
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

    private function writeFile( $path, $contents, $private ) {
        if ( !$private ) {
            return file_put_contents( $path, $contents ) !== false;
        }
        $handle = @fopen( $path, 'x' );
        if ( $handle === false ) {
            return false;
        }
        $ok = @chmod( $path, 0600 ) && ( fileperms( $path ) & 0777 ) === 0600;
        if ( $ok ) {
            $ok = fwrite( $handle, $contents ) === strlen( $contents );
        }
        fclose( $handle );
        return $ok;
    }

    private function assertPrivateMode( $path, $mode, $description ) {
        if ( !function_exists( 'posix_geteuid' ) ) {
            throw new RuntimeException( 'Không thể xác minh chủ sở hữu của ' . $description . '.' );
        }
        $permissions = @fileperms( $path );
        $owner = @fileowner( $path );
        if ( $permissions === false || ( $permissions & 07777 ) !== $mode
            || $owner === false || $owner !== posix_geteuid()
        ) {
            throw new RuntimeException( 'Không thể bảo đảm quyền riêng tư của ' . $description . '.' );
        }
    }

    private function assertPrivateDestination( $path ) {
        $resolved = $this->resolvePath( $path );
        $roots = [ $GLOBALS['IP'] ?? '', $_SERVER['DOCUMENT_ROOT'] ?? '' ];
        foreach ( $roots as $root ) {
            if ( $root === '' ) {
                continue;
            }
            $resolvedRoot = realpath( $root );
            if ( $resolvedRoot === false ) {
                throw new RuntimeException( 'Không xác định được document root để kiểm tra quyền riêng tư.' );
            }
            if ( $resolved === $resolvedRoot
                || strpos( $resolved, rtrim( $resolvedRoot, DIRECTORY_SEPARATOR ) . DIRECTORY_SEPARATOR ) === 0
            ) {
                throw new RuntimeException( 'Thư mục xuất riêng tư phải nằm ngoài document root.' );
            }
        }
    }

    private function resolvePath( $path ) {
        $path = str_replace( '/', DIRECTORY_SEPARATOR, $path );
        foreach ( explode( DIRECTORY_SEPARATOR, $path ) as $component ) {
            if ( $component === '..' ) {
                throw new RuntimeException( 'Đường dẫn xuất riêng tư không được chứa thành phần .. .' );
            }
        }
        $absolute = $path !== '' && $path[0] === DIRECTORY_SEPARATOR;
        if ( !$absolute ) {
            $path = getcwd() . DIRECTORY_SEPARATOR . $path;
        }
        $parts = [];
        foreach ( explode( DIRECTORY_SEPARATOR, $path ) as $part ) {
            if ( $part === '' || $part === '.' ) {
                continue;
            }
            $parts[] = $part;
        }
        $candidate = DIRECTORY_SEPARATOR . implode( DIRECTORY_SEPARATOR, $parts );
        $existing = $candidate;
        while ( !file_exists( $existing ) && !is_link( $existing ) ) {
            $parent = dirname( $existing );
            if ( $parent === $existing ) {
                break;
            }
            $existing = $parent;
        }
        $real = realpath( $existing );
        if ( $real === false ) {
            throw new RuntimeException( 'Không xác định được đường dẫn thư mục xuất.' );
        }
        $suffix = ltrim( substr( $candidate, strlen( $existing ) ), DIRECTORY_SEPARATOR );
        return rtrim( $real, DIRECTORY_SEPARATOR )
            . ( $suffix !== '' ? DIRECTORY_SEPARATOR . $suffix : '' );
    }
}
