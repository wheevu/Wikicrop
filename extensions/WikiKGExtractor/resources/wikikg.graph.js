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
		var evidencePanel;
		var evidenceAside;
		var hasEvidence;

		if ( !graph || !container || typeof cytoscape !== 'function' ||
			!Array.isArray( graph.nodes ) || graph.nodes.length === 0 ) {
			return null;
		}
		if ( container.dataset.wikikgReady === '1' ) {
			return null;
		}
		container.dataset.wikikgReady = '1';
		evidencePanel = document.querySelector( '[data-wikikg-edge-evidence]' );
		evidenceAside = evidencePanel ? evidencePanel.closest( '.wikikg-edge-evidence' ) : null;
		wrapper = container.closest( '.wikikg-graph-visual' );
		if ( wrapper ) {
			wrapper.hidden = false;
		}
		hasEvidence = ( graph.edges || [] ).some( function ( edge ) {
			return edge.data && Array.isArray( edge.data.supporting_snapshot_ids ) &&
				edge.data.supporting_snapshot_ids.length > 0;
		} );

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
			cy.on( 'tap', 'edge', function ( event ) {
				showEdgeEvidence(
					evidencePanel,
					event.target.data( 'supporting_snapshot_ids' ),
					true
				);
			} );
			cy.on( 'select', 'edge', function () {
				updateSelectedEvidence( cy, evidencePanel );
			} );
			cy.on( 'unselect', 'edge', function () {
				updateSelectedEvidence( cy, evidencePanel );
			} );
			cy.on( 'tap', function ( event ) {
				if ( event.target === cy && evidencePanel ) {
					updateSelectedEvidence( cy, evidencePanel );
				}
			} );
			cy.on( 'resize', function () {
				cy.fit( undefined, 24 );
			} );
			cy.fit( undefined, 24 );
			if ( evidenceAside && hasEvidence ) {
				showEdgeEvidence( evidencePanel, [] );
				evidenceAside.hidden = false;
			}
			return cy;
		} catch ( error ) {
			container.dataset.wikikgReady = '0';
			if ( wrapper ) {
				wrapper.hidden = true;
			}
			if ( evidenceAside ) {
				evidenceAside.hidden = true;
			}
			return null;
		}
	}

	function updateSelectedEvidence( cy, panel ) {
		var selected = cy.$( 'edge:selected' );
		if ( selected.length ) {
			showEdgeEvidence(
				panel,
				selected.last().data( 'supporting_snapshot_ids' ),
				true
			);
		} else {
			showEdgeEvidence( panel, [] );
		}
	}

	function showEdgeEvidence( panel, snapshotIds, edgeSelected ) {
		var list;
		var found = 0;
		var partialWarning;
		if ( !panel ) {
			return;
		}
		while ( panel.firstChild ) {
			panel.removeChild( panel.firstChild );
		}
		if ( !Array.isArray( snapshotIds ) || snapshotIds.length === 0 ) {
			panel.appendChild( document.createTextNode( edgeSelected
				? panel.dataset.noSupportMessage || ''
				: panel.dataset.selectMessage || '' ) );
			return;
		}
		list = document.createElement( 'ul' );
		snapshotIds.forEach( function ( snapshotId ) {
			var row = Array.from( document.querySelectorAll( '[data-wikikg-snapshot-id]' ) )
				.find( function ( candidate ) {
					return candidate.dataset.wikikgSnapshotId === snapshotId;
				} );
			var cells;
			var item;
			var statement;
			var links;
			var claimLink;
			if ( !row ) {
				return;
			}
			cells = row.querySelectorAll( 'td' );
			if ( cells.length < 6 ) {
				return;
			}
			item = document.createElement( 'li' );
			statement = document.createElement( 'p' );
			statement.textContent = cells[ 0 ].textContent.trim() + ' - ' +
				cells[ 1 ].textContent.trim() + ' - ' + cells[ 2 ].textContent.trim() +
				( cells[ 3 ].textContent.trim() ? ' (' + cells[ 3 ].textContent.trim() + ')' : '' );
			item.appendChild( statement );
			claimLink = document.createElement( 'a' );
			claimLink.href = '#' + row.id;
			claimLink.textContent = panel.dataset.openClaimMessage || '';
			item.appendChild( claimLink );
			item.appendChild( document.createTextNode( ' ' ) );
			links = row.querySelectorAll( 'td:nth-child(5) a, td:nth-child(6) a' );
			links.forEach( function ( link ) {
				item.appendChild( link.cloneNode( true ) );
				item.appendChild( document.createTextNode( ' ' ) );
			} );
			list.appendChild( item );
			found++;
		} );
		if ( found === 0 ) {
			panel.appendChild( document.createTextNode(
				panel.dataset.noSupportMessage || ''
			) );
			return;
		}
		if ( found < snapshotIds.length ) {
			partialWarning = document.createElement( 'li' );
			partialWarning.textContent = panel.dataset.partialSupportMessage || '';
			list.appendChild( partialWarning );
		}
		panel.appendChild( list );
	}

	mw.wikikgGraph = {
		init: init,
		makeElements: makeElements,
		makeLayout: makeLayout
	};
	mw.hook( 'wikipage.content' ).add( init );
}() );
