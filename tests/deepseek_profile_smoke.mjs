// Actual Desktop loader + Cordis lifecycle, with no resolver overrides or mock services.
// Caller supplies an empty temporary directory; never boot the user's full profile.
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { cpSync, mkdirSync, writeFileSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

const [runtime, installedProfile, temp] = process.argv.slice(2);
assert(runtime && installedProfile && temp, 'Pass runtime, installed profile, empty temporary directory');
const anchor = join(runtime, 'node_modules/@deepseek-ai/dsh/package.json');
const require = createRequire(anchor);
const boot = await import(pathToFileURL(require.resolve('@deepseek-ai/dsh-app-boot')));
const installed = boot.loadProfileDirectory('dsh', installedProfile, anchor);
assert(!installed.skippedBundles.some(b => b.packageName === 'dsh-fmage'), 'Installed Fmage bundle cannot resolve');
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
const patches = [{ insert: [
  { id: 'system-prompt', name: '@deepseek-ai/dsh-system-prompt' },
  { id: 'skill', name: '@deepseek-ai/dsh-skill' },
  { id: 'tools', name: '@deepseek-ai/dsh-tools' },
] }, ...loaded.layers.flatMap(layer => layer.patches)];
let ctx;
try {
  ctx = await boot.boot('dsh-fmage-check', config, patches, async host => {
    host.provide('profileContext', { dir: profile, installAnchor: anchor });
    await host.plugin(boot.PluginPackages, { resolution });
  });
  const skills = await ctx.skills.list();
  assert.equal(skills.length, 5);
  assert.equal(skills.find(s => s.name === 'fmage-direct').invocation.modelInvocable, false);
  const names = [...ctx.tools.view().visible.keys()].filter(n => n.startsWith('mcp__Fmage__'));
  assert.equal(names.length, 10);
  assert(names.includes('mcp__Fmage__generate_image'));
  assert(names.includes('mcp__Fmage__regress_image'));
  const receipt = JSON.parse(readFileSync(join(target, '.fmage-runtime.json'), 'utf8'));
  assert.equal(receipt.pid, process.pid);
  console.log(JSON.stringify({ nativeProfileLoader: true, isolatedProbe: true, skills: skills.length, tools: names.length }));
} finally {
  await ctx?.fiber.dispose();
}
