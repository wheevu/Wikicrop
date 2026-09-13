<?php

namespace MediaWiki\Extension\WikiKGExtractor;

use Exception;
use Html;
use MediaWiki\MediaWikiServices;
use SpecialPage;
use Throwable;

class SpecialWikiKGExtractor extends SpecialPage {
    public function __construct() {
        parent::__construct( 'WikiKGExtractor', 'wikikgextractor-run' );
    }

    public function execute( $subPage ) {
        $this->setHeaders();
        $this->checkPermissions();

        $out = $this->getOutput();
        $request = $this->getRequest();
        $user = $this->getUser();
        $config = $this->getConfig();

        $out->addModuleStyles( 'ext.wikikg.styles' );

        if ( !$request->wasPosted() ) {
            $this->showForm();
            return;
        }

        $sourcePagesText = trim( $request->getText( 'source_pages', '' ) );
        $runKg = $request->getCheck( 'run_kg' );

        if ( !$user->matchEditToken( $request->getVal( 'wpEditToken' ) ) ) {
            $out->addHTML(
                Html::rawElement(
                    'div',
                    [ 'class' => 'wikikgextractor-error' ],
                    'Token bảo mật không hợp lệ. Hãy tải lại trang và thử lại.'
                )
            );
            $this->showForm( $sourcePagesText, $runKg );
            return;
        }

        try {
            $sourcePages = $this->parsePageNames( $sourcePagesText );
            if ( !$sourcePages ) {
                throw new Exception(
                    'Vui lòng nhập ít nhất một tên loài cây, ví dụ: Lúa.'
                );
            }

            $maxSourcePages = max(
                1,
                (int)$config->get( 'WikiKGExtractorMaxSourcePages' )
            );
            if ( count( $sourcePages ) > $maxSourcePages ) {
                throw new Exception(
                    'Chỉ được nhập tối đa ' . $maxSourcePages
                        . ' trang cây trồng trong một lần.'
                );
            }

            if ( $runKg && !(bool)$config->get( 'WikiKGExtractorEnableKG' ) ) {
                throw new Exception(
                    'Tùy chọn tạo KG đang bị tắt trong LocalSettings.php.'
                );
            }

            if ( !class_exists( '\\DOMDocument' ) ) {
                throw new Exception(
                    'PHP DOM chưa được bật. Hãy bật extension php-xml/php-dom trong XAMPP.'
                );
            }

            $services = MediaWikiServices::getInstance();
            $extractor = new LocalWikiExtractor(
                $services,
                $user,
                (array)$config->get( 'WikiKGExtractorLocalNamespaces' ),
                (int)$config->get( 'WikiKGExtractorMaxCollectedPages' ),
                (int)$config->get( 'WikiKGExtractorMaxPageChars' )
            );
            $pages = $extractor->extract( $sourcePages );

            $successfulPages = array_filter(
                $pages,
                static function ( $page ) {
                    return !empty( $page['text'] );
                }
            );
            if ( !$successfulPages ) {
                throw new Exception(
                    'Không tìm thấy trang giống cây để trích xuất. Trang gốc phải có mục “Danh sách” hoặc “Giống” chứa liên kết tới các giống.'
                );
            }

            $outputDirectory = trim(
                (string)$config->get( 'WikiKGExtractorOutputDirectory' )
            );
            $outputBaseUrl = trim(
                (string)$config->get( 'WikiKGExtractorOutputBaseUrl' )
            );

            if ( $outputDirectory === '' ) {
                // Keep the default outside the MediaWiki document root.
                $privateRoot = realpath( sys_get_temp_dir() );
                $documentRoot = realpath( $GLOBALS['IP'] ?? '' );
                if ( $privateRoot === false ) {
                    throw new Exception(
                        'Không xác định được thư mục tạm mặc định. '
                        . 'Hãy cấu hình WikiKGExtractorOutputDirectory.'
                    );
                }
                if ( $documentRoot !== false
                    && ( $privateRoot === $documentRoot
                        || strpos(
                            $privateRoot,
                            $documentRoot . DIRECTORY_SEPARATOR
                        ) === 0 )
                ) {
                    throw new Exception(
                        'Thư mục tạm mặc định nằm trong document root. '
                        . 'Hãy cấu hình WikiKGExtractorOutputDirectory bên ngoài web root.'
                    );
                }
                $outputDirectory = $privateRoot
                    . DIRECTORY_SEPARATOR . 'wiki-kg-exports';
            }

            if ( $outputBaseUrl === '' ) {
                // Raw page exports stay private unless an administrator
                // explicitly configures a public base URL.
                $outputBaseUrl = '';
            }

            $sourceDescription = 'local-wiki crop pages: '
                . implode( ', ', $sourcePages );
            $writer = new ExportWriter( $outputDirectory, $outputBaseUrl );
            $result = $writer->write( $sourceDescription, $pages );

            $kgResult = null;
            if ( $runKg ) {
                $runner = new KgRunner(
                    $config->get( 'WikiKGExtractorPythonCommand' ),
                    dirname( __DIR__ ) . '/bin/kg_worker.py',
                    $config->get( 'WikiKGExtractorGeminiApiKey' ),
                    $config->get( 'WikiKGExtractorGeminiModel' ),
                    $config->get( 'WikiKGExtractorKgTimeout' ),
                    [
                        'batch_size' => $config->get(
                            'WikiKGExtractorKgBatchSize'
                        ),
                        'push_neo4j' => (bool)$config->get(
                            'WikiKGExtractorPushToNeo4j'
                        ),
                        'neo4j_uri' => $config->get( 'WikiKGExtractorNeo4jUri' ),
                        'neo4j_user' => $config->get(
                            'WikiKGExtractorNeo4jUser'
                        ),
                        'neo4j_password' => $config->get(
                            'WikiKGExtractorNeo4jPassword'
                        ),
                        'neo4j_database' => $config->get(
                            'WikiKGExtractorNeo4jDatabase'
                        )
                    ]
                );
                $kgResult = $runner->run(
                    $result['json_path'],
                    $result['directory']
                );
            }

            $this->showResult(
                $result,
                $kgResult,
                $outputBaseUrl,
                $this->countPages( $pages ),
                $result['directory']
            );
        } catch ( Throwable $e ) {
            $out->addHTML(
                Html::rawElement(
                    'div',
                    [ 'class' => 'wikikgextractor-error' ],
                    Html::element( 'strong', [], 'Lỗi: ' )
                        . htmlspecialchars( $e->getMessage() )
                )
            );
        }

        $this->showForm( $sourcePagesText, $runKg );
    }

    /**
     * @return string[]
     */
    private function parsePageNames( $text ) {
        $parts = preg_split( '/(?:\R|;)+/u', trim( (string)$text ) );
        return $this->uniqueNames( $parts ?: [] );
    }


    /**
     * @param string[] $parts
     * @return string[]
     */
    private function uniqueNames( array $parts ) {
        $result = [];
        $seen = [];
        foreach ( $parts as $part ) {
            $name = trim( (string)$part );
            $name = preg_replace( '/^[\-•]+\s*/u', '', $name );
            if ( $name === '' ) {
                continue;
            }

            $keyText = str_replace( '_', ' ', $name );
            $key = function_exists( 'mb_strtolower' )
                ? mb_strtolower( $keyText, 'UTF-8' )
                : strtolower( $keyText );
            if ( isset( $seen[$key] ) ) {
                continue;
            }
            $seen[$key] = true;
            $result[] = $name;
        }
        return $result;
    }

    private function showForm( $sourcePages = '', $runKg = false ) {
        $token = $this->getUser()->getEditToken();

        $html = Html::openElement( 'form', [
            'method' => 'post',
            'class' => 'wikikg-form'
        ] );

        $html .= Html::openElement( 'div', [ 'class' => 'wikikg-row' ] );
        $html .= Html::element(
            'label',
            [ 'for' => 'wikikg-source-pages' ],
            'Tên loài cây'
        );
        $html .= Html::element( 'textarea', [
            'id' => 'wikikg-source-pages',
            'name' => 'source_pages',
            'rows' => 3,
            'placeholder' => 'Lúa',
            'required' => true,
            'autofocus' => true
        ], $sourcePages );
        $html .= Html::closeElement( 'div' );

        $html .= Html::openElement(
            'div',
            [ 'class' => 'wikikg-row wikikg-checkbox-row' ]
        );
        $html .= Html::openElement( 'label', [
            'for' => 'wikikg-run-kg',
            'class' => 'wikikg-checkbox-label'
        ] );
        $html .= Html::element( 'input', [
            'id' => 'wikikg-run-kg',
            'name' => 'run_kg',
            'type' => 'checkbox',
            'value' => '1',
            'checked' => $runKg ? 'checked' : null
        ] );
        $html .= 'Tạo Knowledge Graph';
        $html .= Html::closeElement( 'label' );
        $html .= Html::closeElement( 'div' );

        $html .= Html::hidden( 'wpEditToken', $token );
        $html .= Html::openElement( 'div', [ 'class' => 'wikikg-actions' ] );
        $html .= Html::element( 'button', [
            'type' => 'submit',
            'class' => 'mw-ui-button mw-ui-progressive'
        ], 'Lấy dữ liệu giống cây' );
        $html .= Html::closeElement( 'div' );
        $html .= Html::closeElement( 'form' );

        $this->getOutput()->addHTML( $html );
    }

    /**
     * @param array<int,array<string,mixed>> $pages
     * @return array{species:int,variety:int,errors:int}
     */
    private function countPages( array $pages ) {
        $counts = [ 'species' => 0, 'variety' => 0, 'errors' => 0 ];
        foreach ( $pages as $page ) {
            if ( empty( $page['text'] ) ) {
                $counts['errors']++;
                continue;
            }
            if ( isset( $page['kind'] ) && $page['kind'] === 'species' ) {
                $counts['species']++;
            } else {
                $counts['variety']++;
            }
        }
        return $counts;
    }

    /** @var array<string,string> */
    private static $nodeLabels = [
        'Crop' => 'Loài cây',
        'Variety' => 'Giống cây',
        'Pest' => 'Sâu bệnh'
    ];

    /** @var array<string,string> */
    private static $edgeLabels = [
        'HAS_VARIETY' => 'Loài → giống',
        'RESISTANT_TO' => 'Kháng',
        'SUSCEPTIBLE_TO' => 'Nhiễm',
        'AFFECTED_BY' => 'Bị gây hại'
    ];

    /**
     * @param array<string,mixed> $result
     * @param array<string,mixed>|null $kgResult
     * @param string $outputBaseUrl
     * @param array{species:int,variety:int,errors:int} $counts
     * @param string $outputDirectory
     */
    private function showResult(
        array $result,
        $kgResult,
        $outputBaseUrl,
        array $counts,
        $outputDirectory
    ) {
        $summary = is_array( $kgResult ) && isset( $kgResult['summary'] )
            && is_array( $kgResult['summary'] )
            ? $kgResult['summary']
            : null;

        $html = Html::rawElement(
            'div',
            [ 'class' => 'wikikg-result-head' ],
            Html::element( 'span', [ 'class' => 'wikikg-badge' ], 'Hoàn tất' )
                . Html::element(
                    'h3',
                    [ 'class' => 'wikikg-result-title' ],
                    $kgResult !== null
                        ? 'Đã trích xuất và dựng Knowledge Graph'
                        : 'Đã trích xuất dữ liệu'
                )
        );

        $stats = [
            [ 'Trang loài', $counts['species'] ],
            [ 'Trang giống', $counts['variety'] ]
        ];
        if ( $counts['errors'] > 0 ) {
            $stats[] = [ 'Mục lỗi', $counts['errors'] ];
        }
        if ( $summary ) {
            $stats[] = [ 'Node', (int)( $summary['node_count'] ?? 0 ) ];
            $stats[] = [ 'Quan hệ', (int)( $summary['edge_count'] ?? 0 ) ];
            $stats[] = [
                'Claim chờ duyệt',
                (int)( $summary['candidate_claim_count'] ?? 0 )
            ];
            $stats[] = [
                'Đủ dữ liệu truy vết',
                (int)( $summary['traceability_complete_count'] ?? 0 )
            ];
        }
        $html .= $this->renderStatGrid( $stats );

        if ( $summary ) {
            $html .= $this->renderBreakdown(
                'Node theo loại',
                $summary['nodes_by_label'] ?? [],
                self::$nodeLabels
            );
            $html .= $this->renderBreakdown(
                'Quan hệ theo loại',
                $summary['edges_by_type'] ?? [],
                self::$edgeLabels
            );

            $notes = [];
            if ( !empty( $kgResult['notice'] ) ) {
                $notes[] = (string)$kgResult['notice'];
            }
            if ( empty( $kgResult['used_ai'] ) ) {
                $notes[] = 'Chạy ở chế độ không dùng AI (thiếu Gemini API key), '
                    . 'đồ thị sẽ thưa hơn.';
            }
            $notes[] = 'Các claim mới đang chờ duyệt; chưa phải dữ liệu đã xác nhận.';
            $notes[] = !empty( $summary['neo4j_pushed'] )
                ? 'Đã ghi thẳng vào Neo4j.'
                : 'Chưa ghi vào Neo4j. Dùng file neo4j_import.cypher để nạp.';
            foreach ( $notes as $note ) {
                $html .= Html::element(
                    'p',
                    [ 'class' => 'wikikg-note' ],
                    $note
                );
            }

            $html .= $this->renderWarnings( $summary['warnings'] ?? [] );

            $graphPath = rtrim( (string)$outputDirectory, DIRECTORY_SEPARATOR )
                . DIRECTORY_SEPARATOR . 'graph_nodes_edges.json';
            $graphDocument = GraphDocument::load( $graphPath );
            if ( $graphDocument !== null ) {
                $this->getOutput()->addJsConfigVars(
                    'wgWikiKGGraph',
                    GraphDocument::clientData( $graphDocument )
                );
                $this->getOutput()->addModules( 'ext.wikikg.graph' );
                $html .= GraphDocument::render( $graphDocument );
            } else {
                $html .= GraphDocument::renderFile( $graphPath );
            }
        } elseif ( $kgResult !== null ) {
            $html .= Html::element(
                'p',
                [ 'class' => 'wikikg-note' ],
                'Đã chạy KG worker nhưng không đọc được kg_summary.json.'
            );
        }

        $html .= $this->renderFiles( $result, $kgResult, $outputBaseUrl );

        $this->getOutput()->addHTML(
            Html::rawElement(
                'div',
                [ 'class' => 'wikikgextractor-result' ],
                $html
            )
        );
    }

    /**
     * @param array<int,array{0:string,1:int}> $stats
     */
    private function renderStatGrid( array $stats ) {
        $items = '';
        foreach ( $stats as $stat ) {
            $items .= Html::rawElement(
                'div',
                [ 'class' => 'wikikg-stat' ],
                Html::element(
                    'span',
                    [ 'class' => 'wikikg-stat-value' ],
                    (string)(int)$stat[1]
                )
                    . Html::element(
                        'span',
                        [ 'class' => 'wikikg-stat-label' ],
                        (string)$stat[0]
                    )
            );
        }
        return Html::rawElement( 'div', [ 'class' => 'wikikg-stats' ], $items );
    }

    /**
     * @param array<string,mixed> $data
     * @param array<string,string> $dictionary
     */
    private function renderBreakdown( $heading, $data, array $dictionary ) {
        if ( !is_array( $data ) || !$data ) {
            return '';
        }

        arsort( $data );
        $chips = '';
        foreach ( $data as $key => $value ) {
            $name = $dictionary[$key] ?? (string)$key;
            $chips .= Html::rawElement(
                'span',
                [ 'class' => 'wikikg-chip' ],
                Html::element( 'span', [ 'class' => 'wikikg-chip-name' ], $name )
                    . Html::element(
                        'span',
                        [ 'class' => 'wikikg-chip-count' ],
                        (string)(int)$value
                    )
            );
        }

        return Html::element( 'h4', [ 'class' => 'wikikg-subhead' ], $heading )
            . Html::rawElement( 'div', [ 'class' => 'wikikg-chips' ], $chips );
    }

    /**
     * @param mixed $warnings
     */
    private function renderWarnings( $warnings ) {
        if ( !is_array( $warnings ) || !$warnings ) {
            return '';
        }

        $items = '';
        foreach ( array_slice( $warnings, 0, 8 ) as $warning ) {
            $items .= Html::element( 'li', [], (string)$warning );
        }
        $extra = count( $warnings ) > 8
            ? ' (' . count( $warnings ) . ' cảnh báo, xem kg_summary.json)'
            : '';

        return Html::rawElement(
            'details',
            [ 'class' => 'wikikg-details wikikg-details-warn' ],
            Html::element(
                'summary',
                [],
                'Cảnh báo trong quá trình dựng KG' . $extra
            ) . Html::rawElement( 'ul', [], $items )
        );
    }

    /**
     * Chỉ đưa file thật sự cần dùng ra ngoài; phần còn lại thu gọn lại.
     *
     * @param array<string,mixed> $result
     * @param array<string,mixed>|null $kgResult
     * @param string $outputBaseUrl
     */
    private function renderFiles( array $result, $kgResult, $outputBaseUrl ) {
        $jobBase = '';
        if ( $outputBaseUrl !== '' ) {
            $jobBase = rtrim( $outputBaseUrl, '/' )
                . '/' . rawurlencode( $result['job_id'] );
        }

        $primary = [];
        $secondary = [];

        if ( $kgResult !== null && $jobBase !== '' ) {
            $primary[] = [
                'neo4j_import.cypher',
                'Script Neo4j',
                'Nạp đồ thị vào Neo4j bằng cypher-shell hoặc Neo4j Browser.'
            ];
            $primary[] = [
                'mediawiki_bang_thuoc_tinh_cay_trong.txt',
                'Bảng so sánh giống',
                'Wikitext dán thẳng vào trang loài.'
            ];
            $primary[] = [
                'graph_nodes_edges.json',
                'Nodes & edges',
                'Dữ liệu gọn để vẽ đồ thị trên web.'
            ];
            $secondary[] = [ 'graph_data_raw.json', 'graph_data_raw.json' ];
            $secondary[] = [ 'candidate_claims.json', 'candidate_claims.json' ];
            $secondary[] = [ 'kg_summary.json', 'kg_summary.json' ];
        }

        if ( $result['json_url'] !== '' ) {
            if ( $kgResult === null ) {
                $primary[] = [
                    'raw_data.json',
                    'Dữ liệu JSON',
                    'Đầu vào cho bước dựng Knowledge Graph.'
                ];
                $primary[] = [
                    'raw_data.txt',
                    'Dữ liệu văn bản',
                    'Toàn bộ nội dung trang loài và các giống.'
                ];
            } else {
                $secondary[] = [ 'raw_data.json', 'raw_data.json' ];
                $secondary[] = [ 'raw_data.txt', 'raw_data.txt' ];
            }
        }

        if ( !$primary && !$secondary ) {
            return Html::element(
                'p',
                [ 'class' => 'wikikg-note' ],
                'File đã được ghi trên server nhưng chưa có URL công khai.'
            );
        }

        $base = $jobBase !== '' ? $jobBase : dirname( (string)$result['json_url'] );

        $cards = '';
        foreach ( $primary as $file ) {
            [ $fileName, $label, $description ] = $file;
            $cards .= Html::rawElement(
                'a',
                [
                    'class' => 'wikikg-file',
                    'href' => $base . '/' . $fileName,
                    'download' => $fileName
                ],
                Html::element( 'span', [ 'class' => 'wikikg-file-name' ], $label )
                    . Html::element(
                        'span',
                        [ 'class' => 'wikikg-file-desc' ],
                        $description
                    )
                    . Html::element(
                        'span',
                        [ 'class' => 'wikikg-file-meta' ],
                        $fileName
                    )
            );
        }
        $html = Html::element( 'h4', [ 'class' => 'wikikg-subhead' ], 'Tải về' )
            . Html::rawElement( 'div', [ 'class' => 'wikikg-files' ], $cards );

        if ( $secondary ) {
            $links = '';
            foreach ( $secondary as $file ) {
                $links .= Html::rawElement( 'li', [], Html::element(
                    'a',
                    [
                        'href' => $base . '/' . $file[0],
                        'download' => $file[0]
                    ],
                    $file[1]
                ) );
            }
            $html .= Html::rawElement(
                'details',
                [ 'class' => 'wikikg-details' ],
                Html::element( 'summary', [], 'File phụ' )
                    . Html::rawElement(
                        'ul',
                        [ 'class' => 'wikikg-file-list' ],
                        $links
                    )
            );
        }

        return $html;
    }

    protected function getGroupName() {
        return 'other';
    }
}
