QUnit.module( 'ext.wikikg.graph' );

QUnit.test( 'makeElements keeps bounded server data unchanged', function ( assert ) {
	var nodes = [ { data: { id: 'n-crop' } } ];
	var edges = [ { data: { id: 'e-variety' } } ];
	var elements = mw.wikikgGraph.makeElements( {
		nodes: nodes,
		edges: edges
	} );

	assert.deepEqual( elements, nodes.concat( edges ) );
} );

QUnit.test( 'makeElements accepts missing edge data', function ( assert ) {
	var nodes = [ { data: { id: 'n-crop' } } ];

	assert.deepEqual( mw.wikikgGraph.makeElements( { nodes: nodes } ), nodes );
} );

QUnit.test( 'init reveals the graph before Cytoscape measures it', function ( assert ) {
	var originalCytoscape = window.cytoscape;
	var fixture = document.getElementById( 'qunit-fixture' );
	var wrapper = document.createElement( 'div' );
	var container = document.createElement( 'div' );
	var fitCount = 0;
	var resizeHandler;
	var result;

	wrapper.className = 'wikikg-graph-visual';
	wrapper.hidden = true;
	container.dataset.wikikgGraph = '';
	wrapper.appendChild( container );
	fixture.appendChild( wrapper );
	mw.config.set( 'wgWikiKGGraph', {
		nodes: [ { data: { id: 'n-crop' } } ],
		edges: []
	} );
	window.cytoscape = function () {
		assert.strictEqual( wrapper.hidden, false );
		return {
			on: function ( eventName, selectorOrHandler ) {
				if ( eventName === 'resize' ) {
					resizeHandler = selectorOrHandler;
				}
			},
			fit: function () {
				fitCount++;
			}
		};
	};

	result = mw.wikikgGraph.init();
	assert.ok( result );
	assert.strictEqual( container.dataset.wikikgReady, '1' );
	assert.strictEqual( fitCount, 1 );
	assert.strictEqual( typeof resizeHandler, 'function' );
	resizeHandler();
	assert.strictEqual( fitCount, 2 );
	window.cytoscape = originalCytoscape;
	mw.config.set( 'wgWikiKGGraph', null );
} );

QUnit.test( 'init restores the fallback when Cytoscape fails', function ( assert ) {
	var originalCytoscape = window.cytoscape;
	var fixture = document.getElementById( 'qunit-fixture' );
	var wrapper = document.createElement( 'div' );
	var container = document.createElement( 'div' );

	wrapper.className = 'wikikg-graph-visual';
	wrapper.hidden = true;
	container.dataset.wikikgGraph = '';
	wrapper.appendChild( container );
	fixture.appendChild( wrapper );
	mw.config.set( 'wgWikiKGGraph', {
		nodes: [ { data: { id: 'n-crop' } } ],
		edges: []
	} );
	window.cytoscape = function () {
		throw new Error( 'test failure' );
	};

	assert.strictEqual( mw.wikikgGraph.init(), null );
	assert.strictEqual( wrapper.hidden, true );
	assert.strictEqual( container.dataset.wikikgReady, '0' );
	window.cytoscape = originalCytoscape;
	mw.config.set( 'wgWikiKGGraph', null );
} );
