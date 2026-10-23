// Loads the browser half through the published __ModuleLoader__ protocol with the
// DSH checkout's React; skipped when no checkout is available.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, existsSync } from 'node:fs';
import { createRequire } from 'node:module';
import { join } from 'node:path';
import vm from 'node:vm';

const DSH = process.env.FMAGE_DSH_SOURCE || 'D:/deepseek-harness';
const reactDom = join(DSH, 'node_modules/.pnpm/react-dom@18.3.1_react@18.3.1/node_modules/react-dom');
const available = existsSync(reactDom);

function loadClient(requireFrom) {
  let registered;
  const source = readFileSync(new URL('../adapters/deepseek/client.js', import.meta.url), 'utf8');
  const style = { dataset: {}, remove() {} };
  const document = { querySelector: () => null, createElement: () => style, head: { append() {} } };
  vm.runInNewContext(source, { window: { __ModuleLoader__: { load: entry => { registered = entry; } } }, document });
  assert.equal(registered.id, 'dsh-fmage');
  const primitives = {
    Button: ({ children, ...props }) => requireFrom('react').createElement('button', props, children),
    Switch: ({ checked, label }) => requireFrom('react').createElement('button', { role: 'switch', 'aria-checked': checked, 'aria-label': label }),
  };
  return { plugin: registered.factory(id => id === '@deepseek-ai/dsh-client-ui-primitives' ? primitives : requireFrom(id)), style };
}

test('client registers the Fmage settings section and renders its loading state', { skip: !available && 'DSH checkout not found' }, () => {
  const requireReact = createRequire(join(reactDom, 'package.json'));
  const { plugin, style } = loadClient(id => requireReact(id));
  assert.deepEqual([...plugin.inject], ['slots', 'locale']);
  let dictionaries; let registration; let component;
  const effects = [];
  const ctx = {
    effect: fn => effects.push(fn()),
    locale: { register: (ns, dicts) => { dictionaries = { ns, dicts }; return () => {}; }, bind: () => key => dictionaries.dicts.zh[key] ?? key },
    slots: { inject: (name, cb) => { assert.equal(name, 'settings.section'); cb(); }, register: (options, c) => { registration = options; component = c; } },
  };
  plugin.apply(ctx);
  assert.equal(registration.id, 'fmage');
  assert.equal(registration.locale, 'settings.fmage');
  assert.equal(registration.label(), 'Fmage 生图');
  assert.deepEqual([...Object.keys(dictionaries.dicts.zh)].sort(), [...Object.keys(dictionaries.dicts.en)].sort());
  // Fixed frame: the section fills the host's options column, the title/tabs
  // and action bar stay out of the scroll region, and only the panel scrolls.
  assert.match(style.textContent, /\.fmg\{[^}]*height:100%/);
  assert.match(style.textContent, /\.fmg-top\{[^}]*flex:none/);
  assert.match(style.textContent, /\.fmg-panel\{[^}]*flex:1 1 0[^}]*overflow-y:auto/);
  assert.match(style.textContent, /\.fmg-bar\{[^}]*flex:none/);
  const { renderToString } = requireReact('react-dom/server');
  const html = renderToString(requireReact('react').createElement(component, { t: key => dictionaries.dicts.zh[key], close() {} }));
  assert.match(html, /Fmage 生图/);
  assert.match(html, /读取中/);
});
