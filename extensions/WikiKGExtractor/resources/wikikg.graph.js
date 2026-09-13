( function () {
	'use strict';

	function makeElements( graph ) {
		return ( graph.nodes || [] ).concat( graph.edges || [] );
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
				layout: {
					name: 'cose',
					animate: false,
					idealEdgeLength: 70,
					nodeRepulsion: 6000,
					padding: 24
				},
				style: [
					{
						selector: 'node',
						style: {
							'background-color': '#2f855a',
							'border-color': '#205f3f',
							'border-width': 1,
							'color': '#17231d',
							'font-size': 11,
							'label': 'data(label)',
							'text-background-color': '#ffffff',
							'text-background-opacity': 0.9,
							'text-background-padding': 3,
							'text-margin-y': 18,
							'width': 22,
							'height': 22
						}
					},
					{
						selector: 'node[type = "Crop"]',
						style: {
							'background-color': '#205f3f',
							'width': 30,
							'height': 30
						}
					},
					{
						selector: 'node[type = "Pest"]',
						style: {
							'background-color': '#b7791f',
							'border-color': '#805b16',
							'shape': 'diamond'
						}
					},
					{
						selector: 'edge',
						style: {
							'curve-style': 'bezier',
							'font-size': 9,
							'label': 'data(label)',
							'line-color': '#9db0a5',
							'target-arrow-color': '#71867a',
							'target-arrow-shape': 'triangle',
							'text-background-color': '#ffffff',
							'text-background-opacity': 0.92,
							'text-background-padding': 2,
							'width': 1.5
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
		makeElements: makeElements
	};
	mw.hook( 'wikipage.content' ).add( init );
}() );
