<?php

namespace MediaWiki\Extension\WikiKGExtractor;

use Skin;
use SpecialPage;

class Hooks {
	/**
	 * Thêm Trích xuất Wiki vào thanh menu bên trái.
	 *
	 * @param Skin $skin
	 * @param array &$bar
	 */
	public static function onSkinBuildSidebar( Skin $skin, array &$bar ) {
		if ( !isset( $bar['navigation'] ) ) {
			$bar['navigation'] = [];
		}

		$bar['navigation'][] = [
			'text' => 'Trích xuất Wiki',
			'href' => SpecialPage::getTitleFor(
				'WikiKGExtractor'
			)->getLocalURL(),
			'id' => 'n-wikikgextractor',
			'active' => false
		];
	}
}
