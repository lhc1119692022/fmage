import assert from 'node:assert/strict';

export function assertUnmetDshPeers(issue, expectedPeers, version) {
  assert(issue && !issue.exempted,
    `Fmage must retain its minimum Harness version for ${version}`);
  // New hosts report requirements; older hosts reported only peer mismatches.
  const requirements = Object.hasOwn(issue, 'requirements') ? issue.requirements : issue.peers;
  assert(requirements && typeof requirements === 'object' && !Array.isArray(requirements),
    `Harness ${version} must report unmet requirements or legacy peers`);
  assert.deepEqual(requirements, expectedPeers,
    `Harness ${version} must reject the declared Fmage peer requirements`);
}
