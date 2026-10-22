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
    const CSS_ID = 'dsh-fmage/settings.css';
    const CSS = `
.fmg{max-width:760px;display:flex;flex-direction:column;gap:14px;color:var(--dsw-alias-label-primary);font-size:13px}
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
.fmg-card[data-over=true]{border-color:var(--dsw-alias-label-primary);border-style:dashed}
.fmg-head{display:flex;align-items:center;gap:8px;min-height:32px}
.fmg-rank{min-width:16px;color:var(--dsw-alias-label-tertiary);font-variant-numeric:tabular-nums}
.fmg-handle{cursor:grab;color:var(--dsw-alias-label-tertiary);display:inline-flex;align-items:center;flex:none;padding:2px;border-radius:6px}
.fmg-handle:active{cursor:grabbing}
.fmg-name{flex:1;min-width:0;font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.fmg-sub{color:var(--dsw-alias-label-tertiary);font-size:12px;font-weight:400;max-width:46%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.fmg-fields{display:grid;grid-template-columns:1fr 1fr;gap:10px 12px;align-items:start}
.fmg-field{display:flex;flex-direction:column;gap:4px;min-width:0}
.fmg-field>label{color:var(--dsw-alias-label-secondary);font-size:12px}
.fmg-wide{grid-column:1/-1}
.fmg input,.fmg select,.fmg textarea{box-sizing:border-box;width:100%;font:inherit;color:inherit;background:var(--dsw-alias-bg-l1,transparent);border:.5px solid var(--dsw-alias-border-l2);border-radius:8px;padding:6px 8px}
.fmg textarea{min-height:90px;font-family:var(--dsw-font-mono,monospace);resize:vertical}
.fmg input[readonly]{color:var(--dsw-alias-label-tertiary)}
.fmg-key{display:flex;gap:6px;align-items:center}
.fmg-icon{background:0 0;border:.5px solid var(--dsw-alias-border-l2);border-radius:8px;width:30px;height:30px;display:inline-flex;align-items:center;justify-content:center;cursor:pointer;color:var(--dsw-alias-label-secondary);flex:none}
.fmg-icon:disabled{opacity:.35;cursor:default}
.fmg-icon:focus-visible,.fmg-tab:focus-visible{outline:var(--dsw-focus-ring-width,2px) solid var(--dsw-focus-ring-color,var(--dsw-alias-state-business-primary));outline-offset:2px}
.fmg-bar{display:flex;gap:8px;align-items:center;position:sticky;bottom:0;padding:10px 0;background:var(--dsw-alias-bg-l0,var(--dsw-alias-bg-l1))}
.fmg-grow{flex:1}
.fmg-error{color:var(--dsw-alias-state-danger-primary,#d33)}
.fmg-ok{color:var(--dsw-alias-label-tertiary)}
.fmg-empty{margin:0;color:var(--dsw-alias-label-tertiary)}
`;
    const zh = {
      nav: 'Fmage 生图', title: 'Fmage 生图', intro: '管理 Fmage 的提供商、RunningHub 工作流和输出目录。配置文件：',
      tabProviders: '提供商', tabWorkflows: '工作流', tabPaths: '目录',
      providersIntro: '开启的提供商按从上到下的顺序作为优先级，第一个为默认提供商。拖动左侧手柄或使用箭头调整顺序。',
      add: '添加提供商', addConnection: '添加连接', addWorkflow: '添加工作流',
      name: '名称', transport: 'Transport', baseUrl: 'Base URL', model: 'Model', apiKey: 'API Key',
      enable: '启用 {name}', remove: '删除', show: '显示密钥', hide: '隐藏密钥',
      drag: '拖动排序', expand: '展开', collapse: '折叠',
      keyEmpty: '未设置', keyEnv: '使用环境变量 {env}', default: '默认', setDefault: '设为默认',
      connections: 'RunningHub 连接', workflows: '工作流', connection: '连接', workflowId: '工作流 ID',
      outputs: '输出节点', outputsHint: '多个节点 ID 用逗号分隔', instance: '实例类型', description: '说明', inputs: '输入映射 (JSON)',
      outputDir: '输出目录', cacheDir: '缓存目录', pathHint: '留空使用 Fmage 默认位置。',
      save: '保存', saving: '保存中…', reset: '放弃修改', reload: '重新读取', loading: '读取中…', saved: '已保存，新请求即生效。',
      dirty: '有未保存的修改', none: '暂无条目', noFile: '配置文件尚不存在，保存后自动创建。',
      invalidJson: '{name} 的输入映射不是有效 JSON', confirmRemove: '删除“{name}”？保存后生效。',
    };
    const en = {
      nav: 'Fmage images', title: 'Fmage images', intro: 'Manage Fmage providers, RunningHub workflows, and output folders. Config file: ',
      tabProviders: 'Providers', tabWorkflows: 'Workflows', tabPaths: 'Folders',
      providersIntro: 'Enabled providers are tried top to bottom; the first one is the default. Drag the handle or use the arrows to reorder.',
      add: 'Add provider', addConnection: 'Add connection', addWorkflow: 'Add workflow',
      name: 'Name', transport: 'Transport', baseUrl: 'Base URL', model: 'Model', apiKey: 'API key',
      enable: 'Enable {name}', remove: 'Delete', show: 'Show key', hide: 'Hide key',
      drag: 'Drag to reorder', expand: 'Expand', collapse: 'Collapse',
      keyEmpty: 'Not set', keyEnv: 'Uses environment variable {env}', default: 'Default', setDefault: 'Make default',
      connections: 'RunningHub connections', workflows: 'Workflows', connection: 'Connection', workflowId: 'Workflow ID',
      outputs: 'Output nodes', outputsHint: 'Separate node IDs with commas', instance: 'Instance type', description: 'Description', inputs: 'Input mapping (JSON)',
      outputDir: 'Output folder', cacheDir: 'Cache folder', pathHint: 'Leave empty to use the Fmage default.',
      save: 'Save', saving: 'Saving…', reset: 'Discard changes', reload: 'Reload', loading: 'Loading…', saved: 'Saved. New requests use it.',
      dirty: 'Unsaved changes', none: 'No entries', noFile: 'The config file does not exist yet; saving creates it.',
      invalidJson: 'Input mapping for {name} is not valid JSON', confirmRemove: 'Delete "{name}"? Takes effect after saving.',
    };
    const icon = d => h('svg', { width: 16, height: 16, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 1.8, strokeLinecap: 'round', strokeLinejoin: 'round', 'aria-hidden': true }, h('path', { d }));
    const ICON = {
      trash: 'M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13',
      eye: 'M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12zM12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6z',
      eyeOff: 'M3 3l18 18M10.6 5.1A10 10 0 0 1 12 5c6.5 0 10 7 10 7a17 17 0 0 1-3.2 4.1M6.6 6.6C3.8 8.4 2 12 2 12s3.5 7 10 7a9.6 9.6 0 0 0 5.4-1.6M9.9 9.9a3 3 0 0 0 4.2 4.2',
      grip: 'M9 6h6M9 12h6M9 18h6', chevronDown: 'M6 9l6 6 6-6', chevronRight: 'M9 6l6 6-6 6',
    };
    const IconButton = ({ label, path, onClick, disabled, expanded }) => h('button', {
      type: 'button', className: 'fmg-icon', 'aria-label': label, title: label, onClick, disabled,
      ...(expanded === undefined ? {} : { 'aria-expanded': expanded }),
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
      };
    };
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
      };
    }
    /** Drag reorder: drop the source row onto the target row's position. */
    const reorder = (list, fromId, toId) => {
      const from = list.findIndex(r => r.id === fromId);
      const to = list.findIndex(r => r.id === toId);
      if (from < 0 || to < 0 || from === to) return list;
      const next = list.slice();
      const [item] = next.splice(from, 1);
      next.splice(to, 0, item);
      return next;
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

    function RemoveButton({ t, name, onRemove }) {
      return h(IconButton, {
        label: t('remove'), path: ICON.trash,
        onClick: () => { if (window.confirm(t('confirmRemove', { name: name || '?' }))) onRemove(); },
      });
    }
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

    function ProviderCard({ t, row, transports, update, onRemove, setError, open, onToggle, dragging, over, onDragStart, onDragOver, onDrop, onDragEnd }) {
      const f = field => `fmg-${row.id}-${field}`;
      const rank = row.enabled ? String(row.rank) : '–';
      return h('li', {
        className: 'fmg-card',
        'data-off': String(!row.enabled), 'data-dragging': String(dragging), 'data-over': String(over),
        onDragOver: e => { e.preventDefault(); onDragOver(); },
        onDrop: e => { e.preventDefault(); onDrop(); },
      },
        h('div', { className: 'fmg-head' },
          h('span', { className: 'fmg-rank', 'aria-hidden': true }, rank),
          h(CardHandle, { t, id: row.id, onDragStart, onDragEnd }),
          h('span', { className: 'fmg-name' }, row.name || '—'),
          open ? null : h('span', { className: 'fmg-sub' }, [row.transport, row.model].filter(Boolean).join(' · ')),
          h(Switch, { checked: row.enabled, label: t('enable', { name: row.name }), onChange: enabled => update({ enabled }) }),
          h(IconButton, {
            label: t(open ? 'collapse' : 'expand'), path: open ? ICON.chevronDown : ICON.chevronRight, onClick: onToggle, expanded: open,
          }),
          h(RemoveButton, { t, name: row.name, onRemove })),
        open ? h('div', { className: 'fmg-fields' },
          Field({ id: f('name'), label: t('name'), children: h('input', { id: f('name'), value: row.name, onChange: e => update({ name: e.target.value }) }) }),
          Field({ id: f('transport'), label: t('transport'), children: h(Select, { id: f('transport'), value: row.transport, options: transports, onChange: transport => update({ transport }) }) }),
          Field({ id: f('base'), label: t('baseUrl'), wide: true, children: h('input', { id: f('base'), value: row.base_url, spellCheck: false, onChange: e => update({ base_url: e.target.value }) }) }),
          Field({ id: f('model'), label: t('model'), wide: true, children: h('input', { id: f('model'), value: row.model, spellCheck: false, onChange: e => update({ model: e.target.value }) }) }),
          Field({ id: f('key'), label: t('apiKey'), wide: true, children: h(SecretField, { t, row, kind: 'provider', id: f('key'), onChange: update, setError }) })) : null);
    }
    function ConnectionCard({ t, row, origins, update, onRemove, setError, dragging, over, onDragStart, onDragOver, onDrop, onDragEnd }) {
      const f = field => `fmg-${row.id}-${field}`;
      return h('li', {
        className: 'fmg-card', 'data-dragging': String(dragging), 'data-over': String(over),
        onDragOver: e => { e.preventDefault(); onDragOver(); },
        onDrop: e => { e.preventDefault(); onDrop(); },
      },
        h('div', { className: 'fmg-head' },
          h(CardHandle, { t, id: row.id, onDragStart, onDragEnd }),
          h('span', { className: 'fmg-name' }, row.name || '—'),
          h(RemoveButton, { t, name: row.name, onRemove })),
        h('div', { className: 'fmg-fields' },
          Field({ id: f('name'), label: t('name'), children: h('input', { id: f('name'), value: row.name, onChange: e => update({ name: e.target.value }) }) }),
          Field({ id: f('base'), label: t('baseUrl'), children: h(Select, { id: f('base'), value: row.base_url, options: origins, onChange: base_url => update({ base_url }) }) }),
          Field({ id: f('key'), label: t('apiKey'), wide: true, children: h(SecretField, { t, row, kind: 'connection', id: f('key'), onChange: update, setError }) })));
    }

    function WorkflowCard({ t, row, connections, instances, isDefault, onDefault, update, onRemove, dragging, over, onDragStart, onDragOver, onDrop, onDragEnd }) {
      const f = field => `fmg-${row.id}-${field}`;
      return h('li', {
        className: 'fmg-card', 'data-dragging': String(dragging), 'data-over': String(over),
        onDragOver: e => { e.preventDefault(); onDragOver(); },
        onDrop: e => { e.preventDefault(); onDrop(); },
      },
        h('div', { className: 'fmg-head' },
          h(CardHandle, { t, id: row.id, onDragStart, onDragEnd }),
          h('span', { className: 'fmg-name' }, row.name || '—', isDefault ? ` · ${t('default')}` : ''),
          isDefault ? null : h(Button, { size: 'sm', variant: 'outline', onClick: onDefault }, t('setDefault')),
          h(RemoveButton, { t, name: row.name, onRemove })),
        h('div', { className: 'fmg-fields' },
          Field({ id: f('name'), label: t('name'), children: h('input', { id: f('name'), value: row.name, onChange: e => update({ name: e.target.value }) }) }),
          Field({ id: f('conn'), label: t('connection'), children: h(Select, { id: f('conn'), value: row.connection, options: connections, onChange: connection => update({ connection }) }) }),
          Field({ id: f('wid'), label: t('workflowId'), children: h('input', { id: f('wid'), value: row.workflow_id, inputMode: 'numeric', onChange: e => update({ workflow_id: e.target.value }) }) }),
          Field({ id: f('inst'), label: t('instance'), children: h(Select, { id: f('inst'), value: row.instance_type, options: instances, onChange: instance_type => update({ instance_type }) }) }),
          Field({ id: f('out'), label: t('outputs'), children: h('input', { id: f('out'), value: row.outputs, placeholder: t('outputsHint'), onChange: e => update({ outputs: e.target.value }) }) }),
          Field({ id: f('desc'), label: t('description'), children: h('input', { id: f('desc'), value: row.description, onChange: e => update({ description: e.target.value }) }) }),
          Field({ id: f('in'), label: t('inputs'), wide: true, children: h('textarea', { id: f('in'), value: row.inputsText, spellCheck: false, onChange: e => update({ inputsText: e.target.value }) }) })));
    }

    function useList(setDraft, setDirty, field) {
      return {
        update: (id, patch, dirty = true) => {
          setDraft(d => ({ ...d, [field]: d[field].map(r => r.id === id ? { ...r, ...patch } : r) }));
          if (dirty) setDirty(true);
        },
        move: (index, delta) => { setDraft(d => ({ ...d, [field]: move(d[field], index, delta) })); setDirty(true); },
        moveTo: (fromId, toId) => { setDraft(d => ({ ...d, [field]: reorder(d[field], fromId, toId) })); setDirty(true); },
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
      const [drag, setDrag] = useState({ list: null, from: null, over: null });
      const dragProps = (list, id, api) => ({
        dragging: drag.list === list && drag.from === id,
        over: drag.list === list && drag.over === id && drag.from !== id,
        onDragStart: () => setDrag({ list, from: id, over: null }),
        onDragOver: () => setDrag(d => (d.list === list && d.over === id ? d : { list, from: d.list === list ? d.from : null, over: id })),
        onDrop: () => {
          if (drag.list === list && drag.from && drag.from !== id) api.moveTo(drag.from, id);
          setDrag({ list: null, from: null, over: null });
        },
        onDragEnd: () => setDrag({ list: null, from: null, over: null }),
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
      let rank = 0;
      const tabs = [['providers', t('tabProviders')], ['workflows', t('tabWorkflows')], ['paths', t('tabPaths')]];
      const empty = list => list.length ? null : h('p', { className: 'fmg-empty' }, t('none'));
      const connectionNames = draft.connections.map(c => c.name).filter(Boolean);
      const panels = {
        providers: [
          h('p', { key: 'i', className: 'fmg-intro' }, t('providersIntro')),
          h('ul', { key: 'l', className: 'fmg-list' }, draft.providers.map(row => h(ProviderCard, {
            key: row.id, t, transports: view.transports, setError,
            row: { ...row, rank: row.enabled ? ++rank : 0 },
            open: open.has(row.id), onToggle: () => toggleOpen(row.id),
            ...dragProps('providers', row.id, providers),
            update: (patch, d) => providers.update(row.id, patch, d), onRemove: () => providers.remove(row.id),
          }))),
          h(React.Fragment, { key: 'e' }, empty(draft.providers)),
          h('div', { key: 'a' }, h(Button, { variant: 'outline', size: 'sm', onClick: () => {
            const row = {
              id: key(), from: null, name: '', enabled: true, transport: view.transports[0], base_url: '', model: '',
              api_key: '', api_key_set: false, api_key_masked: '', api_key_env: '', revealed: null,
            };
            providers.add(row);
            setOpen(prev => new Set(prev).add(row.id));
          } }, t('add'))),
        ],
        workflows: [
          h('h4', { key: 'ch', style: { margin: 0 } }, t('connections')),
          h('ul', { key: 'cl', className: 'fmg-list' }, draft.connections.map(row => h(ConnectionCard, {
            key: row.id, t, row, origins: view.runninghubOrigins, setError,
            ...dragProps('connections', row.id, connections),
            update: (patch, d) => {
              // Renaming a connection carries its workflows along.
              if (typeof patch.name === 'string') setDraft(dd => ({ ...dd, workflows: dd.workflows.map(w => w.connection === row.name ? { ...w, connection: patch.name } : w) }));
              connections.update(row.id, patch, d);
            },
            onRemove: () => connections.remove(row.id),
          }))),
          h(React.Fragment, { key: 'ce' }, empty(draft.connections)),
          h('div', { key: 'ca' }, h(Button, { variant: 'outline', size: 'sm', onClick: () => connections.add({
            id: key(), from: null, name: '', base_url: view.runninghubOrigins[0], api_key: '', api_key_set: false, api_key_masked: '', revealed: null,
          }) }, t('addConnection'))),
          h('h4', { key: 'wh', style: { margin: '8px 0 0' } }, t('workflows')),
          h('ul', { key: 'wl', className: 'fmg-list' }, draft.workflows.map(row => h(WorkflowCard, {
            key: row.id, t, row, connections: connectionNames, instances: view.instanceTypes,
            isDefault: draft.defaultWorkflow === row.id,
            onDefault: () => set({ defaultWorkflow: row.id }),
            ...dragProps('workflows', row.id, workflows),
            update: patch => workflows.update(row.id, patch), onRemove: () => workflows.remove(row.id),
          }))),
          h(React.Fragment, { key: 'we' }, empty(draft.workflows)),
          h('div', { key: 'wa' }, h(Button, { variant: 'outline', size: 'sm', onClick: () => workflows.add({
            id: key(), from: null, name: '', connection: connectionNames[0] || '', workflow_id: '', description: '', instance_type: 'default', outputs: '', inputsText: '{}',
          }) }, t('addWorkflow'))),
        ],
        paths: [
          h('p', { key: 'i', className: 'fmg-intro' }, t('pathHint')),
          h('div', { key: 'f', className: 'fmg-fields' },
            Field({ id: 'fmg-out', label: t('outputDir'), children: h('input', { id: 'fmg-out', value: draft.output_dir, spellCheck: false, onChange: e => set({ output_dir: e.target.value }) }) }),
            Field({ id: 'fmg-cache', label: t('cacheDir'), children: h('input', { id: 'fmg-cache', value: draft.cache_dir, spellCheck: false, onChange: e => set({ cache_dir: e.target.value }) }) })),
        ],
      };
      return h('section', { className: 'fmg', 'aria-labelledby': 'fmg-title' },
        h('h3', { id: 'fmg-title' }, t('title')),
        h('p', { className: 'fmg-intro' }, t('intro'), h('span', { className: 'fmg-path' }, view.path)),
        view.exists ? null : h('p', { className: 'fmg-intro' }, t('noFile')),
        h('div', { className: 'fmg-tabs', role: 'tablist' }, tabs.map(([id, label]) => h('button', {
          key: id, type: 'button', role: 'tab', id: `fmg-tab-${id}`, 'aria-selected': tab === id, 'aria-controls': `fmg-panel-${id}`,
          className: 'fmg-tab', onClick: () => setTab(id),
        }, label))),
        h('div', { role: 'tabpanel', id: `fmg-panel-${tab}`, 'aria-labelledby': `fmg-tab-${tab}`, style: { display: 'flex', flexDirection: 'column', gap: 10 } }, panels[tab]),
        h('div', { className: 'fmg-bar' },
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
