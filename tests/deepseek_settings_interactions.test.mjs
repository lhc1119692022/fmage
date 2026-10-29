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
    retry_on_failure: false, retry_count: 2,
  };
  let modelRequests = 0;
  let saved = null;
  const calls = [];
  const response = body => ({ ok: true, status: 200, json: async () => body });
  const fetchStub = async (url, init) => {
    calls.push(String(url));
    if (url === 'api/fmage.config') {
      if (init && init.method === 'PUT') {
        saved = JSON.parse(init.body);
        return response({ ...view, retry_on_failure: true, retry_count: 4 });
      }
      return response(view);
    }
    if (url === 'api/fmage.models') {
      modelRequests += 1;
      const body = JSON.parse(init.body);
      assert.equal(body.provider, 'Second');
      assert.equal(body.transport, 'openai-images');
      assert.equal(body.base_url, 'https://second.example/v1');
      assert.equal(body.api_key, 'sk-second');
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
    Switch: ({ checked, label, onChange }) => React.createElement('button', {
      role: 'switch', 'aria-checked': checked, 'aria-label': label,
      onClick: () => onChange?.(!checked),
    }),
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
  const setValue = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
  await act(async () => { root.render(React.createElement(component, { t, close() {} })); });
  await act(async () => {});

  const cards = () => [...container.querySelectorAll('li.fmg-card')];
  assert.deepEqual(cards().map(card => card.querySelector('.fmg-name').textContent), ['First', 'Second', 'Third']);

  // Fixed frame: title/tabs and the action bar are siblings of the scroll
  // panel, so scrolling the list cannot carry them away.
  const section = container.querySelector('section.fmg');
  const top = section.querySelector('.fmg-top');
  const panel = section.querySelector('.fmg-panel');
  const bar = section.querySelector('.fmg-bar');
  assert.ok(top && panel && bar, 'section keeps a top block, a scroll panel, and an action bar');
  assert.equal(top.nextElementSibling, panel, 'the scroll panel follows the top block');
  assert.equal(panel.nextElementSibling, bar, 'the action bar sits outside the scroll panel');
  assert.equal(panel.getAttribute('role'), 'tabpanel');
  assert.equal(top.querySelectorAll('button[role="tab"]').length, 4);
  assert.equal(bar.parentElement, section);
  assert.ok(panel.contains(cards()[0]), 'cards live inside the scroll panel');

  const pin = cards()[1].querySelector('button[title="置顶"]');
  assert.ok(pin, 'second provider has a pin button');
  assert.equal(pin.disabled, false);
  await act(async () => { pin.dispatchEvent(new window.MouseEvent('click', { bubbles: true })); });
  assert.deepEqual(cards().map(card => card.querySelector('.fmg-name').textContent), ['Second', 'First', 'Third']);
  assert.equal(cards()[0].querySelector('button[title="已置顶"]').disabled, true);

  const toggle = cards()[0].querySelector('button.fmg-toggle');
  await act(async () => { toggle.dispatchEvent(new window.MouseEvent('click', { bubbles: true })); });
  const second = cards()[0];
  const labelOf = text => [...second.querySelectorAll('label')].find(label => label.textContent === text);
  // Credentials come before the model: the fetch needs an address and a key.
  assert.deepEqual([...second.querySelectorAll('label')].map(label => label.textContent),
    ['名称', 'Transport', 'Base URL', 'API Key', 'Model']);
  const modelLabel = labelOf('Model');
  assert.ok(modelLabel, 'model field is visible after expanding');
  const modelInput = window.document.getElementById(modelLabel.htmlFor);
  assert.equal(modelInput.value, 'gpt-image-2.5');

  const fetchButton = second.querySelector('button[title="拉取上游模型"]');
  assert.ok(fetchButton, 'model field has a fetch button');
  // Nothing typed and nothing stored: refuse locally instead of calling upstream.
  await act(async () => { fetchButton.dispatchEvent(new window.MouseEvent('click', { bubbles: true })); });
  await act(async () => {});
  assert.equal(modelRequests, 0, 'a provider without a key never reaches the fetch endpoint');
  assert.match(second.querySelector('.fmg-error').textContent, /Base URL 和 API Key/);

  const keyInput = window.document.getElementById(labelOf('API Key').htmlFor);
  await act(async () => {
    setValue.call(keyInput, 'sk-second');
    keyInput.dispatchEvent(new window.Event('input', { bubbles: true }));
  });
  await act(async () => {
    second.querySelector('button[title="拉取上游模型"]').dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
  });
  await act(async () => {});
  const choices = [...second.querySelectorAll('button.fmg-model')].map(button => button.textContent);
  assert.deepEqual(choices, ['gpt-image-2.5', 'gpt-image-2.5-sunburst']);
  assert.equal(second.querySelector('button.fmg-model[aria-pressed="true"]').textContent, 'gpt-image-2.5');

  const sunburst = [...second.querySelectorAll('button.fmg-model')].find(button => button.textContent === 'gpt-image-2.5-sunburst');
  await act(async () => { sunburst.dispatchEvent(new window.MouseEvent('click', { bubbles: true })); });
  assert.equal(modelInput.value, 'gpt-image-2.5-sunburst');
  assert.equal(modelRequests, 1);
  assert.ok(calls.includes('api/fmage.models'));

  const retryTab = [...container.querySelectorAll('button[role="tab"]')].find(button => button.textContent === '重试');
  assert.ok(retryTab, 'retry tab is rendered');
  await act(async () => { retryTab.dispatchEvent(new window.MouseEvent('click', { bubbles: true })); });
  const retrySwitch = container.querySelector('button[role="switch"][aria-label="失败后自动重试"]');
  assert.ok(retrySwitch, 'retry switch is rendered');
  assert.equal(retrySwitch.getAttribute('aria-checked'), 'false');
  await act(async () => { retrySwitch.dispatchEvent(new window.MouseEvent('click', { bubbles: true })); });
  assert.equal(container.querySelector('button[role="switch"]').getAttribute('aria-checked'), 'true');

  const countLabel = [...container.querySelectorAll('label')].find(label => label.textContent === '重试次数');
  assert.ok(countLabel, 'retry count field is visible');
  const countInput = window.document.getElementById(countLabel.htmlFor);
  assert.equal(countInput.value, '2');
  assert.equal(countInput.max, '10');
  await act(async () => {
    setValue.call(countInput, '4');
    countInput.dispatchEvent(new window.Event('input', { bubbles: true }));
  });
  assert.equal(countInput.value, '4');

  const saveButton = [...container.querySelectorAll('button')].find(button => button.textContent === '保存');
  assert.ok(saveButton, 'save button is rendered');
  assert.equal(saveButton.disabled, false);
  await act(async () => { saveButton.dispatchEvent(new window.MouseEvent('click', { bubbles: true })); });
  await act(async () => {});
  assert.equal(saved.changes.retry_on_failure, true);
  assert.equal(saved.changes.retry_count, 4);
  assert.equal(saved.changes.output_dir, '');

  const accepted = saved;
  const countAfterSave = window.document.getElementById('fmg-retry-count');
  await act(async () => {
    setValue.call(countAfterSave, '11');
    countAfterSave.dispatchEvent(new window.Event('input', { bubbles: true }));
  });
  const saveAgain = [...container.querySelectorAll('button')].find(button => button.textContent === '保存');
  await act(async () => { saveAgain.dispatchEvent(new window.MouseEvent('click', { bubbles: true })); });
  assert.equal(saved, accepted, 'an out-of-range retry count is rejected before the request');
  assert.match(container.querySelector('.fmg-error').textContent, /0 到 10/);

  await act(async () => { root.unmount(); });
  dom.window.close();
  delete globalThis.IS_REACT_ACT_ENVIRONMENT;
  for (const name of ['window', 'document', 'HTMLElement', 'Node', 'Event', 'MouseEvent']) {
    delete globalThis[name];
  }
});
