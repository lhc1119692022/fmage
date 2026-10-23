// Renders the real settings section in jsdom so pinning and model fetching are
// exercised as user actions, not just as source text.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, existsSync } from 'node:fs';
import { createRequire } from 'node:module';
import { join } from 'node:path';
import vm from 'node:vm';

const DSH = process.env.FMAGE_DSH_SOURCE || 'D:/deepseek-harness';
const reactDom = join(DSH, 'node_modules/.pnpm/react-dom@18.3.1_react@18.3.1/node_modules/react-dom');
const jsdomPath = join(DSH, 'node_modules/jsdom');
const available = existsSync(reactDom) && existsSync(jsdomPath);

test('settings page pins providers and lists fetched models', { skip: !available && 'DSH checkout not found' }, async () => {
  const requireReact = createRequire(join(reactDom, 'package.json'));
  const requireJsdom = createRequire(join(jsdomPath, 'package.json'));
  const { JSDOM } = requireJsdom('jsdom');
  const dom = new JSDOM('<!doctype html><div id="root"></div>', { url: 'http://127.0.0.1/' });
  const { window } = dom;
  for (const name of ['window', 'document', 'HTMLElement', 'Node', 'Event', 'MouseEvent']) {
    globalThis[name] = name === 'window' ? window : name === 'document' ? window.document : window[name];
  }
  const React = requireReact('react');
  const { createRoot } = requireReact('react-dom/client');
  const { act } = React;
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;

  const view = {
    path: 'C:/Users/test/providers.json', exists: true, revision: 'r1',
    transports: ['openai-images', 'gemini-generate-content', 'midjourney'],
    runninghubOrigins: ['https://www.runninghub.cn'], instanceTypes: ['default'],
    providers: [
      { name: 'First', transport: 'openai-images', base_url: 'https://first.example/v1', model: 'gpt-image-2', enabled: true, api_key_set: true, api_key_masked: 'sk-****1234', api_key_env: '' },
      { name: 'Second', transport: 'openai-images', base_url: 'https://second.example/v1', model: 'gpt-image-2.5', enabled: true, api_key_set: false, api_key_masked: '', api_key_env: '' },
      { name: 'Third', transport: 'midjourney', base_url: 'https://third.example', model: 'mj-v8.2', enabled: false, api_key_set: false, api_key_masked: '', api_key_env: '' },
    ],
    workflow_connections: [], active_workflow: null, workflows: [],
    output_dir: '', cache_dir: '',
  };
  let modelRequests = 0;
  const calls = [];
  const response = body => ({ ok: true, status: 200, json: async () => body });
  const fetchStub = async (url, init) => {
    calls.push(String(url));
    if (url === 'api/fmage.config') return response(view);
    if (url === 'api/fmage.models') {
      modelRequests += 1;
      const body = JSON.parse(init.body);
      assert.equal(body.provider, 'Second');
      assert.equal(body.transport, 'openai-images');
      assert.equal(body.base_url, 'https://second.example/v1');
      return response({ models: ['gpt-image-2.5', 'gpt-image-2.5-sunburst'] });
    }
    throw new Error(`unexpected request ${url}`);
  };

  let registered;
  const source = readFileSync(new URL('../adapters/deepseek/client.js', import.meta.url), 'utf8');
  window.__ModuleLoader__ = { load: entry => { registered = entry; } };
  vm.runInNewContext(source, { window, document: window.document, fetch: fetchStub });
  const primitives = {
    Button: ({ children, ...props }) => React.createElement('button', props, children),
    Switch: ({ checked, label }) => React.createElement('button', { role: 'switch', 'aria-checked': checked, 'aria-label': label }),
  };
  const plugin = registered.factory(id => id === '@deepseek-ai/dsh-client-ui-primitives' ? primitives : requireReact(id));
  let dictionaries; let registration; let component;
  const ctx = {
    effect: fn => fn(),
    locale: {
      register: (ns, dicts) => { dictionaries = { ns, dicts }; return () => {}; },
      bind: () => key => dictionaries.dicts.zh[key] ?? key,
    },
    slots: { inject: (name, cb) => cb(), register: (options, c) => { registration = options; component = c; } },
  };
  plugin.apply(ctx);
  assert.equal(registration.id, 'fmage');

  const container = window.document.getElementById('root');
  const root = createRoot(container);
  const t = key => dictionaries.dicts.zh[key] ?? key;
  await act(async () => { root.render(React.createElement(component, { t, close() {} })); });
  await act(async () => {});

  const cards = () => [...container.querySelectorAll('li.fmg-card')];
  assert.deepEqual(cards().map(card => card.querySelector('.fmg-name').textContent), ['First', 'Second', 'Third']);

  const pin = cards()[1].querySelector('button[title="置顶"]');
  assert.ok(pin, 'second provider has a pin button');
  assert.equal(pin.disabled, false);
  await act(async () => { pin.dispatchEvent(new window.MouseEvent('click', { bubbles: true })); });
  assert.deepEqual(cards().map(card => card.querySelector('.fmg-name').textContent), ['Second', 'First', 'Third']);
  assert.equal(cards()[0].querySelector('button[title="已置顶"]').disabled, true);

  const toggle = cards()[0].querySelector('button.fmg-toggle');
  await act(async () => { toggle.dispatchEvent(new window.MouseEvent('click', { bubbles: true })); });
  const second = cards()[0];
  const modelLabel = [...second.querySelectorAll('label')].find(label => label.textContent === 'Model');
  assert.ok(modelLabel, 'model field is visible after expanding');
  const modelInput = window.document.getElementById(modelLabel.htmlFor);
  assert.equal(modelInput.value, 'gpt-image-2.5');

  const fetchButton = second.querySelector('button[title="拉取上游模型"]');
  assert.ok(fetchButton, 'model field has a fetch button');
  await act(async () => { fetchButton.dispatchEvent(new window.MouseEvent('click', { bubbles: true })); });
  await act(async () => {});
  const choices = [...second.querySelectorAll('button.fmg-model')].map(button => button.textContent);
  assert.deepEqual(choices, ['gpt-image-2.5', 'gpt-image-2.5-sunburst']);
  assert.equal(second.querySelector('button.fmg-model[aria-pressed="true"]').textContent, 'gpt-image-2.5');

  const sunburst = [...second.querySelectorAll('button.fmg-model')].find(button => button.textContent === 'gpt-image-2.5-sunburst');
  await act(async () => { sunburst.dispatchEvent(new window.MouseEvent('click', { bubbles: true })); });
  assert.equal(modelInput.value, 'gpt-image-2.5-sunburst');
  assert.equal(modelRequests, 1);
  assert.ok(calls.includes('api/fmage.models'));

  await act(async () => { root.unmount(); });
  dom.window.close();
  delete globalThis.IS_REACT_ACT_ENVIRONMENT;
  for (const name of ['window', 'document', 'HTMLElement', 'Node', 'Event', 'MouseEvent']) {
    delete globalThis[name];
  }
});
