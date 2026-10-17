import assert from 'node:assert/strict';
import test from 'node:test';
import { assertUnmetDshPeers } from './deepseek_compatibility.mjs';

const peers = {
  '@deepseek-ai/dsh-skill': '>=0.2.0-rc.2',
  '@deepseek-ai/dsh-mcp-client': '>=0.2.0-rc.2',
};
const version = '0.1.9';

test('accepts current requirements and legacy peers without mutating the issue', () => {
  for (const field of ['requirements', 'peers']) {
    const issue = { [field]: { ...peers }, exempted: false };
    const original = structuredClone(issue);
    assertUnmetDshPeers(issue, peers, version);
    assert.deepEqual(issue, original);
  }
});

test('current requirements take precedence over a stale legacy peers field', () => {
  assertUnmetDshPeers({ requirements: peers, peers: {}, exempted: false }, peers, version);
  assert.throws(() => assertUnmetDshPeers(
    { requirements: {}, peers, exempted: false }, peers, version), /declared Fmage peer requirements/);
});

test('missing rejection and version exemptions remain failures', () => {
  for (const issue of [undefined, null, { requirements: peers, exempted: true }]) {
    assert.throws(() => assertUnmetDshPeers(issue, peers, version), /minimum Harness version/);
  }
});

test('unknown and malformed mismatch reports fail with a useful diagnostic', () => {
  for (const issue of [
    {}, { requirements: null, peers }, { requirements: [] },
    { requirements: 'unsupported' }, { peers: [] }, { peers: null },
  ]) {
    assert.throws(() => assertUnmetDshPeers(issue, peers, version), /must report unmet requirements/);
  }
});

test('missing peers, extra requirements and changed version ranges cannot pass', () => {
  for (const requirements of [
    {},
    { '@deepseek-ai/dsh-skill': '>=0.2.0-rc.2' },
    { ...peers, 'engines.dsh': '>=0.2.0-rc.2' },
    { ...peers, '@deepseek-ai/dsh-skill': '*' },
  ]) {
    for (const field of ['requirements', 'peers']) {
      assert.throws(() => assertUnmetDshPeers(
        { [field]: requirements, exempted: false }, peers, version), /declared Fmage peer requirements/);
    }
  }
});
