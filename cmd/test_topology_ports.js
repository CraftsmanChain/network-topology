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
    formatBytes: value => String(value)
  });
  for (const name of ['portKey', 'portIsUp', 'portStatusSnapshot', 'buildPortIndex', 'findPortByName', 'buildLinkTable']) {
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
