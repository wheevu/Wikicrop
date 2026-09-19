( function () {
	'use strict';

	function makeElements( graph ) {
		return ( graph.nodes || [] ).concat( graph.edges || [] );
	}

	function makeLayout( graph ) {
		if ( graph.nodes.length <= 30 ) {
			return {
				name: 'concentric',
				animate: false,
				avoidOverlap: true,
				equidistant: true,
				minNodeSpacing: 72,
				padding: 36,
				spacingFactor: 0.9,
				startAngle: -Math.PI / 2,
				concentric: function ( node ) {
					if ( node.data( 'type' ) === 'Crop' ) {
						return 3;
					}
					return node.data( 'type' ) === 'Variety' ? 2 : 1;
				},
				levelWidth: function () {
					return 1;
				}
			};
		}

		return {
			name: 'cose',
			animate: false,
			idealEdgeLength: 90,
			nodeRepulsion: 10000,
			padding: 36
		};
	}

	function init() {
		var graph = mw.config.get( 'wgWikiKGGraph' );
		var container = document.querySelector( '[data-wikikg-graph]' );
		var wrapper;
		var cy;

		if ( !graph || !container || typeof cytoscape !== 'function' ||
			!Array.isArray( graph.nodes ) || graph.nodes.length === 0 ) {
			return null;
		}
		if ( container.dataset.wikikgReady === '1' ) {
			return null;
		}
		container.dataset.wikikgReady = '1';
		wrapper = container.closest( '.wikikg-graph-visual' );
		if ( wrapper ) {
			wrapper.hidden = false;
		}

		try {
			cy = cytoscape( {
				container: container,
				elements: makeElements( graph ),
				layout: makeLayout( graph ),
				style: [
					{
						selector: 'node',
						style: {
							'background-color': '#2f855a',
							'border-color': '#205f3f',
							'border-width': 1,
							'color': '#17231d',
							'font-size': 13,
							'label': 'data(label)',
							'text-background-color': '#ffffff',
							'text-background-opacity': 0.94,
							'text-background-padding': 3,
							'text-valign': 'bottom',
							'text-margin-y': 11,
							'width': 32,
							'height': 32
						}
					},
					{
						selector: 'node[type = "Crop"]',
						style: {
							'background-color': '#205f3f',
							'width': 42,
							'height': 42
						}
					},
					{
						selector: 'node[type = "Pest"]',
						style: {
							'background-color': '#b7791f',
							'border-color': '#805b16',
							'shape': 'diamond',
							'width': 28,
							'height': 28
						}
					},
					{
						selector: 'edge',
						style: {
							'curve-style': 'bezier',
							'font-size': 9,
							'line-color': '#9db0a5',
							'target-arrow-color': '#71867a',
							'target-arrow-shape': 'triangle',
							'width': 2
						}
					},
					{
						selector: 'edge:selected',
						style: {
							'font-size': 11,
							'label': 'data(label)',
							'line-color': '#2f855a',
							'target-arrow-color': '#205f3f',
							'text-background-color': '#ffffff',
							'text-background-opacity': 0.96,
							'text-background-padding': 2,
							'text-margin-x': 70,
							'text-margin-y': -7,
							'width': 4
						}
					}
				]
			} );
			cy.on( 'tap', 'node', function ( event ) {
				var href = event.target.data( 'href' );
				if ( href ) {
					window.location.assign( href );
				}
			} );
			cy.on( 'resize', function () {
				cy.fit( undefined, 24 );
			} );
			cy.fit( undefined, 24 );
			return cy;
		} catch ( error ) {
			container.dataset.wikikgReady = '0';
			if ( wrapper ) {
				wrapper.hidden = true;
			}
			return null;
		}
	}

	mw.wikikgGraph = {
		init: init,
		makeElements: makeElements,
		makeLayout: makeLayout
	};
	mw.hook( 'wikipage.content' ).add( init );
}() );
