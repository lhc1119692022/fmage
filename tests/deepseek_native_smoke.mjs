// Run using the installed Desktop Electron in Node mode, against a TEMPORARY payload.
// Verifies the real desktop MCP bridge and adapter without booting a second profile.
import assert from 'node:assert/strict';
import { registerHooks } from 'node:module';
import { pathToFileURL } from 'node:url';
import { join } from 'node:path';
import { unlinkSync } from 'node:fs';

const [runtime, target] = process.argv.slice(2);
assert(runtime && target, 'Pass installed app.asar/dsh and a temporary built plugin directory');
const anchor = pathToFileURL(join(runtime, 'package.json')).href;
const hooks = registerHooks({
  resolve(specifier, context, next) {
    if (specifier.startsWith('@deepseek-ai/')) return next(specifier, { ...context, parentURL: anchor });
    return next(specifier, context);
  },
});
const effects = [];
const lifecycle = new AbortController();
const tools = new Map();
let provider;
let invalidations = 0;
const ctx = {
  logger: console,
  fiber: { uid: 'fmage-test' },
  get() { return undefined; },
  inject() {},
  on() {},
  effect(factory) { const dispose = factory(); effects.push(dispose); return dispose; },
  skills: { registerProvider(create) { provider = create({ signal: lifecycle.signal, invalidate() { invalidations++; } }); } },
  tools: { view() { return { visible: tools }; }, register(tool) { tools.set(tool.name, tool); return () => tools.delete(tool.name); } },
  async plugin(plugin, config) { await plugin.apply(ctx, plugin.Config(config)); },
};
ctx.root = ctx;
try {
  const plugin = await import(pathToFileURL(join(target, 'index.mjs')).href);
  await plugin.apply(ctx);
  const catalog = await provider.list();
  assert.equal(catalog.length, 5);
  assert.equal(catalog.find(s => s.name === 'fmage-direct').invocation.modelInvocable, false);
  for (const candidate of catalog) {
    const skill = await provider.get(candidate, { signal: lifecycle.signal });
    assert(skill.content.includes('DeepSeek Harness 桌面版适配'));
    assert(!skill.content.includes('$fmage'));
  }
  assert.equal(tools.size, 10);
  assert(tools.has('mcp__Fmage__generate_image'));
  assert(tools.has('mcp__Fmage__regress_image'));
  console.log(JSON.stringify({ nativeDesktopBridge: true, skillCount: catalog.length, tools: [...tools.keys()] }));
} finally {
  lifecycle.abort();
  for (const dispose of effects.reverse()) await dispose?.();
  try { unlinkSync(join(target, '.fmage-runtime.json')); } catch (e) { if (e.code !== 'ENOENT') throw e; }
  hooks.deregister();
}
