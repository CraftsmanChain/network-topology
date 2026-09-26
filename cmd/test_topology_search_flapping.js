const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const html = fs.readFileSync(path.join(__dirname, '..', 'topology.html'), 'utf8');
function context() {
  const ctx = vm.createContext({ state: {
    topology: { nodes: [
      { id: 'Switch-A', ip: '10.12.1.3', ports: [{ ifIndex: '30', ifName: '25GE1/0/18' }] },
      { id: 'Switch-B', ip: '10.12.1.30', ports: [{ ifIndex: '1', ifName: 'Mgmt 0' }] }
    ] }, runtimeStatus: { datasets: {} }, showMeth: false,
    flapping: { ports: [], stale: false, updated_at: '2026-09-26T04:00:00Z' }
  }, normalizeName: value => String(value || '').trim(), aliasFor: id => id === 'Switch-A' ? 'TOR17' : '' });
  for (const name of ['searchDevices', 'flappingIsStale', 'visibleFlappingPorts', 'isManagementPortName', 'isHiddenManagementPortName', 'portAffectsFaultState']) {
    const start = html.indexOf(`    function ${name}(`);
    assert.notEqual(start, -1);
    vm.runInContext(html.slice(start, html.indexOf('\n    function ', start + 1)), ctx);
  }
  ctx.state.nodeById = new Map(ctx.state.topology.nodes.map(n => [n.id, n]));
  return ctx;
}

test('search matches names/IP/aliases case-insensitively and prioritizes exact IP', () => {
  const ctx = context();
  assert.equal(ctx.searchDevices('  swITCH-a ')[0].id, 'Switch-A');
  assert.equal(ctx.searchDevices('tor17')[0].id, 'Switch-A');
  assert.equal(ctx.searchDevices('10.12.1.3')[0].id, 'Switch-A');
  assert.equal(ctx.searchDevices('10.12.1.3').length, 2);
  for (const term of ['', '.*', '[', 'missing']) assert.equal(ctx.searchDevices(term).length, 0);
});

test('flapping follows management filter and threshold without altering port status', () => {
  const ctx = context();
  ctx.state.flapping.ports = [
    { device_id: 'Switch-A', ifIndex: '30', changes: 17 },
    { device_id: 'Switch-A', ifIndex: '31', changes: 5 },
    { device_id: 'Switch-B', ifIndex: '1', changes: 6 },
    { device_id: 'other', ifIndex: '1', changes: 10 }
  ];
  assert.equal(ctx.visibleFlappingPorts().length, 1);
  ctx.state.showMeth = true;
  assert.equal(ctx.visibleFlappingPorts().length, 2);
});

test('unknown, failed or aged flapping snapshot is not fresh zero', () => {
  const ctx = context();
  const now = Date.parse('2026-09-26T04:06:00Z');
  assert.equal(ctx.flappingIsStale(now), false);
  assert.equal(ctx.flappingIsStale(now + 1000000), true);
  ctx.state.flapping.stale = true;
  assert.equal(ctx.flappingIsStale(now), true);
  ctx.state.flapping.stale = false;
  ctx.state.flapping.updated_at = null;
  assert.equal(ctx.flappingIsStale(now), true);
});
