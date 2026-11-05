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
  const pills = [];
  const injected = [];
  const effects = [];
  const tabTypes = [];
  const tabBodies = [];
  const injects = [];
  const ctx = {
    effect: fn => effects.push(fn()),
    inject: (names, cb) => { injects.push(names); },
    get: () => undefined,
    locale: { register: (ns, dicts) => { dictionaries = { ns, dicts }; return () => {}; }, bind: () => key => dictionaries.dicts.zh[key] ?? key },
    slots: {
      inject: (name, cb) => { injected.push(name); cb(); },
      register: (options, c) => {
        if (options.name === 'settings.section') { registration = options; component = c; }
        else if (options.name === 'sidebar.right.pane.tab') tabBodies.push({ options, component: c });
        else pills.push({ options, component: c });
      },
    },
    // The sidebar services resolve through ctx.inject; simulate the owner side.
    injectOwner: null,
  };
  plugin.apply(ctx);
  assert.deepEqual(injected, ['settings.section', 'conversation.input.right', 'conversation.input.right']);
  assert.equal(JSON.stringify(injects), JSON.stringify([['sidebarRightTabs', 'sidebarRight']]));
  assert.equal(registration.id, 'fmage');
  assert.equal(registration.locale, 'settings.fmage');
  assert.equal(registration.label(), 'Fmage 生图');
  assert.deepEqual(pills.map(entry => entry.options.id), ['fmage-provider', 'fmage-annotate']);
  assert.equal(pills[0].options.label(), '生图');
  assert.equal(pills[1].options.label(), '标注');
  assert.equal(typeof pills[0].component, 'function');
  assert.equal(typeof pills[1].component, 'function');
  assert.equal(tabBodies.length, 0, 'tab body only registers once the sidebar services resolve');
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

test('the sidebar tab body only appears when the right sidebar services resolve', { skip: !available && 'DSH checkout not found' }, () => {
  const requireReact = createRequire(join(reactDom, 'package.json'));
  const { plugin } = loadClient(id => requireReact(id));
  let dictionaries;
  let registration;
  let tabBody;
  let opened = null;
  const calls = [];
  const owner = {
    get: name => (name === 'sidebarRightTabs'
      ? { register: definition => { registration = definition; return () => {}; } }
      : { openTab: kind => { opened = kind; return undefined; } }),
    effect: fn => { calls.push('effect'); fn(); },
    slots: {
      inject: (name, cb) => { calls.push(name); cb(); },
      register: (options, component) => { if (options.name === 'sidebar.right.pane.tab') tabBody = { options, component }; return () => {}; },
    },
  };
  let annotate;
  const ctx = {
    effect: fn => { fn(); },
    inject: (names, cb) => { if (names.includes('sidebarRightTabs')) cb(owner); },
    locale: { register: (ns, dicts) => { dictionaries = dicts; return () => {}; }, bind: () => key => dictionaries.zh[key] ?? key },
    slots: {
      inject: (name, cb) => {
        if (name === 'settings.section' || name === 'conversation.input.right') cb();
      },
      register: (options, component) => {
        if (options.id === 'fmage-annotate') annotate = component;
        return () => {};
      },
    },
  };
  plugin.apply(ctx);
  assert.equal(registration.id, 'dsh-fmage/annotation');
  assert.equal(registration.kind, 'fmage-annotation');
  assert.equal(registration.priority, 'extension');
  assert.equal(registration.guide, undefined, 'no guide entry: the sidebar default page is left alone');
  assert.ok(tabBody);
  assert.equal(tabBody.options.key, 'dsh-fmage/annotation');
  assert.equal(typeof tabBody.component, 'function');

  // The composer entry must open the registered kind, not a guessed one.
  assert.equal(typeof annotate, 'function');
  const entry = annotate();
  entry.props.onClick();
  assert.equal(opened, 'fmage-annotation');

  const { renderToString } = requireReact('react-dom/server');
  const html = renderToString(requireReact('react').createElement(tabBody.component, {
    inputActions: { insertText: () => false, captureInsertion: () => ({}) },
  }));
  assert.match(html, /标注工作台/);
  assert.match(html, /从输入框选图/);
  assert.match(html, /标记层/);
});
