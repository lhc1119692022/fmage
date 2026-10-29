/** Fmage settings page for the DeepSeek Desktop settings panel (browser half). */
window.__ModuleLoader__.load({
  id: 'dsh-fmage',
  factory(require) {
    const React = require('react');
    const { Button, Switch } = require('@deepseek-ai/dsh-client-ui-primitives');
    const h = React.createElement;
    const { useEffect, useState, useCallback } = React;
    const NS = 'settings.fmage';
    const ROUTE = 'api/fmage.config';
    const REVEAL = 'api/fmage.config.reveal';
    const MODELS = 'api/fmage.models';
    // Mirrors adapters/deepseek/config-api.mjs; the Host rejects anything else.
    const DEFAULT_RETRY_COUNT = 2;
    const MAX_RETRY_COUNT = 10;
    const CSS_ID = 'dsh-fmage/settings.css';
    const CSS = `
/* The settings shell hands this section a fixed-height, already-scrolling
   options column. Filling that height lets the title/tabs row and the action
   bar stay put while only the tab panel scrolls inside. */
.fmg{max-width:760px;display:flex;flex-direction:column;gap:14px;height:100%;color:var(--dsw-alias-label-primary);font-size:13px}
.fmg-top{flex:none;display:flex;flex-direction:column;gap:14px;min-width:0}
.fmg h3{margin:0;font-size:18px;font-weight:600}
.fmg-intro{margin:0;color:var(--dsw-alias-label-tertiary)}
.fmg-path{font-family:var(--dsw-font-mono,monospace);word-break:break-all}
.fmg-tabs{display:flex;gap:22px;border-bottom:.5px solid var(--dsw-alias-border-l2)}
.fmg-tab{background:0 0;border:0;padding:7px 1px 9px;font:inherit;color:var(--dsw-alias-label-tertiary);cursor:pointer;position:relative}
.fmg-tab[aria-selected=true]{color:var(--dsw-alias-label-primary)}
.fmg-tab[aria-selected=true]:after{content:"";position:absolute;left:0;right:0;bottom:-1px;height:2px;border-radius:2px 2px 0 0;background:var(--dsw-alias-label-primary)}
.fmg-list{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:10px}
.fmg-card{border:.5px solid var(--dsw-alias-border-l2);border-radius:14px;padding:10px 12px;display:flex;flex-direction:column;gap:10px}
.fmg-card[data-off=true] .fmg-fields{opacity:.6}
.fmg-card[data-dragging=true]{opacity:.5}
.fmg-card{position:relative}
.fmg-card[data-drop]::before{content:"";position:absolute;left:4px;right:4px;height:2px;border-radius:2px;background:var(--dsw-alias-state-business-primary);pointer-events:none}
.fmg-card[data-drop=before]::before{top:-6px}
.fmg-card[data-drop=after]::before{bottom:-6px}
.fmg-head{display:flex;align-items:center;gap:8px;min-height:32px}
.fmg-toggle{flex:1;min-width:0;display:flex;align-items:baseline;gap:8px;background:0 0;border:0;padding:0;font:inherit;color:inherit;cursor:pointer;text-align:left}
.fmg-toggle:focus-visible{outline:var(--dsw-focus-ring-width,2px) solid var(--dsw-focus-ring-color,var(--dsw-alias-state-business-primary));outline-offset:2px;border-radius:6px}
.fmg-handle{cursor:grab;color:var(--dsw-alias-label-tertiary);display:inline-flex;align-items:center;flex:none;padding:2px;border-radius:6px}
.fmg-handle:active{cursor:grabbing}
.fmg-name{min-width:0;font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.fmg-sub{flex:none;color:var(--dsw-alias-label-tertiary);font-size:12px;font-weight:400;max-width:46%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.fmg-actions{display:flex;justify-content:flex-start}
.fmg-fields{display:grid;grid-template-columns:1fr 1fr;gap:10px 12px;align-items:start}
.fmg-field{display:flex;flex-direction:column;gap:4px;min-width:0}
.fmg-field>label{color:var(--dsw-alias-label-secondary);font-size:12px}
.fmg-wide{grid-column:1/-1}
.fmg input,.fmg select,.fmg textarea{box-sizing:border-box;width:100%;font:inherit;color:inherit;background:var(--dsw-alias-bg-layer-1);border:.5px solid var(--dsw-alias-border-l4);border-radius:8px;padding:6px 8px}
.fmg textarea{min-height:90px;font-family:var(--dsw-font-mono,monospace);resize:vertical}
.fmg input[readonly]{color:var(--dsw-alias-label-tertiary)}
.fmg-key{display:flex;gap:6px;align-items:center}
.fmg-key>input{flex:1;min-width:0}
.fmg-models{display:flex;flex-wrap:wrap;gap:6px;max-height:150px;overflow:auto;padding:2px 0}
.fmg-model{font:inherit;font-size:12px;padding:3px 9px;border-radius:999px;border:.5px solid var(--dsw-alias-border-l2);background:var(--dsw-alias-bg-layer-1);color:var(--dsw-alias-label-secondary);cursor:pointer;max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.fmg-model[aria-pressed=true]{border-color:var(--dsw-alias-state-business-primary);color:var(--dsw-alias-label-primary)}
.fmg-model:focus-visible{outline:var(--dsw-focus-ring-width,2px) solid var(--dsw-focus-ring-color,var(--dsw-alias-state-business-primary));outline-offset:2px}
.fmg-hint{margin:0;font-size:12px;color:var(--dsw-alias-label-tertiary)}
.fmg-icon{background:0 0;border:.5px solid var(--dsw-alias-border-l2);border-radius:8px;width:30px;height:30px;display:inline-flex;align-items:center;justify-content:center;cursor:pointer;color:var(--dsw-alias-label-secondary);flex:none}
.fmg-icon:disabled{opacity:.35;cursor:default}
.fmg-icon:focus-visible,.fmg-tab:focus-visible{outline:var(--dsw-focus-ring-width,2px) solid var(--dsw-focus-ring-color,var(--dsw-alias-state-business-primary));outline-offset:2px}
.fmg-bar{flex:none;display:flex;flex-wrap:wrap;gap:8px;align-items:center;padding:10px 0 0;margin-top:6px;background:var(--dsw-alias-bg-layer-2);border-top:.5px solid var(--dsw-alias-border-l4)}
/* The only scroll region: flex-basis 0 keeps the shell at bay regardless of
   how tall the tab content grows; the reserved gutter stops the cards from
   shifting when a scrollbar appears. */
.fmg-panel{flex:1 1 0;min-height:0;overflow-y:auto;overscroll-behavior:contain;scrollbar-gutter:stable;display:flex;flex-direction:column;gap:10px;padding-bottom:8px}
.fmg-setting{display:flex;align-items:center;gap:14px;border:.5px solid var(--dsw-alias-border-l2);border-radius:12px;padding:10px 12px}
.fmg-setting-main{flex:1;min-width:0;display:flex;flex-direction:column;gap:2px}
.fmg-setting-title{font-weight:600}
.fmg-setting .fmg-hint{margin:0}
.fmg-count{max-width:120px}
.fmg-grow{flex:1}
.fmg-error{color:var(--dsw-alias-state-danger-primary,#d33)}
.fmg-ok{color:var(--dsw-alias-label-tertiary)}
.fmg-empty{margin:0;color:var(--dsw-alias-label-tertiary)}
`;
    const zh = {
      nav: 'Fmage 生图', title: 'Fmage 生图',
      tabProviders: '提供商', tabWorkflows: '工作流', tabPaths: '目录', tabRetry: '重试',
      add: '添加提供商', addWorkflow: '添加工作流',
      name: '名称', transport: 'Transport', baseUrl: 'Base URL', model: 'Model', apiKey: 'API Key',
      enable: '启用 {name}', remove: '移除', show: '显示密钥', hide: '隐藏密钥',
      pin: '置顶', pinned: '已置顶',
      fetchModels: '拉取上游模型', fetchingModels: '拉取中…', noModels: '上游未返回模型。',
      needCredentials: '先填写 Base URL 和 API Key，再拉取模型。',
      modelsHint: '点击模型即可切换；输入框仍可手动编辑。',
      drag: '拖动排序', expand: '展开', collapse: '折叠',
      keyEmpty: '未设置', keyEnv: '使用环境变量 {env}', default: '默认', setDefault: '设为默认',
      connections: 'RunningHub 连接', workflows: '工作流', connection: '连接', workflowId: '工作流 ID', workflowIdHint: '公共或私有工作流 ID',
      outputs: '输出节点', outputsHint: '多个节点 ID 用逗号分隔', instance: '实例类型', inputs: '输入映射 (JSON)',
      outputDir: '输出目录', cacheDir: '缓存目录', pathHint: '留空使用 Fmage 默认位置。',
      retryEnabled: '失败后自动重试', retryEnabledHint: '每次重试都发起一次全新的上游请求，不会复用已失败的结果。',
      retryHint: '对所有提供商生效：仅对限流、并发不足、网关 5xx、超时和网络中断等临时失败重试；鉴权或参数错误会立即返回，已提交到远端的任务不会重复提交。',
      retryCount: '重试次数', retryCountHint: '额外尝试次数，0–10，默认 2；0 表示不额外尝试，开关关闭时该值不生效。',
      retryInvalid: '重试次数必须是 0 到 10 之间的整数。',
      save: '保存', saving: '保存中…', reset: '放弃修改', reload: '重新读取', loading: '读取中…', saved: '已保存，新请求即生效。',
      dirty: '有未保存的修改', none: '暂无条目', noFile: '配置文件尚不存在，保存后自动创建。',
      invalidJson: '{name} 的输入映射不是有效 JSON', confirmRemove: '移除“{name}”？保存后生效。',
    };
    const en = {
      nav: 'Fmage images', title: 'Fmage images',
      tabProviders: 'Providers', tabWorkflows: 'Workflows', tabPaths: 'Folders', tabRetry: 'Retry',
      add: 'Add provider', addWorkflow: 'Add workflow',
      name: 'Name', transport: 'Transport', baseUrl: 'Base URL', model: 'Model', apiKey: 'API key',
      enable: 'Enable {name}', remove: 'Remove', show: 'Show key', hide: 'Hide key',
      pin: 'Move to top', pinned: 'Already first',
      fetchModels: 'Fetch upstream models', fetchingModels: 'Fetching…', noModels: 'The provider returned no models.',
      needCredentials: 'Fill in the Base URL and API key before fetching models.',
      modelsHint: 'Click a model to use it; the field stays editable.',
      drag: 'Drag to reorder', expand: 'Expand', collapse: 'Collapse',
      keyEmpty: 'Not set', keyEnv: 'Uses environment variable {env}', default: 'Default', setDefault: 'Make default',
      connections: 'RunningHub connections', workflows: 'Workflows', connection: 'Connection', workflowId: 'Workflow ID', workflowIdHint: 'Public or private workflow ID',
      outputs: 'Output nodes', outputsHint: 'Separate node IDs with commas', instance: 'Instance type', inputs: 'Input mapping (JSON)',
      outputDir: 'Output folder', cacheDir: 'Cache folder', pathHint: 'Leave empty to use the Fmage default.',
      retryEnabled: 'Retry failed generations', retryEnabledHint: 'Each retry sends a fresh upstream request and never reuses the failed result.',
      retryHint: 'Applies to every provider: only transient failures are retried — rate limits, concurrency limits, gateway 5xx, timeouts, and network errors. Auth and validation errors return immediately, and a task already submitted upstream is never resubmitted.',
      retryCount: 'Retries', retryCountHint: 'Extra attempts, 0–10, default 2. 0 means no extra attempt; ignored while the switch is off.',
      retryInvalid: 'Retries must be a whole number from 0 to 10.',
      save: 'Save', saving: 'Saving…', reset: 'Discard changes', reload: 'Reload', loading: 'Loading…', saved: 'Saved. New requests use it.',
      dirty: 'Unsaved changes', none: 'No entries', noFile: 'The config file does not exist yet; saving creates it.',
      invalidJson: 'Input mapping for {name} is not valid JSON', confirmRemove: 'Delete "{name}"? Takes effect after saving.',
    };
    const icon = d => h('svg', { width: 16, height: 16, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 1.8, strokeLinecap: 'round', strokeLinejoin: 'round', 'aria-hidden': true }, h('path', { d }));
    const ICON = {
      eye: 'M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12zM12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6z',
      eyeOff: 'M3 3l18 18M10.6 5.1A10 10 0 0 1 12 5c6.5 0 10 7 10 7a17 17 0 0 1-3.2 4.1M6.6 6.6C3.8 8.4 2 12 2 12s3.5 7 10 7a9.6 9.6 0 0 0 5.4-1.6M9.9 9.9a3 3 0 0 0 4.2 4.2',
      grip: 'M9 6h6M9 12h6M9 18h6',
      pin: 'M9 3h6l-1 7 4 4v2H6v-2l4-4-1-7zM12 16v5',
      download: 'M12 3v12m0 0l-4-4m4 4l4-4M4 17v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2',
    };
    const IconButton = ({ label, path, onClick, disabled }) => h('button', {
      type: 'button', className: 'fmg-icon', 'aria-label': label, title: label, onClick, disabled,
    }, icon(path));

    async function request(url, init) {
      const response = await fetch(url, { cache: 'no-store', ...init, headers: { 'content-type': 'application/json', ...(init && init.headers) } });
      let data = null;
      try { data = await response.json(); } catch { /* non-JSON failure */ }
      if (!response.ok) throw new Error((data && data.error) || `HTTP ${response.status}`);
      return data;
    }

    let uid = 0;
    const key = () => `row-${++uid}`;
    // Editable draft rows; `from` remembers the stored name so renames keep unshown fields.
    const toDraft = view => {
      const workflows = view.workflows.map(w => ({ ...w, id: key(), from: w.name, outputs: w.output_node_ids.join(', '), inputsText: JSON.stringify(w.inputs, null, 2) }));
      return {
        providers: view.providers.map(p => ({ ...p, id: key(), from: p.name, api_key: undefined, revealed: null })),
        connections: view.workflow_connections.map(c => ({ ...c, id: key(), from: c.name, api_key: undefined, revealed: null })),
        workflows,
        // Default workflow tracked by row id, so renaming it keeps the marker.
        defaultWorkflow: (workflows.find(w => w.name === view.active_workflow) || {}).id || null,
        output_dir: view.output_dir, cache_dir: view.cache_dir,
        retry_on_failure: view.retry_on_failure === true,
        retry_count: String(view.retry_count ?? DEFAULT_RETRY_COUNT),
      };
    };
    /** Retry count stays a string while editing, so the field can be empty mid-edit. */
    function retryCountOf(value, t) {
      const raw = String(value).trim();
      const count = Number(raw);
      if (!raw || !Number.isInteger(count) || count < 0 || count > MAX_RETRY_COUNT) throw new Error(t('retryInvalid'));
      return count;
    }
    function toChanges(draft, t) {
      const active = draft.workflows.find(w => w.id === draft.defaultWorkflow);
      const secret = row => row.api_key === undefined ? {} : { api_key: row.api_key };
      return {
        providers: draft.providers.map(p => ({ name: p.name, from: p.from, enabled: p.enabled, transport: p.transport, base_url: p.base_url, model: p.model, ...secret(p) })),
        workflow_connections: draft.connections.map(c => ({ name: c.name, from: c.from, base_url: c.base_url, ...secret(c) })),
        workflows: draft.workflows.map(w => {
          let inputs;
          try { inputs = w.inputsText.trim() ? JSON.parse(w.inputsText) : {}; }
          catch { throw new Error(t('invalidJson', { name: w.name || '?' })); }
          return { name: w.name, from: w.from, connection: w.connection, workflow_id: w.workflow_id, description: w.description,
            instance_type: w.instance_type, output_node_ids: w.outputs.split(/[,\s]+/).filter(Boolean), inputs };
        }),
        active_workflow: active ? active.name : null, output_dir: draft.output_dir, cache_dir: draft.cache_dir,
        retry_on_failure: draft.retry_on_failure === true, retry_count: retryCountOf(draft.retry_count, t),
      };
    }
    /** Insertion slot in the original list for dropping before/after a target row. */
    const slotOf = (list, toId, pos) => {
      const to = list.findIndex(r => r.id === toId);
      return to < 0 ? -1 : pos === 'before' ? to : to + 1;
    };
    /** True when the slot leaves the dragged row where it already is. */
    const isNoop = (list, fromId, toId, pos) => {
      const from = list.findIndex(r => r.id === fromId);
      const slot = slotOf(list, toId, pos);
      return from < 0 || slot < 0 || slot === from || slot === from + 1;
    };
    /** Drag reorder: insert the source row at the slot before or after the target row. */
    const reorder = (list, fromId, toId, pos) => {
      if (isNoop(list, fromId, toId, pos)) return list;
      const from = list.findIndex(r => r.id === fromId);
      const slot = slotOf(list, toId, pos);
      const next = list.slice();
      const [item] = next.splice(from, 1);
      next.splice(slot > from ? slot - 1 : slot, 0, item);
      return next;
    };
    /** Upper or lower half of the hovered card decides the insertion side. */
    const dropSide = e => {
      const rect = e.currentTarget.getBoundingClientRect();
      return e.clientY < rect.top + rect.height / 2 ? 'before' : 'after';
    };
    /** API key input: masked placeholder by default, eye toggles the full value. */
    function SecretField({ t, row, kind, id, onChange, setError }) {
      const [visible, setVisible] = useState(false);
      const edited = row.api_key !== undefined;
      const toggle = async () => {
        if (visible) { setVisible(false); return; }
        if (!edited && row.from && row.api_key_set && row.revealed === null) {
          try {
            const { api_key } = await request(REVEAL, { method: 'POST', body: JSON.stringify({ kind, name: row.from }) });
            onChange({ revealed: api_key }, false);
          } catch (error) { setError(error.message); return; }
        }
        setVisible(true);
      };
      const value = edited ? row.api_key : visible && row.revealed !== null ? row.revealed : '';
      const placeholder = row.api_key_env ? t('keyEnv', { env: row.api_key_env })
        : row.api_key_set && !edited ? row.api_key_masked : t('keyEmpty');
      return h('div', { className: 'fmg-key' },
        h('input', {
          id, type: visible ? 'text' : 'password', value, placeholder, autoComplete: 'off', spellCheck: false,
          onChange: e => onChange({ api_key: e.target.value }),
        }),
        h(IconButton, { label: visible ? t('hide') : t('show'), path: visible ? ICON.eyeOff : ICON.eye, onClick: toggle }));
    }

    const Field = ({ id, label, wide, children }) => h('div', { className: wide ? 'fmg-field fmg-wide' : 'fmg-field' },
      h('label', { htmlFor: id }, label), children);
    const Select = ({ id, value, options, onChange }) => h('select', { id, value, onChange: e => onChange(e.target.value) },
      (options.includes(value) ? options : [value, ...options]).map(o => h('option', { key: o, value: o }, o || '—')));

    /** Drag handle: the only draggable node, so text inside the card stays selectable. */
    function CardHandle({ t, id, onDragStart, onDragEnd }) {
      return h('span', {
        className: 'fmg-handle', draggable: true, title: t('drag'), 'aria-hidden': true,
        onDragStart: e => {
          e.dataTransfer.effectAllowed = 'move';
          e.dataTransfer.setData('text/plain', id);
          onDragStart();
        },
        onDragEnd,
      }, icon(ICON.grip));
    }

    function ProviderCard({ t, row, transports, update, onRemove, onPin, isFirst, setError, open, onToggle, dragging, drop, onDragStart, onDragOver, onDrop, onDragEnd }) {
      const f = field => `fmg-${row.id}-${field}`;
      const remove = () => { if (window.confirm(t('confirmRemove', { name: row.name || '?' }))) onRemove(); };
      const IDLE_MODELS = { base: '', transport: '', loading: false, error: '', items: null };
      const [modelList, setModelList] = useState(IDLE_MODELS);
      // Fetching is an upstream call, so it needs the same two inputs the call
      // uses: an address and a key (typed now, stored, or from the environment).
      const keyReady = row.api_key === undefined
        ? Boolean(row.api_key_set || row.api_key_env)
        : Boolean(row.api_key.trim());
      const fetchModels = async () => {
        const base = row.base_url;
        const transport = row.transport;
        if (!String(base).trim() || !keyReady) {
          setModelList({ base, transport, loading: false, error: t('needCredentials'), items: null });
          return;
        }
        setModelList({ base, transport, loading: true, error: '', items: null });
        try {
          const data = await request(MODELS, { method: 'POST', body: JSON.stringify({
            provider: row.from || undefined, transport, base_url: base,
            ...(row.api_key === undefined ? {} : { api_key: row.api_key }),
          }) });
          setModelList({ base, transport, loading: false, error: '', items: Array.isArray(data?.models) ? data.models : [] });
        } catch (error) {
          setModelList({ base, transport, loading: false, error: error.message, items: null });
        }
      };
      // A fetched list belongs to the form values it was requested for.
      const currentModels = modelList.base === row.base_url && modelList.transport === row.transport ? modelList : null;
      const models = currentModels && currentModels.items && currentModels.items.length
        ? h('div', { className: 'fmg-models' }, currentModels.items.map(model => h('button', {
          key: model, type: 'button', className: 'fmg-model', 'aria-pressed': row.model === model,
          onClick: () => update({ model }),
        }, model)))
        : null;
      return h('li', {
        className: 'fmg-card',
        'data-off': String(!row.enabled), 'data-dragging': String(dragging), 'data-drop': drop || undefined,
        onDragOver: e => { e.preventDefault(); onDragOver(dropSide(e)); },
        onDrop: e => { e.preventDefault(); e.stopPropagation(); onDrop(); },
      },
        h('div', { className: 'fmg-head' },
          h(CardHandle, { t, id: row.id, onDragStart, onDragEnd }),
          // The title row is the disclosure control; no separate chevron.
          h('button', {
            type: 'button', className: 'fmg-toggle', onClick: onToggle, 'aria-expanded': open, title: t(open ? 'collapse' : 'expand'),
          },
            h('span', { className: 'fmg-name' }, row.name || '—'),
            open ? null : h('span', { className: 'fmg-sub' }, row.model)),
          h(IconButton, {
            label: t(isFirst ? 'pinned' : 'pin'), path: ICON.pin, onClick: onPin,
            disabled: !row.enabled || isFirst,
          }),
          h(Switch, { checked: row.enabled, label: t('enable', { name: row.name }), onChange: enabled => update({ enabled }) })),
        open ? h('div', { className: 'fmg-fields' },
          Field({ id: f('name'), label: t('name'), children: h('input', { id: f('name'), value: row.name, onChange: e => update({ name: e.target.value }) }) }),
          Field({ id: f('transport'), label: t('transport'), children: h(Select, { id: f('transport'), value: row.transport, options: transports, onChange: transport => update({ transport }) }) }),
          Field({ id: f('base'), label: t('baseUrl'), wide: true, children: h('input', { id: f('base'), value: row.base_url, spellCheck: false, onChange: e => update({ base_url: e.target.value }) }) }),
          // Credentials come before the model so the form follows the order the
          // upstream call needs: a URL and a key are what make fetching possible.
          Field({ id: f('key'), label: t('apiKey'), wide: true, children: h(SecretField, { t, row, kind: 'provider', id: f('key'), onChange: update, setError }) }),
          Field({ id: f('model'), label: t('model'), wide: true, children: h(React.Fragment, null,
            h('div', { className: 'fmg-key' },
              h('input', { id: f('model'), value: row.model, spellCheck: false, onChange: e => update({ model: e.target.value }) }),
              h(IconButton, { label: t('fetchModels'), path: ICON.download, onClick: fetchModels, disabled: currentModels?.loading })),
            currentModels?.loading ? h('p', { className: 'fmg-hint' }, t('fetchingModels')) : null,
            currentModels?.error ? h('p', { className: 'fmg-hint fmg-error', role: 'alert' }, currentModels.error) : null,
            currentModels?.items && !currentModels.items.length ? h('p', { className: 'fmg-hint' }, t('noModels')) : null,
            models,
            models ? h('p', { className: 'fmg-hint' }, t('modelsHint')) : null) })) : null,
        open ? h('div', { className: 'fmg-actions' },
          h(Button, { size: 'sm', variant: 'ghost', onClick: remove }, t('remove'))) : null);
    }
    function ConnectionCard({ t, row, update, setError }) {
      const f = field => `fmg-${row.id}-${field}`;
      return h('li', { className: 'fmg-card' },
        h('div', { className: 'fmg-fields' },
          Field({ id: f('key'), label: t('apiKey'), wide: true, children: h(SecretField, { t, row, kind: 'connection', id: f('key'), onChange: update, setError }) })));
    }

    function WorkflowCard({ t, row, connections, instances, isDefault, onDefault, update, onRemove, open, onToggle, dragging, drop, onDragStart, onDragOver, onDrop, onDragEnd }) {
      const f = field => `fmg-${row.id}-${field}`;
      const remove = () => { if (window.confirm(t('confirmRemove', { name: row.name || '?' }))) onRemove(); };
      return h('li', {
        className: 'fmg-card', 'data-dragging': String(dragging), 'data-drop': drop || undefined,
        onDragOver: e => { e.preventDefault(); onDragOver(dropSide(e)); },
        onDrop: e => { e.preventDefault(); e.stopPropagation(); onDrop(); },
      },
        h('div', { className: 'fmg-head' },
          h(CardHandle, { t, id: row.id, onDragStart, onDragEnd }),
          h('button', {
            type: 'button', className: 'fmg-toggle', onClick: onToggle, 'aria-expanded': open, title: t(open ? 'collapse' : 'expand'),
          },
            h('span', { className: 'fmg-name' }, row.name || '—', isDefault ? ` · ${t('default')}` : ''),
            open ? null : h('span', { className: 'fmg-sub' }, row.workflow_id)),
          isDefault ? null : h(Button, { size: 'sm', variant: 'outline', onClick: onDefault }, t('setDefault'))),
        open ? h('div', { className: 'fmg-fields' },
          Field({ id: f('name'), label: t('name'), children: h('input', { id: f('name'), value: row.name, onChange: e => update({ name: e.target.value }) }) }),
          connections.length > 1
            ? Field({ id: f('conn'), label: t('connection'), children: h(Select, { id: f('conn'), value: row.connection, options: connections, onChange: connection => update({ connection }) }) })
            : null,
          Field({ id: f('wid'), label: t('workflowId'), children: h('input', { id: f('wid'), value: row.workflow_id, inputMode: 'numeric', placeholder: t('workflowIdHint'), onChange: e => update({ workflow_id: e.target.value }) }) }),
          Field({ id: f('inst'), label: t('instance'), children: h(Select, { id: f('inst'), value: row.instance_type, options: instances, onChange: instance_type => update({ instance_type }) }) }),
          Field({ id: f('out'), label: t('outputs'), children: h('input', { id: f('out'), value: row.outputs, placeholder: t('outputsHint'), onChange: e => update({ outputs: e.target.value }) }) }),
          Field({ id: f('in'), label: t('inputs'), wide: true, children: h('textarea', { id: f('in'), value: row.inputsText, spellCheck: false, onChange: e => update({ inputsText: e.target.value }) }) }),
          h('div', { className: 'fmg-actions' }, h(Button, { size: 'sm', variant: 'ghost', onClick: remove }, t('remove')))) : null);
    }

    function useList(setDraft, setDirty, field) {
      return {
        update: (id, patch, dirty = true) => {
          setDraft(d => ({ ...d, [field]: d[field].map(r => r.id === id ? { ...r, ...patch } : r) }));
          if (dirty) setDirty(true);
        },
        moveTo: (fromId, toId, pos) => { setDraft(d => ({ ...d, [field]: reorder(d[field], fromId, toId, pos) })); setDirty(true); },
        toTop: id => {
          setDraft(d => {
            const from = d[field].findIndex(r => r.id === id);
            if (from <= 0) return d;
            const next = d[field].slice();
            const [row] = next.splice(from, 1);
            next.unshift(row);
            return { ...d, [field]: next };
          });
          setDirty(true);
        },
        remove: id => { setDraft(d => ({ ...d, [field]: d[field].filter(r => r.id !== id) })); setDirty(true); },
        add: row => { setDraft(d => ({ ...d, [field]: [...d[field], row] })); setDirty(true); },
      };
    }
    function FmageSection({ t }) {
      const [view, setView] = useState(null);
      const [draft, setDraft] = useState(null);
      const [dirty, setDirty] = useState(false);
      const [busy, setBusy] = useState(false);
      const [error, setError] = useState('');
      const [notice, setNotice] = useState('');
      const [tab, setTab] = useState('providers');
      // Collapsed by default; the summary row keeps the list scannable.
      const [open, setOpen] = useState(() => new Set());
      const IDLE = { list: null, from: null, over: null, pos: null };
      const [drag, setDrag] = useState(IDLE);
      // Commit the slot the insertion line currently shows; dropping in a gap uses the same slot.
      const commitDrop = (list, api) => {
        if (drag.list === list && drag.from && drag.over) api.moveTo(drag.from, drag.over, drag.pos);
        setDrag(IDLE);
      };
      const dragProps = (list, id, api, rows) => ({
        dragging: drag.list === list && drag.from === id,
        // Only draw a line where the drop would actually move the row.
        drop: drag.list === list && drag.over === id && drag.from && !isNoop(rows, drag.from, id, drag.pos) ? drag.pos : null,
        onDragStart: () => setDrag({ list, from: id, over: null, pos: null }),
        onDragOver: pos => setDrag(d => (d.list !== list ? d : d.over === id && d.pos === pos ? d : { ...d, over: id, pos })),
        onDrop: () => commitDrop(list, api),
        onDragEnd: () => setDrag(IDLE),
      });
      // The gaps between cards belong to the list, so a drop there is not lost.
      const listProps = (list, api) => ({
        onDragOver: e => { if (drag.list === list) e.preventDefault(); },
        onDrop: e => { e.preventDefault(); commitDrop(list, api); },
      });
      const toggleOpen = id => setOpen(prev => {
        const next = new Set(prev);
        if (next.has(id)) next.delete(id); else next.add(id);
        return next;
      });
      const load = useCallback(async () => {
        setBusy(true); setError(''); setNotice('');
        try { const next = await request(ROUTE); setView(next); setDraft(toDraft(next)); setDirty(false); }
        catch (e) { setError(e.message); }
        finally { setBusy(false); }
      }, []);
      useEffect(() => { void load(); }, [load]);
      const providers = useList(setDraft, setDirty, 'providers');
      const connections = useList(setDraft, setDirty, 'connections');
      const workflows = useList(setDraft, setDirty, 'workflows');
      if (draft === null) return h('section', { className: 'fmg' }, h('h3', null, t('title')),
        error ? h('p', { className: 'fmg-error', role: 'alert' }, error, ' ', h(Button, { size: 'sm', variant: 'outline', onClick: load }, t('reload')))
          : h('p', { className: 'fmg-intro' }, t('loading')));
      const save = async () => {
        setBusy(true); setError(''); setNotice('');
        try {
          const next = await request(ROUTE, { method: 'PUT', body: JSON.stringify({ revision: view.revision, changes: toChanges(draft, t) }) });
          setView(next); setDraft(toDraft(next)); setDirty(false); setNotice(t('saved'));
        } catch (e) { setError(e.message); }
        finally { setBusy(false); }
      };
      const set = patch => { setDraft(d => ({ ...d, ...patch })); setDirty(true); };
      const addProvider = () => {
        const row = {
          id: key(), from: null, name: '', enabled: true, transport: view.transports[0], base_url: '', model: '',
          api_key: '', api_key_set: false, api_key_masked: '', api_key_env: '', revealed: null,
        };
        providers.add(row);
        setOpen(prev => new Set(prev).add(row.id));
      };
      const addWorkflow = () => workflows.add({
        id: key(), from: null, name: '', connection: connectionNames[0] || '', workflow_id: '',
        description: '', instance_type: 'default', outputs: '', inputsText: '{}',
      });
      const tabs = [['providers', t('tabProviders')], ['workflows', t('tabWorkflows')], ['paths', t('tabPaths')], ['retry', t('tabRetry')]];
      const empty = list => list.length ? null : h('p', { className: 'fmg-empty' }, t('none'));
      const connectionNames = draft.connections.map(c => c.name).filter(Boolean);
      const panels = {
        providers: [
          h('ul', { key: 'l', className: 'fmg-list', ...listProps('providers', providers) }, draft.providers.map((row, index) => h(ProviderCard, {
            key: row.id, t, row, transports: view.transports, setError,
            open: open.has(row.id), onToggle: () => toggleOpen(row.id),
            isFirst: index === 0, onPin: () => providers.toTop(row.id),
            ...dragProps('providers', row.id, providers, draft.providers),
            update: (patch, d) => providers.update(row.id, patch, d), onRemove: () => providers.remove(row.id),
          }))),
          h(React.Fragment, { key: 'e' }, empty(draft.providers)),
        ],
        workflows: [
          h('h4', { key: 'ch', style: { margin: 0 } }, t('connections')),
          h('ul', { key: 'cl', className: 'fmg-list' }, draft.connections.map(row => h(ConnectionCard, {
            key: row.id, t, row, setError, update: patch => connections.update(row.id, patch),
          }))),
          h(React.Fragment, { key: 'ce' }, empty(draft.connections)),
          h('h4', { key: 'wh', style: { margin: '8px 0 0' } }, t('workflows')),
          h('ul', { key: 'wl', className: 'fmg-list', ...listProps('workflows', workflows) }, draft.workflows.map(row => h(WorkflowCard, {
            key: row.id, t, row, connections: connectionNames, instances: view.instanceTypes,
            isDefault: draft.defaultWorkflow === row.id,
            onDefault: () => set({ defaultWorkflow: row.id }),
            open: open.has(row.id), onToggle: () => toggleOpen(row.id),
            ...dragProps('workflows', row.id, workflows, draft.workflows),
            update: patch => workflows.update(row.id, patch), onRemove: () => workflows.remove(row.id),
          }))),
          h(React.Fragment, { key: 'we' }, empty(draft.workflows)),
        ],
        paths: [
          h('p', { key: 'i', className: 'fmg-intro' }, t('pathHint')),
          h('div', { key: 'f', className: 'fmg-fields' },
            Field({ id: 'fmg-out', label: t('outputDir'), children: h('input', { id: 'fmg-out', value: draft.output_dir, spellCheck: false, onChange: e => set({ output_dir: e.target.value }) }) }),
            Field({ id: 'fmg-cache', label: t('cacheDir'), children: h('input', { id: 'fmg-cache', value: draft.cache_dir, spellCheck: false, onChange: e => set({ cache_dir: e.target.value }) }) })),
        ],
        retry: [
          h('p', { key: 'i', className: 'fmg-intro' }, t('retryHint')),
          h('div', { key: 's', className: 'fmg-setting' },
            h('div', { className: 'fmg-setting-main' },
              h('span', { className: 'fmg-setting-title' }, t('retryEnabled')),
              h('p', { className: 'fmg-hint' }, t('retryEnabledHint'))),
            h(Switch, {
              checked: draft.retry_on_failure, label: t('retryEnabled'),
              onChange: retry_on_failure => set({ retry_on_failure }),
            })),
          h('div', { key: 'f', className: 'fmg-fields' },
            Field({ id: 'fmg-retry-count', label: t('retryCount'), wide: true, children: h(React.Fragment, null,
              h('input', {
                id: 'fmg-retry-count', className: 'fmg-count', type: 'number', min: 0, max: MAX_RETRY_COUNT, step: 1,
                inputMode: 'numeric', value: draft.retry_count, onChange: e => set({ retry_count: e.target.value }),
              }),
              h('p', { className: 'fmg-hint' }, t('retryCountHint'))) })),
        ],
      };
      return h('section', { className: 'fmg', 'aria-labelledby': 'fmg-title' },
        // Title, path, and tabs stay outside the scroll region.
        h('div', { className: 'fmg-top' },
          h('h3', { id: 'fmg-title' }, t('title')),
          h('p', { className: 'fmg-intro' }, h('span', { className: 'fmg-path' }, view.path)),
          view.exists ? null : h('p', { className: 'fmg-intro' }, t('noFile')),
          h('div', { className: 'fmg-tabs', role: 'tablist' }, tabs.map(([id, label]) => h('button', {
            key: id, type: 'button', role: 'tab', id: `fmg-tab-${id}`, 'aria-selected': tab === id, 'aria-controls': `fmg-panel-${id}`,
            className: 'fmg-tab', onClick: () => setTab(id),
          }, label)))),
        // Keyed by tab so a new panel starts at its own top instead of keeping
        // the previous tab's scroll offset.
        h('div', { key: tab, role: 'tabpanel', id: `fmg-panel-${tab}`, 'aria-labelledby': `fmg-tab-${tab}`, className: 'fmg-panel' }, panels[tab]),
        h('div', { className: 'fmg-bar' },
          // The add action belongs to the reserved button area, so nothing renders below it.
          tab === 'providers' ? h(Button, { size: 'sm', variant: 'outline', onClick: addProvider }, t('add')) : null,
          tab === 'workflows' ? h(Button, { size: 'sm', variant: 'outline', onClick: addWorkflow }, t('addWorkflow')) : null,
          h('span', { className: 'fmg-grow', role: 'status', 'aria-live': 'polite' },
            error ? h('span', { className: 'fmg-error' }, error) : dirty ? t('dirty') : notice ? h('span', { className: 'fmg-ok' }, notice) : null),
          h(Button, { size: 'sm', variant: 'ghost', disabled: busy, onClick: load }, dirty ? t('reset') : t('reload')),
          h(Button, { size: 'sm', variant: 'primary', disabled: busy || !dirty, onClick: save }, busy ? t('saving') : t('save'))));
    }

    return {
      inject: ['slots', 'locale'],
      apply(ctx) {
        ctx.effect(() => {
          if (document.querySelector(`style[data-plugin-css="${CSS_ID}"]`)) return () => {};
          const style = document.createElement('style');
          style.dataset.plugin = 'dsh-fmage';
          style.dataset.pluginCss = CSS_ID;
          style.textContent = CSS;
          document.head.append(style);
          return () => style.remove();
        }, 'fmage: settings styles');
        ctx.effect(() => ctx.locale.register(NS, { zh, en }), 'fmage: settings dictionaries');
        const t = ctx.locale.bind(NS);
        ctx.slots.inject('settings.section', () => ctx.slots.register({
          name: 'settings.section', id: 'fmage', order: 18, label: () => t('nav'), locale: NS,
        }, FmageSection));
      },
    };
  },
});
