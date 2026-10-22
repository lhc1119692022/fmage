// Boot a real DSH runtime with the installed Fmage package and assert the
// browser half is discovered: dsh.client + exports["./client"] must produce a
// client-module graph row the shell can load. Caller supplies a temporary dir.
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { cpSync, mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

const [anchor, installedProfile, temp] = process.argv.slice(2);
assert(anchor && installedProfile && temp, 'Pass CLI package.json anchor, installed profile, empty temporary directory');
const require = createRequire(anchor);
const boot = await import(pathToFileURL(require.resolve('@deepseek-ai/dsh-app-boot')));
const installed = boot.loadProfileDirectory('dsh', installedProfile, anchor);
const layer = installed.layers.find(l => l.packageName === 'dsh-fmage');
assert(layer, 'Installed Fmage bundle is not enabled');
const profile = join(temp, 'profiles/desktop');
const target = join(profile, 'node_modules/dsh-fmage');
mkdirSync(target, { recursive: true });
cpSync(layer.packageDir, target, { recursive: true });
writeFileSync(join(profile, 'package.json'), JSON.stringify({ private: true,
  dependencies: { 'dsh-fmage': 'file:./node_modules/dsh-fmage' },
  dsh: { profile: { bundles: ['dsh-fmage'] } },
}));
const config = join(profile, 'cordis.yml');
writeFileSync(config, '[]\n');
const loaded = boot.loadProfileDirectory('dsh', profile, anchor);
assert.deepEqual(loaded.skippedBundles, []);
const resolution = await boot.createRuntimeResolution({ installAnchor: anchor, profile: loaded, home: temp });
const patches = [{ insert: [{ id: 'client-modules', name: '@deepseek-ai/dsh-client-modules' }] },
  ...loaded.layers.flatMap(layer => layer.patches)];
let ctx;
try {
  ctx = await boot.boot('dsh-fmage-client-graph', config, patches, async host => {
    host.provide('profileContext', { dir: profile, installAnchor: anchor });
    await host.plugin(boot.PluginPackages, { resolution });
  });
  const graph = ctx.clientModules.graph();
  const row = graph.entries.find(entry => entry.id === 'dsh-fmage');
  assert(row, `Fmage has no client-module row; graph ids: ${graph.entries.map(e => e.id).join(', ')}`);
  assert.equal(ctx.clientModules.clientPath('dsh-fmage'), join(target, 'client.js'));
  console.log(JSON.stringify({ clientModuleRow: row.id, inject: row.inject ?? [], bundle: 'client.js' }));
} finally {
  await ctx?.fiber.dispose();
}
