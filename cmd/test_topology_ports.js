const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const html = fs.readFileSync(path.join(__dirname, '..', 'topology.html'), 'utf8');
function pageFunction(name) {
  const start = html.indexOf(`    function ${name}(`);
  assert.notEqual(start, -1);
  const end = html.indexOf('\n    function ', start + 1);
  return html.slice(start, end);
}

function context() {
  const ctx = vm.createContext({
    normalizeName: value => String(value ?? '').trim(),
    state: { nodeById: new Map(), portIndex: new Map() },
    resolvePortStateOverride: () => null,
    buildLinkDetailEntries: link => link.raw,
    esc: value => String(value),
    aliasFor: () => '',
    formatBytes: value => String(value),
    linkUsesMeth: link => /^(MEth|Mgmt\s+\d+)/i.test(link.sourcePort) || /^(MEth|Mgmt\s+\d+)/i.test(link.targetPort)
  });
  for (const name of ['portKey', 'portIsUp', 'portStatusSnapshot', 'buildPortIndex', 'findPortByName', 'buildLinkTable', 'rawLinkDown', 'physicalFaultLinks']) {
    vm.runInContext(pageFunction(name), ctx);
  }
  return ctx;
}

test('explicit DOWN cannot be overridden by historical traffic', () => {
  const ctx = context();
  assert.equal(ctx.portIsUp({ status: 1, transmit: 2600, receive: 1800 }), false);
  assert.equal(ctx.portIsUp({ status: '1', transmit: 2600 }), false);
  assert.equal(ctx.portIsUp({ status: 0, transmit: 0, receive: 0 }), true);
  assert.equal(ctx.portIsUp({ status: null, transmit: 0, receive: 0 }), false);
});

test('fault count uses unique port pairs, not aggregate edges or LLDP directions', () => {
  const ctx = context();
  const pair = { source: 'a', sourcePort: 'Eth1', target: 'b', targetPort: 'Eth2' };
  ctx.state.portIndex = ctx.buildPortIndex([
    { id: 'a', ports: [{ ifName: 'Eth1', ifDescr: 'alias1', status: 1 }, { ifName: 'Eth3', status: 1 }] },
    { id: 'b', ports: [{ ifName: 'Eth2', status: 1 }, { ifName: 'Eth4', status: 0 }] }
  ]);
  ctx.state.allLinks = [pair, pair, { source: 'b', sourcePort: 'Eth2', target: 'a', targetPort: 'Eth1' },
    { ...pair, sourcePort: 'alias1' }, { ...pair, sourcePort: 'Eth3', targetPort: 'Eth4' }];
  ctx.state.displayLinks = []; // Intra-group physical links may have no visible aggregate edge.
  assert.equal(ctx.physicalFaultLinks().length, 2);
  assert.equal(ctx.physicalFaultLinks()[0].sourcePort, 'Eth1');
});

test('hidden management ports and missing-only endpoints never count as faults', () => {
  const ctx = context();
  ctx.state.portIndex = ctx.buildPortIndex([{ id: 'a', ports: [{ ifName: 'Mgmt 1', status: 1 }, { ifName: 'MEth0', status: 1 }] }]);
  ctx.state.allLinks = ['missing', 'Mgmt 1', 'MEth0'].map(sourcePort => ({ source: 'a', sourcePort, target: 'b', targetPort: 'missing' }));
  assert.equal(ctx.physicalFaultLinks().length, 0);
  ctx.state.showMeth = true;
  assert.equal(ctx.physicalFaultLinks().length, 2);
});

test('fault detail exact table cannot add inferred or reverse entries', () => {
  const ctx = context();
  ctx.buildLinkDetailEntries = () => { throw Error('must not infer links'); };
  const table = ctx.buildLinkTable({ raw: [{ source: 'a', sourcePort: 'one', target: 'b', targetPort: 'two' }] }, true);
  assert.equal((table.match(/<tr/g) || []).length, 2);
});

test('idle fault paths render above normal paths, below nodes and selection', () => {
  const render = pageFunction('render');
  assert.ok(render.indexOf('svg.appendChild(linkLayer)') < render.indexOf('svg.appendChild(faultLinkLayer)'));
  assert.ok(render.indexOf('svg.appendChild(faultLinkLayer)') < render.indexOf('svg.appendChild(nodeLayer)'));
  assert.ok(render.indexOf('svg.appendChild(nodeLayer)') < render.indexOf('svg.appendChild(focusLinkLayer)'));
  assert.ok(render.includes('const targetLayer = link.down ? faultLinkLayer : linkLayer'));
});

test('duplicate descriptions cannot select a random interface or shadow a real name', () => {
  const ctx = context();
  const ports = [{ ifName: 'Eth1', ifAlias: 'shared' }, { ifName: 'Eth2', ifAlias: 'shared' }];
  assert.equal(ctx.buildPortIndex([{ id: 'a', ports }]).get('a').get('shared'), null);
  const exact = { ifName: 'shared' };
  ports.unshift(exact);
  assert.equal(ctx.buildPortIndex([{ id: 'a', ports }]).get('a').get('shared'), exact);
});

test('unmatched endpoints are neutral, not DOWN or zero utilization', () => {
  const ctx = context();
  const table = ctx.buildLinkTable({ raw: [{ source: 'a', sourcePort: 'missing1', target: 'b', targetPort: 'missing2' }] });
  assert.equal((table.match(/class="port-muted"/g) || []).length, 2);
  assert.ok(!table.includes('alarm-row'));
  assert.ok(!table.includes('port-down'));
  assert.ok(!table.includes('0.00%'));
});

test('a real DOWN endpoint still marks the link even when the peer is missing', () => {
  const ctx = context();
  ctx.state.portIndex = ctx.buildPortIndex([{ id: 'a', ports: [{ ifName: 'Eth1', status: 1 }] }]);
  const table = ctx.buildLinkTable({ raw: [{ source: 'a', sourcePort: 'Eth1', target: 'b', targetPort: 'missing' }] });
  assert.ok(table.includes('alarm-row'));
  assert.ok(table.includes('class="port-down">DOWN'));
  assert.equal((table.match(/class="port-muted"/g) || []).length, 1);
});
