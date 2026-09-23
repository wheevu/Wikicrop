<?php

namespace MediaWiki\Extension\WikiKGExtractor;

use DOMDocument;
use DOMElement;
use DOMNode;
use DOMXPath;
use MediaWiki\MediaWikiServices;
use MediaWiki\Parser\ParserOutputLinkTypes;
use MediaWiki\Revision\SlotRecord;
use ParserOptions;
use Throwable;
use Title;

/**
 * Reads recognized crop pages from the current MediaWiki database, keeps the
 * crop page itself (kind = species), discovers variety links under headings
 * containing "Danh sách" or "Giống", then reads eligible linked pages from the
 * same backend (kind = variety).
 */
class LocalWikiExtractor {
    private const CROP_TEMPLATES = [ 'InfoCropPlant', 'InfoPlant1' ];
    private const VARIETY_TEMPLATES = [ 'InfoCropPlant', 'InfoPlant1Giong' ];

    /** @var MediaWikiServices */
    private $services;

    /** @var mixed */
    private $user;

    /** @var int[] */
    private $namespaces;

    /** @var mixed */
    private $revisionLookup;

    /** @var mixed */
    private $permissionManager;

    /** @var mixed */
    private $contentRenderer;

    /** @var ParserOptions */
    private $parserOptions;

    /** @var int */
    private $maxCollectedPages;

    /** @var int */
    private $maxPageChars;

    /**
     * @param MediaWikiServices $services
     * @param mixed $user Current MediaWiki user
     * @param int[] $namespaces Namespace IDs that may be read
     * @param int $maxCollectedPages Maximum species and variety pages
     * @param int $maxPageChars Maximum source wikitext characters per page
     */
    public function __construct(
        MediaWikiServices $services,
        $user,
        array $namespaces = [ 0 ],
        $maxCollectedPages = 100,
        $maxPageChars = 250000
    ) {
        $this->services = $services;
        $this->user = $user;
        $this->namespaces = array_values( array_unique( array_map(
            'intval',
            $namespaces ?: [ 0 ]
        ) ) );
        $this->revisionLookup = $services->getRevisionLookup();
        $this->permissionManager = $services->getPermissionManager();
        $this->contentRenderer = $services->getContentRenderer();
        $this->parserOptions = ParserOptions::newFromUser( $user );
        $this->maxCollectedPages = max( 1, (int)$maxCollectedPages );
        $this->maxPageChars = max( 1000, (int)$maxPageChars );
    }

    /**
     * @param string[] $sourcePageNames Broad crop pages such as Lúa
     * @return array<int,array<string,mixed>>
     */
    public function extract( array $sourcePageNames ) {
        $skipSet = $this->buildNameSet( [ 'sửa' ] );
        $sourceTitles = [];
        $errors = [];

        foreach ( $sourcePageNames as $sourceName ) {
            $sourceName = trim( (string)$sourceName );
            if ( $sourceName === '' ) {
                continue;
            }

            $title = Title::newFromText( $sourceName, NS_MAIN );
            if ( !$title ) {
                $errors[] = $this->errorPage(
                    $sourceName,
                    'Tên trang gốc không hợp lệ.'
                );
                continue;
            }

            if ( !in_array( $title->getNamespace(), $this->namespaces, true ) ) {
                $errors[] = $this->errorPage(
                    $title->getPrefixedText(),
                    'Trang gốc không thuộc namespace được phép.'
                );
                continue;
            }

            $key = $title->getPrefixedDBkey();
            if ( isset( $sourceTitles[$key] ) ) {
                continue;
            }

            $sourceTitles[$key] = $title;
            $skipSet[$this->normalizeName( $title->getPrefixedText() )] = true;
        }

        if ( !$sourceTitles ) {
            return $errors;
        }

        /** @var array<string,array{title:Title,sources:array<string,bool>}> $candidates */
        $candidates = [];

        // Trang loài (ví dụ "Lúa") luôn được lấy trước các trang giống.
        $speciesPages = [];

        foreach ( $sourceTitles as $sourceTitle ) {
            try {
                $sourcePage = $this->loadRenderedPage( $sourceTitle );
                if ( isset( $sourcePage['error'] ) ) {
                    $errors[] = $this->errorPage(
                        $sourceTitle->getPrefixedText(),
                        $sourcePage['error'],
                        $sourceTitle->getFullURL()
                    );
                    continue;
                }

                // 1. Nội dung của chính trang loài.
                /** @var Title $resolvedSourceTitle */
                $resolvedSourceTitle = $sourcePage['title'];
                if ( !$this->hasActiveTemplate(
                    $sourcePage['templates'],
                    self::CROP_TEMPLATES
                ) ) {
                    $errors[] = $this->errorPage(
                        $resolvedSourceTitle->getPrefixedText(),
                        'Trang gốc không có mẫu cây trồng được nhận diện.',
                        $resolvedSourceTitle->getFullURL()
                    );
                    continue;
                }

                $speciesText = $this->htmlToText( $sourcePage['html'] );
                if ( $speciesText !== '' ) {
                    if ( count( $speciesPages ) >= $this->maxCollectedPages ) {
                        $errors[] = $this->errorPage(
                            $resolvedSourceTitle->getPrefixedText(),
                            'Đã đạt giới hạn số trang thu thập trong một lần chạy.'
                        );
                        continue;
                    }
                    $speciesPages[] = [
                        'title' => $resolvedSourceTitle->getPrefixedText(),
                        'url' => $resolvedSourceTitle->getFullURL(),
                        'wikitext' => $sourcePage['wikitext'],
                        'text' => $speciesText,
                        'kind' => 'species',
                        'crop' => $resolvedSourceTitle->getPrefixedText(),
                        'source_pages' => [],
                        'page_id' => $sourcePage['page_id'],
                        'revision_id' => $sourcePage['revision_id'],
                        'content_hash' => $sourcePage['content_hash']
                    ];
                } else {
                    $errors[] = $this->errorPage(
                        $sourceTitle->getPrefixedText(),
                        'Không đọc được nội dung trang loài.',
                        $sourceTitle->getFullURL()
                    );
                }

                // 2. Các trang giống được liên kết từ mục "Danh sách"/"Giống".
                $linkedTitles = $this->findVarietyTitles(
                    $sourcePage['html'],
                    $resolvedSourceTitle,
                    $skipSet
                );

                if ( !$linkedTitles ) {
                    // Vẫn giữ trang loài để KG có node gốc.
                    $errors[] = $this->errorPage(
                        $sourceTitle->getPrefixedText(),
                        'Không tìm thấy liên kết giống cây dưới mục “Danh sách” hoặc “Giống”; chỉ lấy được trang loài.',
                        $sourceTitle->getFullURL()
                    );
                    continue;
                }

                $limitReached = false;
                foreach ( $linkedTitles as $linkedTitle ) {
                    $candidateKey = $linkedTitle->getPrefixedDBkey();
                    if ( !isset( $candidates[$candidateKey] ) ) {
                        if ( count( $speciesPages ) + count( $candidates )
                            >= $this->maxCollectedPages
                        ) {
                            $limitReached = true;
                            break;
                        }
                        $candidates[$candidateKey] = [
                            'title' => $linkedTitle,
                            'sources' => []
                        ];
                    }
                    $candidates[$candidateKey]['sources'][
                        $resolvedSourceTitle->getPrefixedText()
                    ] = true;
                }
                if ( $limitReached ) {
                    $errors[] = $this->errorPage(
                        $resolvedSourceTitle->getPrefixedText(),
                        'Đã đạt giới hạn số trang thu thập trong một lần chạy.'
                    );
                }
            } catch ( Throwable $e ) {
                $errors[] = $this->errorPage(
                    $sourceTitle->getPrefixedText(),
                    $e->getMessage(),
                    $sourceTitle->getFullURL()
                );
            }
        }

        $pages = $speciesPages;
        foreach ( $candidates as $candidate ) {
            /** @var Title $varietyTitle */
            $varietyTitle = $candidate['title'];
            try {
                $page = $this->loadRenderedPage( $varietyTitle );
                if ( isset( $page['error'] ) ) {
                    $errors[] = $this->errorPage(
                        $varietyTitle->getPrefixedText(),
                        $page['error'],
                        $varietyTitle->getFullURL()
                    );
                    continue;
                }

                $sourceNames = array_keys( $candidate['sources'] );
                /** @var Title $resolvedTitle */
                $resolvedTitle = $page['title'];
                if ( !$this->hasActiveTemplate(
                    $page['templates'],
                    self::VARIETY_TEMPLATES
                ) && !$this->isCropTiedTitle( $varietyTitle, $sourceNames )
                    && !$this->isCropTiedTitle( $resolvedTitle, $sourceNames )
                ) {
                    $errors[] = $this->errorPage(
                        $varietyTitle->getPrefixedText(),
                        'Trang liên kết không có mẫu giống được nhận diện '
                            . 'và tên không gắn với loài nguồn.',
                        $varietyTitle->getFullURL()
                    );
                    continue;
                }

                $text = $this->htmlToText( $page['html'] );
                if ( $text === '' ) {
                    $errors[] = $this->errorPage(
                        $varietyTitle->getPrefixedText(),
                        'Không trích xuất được nội dung đọc được.',
                        $varietyTitle->getFullURL()
                    );
                    continue;
                }

                $entry = [
                    'title' => $resolvedTitle->getPrefixedText(),
                    'url' => $resolvedTitle->getFullURL(),
                    'wikitext' => $page['wikitext'],
                    'text' => $text,
                    'kind' => 'variety',
                    'crop' => $sourceNames ? (string)$sourceNames[0] : '',
                    'source_pages' => $sourceNames,
                    'page_id' => $page['page_id'],
                    'revision_id' => $page['revision_id'],
                    'content_hash' => $page['content_hash']
                ];

                if ( !$varietyTitle->equals( $resolvedTitle ) ) {
                    $entry['requested_title'] = $varietyTitle->getPrefixedText();
                    $entry['requested_url'] = $varietyTitle->getFullURL();
                    $entry['resolved_title'] = $resolvedTitle->getPrefixedText();
                    $entry['resolved_url'] = $resolvedTitle->getFullURL();
                }

                $pages[] = $entry;
            } catch ( Throwable $e ) {
                $errors[] = $this->errorPage(
                    $varietyTitle->getPrefixedText(),
                    $e->getMessage(),
                    $varietyTitle->getFullURL()
                );
            }
        }

        return array_merge( $pages, $errors );
    }

    /**
     * @return array{title:Title,html:string,wikitext:string,templates:string[],page_id:int,revision_id:int,content_hash:string}|array{error:string}
     */
    private function loadRenderedPage( Title $requestedTitle ) {
        if ( !$this->permissionManager->userCan(
            'read',
            $this->user,
            $requestedTitle
        ) ) {
            return [ 'error' => 'Bạn không có quyền đọc trang này.' ];
        }

        $title = $requestedTitle;
        $revision = $this->revisionLookup->getRevisionByTitle( $title );
        if ( !$revision ) {
            return [ 'error' => 'Không tìm thấy trang trong Wiki hiện tại.' ];
        }

        $content = $revision->getContent( SlotRecord::MAIN );
        if ( !$content ) {
            return [ 'error' => 'Trang không có nội dung chính.' ];
        }

        // Follow one regular MediaWiki redirect.
        if ( method_exists( $content, 'getRedirectTarget' ) ) {
            $redirectTarget = $content->getRedirectTarget();
            if ( $redirectTarget instanceof Title
                && in_array(
                    $redirectTarget->getNamespace(),
                    $this->namespaces,
                    true
                )
                && $this->permissionManager->userCan(
                    'read',
                    $this->user,
                    $redirectTarget
                )
            ) {
                $redirectRevision = $this->revisionLookup->getRevisionByTitle(
                    $redirectTarget
                );
                if ( $redirectRevision ) {
                    $redirectContent = $redirectRevision->getContent(
                        SlotRecord::MAIN
                    );
                    if ( $redirectContent ) {
                        $title = $redirectTarget;
                        $revision = $redirectRevision;
                        $content = $redirectContent;
                    }
                }
            }
        }

        if ( $content->getModel() !== CONTENT_MODEL_WIKITEXT ) {
            return [ 'error' => 'Mô hình nội dung của trang không phải wikitext.' ];
        }

        // WikitextContent extends TextContent and exposes getText().
        // ContentHandler::getContentText() was deprecated in MediaWiki 1.37.
        if ( !method_exists( $content, 'getText' ) ) {
            return [ 'error' => 'Mô hình nội dung của trang không phải dạng văn bản.' ];
        }

        $sourceText = $content->getText();
        if ( !is_string( $sourceText ) || trim( $sourceText ) === '' ) {
            return [ 'error' => 'Trang không có nội dung văn bản.' ];
        }
        if ( strlen( $sourceText ) > $this->maxPageChars ) {
            return [
                'error' => 'Trang vượt quá giới hạn kích thước được phép '
                    . '(' . $this->maxPageChars . ' ký tự).'
            ];
        }

        $parserOutput = $this->contentRenderer->getParserOutput(
            $content,
            $title,
            $revision,
            $this->parserOptions
        );

        $templates = [];
        foreach ( $parserOutput->getLinkList( ParserOutputLinkTypes::TEMPLATE ) as $entry ) {
            $template = $entry['link'];
            if ( $template->getNamespace() === NS_TEMPLATE ) {
                $templates[] = $this->normalizeName( $template->getDBkey() );
            }
        }

        return [
            'title' => $title,
            'html' => (string)$parserOutput
                ->runOutputPipeline( $this->parserOptions, [] )
                ->getContentHolderText(),
            'wikitext' => $sourceText,
            'templates' => $templates,
            'page_id' => (int)$title->getArticleID(),
            'revision_id' => (int)$revision->getId(),
            'content_hash' => hash( 'sha256', $sourceText )
        ];
    }

    /**
     * Finds local page links under matching H2/H3 sections.
     *
     * @param string $html
     * @param Title $sourceTitle
     * @param array<string,bool> $skipSet
     * @return Title[]
     */
    private function findVarietyTitles(
        $html,
        Title $sourceTitle,
        array $skipSet
    ) {
        $domData = $this->createDom( $html );
        if ( $domData === null ) {
            return [];
        }

        [ $dom, $xpath, $root ] = $domData;
        $headings = $xpath->query( './/h2 | .//h3', $root );
        if ( !$headings ) {
            return [];
        }

        $results = [];
        foreach ( $headings as $heading ) {
            if ( !$heading instanceof DOMElement ) {
                continue;
            }

            $headingText = $this->normalizeText( $heading->textContent );
            if ( !$this->isVarietyHeading( $headingText ) ) {
                continue;
            }

            $boundary = $heading;
            if ( $heading->parentNode instanceof DOMElement
                && $this->elementHasClass(
                    $heading->parentNode,
                    'mw-heading'
                )
            ) {
                $boundary = $heading->parentNode;
            }

            for ( $node = $boundary->nextSibling;
                $node !== null;
                $node = $node->nextSibling
            ) {
                if ( $this->isNextTopLevelHeading( $node ) ) {
                    break;
                }

                foreach ( $this->anchorsInNode( $node ) as $anchor ) {
                    $title = $this->titleFromAnchor( $anchor );
                    if ( !$title ) {
                        continue;
                    }
                    if ( !in_array(
                        $title->getNamespace(),
                        $this->namespaces,
                        true
                    ) ) {
                        continue;
                    }
                    if ( $sourceTitle->equals( $title ) ) {
                        continue;
                    }

                    $anchorText = $this->normalizeText( $anchor->textContent );
                    $titleKey = $this->normalizeName(
                        $title->getPrefixedText()
                    );
                    $textKey = $this->normalizeName( $anchorText );
                    if ( isset( $skipSet[$titleKey] )
                        || ( $textKey !== '' && isset( $skipSet[$textKey] ) )
                    ) {
                        continue;
                    }

                    $key = $title->getPrefixedDBkey();
                    if ( !isset( $results[$key] ) ) {
                        $results[$key] = $title;
                    }
                    if ( count( $results ) >= $this->maxCollectedPages ) {
                        return array_values( $results );
                    }
                }
            }
        }

        return array_values( $results );
    }

    private function isVarietyHeading( $text ) {
        $text = $this->lower( $this->normalizeText( $text ) );
        return strpos( $text, 'danh sách' ) !== false
            || strpos( $text, 'giống' ) !== false;
    }

    private function isNextTopLevelHeading( DOMNode $node ) {
        if ( $node instanceof DOMElement ) {
            $tag = strtolower( $node->tagName );
            if ( $tag === 'h2' ) {
                return true;
            }
            if ( $this->elementHasClass( $node, 'mw-heading2' ) ) {
                return true;
            }
            if ( $node->getElementsByTagName( 'h2' )->length > 0
                && $this->elementHasClass( $node, 'mw-heading' )
            ) {
                return true;
            }
        }
        return false;
    }

    /**
     * @return DOMElement[]
     */
    private function anchorsInNode( DOMNode $node ) {
        $anchors = [];
        if ( $node instanceof DOMElement ) {
            if ( strtolower( $node->tagName ) === 'a' ) {
                $anchors[] = $node;
            }
            foreach ( $node->getElementsByTagName( 'a' ) as $anchor ) {
                if ( $anchor instanceof DOMElement ) {
                    $anchors[] = $anchor;
                }
            }
        }
        return $anchors;
    }

    private function titleFromAnchor( DOMElement $anchor ) {
        $class = ' ' . trim( $anchor->getAttribute( 'class' ) ) . ' ';
        if ( strpos( $class, ' external ' ) !== false
            || strpos( $class, ' new ' ) !== false
        ) {
            return null;
        }

        $href = trim( $anchor->getAttribute( 'href' ) );
        if ( $href === '' || $href[0] === '#' ) {
            return null;
        }
        if ( preg_match( '~^(?:https?:)?//~i', $href ) ) {
            return null;
        }
        if ( preg_match( '~^(?:mailto|tel|javascript|data):~i', $href ) ) {
            return null;
        }

        $name = trim( $anchor->getAttribute( 'title' ) );
        if ( $name === '' ) {
            $name = $this->pageNameFromHref( $href );
        }
        if ( $name === '' ) {
            return null;
        }

        $title = Title::newFromText( $name, NS_MAIN );
        if ( !$title ) {
            return null;
        }

        return $title;
    }

    private function pageNameFromHref( $href ) {
        $href = html_entity_decode(
            (string)$href,
            ENT_QUOTES | ENT_HTML5,
            'UTF-8'
        );
        $parts = parse_url( $href );
        if ( !is_array( $parts ) ) {
            return '';
        }

        if ( !empty( $parts['query'] ) ) {
            parse_str( $parts['query'], $query );
            if ( isset( $query['title'] ) && is_string( $query['title'] ) ) {
                return str_replace( '_', ' ', rawurldecode( $query['title'] ) );
            }
        }

        $path = isset( $parts['path'] ) ? (string)$parts['path'] : '';
        if ( $path === '' ) {
            return '';
        }

        $marker = '/index.php/';
        $position = strpos( $path, $marker );
        if ( $position !== false ) {
            $tail = substr( $path, $position + strlen( $marker ) );
            return str_replace( '_', ' ', rawurldecode( $tail ) );
        }

        if ( strpos( $path, './' ) === 0 ) {
            return str_replace( '_', ' ', rawurldecode( substr( $path, 2 ) ) );
        }

        return '';
    }

    /**
     * @return array{0:DOMDocument,1:DOMXPath,2:DOMElement}|null
     */
    private function createDom( $html ) {
        if ( !class_exists( '\\DOMDocument' ) ) {
            return null;
        }

        $previous = libxml_use_internal_errors( true );
        $dom = new DOMDocument();
        $loaded = $dom->loadHTML(
            '<?xml encoding="UTF-8"><div id="wikikg-local-root">'
                . (string)$html . '</div>',
            LIBXML_NOWARNING | LIBXML_NOERROR | LIBXML_NONET
        );
        libxml_clear_errors();
        libxml_use_internal_errors( $previous );

        if ( !$loaded ) {
            return null;
        }

        $xpath = new DOMXPath( $dom );
        $roots = $xpath->query( '//*[@id="wikikg-local-root"]' );
        $root = $roots && $roots->length > 0 ? $roots->item( 0 ) : null;
        if ( !$root instanceof DOMElement ) {
            return null;
        }

        return [ $dom, $xpath, $root ];
    }

    private function htmlToText( $html ) {
        $html = (string)$html;
        if ( trim( $html ) === '' ) {
            return '';
        }

        $domData = $this->createDom( $html );
        if ( $domData === null ) {
            return $this->normalizeText( html_entity_decode(
                strip_tags( preg_replace(
                    '~<(?:br\s*/?|/p|/div|/li|/tr|/h[1-6])[^>]*>~i',
                    "\n",
                    $html
                ) ),
                ENT_QUOTES | ENT_HTML5,
                'UTF-8'
            ) );
        }

        [ $dom, $xpath, $root ] = $domData;
        $noiseQueries = [
            './/script',
            './/style',
            './/noscript',
            './/svg',
            './/form',
            './/nav',
            './/*[contains(concat(" ", normalize-space(@class), " "), " mw-editsection ")]',
            './/*[contains(concat(" ", normalize-space(@class), " "), " navbox ")]',
            './/*[contains(concat(" ", normalize-space(@class), " "), " toc ")]',
            './/*[contains(concat(" ", normalize-space(@class), " "), " printfooter ")]'
        ];

        foreach ( $noiseQueries as $query ) {
            $nodes = $xpath->query( $query, $root );
            if ( !$nodes ) {
                continue;
            }
            $remove = [];
            foreach ( $nodes as $node ) {
                $remove[] = $node;
            }
            foreach ( $remove as $node ) {
                if ( $node->parentNode ) {
                    $node->parentNode->removeChild( $node );
                }
            }
        }

        foreach ( [
            'br', 'p', 'div', 'li', 'tr',
            'h1', 'h2', 'h3', 'h4', 'h5', 'h6'
        ] as $tag ) {
            $nodes = [];
            foreach ( $root->getElementsByTagName( $tag ) as $node ) {
                $nodes[] = $node;
            }
            foreach ( $nodes as $node ) {
                $node->appendChild( $dom->createTextNode( "\n" ) );
            }
        }

        return $this->normalizeText( $root->textContent );
    }

    private function elementHasClass( DOMElement $element, $className ) {
        $classes = preg_split(
            '/\s+/',
            trim( $element->getAttribute( 'class' ) )
        );
        return in_array( $className, $classes ?: [], true );
    }

    /**
     * @param string[] $names
     * @return array<string,bool>
     */
    private function buildNameSet( array $names ) {
        $set = [];
        foreach ( $names as $name ) {
            $key = $this->normalizeName( $name );
            if ( $key !== '' ) {
                $set[$key] = true;
            }
        }
        return $set;
    }

    private function normalizeName( $name ) {
        $name = str_replace( '_', ' ', trim( (string)$name ) );
        $name = preg_replace( '/\s+/u', ' ', $name );
        return $this->lower( (string)$name );
    }

    /**
     * @param string[] $usedTemplates Canonical names from the rendered revision
     * @param string[] $templateNames
     * @return bool
     */
    private function hasActiveTemplate( array $usedTemplates, array $templateNames ) {
        $recognized = $this->buildNameSet( $templateNames );
        foreach ( $usedTemplates as $template ) {
            if ( isset( $recognized[$template] ) ) {
                return true;
            }
        }
        return false;
    }

    /**
     * @param Title $title
     * @param string[] $cropNames
     * @return bool
     */
    private function isCropTiedTitle( Title $title, array $cropNames ) {
        $titleName = $this->normalizeName( $title->getPrefixedText() );
        foreach ( $cropNames as $cropName ) {
            $cropName = $this->normalizeName( $cropName );
            if ( $cropName === '' ) {
                continue;
            }

            foreach ( [
                $cropName,
                'giống ' . $cropName,
                'cây ' . $cropName
            ] as $prefix ) {
                if ( strpos( $titleName, $prefix . ' ' ) === 0 ) {
                    return true;
                }
            }
        }
        return false;
    }

    private function lower( $text ) {
        return function_exists( 'mb_strtolower' )
            ? mb_strtolower( (string)$text, 'UTF-8' )
            : strtolower( (string)$text );
    }

    private function errorPage( $title, $message, $url = '' ) {
        return [
            'title' => (string)$title,
            'url' => (string)$url,
            'text' => '',
            'error' => (string)$message
        ];
    }

    private function normalizeText( $text ) {
        $text = html_entity_decode(
            (string)$text,
            ENT_QUOTES | ENT_HTML5,
            'UTF-8'
        );
        $text = preg_replace( '/[ \t\x{00A0}]+/u', ' ', $text );
        $text = preg_replace( '/ *\R */u', "\n", $text );
        $text = preg_replace( '/\n{3,}/u', "\n\n", $text );
        return trim( (string)$text );
    }
}
