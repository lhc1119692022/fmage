/** Fmage settings page for the DeepSeek Desktop settings panel (browser half). */
window.__ModuleLoader__.load({
  id: 'dsh-fmage',
  factory(require) {
    const React = require('react');
    const { Button, Switch } = require('@deepseek-ai/dsh-client-ui-primitives');
    // Menus portal to <body> as the host's own pickers do: the composer sits in
    // a container that becomes the containing block for position:fixed, which
    // would shift an inline menu by the width of the left sidebar.
    let createPortal = null;
    try { ({ createPortal } = require('react-dom')); } catch { /* render inline */ }
    const h = React.createElement;
    const { useEffect, useState, useCallback, useRef } = React;
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
.fmg-group{display:flex;flex-direction:column;gap:10px}
.fmg-group+.fmg-group{border-top:.5px solid var(--dsw-alias-border-l2);padding-top:14px;margin-top:4px}
.fmg-group-title{margin:0}
.fmg-setting{display:flex;align-items:center;gap:14px;border:.5px solid var(--dsw-alias-border-l2);border-radius:12px;padding:10px 12px}
.fmg-setting-main{flex:1;min-width:0;display:flex;flex-direction:column;gap:2px}
.fmg-setting-title{font-weight:600}
.fmg-setting .fmg-hint{margin:0}
.fmg-count{max-width:120px}
.fmg-grow{flex:1}
.fmg-error{color:var(--dsw-alias-state-danger-primary,#d33)}
.fmg-ok{color:var(--dsw-alias-label-tertiary)}
.fmg-empty{margin:0;color:var(--dsw-alias-label-tertiary)}
.fmg-star-on{color:var(--dsw-alias-state-business-primary,#4f6ef7)}
.fmg-star-on svg{fill:currentColor}
.fmg-pill-setting{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;padding:10px 12px;border:.5px solid var(--dsw-alias-border-l2);border-radius:12px}
.fmg-pill-setting-text{min-width:0}
.fmg-pill-setting-text strong{font-size:13px;font-weight:600}
.fmg-pill-setting-text p{margin:4px 0 0}
.fmg-advanced{border:.5px solid var(--dsw-alias-border-l2);border-radius:12px;padding:8px 12px}
.fmg-advanced>summary{cursor:pointer;font-size:12.5px;color:var(--dsw-alias-label-secondary)}
.fmg-advanced .fmg-hint{margin:6px 0 0}
/* Composer pill: sits in the conversation.input.right seat. */
.fmg-pill{position:relative;display:inline-flex;align-items:center}
/* Same ghost trigger as the host's composer model selector. */
.fmg-pill-button{display:inline-flex;align-items:center;gap:4px;height:28px;padding:0 4px 0 8px;border:0;border-radius:8px;background:transparent;color:var(--dsw-alias-label-secondary,#4b5563);font:inherit;font-size:13px;font-weight:400;line-height:20px;cursor:pointer;transition:background .15s}
.fmg-pill-button:hover:not(:disabled),.fmg-pill-button.is-open{background:var(--dsw-alias-interactive-bg-hover,rgba(0,0,0,.05))}
.fmg-pill-button:focus-visible{outline:2px solid var(--dsw-alias-brand-primary,#4c78ff);outline-offset:1px}
.fmg-pill-button svg{flex:none}
.fmg-pill-icon{flex:none;color:var(--dsw-alias-brand-primary,#4c78ff)}
.fmg-pill-label{max-width:150px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.fmg-pill-caret{display:inline-flex;flex:none;color:var(--dsw-alias-label-caption,#9ca3af);transition:transform .12s}
.fmg-pill-button.is-open .fmg-pill-caret{transform:rotate(180deg)}
@container (max-width:700px){.fmg-pill-caret{display:none}.fmg-pill-button{padding:0 4px 0 6px}}
/* One left-aligned line, "<model> · <provider>", with the selected check on the right. */
.fmg-pill-menu{position:fixed;z-index:1200;display:flex;flex-direction:column;width:min(340px,calc(100vw - 24px));max-height:min(360px,calc(100vh - 48px));padding:4px;border:1px solid var(--dsw-alias-border-l2,#e5e7eb);border-radius:12px;background:var(--dsw-alias-bg-layer-3,#fff);box-shadow:0 10px 28px rgba(0,0,0,.16);overflow:hidden;color:var(--dsw-alias-label-primary)}
.fmg-pill-list{min-height:0;overflow-y:auto;display:flex;flex-direction:column;gap:2px;padding-right:2px}
.fmg-pill-list::-webkit-scrollbar{width:8px}
.fmg-pill-list::-webkit-scrollbar-track{background:transparent}
.fmg-pill-list::-webkit-scrollbar-thumb{background:var(--dsw-alias-scrollbar-bg-l2,rgba(0,0,0,.2));border:2px solid transparent;border-radius:999px;background-clip:padding-box}
.fmg-pill-option{display:flex;align-items:center;gap:10px;width:100%;min-height:34px;padding:7px 10px;border:0;border-radius:8px;background:transparent;font:inherit;font-size:13px;line-height:18px;color:var(--dsw-alias-label-primary);text-align:left;cursor:pointer}
.fmg-pill-option:hover:not(:disabled){background:var(--dsw-alias-interactive-bg-hover,rgba(0,0,0,.05))}
.fmg-pill-option:focus-visible{outline:2px solid var(--dsw-alias-brand-primary,#4c78ff);outline-offset:-2px}
.fmg-pill-option:disabled{color:var(--dsw-alias-label-dimmed);cursor:default}
.fmg-pill-text{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:13px;font-weight:400}
.fmg-pill-sep{margin:0 6px;color:var(--dsw-alias-label-tertiary,#6b7280)}
.fmg-pill-check{display:grid;place-items:center;flex:0 0 16px;color:var(--dsw-alias-label-primary)}
.fmg-pill-check svg{width:16px;height:16px}
.fmg-pill-error{margin:4px;padding:6px 7px;border-radius:8px;background:rgba(239,68,68,.08);color:#ef4444;font-size:11px;line-height:16px;word-break:break-all}
/* Sidebar annotation workbench, modelled on the Codex image viewer. */
.fmg-ann{position:relative;display:flex;flex-direction:column;height:100%;min-height:0;background:var(--dsw-alias-bg-layer-1);overflow:hidden;user-select:none;container-type:inline-size}
.fmg-ann-stage{position:relative;flex:1 1 0;min-height:0;overflow:hidden;touch-action:none}
.fmg-ann-frame{position:absolute;transform-origin:50% 50%;will-change:transform}
.fmg-ann-frame img{display:block;width:100%;height:100%;user-select:none;-webkit-user-drag:none;pointer-events:none;box-shadow:0 1px 4px rgba(0,0,0,.14)}
.fmg-ann-svg{position:absolute;left:0;top:0;width:100%;height:100%;pointer-events:none}
.fmg-ann-layer{position:absolute;inset:0;pointer-events:none;z-index:4}
.fmg-ann-bar{position:absolute;top:10px;left:50%;transform:translateX(-50%);z-index:8;display:flex;flex-wrap:wrap;justify-content:center;align-items:center;gap:2px;min-height:40px;padding:5px;border-radius:12px;background:var(--dsw-alias-bg-layer-3,#fff);border:.5px solid var(--dsw-alias-border-l2,rgba(0,0,0,.08));box-shadow:0 6px 20px rgba(0,0,0,.12),0 1px 2px rgba(0,0,0,.06);white-space:nowrap;width:max-content;max-width:calc(100% - 16px);box-sizing:border-box}
.fmg-ann-tb{display:inline-flex;align-items:center;gap:6px;height:30px;padding:0 10px;border:0;border-radius:8px;background:transparent;color:var(--dsw-alias-label-primary,#1c2024);font:inherit;font-size:13px;line-height:1;cursor:pointer;flex:none}
.fmg-ann-tb svg{flex:none;color:var(--dsw-alias-label-secondary,#60646c)}
.fmg-ann-tb:hover:not(:disabled){background:var(--dsw-alias-interactive-bg-hover,rgba(0,0,0,.05))}
.fmg-ann-tb:disabled{opacity:.4;cursor:default}
.fmg-ann-mode span{transition:color .12s}
.fmg-ann-mode .is-on{color:var(--dsw-alias-label-primary,#1c2024);font-weight:600}
.fmg-ann-mode .is-off{color:var(--dsw-alias-label-tertiary,#8b8d98)}
.fmg-ann-mode .is-slash{color:var(--dsw-alias-label-tertiary,#b9bbc6);margin:0 -3px}
.fmg-ann-save{display:inline-flex;align-items:center;gap:6px;height:30px;padding:0 12px;border:0;border-radius:8px;background:#3e63dd;color:#fff;font:inherit;font-size:13px;font-weight:500;line-height:1;cursor:pointer;flex:none}
.fmg-ann-save:hover:not(:disabled){background:#3358d4}
.fmg-ann-save:disabled{opacity:.5;cursor:default}
.fmg-ann-sep{width:1px;height:18px;margin:0 8px;background:var(--dsw-alias-border-l2,rgba(0,0,0,.1));flex:none}
.fmg-ann-ib{display:inline-grid;place-items:center;width:28px;height:30px;padding:0;border:0;border-radius:8px;background:transparent;color:var(--dsw-alias-label-secondary,#60646c);cursor:pointer;flex:none}
.fmg-ann-ib:hover:not(:disabled){background:var(--dsw-alias-interactive-bg-hover,rgba(0,0,0,.05));color:var(--dsw-alias-label-primary,#1c2024)}
.fmg-ann-ib[aria-pressed=true]{background:rgba(62,99,221,.12);color:#3e63dd}
.fmg-ann-ib:disabled{opacity:.3;cursor:default}
.fmg-ann-dot{display:grid;place-items:center;width:30px;height:30px;padding:0;border:0;border-radius:8px;background:transparent;cursor:pointer}
.fmg-ann-dot:hover{background:var(--dsw-alias-interactive-bg-hover,rgba(0,0,0,.05))}
.fmg-ann-dot i{display:block;width:16px;height:16px;border-radius:50%;box-shadow:inset 0 0 0 1px rgba(0,0,0,.18)}
/* Narrow sidebars: tighten spacing so every control stays on screen with its icon and label. */
@container (max-width:420px){.fmg-ann-bar{gap:1px;padding:5px 4px}.fmg-ann-tb{gap:4px;padding:0 6px}.fmg-ann-save{gap:4px;padding:0 9px}.fmg-ann-sep{margin:0 5px}.fmg-ann-ib{width:24px}.fmg-ann-dot{width:26px}}
@container (max-width:360px){.fmg-ann-tb,.fmg-ann-save,.fmg-ann-prompt{font-size:12px;padding:0 5px}.fmg-ann-tb svg,.fmg-ann-save svg{width:14px;height:14px}.fmg-ann-sep{margin:0 2px}.fmg-ann-ib{width:21px}.fmg-ann-ib svg{width:16px;height:16px}.fmg-ann-dot{width:22px}}
.fmg-ann-palette{position:absolute;top:56px;left:50%;transform:translateX(-50%);display:grid;grid-template-columns:repeat(4,22px);gap:7px;padding:9px;border-radius:12px;background:var(--dsw-alias-bg-layer-3,#fff);border:.5px solid var(--dsw-alias-border-l2,rgba(0,0,0,.08));box-shadow:0 8px 24px rgba(0,0,0,.16);z-index:9}
.fmg-ann-palette button{width:22px;height:22px;padding:0;border:0;border-radius:50%;cursor:pointer;box-shadow:inset 0 0 0 1px rgba(0,0,0,.18)}
.fmg-ann-palette button[aria-pressed=true]{box-shadow:0 0 0 2px var(--dsw-alias-bg-layer-3,#fff),0 0 0 3.5px #3e63dd}
.fmg-ann-prompt{display:inline-flex;align-items:center;gap:7px;padding:0 8px 0 6px;color:var(--dsw-alias-label-primary,#1c2024);font-size:13px}
.fmg-ann-prompt svg{color:var(--dsw-alias-label-secondary,#60646c)}
/* Codex stroke slider: a white pill on the left edge, thin rail, round knob. */
.fmg-ann-slider{position:absolute;left:12px;top:50%;transform:translateY(-50%);z-index:7;width:34px;height:min(220px,62%);border-radius:17px;background:var(--dsw-alias-bg-layer-3,#fff);border:.5px solid var(--dsw-alias-border-l2,rgba(0,0,0,.08));box-shadow:0 4px 14px rgba(0,0,0,.12);cursor:pointer;touch-action:none;outline:none;box-sizing:border-box}
.fmg-ann-slider:focus-visible{box-shadow:0 0 0 2px #3e63dd}
.fmg-ann-track{position:absolute;left:50%;top:16px;bottom:16px;width:2px;margin-left:-1px;border-radius:1px;background:rgba(0,0,0,.14)}
.fmg-ann-knob{position:absolute;left:50%;width:16px;height:16px;margin:0 0 -8px -8px;border-radius:50%;background:#fff;box-shadow:0 0 0 1px rgba(0,0,0,.2),0 1px 3px rgba(0,0,0,.22);cursor:grab}
.fmg-ann-slider.is-sliding,.fmg-ann-slider.is-sliding .fmg-ann-knob{cursor:grabbing}
.fmg-ann-bubble{position:absolute;left:30px;top:50%;transform:translateY(-50%);display:flex;align-items:center;gap:7px;height:26px;padding:0 9px;border-radius:8px;background:#1c2024;color:#fff;font-size:11.5px;white-space:nowrap;pointer-events:none;font-variant-numeric:tabular-nums}
.fmg-ann-bubble i{display:block;border-radius:50%;flex:none;box-shadow:0 0 0 1px rgba(255,255,255,.5)}
.fmg-ann-pip{position:absolute;border-radius:50%;transform:translate(-50%,-50%);pointer-events:none;box-shadow:0 0 0 1px rgba(255,255,255,.95),0 0 0 2px rgba(0,0,0,.4)}
/* Comments: teardrop pin, Codex pill for a new one, card for editing. */
.fmg-ann-pin{position:absolute;width:24px;height:24px;margin-top:-24px;display:grid;place-items:center;box-sizing:border-box;padding:0;border:2px solid #fff;border-radius:12px 12px 12px 2px;background:#3e63dd;color:#fff;font:inherit;font-size:11.5px;font-weight:700;line-height:1;box-shadow:0 2px 6px rgba(0,0,0,.3);cursor:pointer;pointer-events:auto}
.fmg-ann-pin.is-flip{margin-left:-24px;border-radius:12px 12px 2px 12px}
.fmg-ann-pin[aria-pressed=true]{background:#2f52c8}
.fmg-ann-pill{position:absolute;display:flex;align-items:center;gap:6px;height:40px;padding:0 5px 0 15px;border-radius:20px;background:#fff;box-shadow:0 6px 22px rgba(0,0,0,.2),0 0 0 .5px rgba(0,0,0,.06);transform:translateY(-50%);box-sizing:border-box;pointer-events:auto}
.fmg-ann-pill input{flex:1;min-width:0;height:100%;padding:0;border:0;outline:none;background:transparent;color:#1c2024;font:inherit;font-size:13px}
.fmg-ann-pill input::placeholder,.fmg-ann-card textarea::placeholder{color:#8b8d98}
.fmg-ann-send{flex:none;display:grid;place-items:center;width:30px;height:30px;padding:0;border:0;border-radius:50%;background:#1c2024;color:#fff;cursor:pointer}
.fmg-ann-card{position:absolute;display:flex;flex-direction:column;gap:8px;padding:12px 12px 10px 15px;border-radius:18px;background:#fff;box-shadow:0 8px 28px rgba(0,0,0,.22),0 0 0 .5px rgba(0,0,0,.06);transform:translateY(-50%);box-sizing:border-box;pointer-events:auto}
.fmg-ann-card textarea{display:block;width:100%;min-height:56px;max-height:160px;margin:0;padding:0;border:0;outline:none;resize:none;background:transparent;color:#1c2024;font:inherit;font-size:13px;line-height:20px;user-select:text}
.fmg-ann-card-foot{display:flex;align-items:center;gap:6px}
.fmg-ann-card-foot .fmg-ann-ib{width:28px;height:28px;margin:0 auto 0 -6px;color:#1c2024}
.fmg-ann-ghost{height:28px;padding:0 13px;border:.5px solid rgba(0,0,0,.16);border-radius:14px;background:#fff;color:#1c2024;font:inherit;font-size:12.5px;cursor:pointer}
.fmg-ann-confirm{height:28px;padding:0 13px;border:0;border-radius:14px;background:#1c2024;color:#fff;font:inherit;font-size:12.5px;cursor:pointer}
/* Text: typed in place, then held by a selection box with corner handles. */
.fmg-ann-textin{position:absolute;margin:0;padding:0;border:0;outline:1px dashed rgba(62,99,221,.95);outline-offset:3px;background:transparent;resize:none;overflow:hidden;white-space:pre;line-height:1.25;z-index:3;user-select:text;box-sizing:content-box;pointer-events:auto;caret-color:currentColor}
.fmg-ann-sel{position:absolute;box-sizing:border-box;border:1.5px solid #3e63dd;border-radius:2px;cursor:move;pointer-events:auto}
.fmg-ann-handle{position:absolute;width:22px;height:22px;display:grid;place-items:center;padding:0;border:0;border-radius:50%;background:#fff;color:#1c2024;box-shadow:0 1px 4px rgba(0,0,0,.32);cursor:pointer}
.fmg-ann-handle.is-rotate{left:-11px;top:-11px;background:#3e63dd;color:#fff;cursor:grab}
.fmg-ann-handle.is-del{right:-11px;top:-11px;background:#e5484d;color:#fff}
.fmg-ann-handle.is-size{right:-11px;bottom:-11px;cursor:nwse-resize}
.fmg-ann-handle.is-move{left:-11px;bottom:-11px;cursor:move}
.fmg-ann-zoom{position:absolute;right:12px;bottom:12px;z-index:7;display:flex;align-items:center;height:32px;padding:0 3px;border-radius:10px;background:var(--dsw-alias-bg-layer-3,#fff);border:.5px solid var(--dsw-alias-border-l2,rgba(0,0,0,.08));box-shadow:0 4px 14px rgba(0,0,0,.1);box-sizing:border-box}
.fmg-ann-zoom .fmg-ann-ib{width:26px;height:26px;border-radius:7px}
.fmg-ann-zoom-val{min-width:46px;height:26px;padding:0;border:0;border-radius:7px;background:transparent;color:var(--dsw-alias-label-primary,#1c2024);font:inherit;font-size:12px;cursor:pointer;font-variant-numeric:tabular-nums}
.fmg-ann-zoom-val:hover{background:var(--dsw-alias-interactive-bg-hover,rgba(0,0,0,.05))}
.fmg-ann-toast{position:absolute;left:50%;bottom:54px;transform:translateX(-50%);z-index:9;max-width:calc(100% - 32px);padding:7px 12px;border-radius:9px;background:#1c2024;color:#fff;font-size:12px;line-height:17px;box-shadow:0 6px 18px rgba(0,0,0,.2);pointer-events:none;box-sizing:border-box}
.fmg-ann-toast.is-error{background:#e5484d}
.fmg-ann-status{position:absolute;left:12px;bottom:12px;z-index:7;display:flex;align-items:center;gap:6px;max-width:calc(100% - 150px);height:32px;padding:0 4px 0 10px;border-radius:10px;background:var(--dsw-alias-bg-layer-3,#fff);border:.5px solid var(--dsw-alias-border-l2,rgba(0,0,0,.08));box-shadow:0 4px 14px rgba(0,0,0,.1);box-sizing:border-box;font-size:12px;color:var(--dsw-alias-label-secondary,#60646c)}
.fmg-ann-status-dot{flex:none;width:7px;height:7px;border-radius:50%;background:#12a594}
.fmg-ann-status.is-stale .fmg-ann-status-dot{background:#f5a524}
.fmg-ann-status-text{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.fmg-ann-status button{flex:none;height:24px;padding:0 8px;border:0;border-radius:7px;background:transparent;color:var(--dsw-alias-brand-primary,#4c78ff);font:inherit;font-size:12px;cursor:pointer}
.fmg-ann-status button:hover:not(:disabled){background:var(--dsw-alias-interactive-bg-hover,rgba(0,0,0,.05))}
.fmg-ann-status button:disabled{opacity:.4;cursor:default}
.fmg-ann-recent{margin:0;max-width:100%;color:var(--dsw-alias-label-secondary,#60646c)}
.fmg-ann-empty{position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:12px;padding:24px 14px;text-align:center;color:var(--dsw-alias-label-tertiary);font-size:12.5px}
.fmg-ann-thumbs{display:flex;flex-wrap:wrap;gap:6px;justify-content:center;max-width:100%}
.fmg-ann-thumbs button{padding:0;border:0;background:transparent;cursor:pointer;line-height:0;border-radius:6px;overflow:hidden}
.fmg-ann-thumbs img{width:56px;height:56px;object-fit:cover;border-radius:6px;display:block}
.fmg-ann-drop{position:absolute;inset:8px;display:flex;align-items:center;justify-content:center;border:1.5px dashed #3e63dd;border-radius:12px;background:rgba(62,99,221,.06);color:#3e63dd;font-size:13px;z-index:10;pointer-events:none}
`;
    const zh = {
      nav: 'Fmage 生图', title: 'Fmage 生图',
      tabProviders: '提供商', tabWorkflows: '工作流', tabSettings: '设置',
      groupPaths: '目录', groupRetry: '重试', groupGeneration: '生成',
      complexity: '复杂度 (complexity)', complexityOff: 'False（不发送）', complexityLow: 'Low', complexityMedium: 'Medium', complexityHigh: 'High',
      complexityHint: '仅对 gpt-image-2.6 系列模型生效，生成与编辑都会发送；2.5 及更早模型永不发送。False 表示不发送，由官方默认按 high 处理。',
      add: '添加提供商', addWorkflow: '添加工作流',
      name: '名称', transport: 'Transport', baseUrl: 'Base URL', model: 'Model', apiKey: 'API Key',
      enable: '启用 {name}', remove: '移除', show: '显示密钥', hide: '隐藏密钥',
      pill: '生图', pillTitle: '生图提供商', pillKeySet: 'Key 已配置', pillKeyMissing: '未配置 Key',
      pillSwitchFailed: '切换失败',
      annTitle: 'Fmage 工作台', annEmpty: '还没有图片。先在输入框添加一张图片。',
      annNeedsComposerImage: '标注只能作用于输入框里的图片。',
      annSourceGone: '这张图已从输入框移除，请重新选图。',
      annToolArrow: '箭头', annToolRect: '方框', annToolEllipse: '椭圆', annToolPen: '画笔',
      annToolText: '文字',
      annColor: '换颜色', annUndo: '撤销', annRedo: '重做', annClear: '清空',
      annCommentHint: '点击图像以添加评论',
      annDropHere: '松开即在此打开', annDropUnsupported: '拖入的内容不是输入框里的图片。',
      annMustBeFirst: '标记层只能作用于第一张输入图；这张是第 {n} 张。换成第一张，或改用合成。',
      annExported: '已保存，评论已插入输入框。', annExportedCopy: '已保存，评论已复制。',
      annExportedSilent: '已保存',
      annPending: '已保存（{mode}）· 待发送', annPendingHint: '下一次编辑这张图的生图请求会带上它',
      annPendingStale: '有新改动未保存', annPendingStaleHint: '发送的仍是上次保存的版本；点保存更新',
      annWithdraw: '撤销保存', annWithdrawn: '已撤销保存', annWithdrawUsed: '这份标注已被生图请求使用，无法撤销。',
      annRecentSent: '上一次标注（{mode}）已随生图请求发送 · {time}',
      annRecentWaiting: '上一次标注（{mode}）已保存，还没有生图请求使用它',
      annRecentReleased: '上一次标注已作废：同一张图被重新上传了',
      annRecentExpired: '上一次标注已失效，没有被使用',
      annModeMarkup: '分层', annModeFlatten: '合成',
      annModeMarkupHint: '把透明标注层作为 markup 单独发送；原图不变，标记由模型抹除。',
      annModeFlattenHint: '保存时用合成图替换输入框里的原图，发送前可恢复；标记可能残留在结果里。',
      annModeUnsupported: '当前模型不是 GPT Image 2.5/2.6，只能用合成。',
      annComment: '评论', annCommentPlaceholder: '添加评论…',
      annToolSelect: '选择', annDone: '完成', annSend: '发送', annRotate: '旋转', annMove: '移动', annDeleteText: '删除文字',
      annZoomIn: '放大', annZoomOut: '缩小', annZoomFit: '适应窗口', annNothing: '还没有标注或评论。',
      annExport: '保存', annExporting: '保存中…',
      annAnnotate: '标注', annBack: '返回', annStroke: '粗细', annAddComment: '添加评论', annResize: '缩放文字',
      annToolEraser: '橡皮', annCancel: '取消', annSaveComment: '保存',
      annTextPrompt: '输入文字',
      annRemoveComment: '删除这条评论',
      annLoadFailed: '图片读取失败。', annFailed: '操作失败',
      annReplaced: '已用合成图替换原图，评论已插入输入框。', annReplacedCopy: '已用合成图替换原图，评论已复制。',
      annReplacedSilent: '已用合成图替换原图',
      annFallback: '没能替换输入框里的图片，改为生图时合成；评论已插入输入框。',
      annFallbackCopy: '没能替换输入框里的图片，改为生图时合成；评论已复制。',
      annFallbackSilent: '没能替换输入框里的图片，改为生图时合成。',
      annSwapped: '输入框里是合成图', annSwappedHint: '发送的是合成图；原图仍保留，发送前可以换回',
      annRestore: '恢复原图', annRestored: '已恢复原图，标注可以继续修改。', annRestoreFailed: '没能恢复原图。',
      showPill: '在输入栏显示生图胶囊', showPillHint: '关闭后仍可在下方列表切换默认提供商；改动在下一次请求生效。',
      orderHint: '拖动只改变回退顺序；默认提供商由星标决定。',
      advanced: '高级：排序与回退',
      conflict: '配置文件已在别处修改。', overwrite: '重新读取并覆盖',
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
      tabProviders: 'Providers', tabWorkflows: 'Workflows', tabSettings: 'Settings',
      groupPaths: 'Folders', groupRetry: 'Retry', groupGeneration: 'Generation',
      complexity: 'Complexity', complexityOff: 'False (not sent)', complexityLow: 'Low', complexityMedium: 'Medium', complexityHigh: 'High',
      complexityHint: 'Applies only to gpt-image-2.6 series models, on both generation and edits; never sent to 2.5 or earlier. False omits the field, so the API default (high) applies.',
      add: 'Add provider', addWorkflow: 'Add workflow',
      name: 'Name', transport: 'Transport', baseUrl: 'Base URL', model: 'Model', apiKey: 'API key',
      enable: 'Enable {name}', remove: 'Remove', show: 'Show key', hide: 'Hide key',
      pill: 'Image', pillTitle: 'Image provider', pillKeySet: 'Key configured', pillKeyMissing: 'No key',
      pillSwitchFailed: 'Switch failed',
      annTitle: 'Fmage workspace', annEmpty: 'No image yet. Add one to the composer first.',
      annToolArrow: 'Arrow', annToolRect: 'Box', annToolEllipse: 'Ellipse', annToolPen: 'Pen',
      annToolText: 'Text',
      annColor: 'Change colour', annUndo: 'Undo', annRedo: 'Redo', annClear: 'Clear',
      annCommentHint: 'Click the image to add a comment',
      annDropHere: 'Drop to open it here', annDropUnsupported: 'That drop was not an image from the composer.',
      annNeedsComposerImage: 'Annotations only apply to an image in the composer.',
      annSourceGone: 'That image left the composer; pick it again.',
      annMustBeFirst: 'The markup layer applies only to the first input image; this is image {n}. Use the first one, or flatten instead.',
      annModeMarkup: 'Layer', annModeFlatten: 'Flatten',
      annModeMarkupHint: 'Send the transparent layer as markup; the target image stays untouched and the model removes the marks.',
      annModeFlattenHint: 'Saving swaps the composer image for a copy with the marks burned in; you can undo it before sending. The marks may survive into the result.',
      annModeUnsupported: 'This model is not GPT Image 2.5/2.6, so only flatten is available.',
      annComment: 'Comment', annCommentPlaceholder: 'Add a comment…',
      annToolSelect: 'Select', annDone: 'Done', annSend: 'Send', annRotate: 'Rotate', annMove: 'Move', annDeleteText: 'Delete text',
      annZoomIn: 'Zoom in', annZoomOut: 'Zoom out', annZoomFit: 'Fit to view', annNothing: 'Nothing to save yet.',
      annExport: 'Save', annExporting: 'Saving…',
      annAnnotate: 'Annotate', annBack: 'Back', annStroke: 'Stroke width', annAddComment: 'Add a comment', annResize: 'Resize text',
      annToolEraser: 'Eraser', annCancel: 'Cancel', annSaveComment: 'Save',
      annExported: 'Saved; the comments were inserted into the composer.', annExportedCopy: 'Saved; the comments were copied.',
      annExportedSilent: 'Saved',
      annPending: 'Saved ({mode}) · pending', annPendingHint: 'The next image request that edits this picture takes it',
      annPendingStale: 'Unsaved changes', annPendingStaleHint: 'The last saved version is what gets sent; save to update it',
      annWithdraw: 'Withdraw', annWithdrawn: 'Save withdrawn', annWithdrawUsed: 'An image request already used this annotation, so it cannot be withdrawn.',
      annRecentSent: 'Last annotation ({mode}) went out with an image request · {time}',
      annRecentWaiting: 'Last annotation ({mode}) is saved; no image request has used it yet',
      annRecentReleased: 'Last annotation was dropped: the same picture was uploaded again',
      annRecentExpired: 'Last annotation expired unused',
      annTextPrompt: 'Type text',
      annRemoveComment: 'Remove this comment',
      annLoadFailed: 'The image could not be read.', annFailed: 'The operation failed',
      annReplaced: 'The composer image now has the marks; the comments were inserted into the composer.',
      annReplacedCopy: 'The composer image now has the marks; the comments were copied.',
      annReplacedSilent: 'The composer image now has the marks',
      annFallback: 'The composer image could not be swapped, so the marks are flattened at edit time; the comments were inserted into the composer.',
      annFallbackCopy: 'The composer image could not be swapped, so the marks are flattened at edit time; the comments were copied.',
      annFallbackSilent: 'The composer image could not be swapped, so the marks are flattened at edit time.',
      annSwapped: 'The composer holds the marked copy', annSwappedHint: 'The marked copy is what gets sent; the original is kept and can come back before sending',
      annRestore: 'Restore original', annRestored: 'The original is back; the marks can be edited again.', annRestoreFailed: 'The original could not be restored.',
      showPill: 'Show the image provider pill in the composer', showPillHint: 'Turning it off keeps the list below and the saved default; changes apply to the next request.',
      orderHint: 'Dragging only changes the fallback order; the star sets the default provider.',
      advanced: 'Advanced: order and fallback',
      conflict: 'The config file changed elsewhere.', overwrite: 'Reload and overwrite',
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
      download: 'M12 3v12m0 0l-4-4m4 4l4-4M4 17v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2',
      star: 'M12 3.5l2.7 5.6 6.1.9-4.4 4.3 1 6.1-5.4-2.9-5.4 2.9 1-6.1L3.2 10l6.1-.9z',
    };
    // Same glyph as the reference composer pill: rounded frame, dot, ridge.
    const pillIcon = () => h('svg', {
      className: 'fmg-pill-icon', width: 13, height: 13, viewBox: '0 0 16 16', fill: 'none',
      stroke: 'currentColor', strokeWidth: 1.6, strokeLinecap: 'round', strokeLinejoin: 'round', 'aria-hidden': true,
    },
      h('rect', { x: 1.5, y: 2.5, width: 13, height: 11, rx: 2 }),
      h('circle', { cx: 5.5, cy: 6.5, r: 1.3 }),
      h('path', { d: 'M14.5 10.5l-3.2-3.2-6.3 6.2' }));
    const IconButton = ({ label, path, onClick, disabled, className }) => h('button', {
      type: 'button', className: `fmg-icon${className ? ` ${className}` : ''}`, 'aria-label': label, title: label, onClick, disabled,
    }, icon(path));

    async function request(url, init) {
      const response = await fetch(url, { cache: 'no-store', ...init, headers: { 'content-type': 'application/json', ...(init && init.headers) } });
      let data = null;
      try { data = await response.json(); } catch { /* non-JSON failure */ }
      if (!response.ok) {
        const error = new Error((data && data.error) || `HTTP ${response.status}`);
        error.status = response.status;
        throw error;
      }
      return data;
    }

    let uid = 0;
    const key = () => `row-${++uid}`;
    // Editable draft rows; `from` remembers the stored name so renames keep unshown fields.
    const toDraft = view => {
      const workflows = view.workflows.map(w => ({ ...w, id: key(), from: w.name, outputs: w.output_node_ids.join(', '), inputsText: JSON.stringify(w.inputs, null, 2) }));
      const providers = view.providers.map(p => ({ ...p, id: key(), from: p.name, api_key: undefined, revealed: null }));
      return {
        providers,
        connections: view.workflow_connections.map(c => ({ ...c, id: key(), from: c.name, api_key: undefined, revealed: null })),
        workflows,
        // Both defaults are tracked by row id, so renaming the row keeps the marker.
        defaultWorkflow: (workflows.find(w => w.name === view.active_workflow) || {}).id || null,
        defaultProvider: (providers.find(p => p.name === view.default_provider) || {}).id || null,
        showProviderPill: view.show_provider_pill !== false,
        output_dir: view.output_dir, cache_dir: view.cache_dir,
        retry_on_failure: view.retry_on_failure === true,
        retry_count: String(view.retry_count ?? DEFAULT_RETRY_COUNT),
        complexity: ['low', 'medium', 'high'].includes(view.complexity) ? view.complexity : false,
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
        active_workflow: active ? active.name : null,
        default_provider: (draft.providers.find(p => p.id === draft.defaultProvider) || {}).name || null,
        show_provider_pill: draft.showProviderPill !== false,
        output_dir: draft.output_dir, cache_dir: draft.cache_dir,
        retry_on_failure: draft.retry_on_failure === true, retry_count: retryCountOf(draft.retry_count, t),
        complexity: draft.complexity || false,
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

    function ProviderCard({ t, row, transports, update, onRemove, onDefault, isDefault, showHandle, setError, open, onToggle, dragging, drop, onDragStart, onDragOver, onDrop, onDragEnd }) {
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
          showHandle ? h(CardHandle, { t, id: row.id, onDragStart, onDragEnd }) : null,
          // The title row is the disclosure control; no separate chevron.
          h('button', {
            type: 'button', className: 'fmg-toggle', onClick: onToggle, 'aria-expanded': open, title: t(open ? 'collapse' : 'expand'),
          },
            h('span', { className: 'fmg-name' }, row.name || '—', isDefault ? ` · ${t('default')}` : ''),
            open ? null : h('span', { className: 'fmg-sub' }, row.model)),
          h(IconButton, {
            label: t(isDefault ? 'default' : 'setDefault'), path: ICON.star, onClick: onDefault,
            disabled: !row.enabled || isDefault, className: isDefault ? 'fmg-star-on' : undefined,
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
      // Set only for a 409 from the revision guard, so the bar can offer an
      // explicit overwrite instead of a dead end.
      const [conflict, setConflict] = useState(false);
      const [tab, setTab] = useState('providers');
      // Ordering is the fallback path now that the pill and the star set the
      // default, so its handles stay behind a disclosure.
      const [advanced, setAdvanced] = useState(false);
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
        setBusy(true); setError(''); setNotice(''); setConflict(false);
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
      // `force` is an explicit user choice after a 409: re-read the file for the
      // current revision, then apply the draft on top of it.
      const save = async (force = false) => {
        setBusy(true); setError(''); setNotice(''); setConflict(false);
        try {
          const base = force ? await request(ROUTE) : view;
          if (force) setView(base);
          const next = await request(ROUTE, { method: 'PUT', body: JSON.stringify({ revision: base.revision, changes: toChanges(draft, t) }) });
          setView(next); setDraft(toDraft(next)); setDirty(false); setNotice(t('saved'));
        } catch (e) {
          setError(e.message);
          setConflict(e.status === 409);
        } finally { setBusy(false); }
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
      const tabs = [['providers', t('tabProviders')], ['workflows', t('tabWorkflows')], ['settings', t('tabSettings')]];
      const group = (id, title, children) => h('section', { key: id, className: 'fmg-group', 'aria-labelledby': `fmg-group-${id}` },
        h('h4', { id: `fmg-group-${id}`, className: 'fmg-group-title' }, title), ...children);
      const empty = list => list.length ? null : h('p', { className: 'fmg-empty' }, t('none'));
      const connectionNames = draft.connections.map(c => c.name).filter(Boolean);
      const panels = {
        providers: [
          h('div', { key: 'pill-toggle', className: 'fmg-pill-setting' },
            h('div', { className: 'fmg-pill-setting-text' },
              h('strong', null, t('showPill')),
              h('p', { className: 'fmg-hint' }, t('showPillHint'))),
            h(Switch, { checked: draft.showProviderPill, label: t('showPill'), onChange: showProviderPill => set({ showProviderPill }) })),
          h('details', {
            key: 'advanced', className: 'fmg-advanced', open: advanced,
            onToggle: event => setAdvanced(event.currentTarget.open),
          },
            h('summary', null, t('advanced')),
            h('p', { className: 'fmg-hint' }, t('orderHint'))),
          h('ul', { key: 'l', className: 'fmg-list', ...listProps('providers', providers) }, draft.providers.map(row => h(ProviderCard, {
            key: row.id, t, row, transports: view.transports, setError,
            open: open.has(row.id), onToggle: () => toggleOpen(row.id),
            isDefault: row.id === draft.defaultProvider, showHandle: advanced,
            onDefault: () => set({ defaultProvider: row.id }),
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
        settings: [group('paths', t('groupPaths'), [
          h('p', { key: 'i', className: 'fmg-intro' }, t('pathHint')),
          h('div', { key: 'f', className: 'fmg-fields' },
            Field({ id: 'fmg-out', label: t('outputDir'), children: h('input', { id: 'fmg-out', value: draft.output_dir, spellCheck: false, onChange: e => set({ output_dir: e.target.value }) }) }),
            Field({ id: 'fmg-cache', label: t('cacheDir'), children: h('input', { id: 'fmg-cache', value: draft.cache_dir, spellCheck: false, onChange: e => set({ cache_dir: e.target.value }) }) })),
        ]), group('retry', t('groupRetry'), [
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
        ]), group('generation', t('groupGeneration'), [
          h('p', { key: 'i', className: 'fmg-intro' }, t('complexityHint')),
          h('div', { key: 'f', className: 'fmg-fields' },
            Field({ id: 'fmg-complexity', label: t('complexity'), children: h('select', {
              id: 'fmg-complexity', value: draft.complexity || 'false',
              onChange: e => set({ complexity: e.target.value === 'false' ? false : e.target.value }),
            }, [['false', 'complexityOff'], ['low', 'complexityLow'], ['medium', 'complexityMedium'], ['high', 'complexityHigh']]
              .map(([value, label]) => h('option', { key: value, value }, t(label)))) })),
        ])],
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
            error ? h('span', { className: 'fmg-error' }, conflict ? t('conflict') : error, ' ') : dirty ? t('dirty') : notice ? h('span', { className: 'fmg-ok' }, notice) : null,
            conflict ? h(Button, { size: 'sm', variant: 'outline', disabled: busy, onClick: () => save(true) }, t('overwrite')) : null),
          h(Button, { size: 'sm', variant: 'ghost', disabled: busy, onClick: load }, dirty ? t('reset') : t('reload')),
          h(Button, { size: 'sm', variant: 'primary', disabled: busy || !dirty, onClick: () => save() }, busy ? t('saving') : t('save'))));
    }

    // ---- Sidebar annotation workbench -------------------------------------
    // The panel never touches draft attachments. It reads a composer
    // thumbnail's blob URL (or a local file), draws a layer, and writes the
    // result through the Host route; the only thing it adds to the draft is a
    // plain text path.
    const ANN_ROUTE = 'api/fmage.annotation';
    const ANN_KIND = 'fmage-annotation';
    const ANN_TAB_ID = 'dsh-fmage/annotation';
    const ANN_MODEL = /gpt[-_\s]+image[-_\s]+2\.[56](?:\.[0-9]+)*(?=$|[^a-z0-9.])/i;
    const ANN_COLORS = ['#e5484d', '#f5a524', '#12a594', '#4c78ff'];
    // Marks belong to one composer attachment, not to the picture: a draft is
    // keyed by the attachment's object URL (new for every upload) and kept in
    // memory only, so it survives closing the panel or switching thumbnails but
    // is dropped once the attachment leaves the composer (sent or removed).
    // Uploading the same image again therefore starts from a blank canvas.
    const annDrafts = new Map();
    // Earlier builds kept drafts in localStorage keyed by file name and size,
    // which brought old marks back on a new task. Clear those once.
    try {
      const legacy = [];
      for (let i = 0; i < window.localStorage.length; i += 1) {
        const key = window.localStorage.key(i);
        if (key && key.startsWith('dsh-fmage/annotation/')) legacy.push(key);
      }
      legacy.forEach(key => window.localStorage.removeItem(key));
    } catch { /* storage unavailable */ }
    // Saves the server is holding for an edit, per composer upload, plus the
    // last few whose upload has left the composer (normally: it was sent).
    // `release` drops a sent save once the same picture is uploaded again, so
    // a new task never inherits it. In memory only, like the drafts.
    const annSaved = new Map();
    const annRecent = [];
    const annListeners = new Set();
    const annNotify = () => { annListeners.forEach(listener => listener()); };
    let annText = key => key;
    // The user's last 分层/合成 choice, kept across panels and restarts.
    // Flatten until the user picks 分层; a model without the markup channel
    // shows 合成 but keeps the stored choice for when it is back.
    const ANN_MODE_KEY = 'dsh-fmage/annotation-mode';
    function storedAnnMode() {
      try { return window.localStorage.getItem(ANN_MODE_KEY) === 'markup' ? 'markup' : 'flatten'; } catch { return 'flatten'; }
    }
    function storeAnnMode(value) {
      try { window.localStorage.setItem(ANN_MODE_KEY, value); } catch { /* storage unavailable */ }
    }

    function isMarkupModel(model) { return ANN_MODEL.test(String(model || '')); }

    // Draft images keep their browser object URL, so the rail is the only place
    // the bytes are reachable before send. Read-only: no state is touched.
    function composerImages() {
      const card = document.querySelector('[data-composer-card]');
      if (!card) return [];
      return Array.from(card.querySelectorAll('img[src^="blob:"]')).map((img, index) => ({
        index, url: img.src, name: img.getAttribute('alt') || `image-${index + 1}`,
      }));
    }

    /** SHA-256 of the bytes behind a composer object URL, or null. */
    async function fileSha256(url) {
      try {
        if (!window.crypto || !window.crypto.subtle) return null;
        const bytes = await (await fetch(url)).arrayBuffer();
        const digest = await window.crypto.subtle.digest('SHA-256', bytes);
        return Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('');
      } catch {
        return null;
      }
    }

    // 16x16 luma grid; must match scripts/image_fingerprint.py, which the
    // edit uses to recognize this picture in a resized or re-encoded copy.
    const ANN_GRID = 16;
    function annFingerprint(image) {
      try {
        const scale = Math.min(1, Math.sqrt(4e6 / (image.naturalWidth * image.naturalHeight)));
        const width = Math.max(ANN_GRID, Math.round(image.naturalWidth * scale));
        const height = Math.max(ANN_GRID, Math.round(image.naturalHeight * scale));
        const canvas = document.createElement('canvas');
        canvas.width = width;
        canvas.height = height;
        const context = canvas.getContext('2d', { willReadFrequently: true });
        context.drawImage(image, 0, 0, width, height);
        const data = context.getImageData(0, 0, width, height).data;
        const bounds = size => Array.from({ length: ANN_GRID }, (_, index) => {
          const start = Math.floor(index * size / ANN_GRID);
          const end = Math.max(start + 1, Math.floor((index + 1) * size / ANN_GRID));
          return [Math.min(start, size - 1), Math.min(end, size)];
        });
        const rows = bounds(height);
        const columns = bounds(width);
        const grid = [];
        for (const [top, bottom] of rows) {
          for (const [left, right] of columns) {
            let total = 0;
            for (let y = top; y < bottom; y += 1) {
              for (let x = left; x < right; x += 1) {
                const at = (y * width + x) * 4;
                const luma = (data[at] * 19595 + data[at + 1] * 38470 + data[at + 2] * 7471 + 0x8000) >> 16;
                total += Math.floor(luma * data[at + 3] / 255);
              }
            }
            grid.push(Math.round(total / ((bottom - top) * (right - left)) * 10) / 10);
          }
        }
        return grid;
      } catch {
        return null;
      }
    }

    // Runs from the first save on, whether or not the panel is open.
    let annWatching = false;
    const annSeen = new Set();
    function watchUploads() {
      if (annWatching || typeof MutationObserver !== 'function') return;
      annWatching = true;
      let frame = 0;
      const check = () => {
        frame = 0;
        if (!document.querySelector('[data-composer-card]')) return;
        const live = new Set(composerImages().map(item => item.url));
        let changed = false;
        for (const [url, saved] of Array.from(annSaved)) {
          if (live.has(url)) continue;
          annSaved.delete(url);
          annRecent.unshift({ ...saved, status: 'waiting' });
          changed = true;
        }
        annRecent.splice(5);
        for (const url of live) {
          if (annSeen.has(url)) continue;
          annSeen.add(url);
          if (!annRecent.some(item => item.status === 'waiting' && item.sha256)) continue;
          void fileSha256(url).then(async sha256 => {
            const hits = annRecent.filter(item => item.status === 'waiting' && item.sha256 && item.sha256 === sha256);
            if (!hits.length) return;
            const keep = composerImages().map(item => item.url);
            await request(ANN_ROUTE, { method: 'POST', body: JSON.stringify({ action: 'release', sha256, keep }) });
            hits.forEach(item => { item.status = 'released'; });
            annNotify();
          }).catch(() => { /* the TTL still retires it */ });
        }
        for (const url of Array.from(annSeen)) if (!live.has(url)) annSeen.delete(url);
        if (changed) annNotify();
      };
      const schedule = () => { if (!frame) frame = window.requestAnimationFrame(check); };
      new MutationObserver(schedule).observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ['src'] });
      window.addEventListener('focus', schedule);
      check();
    }

    // The conversation keeps a normalized copy of each attached picture (at
    // most 2048x2048) and hands tools that copy. The composer still holds the
    // file the user picked, so its bytes go to the Host as soon as the picture
    // appears; edit_image recognizes the copy and sends this original instead.
    // Read-only toward the composer: attachments, their order and the draft
    // are never touched, and every failure is silent (the copy still goes).
    const ORIG_ROUTE = 'api/fmage.original';
    const ORIG_MAX_BYTES = 64 * 1024 * 1024;
    const origSeen = new Set();
    const origDone = new Set();
    let origQueue = Promise.resolve();

    function hexDigest(buffer) {
      return Array.from(new Uint8Array(buffer), byte => byte.toString(16).padStart(2, '0')).join('');
    }

    async function collectOriginal(url) {
      if (!window.crypto || !window.crypto.subtle) return;
      const blob = await (await fetch(url)).blob();
      if (!blob.size || blob.size > ORIG_MAX_BYTES) return;
      const sha256 = hexDigest(await window.crypto.subtle.digest('SHA-256', await blob.arrayBuffer()));
      if (origDone.has(sha256)) return;
      const known = await fetch(`${ORIG_ROUTE}?sha256=${sha256}`, { cache: 'no-store' });
      if (known.ok) {
        const state = await known.json();
        if (state && (state.status === 'ready' || state.status === 'uploading')) { origDone.add(sha256); return; }
      }
      const image = await loadImageElement(url);
      const width = image.naturalWidth;
      const height = image.naturalHeight;
      const grid = annFingerprint(image);
      if (!width || !height || !grid) return;
      const query = new URLSearchParams({ sha256, width: String(width), height: String(height), fingerprint: grid.join(',') });
      const response = await fetch(`${ORIG_ROUTE}?${query}`, {
        method: 'POST', cache: 'no-store', headers: { 'content-type': 'application/octet-stream' }, body: blob,
      });
      if (response.ok) origDone.add(sha256);
    }

    /** Uploads each new composer picture once, one at a time. Returns the stop function. */
    function watchOriginals() {
      if (typeof MutationObserver !== 'function' || !document.body) return () => {};
      let frame = 0;
      const check = () => {
        frame = 0;
        const live = new Set(composerImages().map(item => item.url));
        for (const url of live) {
          if (origSeen.has(url)) continue;
          origSeen.add(url);
          origQueue = origQueue.then(() => collectOriginal(url)).catch(() => { /* the copy still goes */ });
        }
        for (const url of Array.from(origSeen)) if (!live.has(url)) origSeen.delete(url);
      };
      const schedule = () => { if (!frame) frame = window.requestAnimationFrame(check); };
      const observer = new MutationObserver(schedule);
      observer.observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ['src'] });
      check();
      return () => {
        observer.disconnect();
        if (frame) window.cancelAnimationFrame(frame);
      };
    }

    // Flatten saves swap the composer image for the marked copy. Keyed by the
    // copy's object URL; holds what 恢复原图 needs. The host's plugin
    // removeAttachment only takes an id off the draft and leaves the browser
    // attachment alive, so the original can be put back until the send.
    const annReplaced = new Map();

    const annPause = ms => new Promise(resolve => { setTimeout(resolve, ms); });

    async function annWaitFor(check, timeout) {
      const end = Date.now() + timeout;
      for (;;) {
        const value = check();
        if (value) return value;
        if (Date.now() > end) return null;
        await annPause(16);
      }
    }

    /** Position of a composer image in the attachment rail, which lists every attachment in draft order. */
    function railSlot(url) {
      const card = document.querySelector('[data-composer-card]');
      if (!card) return null;
      const image = Array.from(card.querySelectorAll('img[src^="blob:"]')).find(item => item.src === url);
      const group = image ? image.closest('[role="group"]') : null;
      if (!group || !card.contains(group)) return null;
      const items = Array.from(group.children);
      const index = items.findIndex(item => item.contains(image));
      return index < 0 ? null : { index, count: items.length };
    }

    /** Reorder the draft with the plugin actions: keep the common prefix, append the rest. */
    function arrangeAttachments(actions, current, target) {
      let keep = 0;
      while (keep < current.length && keep < target.length && current[keep] === target[keep]) keep += 1;
      if (keep < current.length) actions.pruneAttachments(current.slice(0, keep));
      if (keep < target.length) actions.addAttachments(target.slice(keep));
    }

    /**
     * Hand a file to the composer the way a dropped file arrives, so it goes
     * through the host's own checks and upload. False when nothing took it.
     */
    function dropIntoComposer(file) {
      const card = document.querySelector('[data-composer-card]');
      if (!card || typeof DataTransfer !== 'function' || typeof DragEvent !== 'function') return false;
      try {
        const transfer = new DataTransfer();
        transfer.items.add(file);
        if (!Array.from(transfer.types).includes('Files')) return false;
        const event = new DragEvent('drop', { bubbles: true, cancelable: true, composed: true, dataTransfer: transfer });
        card.dispatchEvent(event);
        return event.defaultPrevented;
      } catch {
        return false;
      }
    }

    // The host admits at most 50 MiB per image; a larger copy would be refused.
    const COMPOSITE_MAX_BYTES = 50 * 1024 * 1024;
    // Chrome encodes WebP only up to this many pixels per side.
    const WEBP_MAX_SIDE = 16383;

    function canvasBlob(canvas, type, quality) {
      return new Promise(resolve => { canvas.toBlob(resolve, type, quality); });
    }

    /**
     * The source with the marks painted on, in the source's format where it
     * fits. A copy over the host's 50 MiB per-image limit is re-encoded as
     * WebP at quality 100; one still too large is scaled down, keeping its
     * aspect ratio, until it fits.
     */
    async function compositeFile(source, shapes) {
      const original = await (await fetch(source.url)).blob();
      const image = await loadImageElement(source.url);
      const layer = document.createElement('canvas');
      layer.width = source.width;
      layer.height = source.height;
      drawShapes(layer.getContext('2d'), shapes);
      const canvas = document.createElement('canvas');
      canvas.width = source.width;
      canvas.height = source.height;
      const context = canvas.getContext('2d');
      context.drawImage(image, 0, 0, source.width, source.height);
      context.drawImage(layer, 0, 0);
      const stem = String(source.name || '').replace(/\.[^./\\]+$/, '') || 'image';
      const fileOf = (blob, extension) => new File([blob], `${stem}-annotated.${extension}`, { type: blob.type, lastModified: Date.now() });
      const jpeg = original.type === 'image/jpeg';
      const native = await canvasBlob(canvas, jpeg ? 'image/jpeg' : 'image/png', jpeg ? 0.95 : undefined);
      if (native && native.size <= COMPOSITE_MAX_BYTES) return fileOf(native, jpeg ? 'jpg' : 'png');
      // A browser that cannot encode WebP at this size hands back PNG instead,
      // so only a blob that really is WebP counts.
      const webpOf = async target => {
        const blob = await canvasBlob(target, 'image/webp', 1);
        return blob && blob.type === 'image/webp' ? blob : null;
      };
      let blob = Math.max(canvas.width, canvas.height) <= WEBP_MAX_SIDE ? await webpOf(canvas) : null;
      if (blob && blob.size <= COMPOSITE_MAX_BYTES) return fileOf(blob, 'webp');
      let scale = Math.min(1, WEBP_MAX_SIDE / Math.max(canvas.width, canvas.height));
      // The first try sizes from the WebP just measured, or else only from the
      // side cap; a PNG's size says little about how large the WebP will be.
      let measured = blob ? blob.size : 0;
      for (let attempt = 0; attempt < 8; attempt += 1) {
        // Bytes grow roughly with area, so shrink each side by the square
        // root of the overshoot, with a margin so the next try usually fits.
        if (measured > COMPOSITE_MAX_BYTES) scale *= Math.sqrt(COMPOSITE_MAX_BYTES / measured) * 0.95;
        else if (attempt > 0) scale *= 0.85;
        const width = Math.max(1, Math.round(canvas.width * scale));
        const height = Math.max(1, Math.round(canvas.height * scale));
        const smaller = document.createElement('canvas');
        smaller.width = width;
        smaller.height = height;
        const small = smaller.getContext('2d');
        small.imageSmoothingEnabled = true;
        small.imageSmoothingQuality = 'high';
        small.drawImage(canvas, 0, 0, width, height);
        blob = await webpOf(smaller);
        if (blob && blob.size <= COMPOSITE_MAX_BYTES) return fileOf(blob, 'webp');
        measured = blob ? blob.size : 0;
      }
      throw new Error('composite encode failed');
    }

    function loadImageElement(url) {
      return new Promise((resolve, reject) => {
        const image = new Image();
        image.onload = () => resolve(image);
        image.onerror = () => reject(new Error('image decode failed'));
        image.src = url;
      });
    }

    function blobToDataUrl(blob) {
      return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result));
        reader.onerror = () => reject(reader.error || new Error('read failed'));
        reader.readAsDataURL(blob);
      });
    }

    function pngBlobOf(canvas) {
      return new Promise(resolve => { canvas.toBlob(blob => resolve(blob), 'image/png'); });
    }

    function readAnnotation(url) {
      return annDrafts.get(url) || null;
    }

    function writeAnnotation(url, value) {
      if (value.shapes.length || value.comments.length) annDrafts.set(url, value);
      else annDrafts.delete(url);
    }

    /** Forget drafts whose attachment is no longer in the composer. */
    function pruneAnnotations(live) {
      for (const url of Array.from(annDrafts.keys())) if (!live.has(url)) annDrafts.delete(url);
    }

    function arrowHead(from, to, size) {
      const angle = Math.atan2(to.y - from.y, to.x - from.x);
      const spread = 0.42;
      return [
        `${to.x},${to.y}`,
        `${to.x - size * Math.cos(angle - spread)},${to.y - size * Math.sin(angle - spread)}`,
        `${to.x - size * Math.cos(angle + spread)},${to.y - size * Math.sin(angle + spread)}`,
      ].join(' ');
    }

    // Shapes live in source pixels so the exported layer matches the image
    // exactly. Text boxes are measured with a canvas so CJK and Latin glyphs
    // get real widths, and the same numbers drive the SVG, the export and the
    // selection box.
    const ANN_LINE = 1.25;
    // Baseline within a 1.25em line, matching where a textarea draws it.
    const ANN_ASCENT = 1.0;
    const ANN_FONT = '"PingFang SC","Microsoft YaHei","Segoe UI",sans-serif';
    let annMeasure;
    function measureLines(text, size) {
      if (annMeasure === undefined) {
        try {
          const canvas = document.createElement('canvas');
          annMeasure = canvas && typeof canvas.getContext === 'function' ? canvas.getContext('2d') : null;
        } catch { annMeasure = null; }
      }
      const lines = String(text || '').split('\n');
      let width = 0;
      lines.forEach(line => {
        let w = line.length * size * 0.6;
        if (annMeasure) {
          annMeasure.font = `${size}px ${ANN_FONT}`;
          w = annMeasure.measureText(line).width;
        }
        width = Math.max(width, w);
      });
      return { lines, width: Math.max(size * 0.5, width), height: lines.length * size * ANN_LINE };
    }

    function textBox(shape) {
      const measured = measureLines(shape.text, shape.size);
      return { x: shape.x, y: shape.y, width: measured.width, height: measured.height, lines: measured.lines };
    }

    function boxOf(shape) {
      return {
        x: Math.min(shape.x1, shape.x2), y: Math.min(shape.y1, shape.y2),
        width: Math.abs(shape.x2 - shape.x1), height: Math.abs(shape.y2 - shape.y1),
      };
    }

    const polyPoints = points => (points || []).map(point => `${point.x},${point.y}`).join(' ');

    function renderShape(shape) {
      const stroke = {
        stroke: shape.color, strokeWidth: shape.width, fill: 'none', strokeLinecap: 'round', strokeLinejoin: 'round',
      };
      if (shape.type === 'arrow') {
        return h('g', { key: shape.id },
          h('line', { ...stroke, x1: shape.x1, y1: shape.y1, x2: shape.x2, y2: shape.y2 }),
          h('polygon', {
            fill: shape.color, stroke: shape.color, strokeWidth: shape.width * 0.5, strokeLinejoin: 'round',
            points: arrowHead({ x: shape.x1, y: shape.y1 }, { x: shape.x2, y: shape.y2 }, shape.width * 3.2),
          }));
      }
      if (shape.type === 'rect') return h('rect', { key: shape.id, ...stroke, ...boxOf(shape) });
      if (shape.type === 'ellipse') {
        const box = boxOf(shape);
        return h('ellipse', {
          key: shape.id, ...stroke,
          cx: box.x + box.width / 2, cy: box.y + box.height / 2, rx: box.width / 2, ry: box.height / 2,
        });
      }
      if (shape.type === 'pen') return h('polyline', { key: shape.id, ...stroke, points: polyPoints(shape.points) });
      if (shape.type === 'text') {
        const box = textBox(shape);
        const cx = box.x + box.width / 2;
        const cy = box.y + box.height / 2;
        return h('text', {
          key: shape.id, fill: shape.color, fontSize: shape.size, fontFamily: ANN_FONT, xmlSpace: 'preserve',
          transform: shape.rotation ? `rotate(${shape.rotation} ${cx} ${cy})` : undefined,
        }, box.lines.map((line, index) => h('tspan', {
          key: index, x: shape.x, y: shape.y + shape.size * (ANN_ASCENT + index * ANN_LINE),
        }, line || ' ')));
      }
      return null;
    }

    function drawShape(ctx, shape) {
      ctx.save();
      ctx.strokeStyle = shape.color;
      ctx.fillStyle = shape.color;
      ctx.lineWidth = shape.width;
      ctx.lineCap = 'round';
      ctx.lineJoin = 'round';
      if (shape.type === 'arrow') {
        const head = shape.width * 3.2;
        const angle = Math.atan2(shape.y2 - shape.y1, shape.x2 - shape.x1);
        ctx.beginPath();
        ctx.moveTo(shape.x1, shape.y1);
        ctx.lineTo(shape.x2, shape.y2);
        ctx.stroke();
        ctx.beginPath();
        ctx.moveTo(shape.x2, shape.y2);
        ctx.lineTo(shape.x2 - head * Math.cos(angle - 0.42), shape.y2 - head * Math.sin(angle - 0.42));
        ctx.lineTo(shape.x2 - head * Math.cos(angle + 0.42), shape.y2 - head * Math.sin(angle + 0.42));
        ctx.closePath();
        ctx.lineWidth = shape.width * 0.5;
        ctx.fill();
        ctx.stroke();
      } else if (shape.type === 'rect' || shape.type === 'ellipse') {
        const box = boxOf(shape);
        ctx.beginPath();
        if (shape.type === 'rect') ctx.rect(box.x, box.y, box.width, box.height);
        else ctx.ellipse(box.x + box.width / 2, box.y + box.height / 2, box.width / 2, box.height / 2, 0, 0, Math.PI * 2);
        ctx.stroke();
      } else if (shape.type === 'pen') {
        ctx.beginPath();
        (shape.points || []).forEach((point, index) => {
          if (index === 0) ctx.moveTo(point.x, point.y);
          else ctx.lineTo(point.x, point.y);
        });
        ctx.stroke();
      } else if (shape.type === 'text') {
        const box = textBox(shape);
        const cx = box.x + box.width / 2;
        const cy = box.y + box.height / 2;
        ctx.translate(cx, cy);
        ctx.rotate(((shape.rotation || 0) * Math.PI) / 180);
        ctx.translate(-cx, -cy);
        ctx.font = `${shape.size}px ${ANN_FONT}`;
        ctx.textBaseline = 'alphabetic';
        box.lines.forEach((line, index) => {
          ctx.fillText(line, shape.x, shape.y + shape.size * (ANN_ASCENT + index * ANN_LINE));
        });
      }
      ctx.restore();
    }

    /** Paint the shapes in order; an eraser stroke removes only what was drawn before it. */
    function drawShapes(ctx, shapes) {
      shapes.forEach(shape => {
        if (shape.type !== 'erase') { drawShape(ctx, shape); return; }
        ctx.save();
        ctx.globalCompositeOperation = 'destination-out';
        ctx.strokeStyle = '#000';
        ctx.lineWidth = shape.width;
        ctx.lineCap = 'round';
        ctx.lineJoin = 'round';
        ctx.beginPath();
        shape.points.forEach((point, index) => {
          if (index === 0) ctx.moveTo(point.x, point.y);
          else ctx.lineTo(point.x, point.y);
        });
        if (shape.points.length === 1) ctx.lineTo(shape.points[0].x + 0.01, shape.points[0].y);
        ctx.stroke();
        ctx.restore();
      });
    }

    // Glyphs are Lucide icons (ISC licence, lucide.dev) on their 24px grid with
    // the 2px round stroke they are drawn for, so the set reads as one family.
    function annGlyph(paths, size, weight) {
      return h('svg', {
        width: size || 16, height: size || 16, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor',
        strokeWidth: weight || 2, strokeLinecap: 'round', strokeLinejoin: 'round', 'aria-hidden': true,
      }, (Array.isArray(paths) ? paths : [paths]).map((d, index) => h('path', { key: index, d })));
    }

    const ANN_ICONS = {
      // pencil-line
      annotate: ['M12 20h9', 'M16.376 3.622a1 1 0 0 1 3.002 3.002L7.368 18.635a2 2 0 0 1-.855.506l-2.872.838a.5.5 0 0 1-.62-.62l.838-2.872a2 2 0 0 1 .506-.854z', 'm15 5 3 3'],
      // message-square-plus
      comment: ['M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z', 'M12 7v6', 'M9 10h6'],
      // layers
      layers: ['M12.83 2.18a2 2 0 0 0-1.66 0L2.6 6.08a1 1 0 0 0 0 1.83l8.58 3.91a2 2 0 0 0 1.66 0l8.58-3.9a1 1 0 0 0 0-1.83z', 'M2 12a1 1 0 0 0 .58.91l8.6 3.91a2 2 0 0 0 1.65 0l8.58-3.9A1 1 0 0 0 22 12', 'M2 17a1 1 0 0 0 .58.91l8.6 3.91a2 2 0 0 0 1.65 0l8.58-3.9A1 1 0 0 0 22 17'],
      // check
      save: ['M20 6 9 17l-5-5'],
      // mouse-pointer-2
      select: ['M4.037 4.688a.495.495 0 0 1 .651-.651l16 6.5a.5.5 0 0 1-.063.947l-6.124 1.58a2 2 0 0 0-1.438 1.435l-1.579 6.126a.5.5 0 0 1-.947.063z'],
      // arrow-up-right
      arrow: ['M7 7h10v10', 'M7 17 17 7'],
      // pencil
      pen: ['M21.174 6.812a1 1 0 0 0-3.986-3.987L3.842 16.174a2 2 0 0 0-.5.83l-1.321 4.352a.5.5 0 0 0 .623.622l4.353-1.32a2 2 0 0 0 .83-.497z', 'm15 5 4 4'],
      // square
      rect: ['M5 3h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2z'],
      // circle
      ellipse: ['M12 2a10 10 0 1 0 0 20a10 10 0 1 0 0-20z'],
      // type
      text: ['M4 7V4h16v3', 'M9 20h6', 'M12 4v16'],
      // eraser
      eraser: ['m7 21-4.3-4.3c-1-1-1-2.5 0-3.4l9.6-9.6c1-1 2.5-1 3.4 0l5.6 5.6c1 1 1 2.5 0 3.4L13 21', 'M22 21H7', 'm5 11 9 9'],
      // undo-2 / redo-2
      undo: ['M9 14 4 9l5-5', 'M4 9h10.5a5.5 5.5 0 0 1 5.5 5.5a5.5 5.5 0 0 1-5.5 5.5H11'],
      redo: ['m15 14 5-5-5-5', 'M20 9H9.5A5.5 5.5 0 0 0 4 14.5A5.5 5.5 0 0 0 9.5 20H13'],
      // trash-2
      trash: ['M3 6h18', 'M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6', 'M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2', 'M10 11v6', 'M14 11v6'],
      check: ['M20 6 9 17l-5-5'],
      // x
      close: ['M18 6 6 18', 'm6 6 12 12'],
      // arrow-up
      send: ['m5 12 7-7 7 7', 'M12 19V5'],
      // rotate-cw
      rotate: ['M21 12a9 9 0 1 1-9-9c2.52 0 4.93 1 6.74 2.74L21 8', 'M21 3v5h-5'],
      // move-diagonal-2
      resize: ['M19 13v6h-6', 'M5 11V5h6', 'm5 5 14 14'],
      // move
      move: ['M12 2v20', 'm15 19-3 3-3-3', 'm19 9 3 3-3 3', 'M2 12h20', 'm5 9-3 3 3 3', 'm9 5 3-3 3 3'],
      // minus / plus
      minus: ['M5 12h14'],
      plus: ['M5 12h14', 'M12 5v14'],
    };

    const ANN_DRAW_TOOLS = [
      ['select', 'annToolSelect'], ['pen', 'annToolPen'], ['arrow', 'annToolArrow'],
      ['rect', 'annToolRect'], ['ellipse', 'annToolEllipse'], ['text', 'annToolText'],
    ];
    // Tools whose size is the stroke slider; only these show it.
    const ANN_STROKE_TOOLS = ['pen', 'arrow', 'rect', 'ellipse', 'eraser'];
    // Tools that show the brush circle instead of a cursor.
    const ANN_PIP_TOOLS = ['pen', 'arrow', 'eraser'];
    const ANN_STROKE_MIN = 2;
    const ANN_STROKE_MAX = 40;
    const ANN_ZOOM_MIN = 0.25;
    const ANN_ZOOM_MAX = 8;
    const ANN_PAD = { top: 62, right: 16, bottom: 16, left: 16 };
    const annId = () => `s-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}`;

    // Codex-style workbench. The bar's first level is 标注 / 添加评论 / 分层·合成
    // / 保存; drawing tools sit behind 标注 and return with ✓. The image is laid
    // out in JS (fit × zoom, plus pan) so every overlay (pins, cards, text
    // editor, handles, brush circle) is positioned in screen pixels and keeps
    // its size at any zoom. `initialSource`/`initialMenu` exist for the render
    // test; the slot never passes them.
    function AnnotationPanel({ inputActions, useInput, initialSource, initialMenu }) {
      const t = annText;
      // Draft attachment ids in order; the swap needs them to put the marked
      // copy where the original was.
      const attachmentIds = typeof useInput === 'function' ? useInput(state => state.attachmentIds) : null;
      const idsRef = useRef(null);
      idsRef.current = Array.isArray(attachmentIds) ? attachmentIds : null;
      const swapping = useRef(false);
      const [source, setSource] = useState(initialSource || null);
      const [shapes, setShapes] = useState([]);
      const [comments, setComments] = useState([]);
      const [past, setPast] = useState([]);
      const [future, setFuture] = useState([]);
      const [menu, setMenu] = useState(initialMenu === 'draw' || initialMenu === 'comment' ? initialMenu : 'main');
      const [tool, setTool] = useState('pen');
      const [color, setColor] = useState(ANN_COLORS[0]);
      const [palette, setPalette] = useState(false);
      const [stroke, setStroke] = useState(6);
      const [sliding, setSliding] = useState(false);
      const [preferredMode, setPreferredMode] = useState(storedAnnMode);
      const [capable, setCapable] = useState(false);
      const mode = capable && preferredMode === 'markup' ? 'markup' : 'flatten';
      const [busy, setBusy] = useState(false);
      const [toast, setToast] = useState(null);
      const [view, setView] = useState({ zoom: 1, x: 0, y: 0 });
      const [stageSize, setStageSize] = useState({ width: 0, height: 0 });
      const [editing, setEditing] = useState(null);
      const [textEdit, setTextEdit] = useState(null);
      const [selected, setSelected] = useState(null);
      const [hover, setHover] = useState(null);
      const [spaceHeld, setSpaceHeld] = useState(false);
      const [panning, setPanning] = useState(false);
      const [drafts, setDrafts] = useState([]);
      // Which attachment the current shapes belong to; writes wait until the
      // restore for a newly opened attachment has landed.
      const [owner, setOwner] = useState(null);
      const [dragging, setDragging] = useState(false);
      const [, setSavedTick] = useState(0);
      const rootRef = useRef(null);
      const stageRef = useRef(null);
      const sliderRef = useRef(null);
      const dragRef = useRef(null);
      const wheelRef = useRef(null);
      const textLive = useRef(null);
      const commentLive = useRef(null);
      textLive.current = textEdit;
      commentLive.current = editing;

      const notify = (text, error) => setToast(text ? { text, error: Boolean(error), at: Date.now() } : null);

      useEffect(() => {
        const listener = () => setSavedTick(tick => tick + 1);
        annListeners.add(listener);
        return () => { annListeners.delete(listener); };
      }, []);

      // With the composer empty, follow the last save until a request uses it.
      const recent = annRecent[0] || null;
      const recentStatus = recent ? recent.status : null;
      useEffect(() => {
        if (source || !recent || recent.status !== 'waiting') return undefined;
        let alive = true;
        let timer = 0;
        const settle = (status, consumedAt) => {
          recent.status = status;
          if (consumedAt) recent.consumedAt = consumedAt;
          annNotify();
        };
        const poll = async () => {
          if (Date.now() - recent.savedAt > 60 * 60 * 1000) { settle('expired'); return; }
          try {
            const meta = await request(`${ANN_ROUTE}?id=${encodeURIComponent(recent.id)}`);
            if (!alive) return;
            if (meta && meta.consumedAt) { settle('sent', meta.consumedAt); return; }
          } catch (failure) {
            if (!alive) return;
            if (failure && failure.status === 404) { settle('expired'); return; }
          }
          if (alive && recent.status === 'waiting') timer = setTimeout(poll, 3000);
        };
        timer = setTimeout(poll, 0);
        return () => { alive = false; clearTimeout(timer); };
      }, [source, recent, recentStatus]);

      useEffect(() => {
        if (!toast) return undefined;
        const timer = setTimeout(() => setToast(null), toast.error ? 6000 : 3200);
        return () => clearTimeout(timer);
      }, [toast]);

      useEffect(() => {
        let alive = true;
        const sync = () => {
          request(ROUTE).then(config => {
            if (!alive) return;
            const providers = Array.isArray(config.providers) ? config.providers : [];
            const active = providers.find(item => item.name === config.default_provider && item.enabled)
              || providers.find(item => item.enabled) || null;
            const supported = Boolean(active) && active.transport === 'openai-images' && isMarkupModel(active.model);
            setCapable(supported);
          }).catch(() => { /* config unavailable: keep the current mode */ });
        };
        sync();
        window.addEventListener('focus', sync);
        return () => { alive = false; window.removeEventListener('focus', sync); };
      }, []);

      // Stage size drives the fit scale.
      useEffect(() => {
        const element = stageRef.current;
        if (!element) return undefined;
        const measure = () => setStageSize({ width: element.clientWidth, height: element.clientHeight });
        measure();
        if (typeof ResizeObserver !== 'function') {
          window.addEventListener('resize', measure);
          return () => window.removeEventListener('resize', measure);
        }
        const observer = new ResizeObserver(measure);
        observer.observe(element);
        return () => observer.disconnect();
      }, [source]);

      // React's wheel listener is passive, so preventDefault there is ignored
      // and the pane would scroll. Bind natively and read the latest handler.
      useEffect(() => {
        const element = stageRef.current;
        if (!element) return undefined;
        const onWheel = event => {
          event.preventDefault();
          if (wheelRef.current) wheelRef.current(event);
        };
        element.addEventListener('wheel', onWheel, { passive: false });
        return () => element.removeEventListener('wheel', onWheel);
      }, [source]);

      useEffect(() => {
        const reset = () => { setSpaceHeld(false); };
        window.addEventListener('blur', reset);
        return () => window.removeEventListener('blur', reset);
      }, []);

      useEffect(() => {
        if (!source) return;
        const stored = readAnnotation(source.url);
        setShapes(stored ? stored.shapes : []);
        setComments(stored ? stored.comments : []);
        setOwner(source.url);
        setPast([]);
        setFuture([]);
        setEditing(null);
        setTextEdit(null);
        setSelected(null);
        setView({ zoom: 1, x: 0, y: 0 });
      }, [source]);

      useEffect(() => {
        if (!source || owner !== source.url) return;
        writeAnnotation(source.url, { shapes, comments });
      }, [source, owner, shapes, comments]);


      const openSource = async candidate => {
        try {
          const image = await loadImageElement(candidate.url);
          setSource({
            url: candidate.url,
            name: candidate.name,
            draftIndex: candidate.draftIndex ?? null,
            width: image.naturalWidth,
            height: image.naturalHeight,
          });
          setMenu('main');
        } catch {
          notify(t('annLoadFailed'), true);
        }
      };

      // Follow the composer. With nothing open, the first attachment opens by
      // itself; once the open attachment is sent or removed, its draft goes
      // and the panel moves on, so a later upload of the same picture starts
      // blank.
      const opening = useRef(null);
      useEffect(() => {
        let frame = 0;
        const check = () => {
          frame = 0;
          // A swap in progress moves attachments on purpose; it opens the result itself.
          if (swapping.current) return;
          // No composer on screen (another view): nothing to compare against.
          if (!document.querySelector('[data-composer-card]')) return;
          const found = composerImages();
          const live = new Set(found.map(item => item.url));
          pruneAnnotations(live);
          for (const url of Array.from(annReplaced.keys())) if (!live.has(url)) annReplaced.delete(url);
          if (source && live.has(source.url)) return;
          if (source) { setSource(null); setOwner(null); }
          setDrafts(found);
          if (found.length && opening.current !== found[0].url) {
            opening.current = found[0].url;
            void openSource({ ...found[0], draftIndex: 0 }).finally(() => { opening.current = null; });
          }
        };
        const schedule = () => { if (!frame) frame = window.requestAnimationFrame(check); };
        const observer = new MutationObserver(schedule);
        observer.observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ['src'] });
        window.addEventListener('focus', schedule);
        check();
        return () => {
          observer.disconnect();
          window.removeEventListener('focus', schedule);
          if (frame) window.cancelAnimationFrame(frame);
        };
      }, [source]);

      // ---- layout ---------------------------------------------------------
      const availWidth = Math.max(1, stageSize.width - ANN_PAD.left - ANN_PAD.right);
      const availHeight = Math.max(1, stageSize.height - ANN_PAD.top - ANN_PAD.bottom);
      const fit = source && stageSize.width
        ? Math.min(1, availWidth / source.width, availHeight / source.height)
        : 1;
      const layoutFor = state => {
        const scale = fit * state.zoom;
        const width = source.width * scale;
        const height = source.height * scale;
        return {
          scale, width, height,
          left: ANN_PAD.left + (availWidth - width) / 2 + state.x,
          top: ANN_PAD.top + (availHeight - height) / 2 + state.y,
        };
      };
      const frame = source ? layoutFor(view) : null;
      const scale = frame ? frame.scale : 1;
      const toScreen = (x, y) => ({ left: frame.left + x * scale, top: frame.top + y * scale });
      const stagePoint = event => {
        const rect = stageRef.current.getBoundingClientRect();
        return { x: event.clientX - rect.left, y: event.clientY - rect.top };
      };
      const pointOf = event => {
        const at = stagePoint(event);
        return { x: (at.x - frame.left) / scale, y: (at.y - frame.top) / scale };
      };
      const insideImage = point => point.x >= 0 && point.y >= 0 && point.x <= source.width && point.y <= source.height;

      // Zoom about a stage point so the pixel under the pointer stays put.
      const zoomTo = (nextZoom, anchor) => {
        if (!source) return;
        setView(current => {
          const zoom = Math.min(ANN_ZOOM_MAX, Math.max(ANN_ZOOM_MIN, nextZoom(current.zoom)));
          const at = anchor || { x: ANN_PAD.left + availWidth / 2, y: ANN_PAD.top + availHeight / 2 };
          const before = layoutFor(current);
          const px = (at.x - before.left) / before.scale;
          const py = (at.y - before.top) / before.scale;
          const after = layoutFor({ zoom, x: 0, y: 0 });
          return { zoom, x: at.x - px * after.scale - after.left, y: at.y - py * after.scale - after.top };
        });
      };
      wheelRef.current = event => {
        if (!source || !stageRef.current) return;
        const delta = Math.max(-60, Math.min(60, event.deltaY));
        zoomTo(zoom => zoom * Math.exp(-delta * 0.006), stagePoint(event));
      };

      // ---- history --------------------------------------------------------
      const snapshot = () => ({ shapes, comments });
      const remember = (state = snapshot()) => {
        setPast(list => [...list.slice(-79), state]);
        setFuture([]);
      };
      const closeEditors = () => { setEditing(null); setTextEdit(null); setSelected(null); };
      const undo = () => {
        if (!past.length) return;
        const previous = past[past.length - 1];
        setPast(past.slice(0, -1));
        setFuture([...future, snapshot()]);
        setShapes(previous.shapes);
        setComments(previous.comments);
        closeEditors();
      };
      const redoLast = () => {
        if (!future.length) return;
        const next = future[future.length - 1];
        setFuture(future.slice(0, -1));
        setPast([...past, snapshot()]);
        setShapes(next.shapes);
        setComments(next.comments);
        closeEditors();
      };

      // ---- text -----------------------------------------------------------
      const hitText = point => {
        const slack = 6 / scale;
        for (let index = shapes.length - 1; index >= 0; index -= 1) {
          const shape = shapes[index];
          if (shape.type !== 'text') continue;
          const box = textBox(shape);
          const cx = box.x + box.width / 2;
          const cy = box.y + box.height / 2;
          const angle = (-(shape.rotation || 0) * Math.PI) / 180;
          const dx = point.x - cx;
          const dy = point.y - cy;
          const lx = dx * Math.cos(angle) - dy * Math.sin(angle);
          const ly = dx * Math.sin(angle) + dy * Math.cos(angle);
          if (Math.abs(lx) <= box.width / 2 + slack && Math.abs(ly) <= box.height / 2 + slack) return shape;
        }
        return null;
      };

      const beginTextEdit = shape => {
        setSelected(null);
        setTextEdit({
          id: shape.id, isNew: false, x: shape.x, y: shape.y, size: shape.size,
          rotation: shape.rotation || 0, color: shape.color, value: shape.text,
        });
      };

      // The editor unmounts on commit; return focus to the panel so Delete,
      // Space and Ctrl+Z keep working without another click.
      const refocus = () => window.requestAnimationFrame(() => {
        const root = rootRef.current;
        // Only reclaim focus that was dropped; never steal it from elsewhere.
        const active = document.activeElement;
        if (root && typeof root.focus === 'function' && (!active || active === document.body)) root.focus({ preventScroll: true });
      });

      // Idempotent: blur and an outside click may both commit the same edit,
      // so the live ref is cleared before React re-renders.
      const commitText = () => {
        const edit = textLive.current;
        if (!edit) return;
        textLive.current = null;
        setTextEdit(null);
        refocus();
        const value = edit.value.replace(/\s+$/, '');
        const existing = shapes.find(item => item.id === edit.id);
        if (!value.trim()) {
          if (existing) { remember(); setShapes(list => list.filter(item => item.id !== edit.id)); }
          return;
        }
        if (existing) {
          if (existing.text === value) return;
          remember();
          setShapes(list => list.map(item => (item.id === edit.id ? { ...item, text: value } : item)));
        } else {
          remember();
          setShapes(list => (list.some(item => item.id === edit.id) ? list : [...list, {
            id: edit.id, type: 'text', color: edit.color, text: value, size: edit.size,
            rotation: edit.rotation, x: edit.x, y: edit.y,
          }]));
        }
        setSelected(edit.id);
      };

      // ---- comments -------------------------------------------------------
      const openComment = comment => {
        setTextEdit(null);
        setSelected(null);
        setEditing({ id: comment.id, x: comment.x, y: comment.y, text: comment.text, isNew: false });
      };

      // A new comment exists only in the editor until it has text, so an empty
      // one never leaves a pin behind.
      const commitComment = () => {
        const edit = commentLive.current;
        if (!edit) return;
        commentLive.current = null;
        setEditing(null);
        refocus();
        const value = edit.text.trim();
        const existing = comments.find(item => item.id === edit.id);
        if (!value) {
          if (existing) { remember(); setComments(list => list.filter(item => item.id !== edit.id)); }
          return;
        }
        if (existing) {
          if (existing.text === value) return;
          remember();
          setComments(list => list.map(item => (item.id === edit.id ? { ...item, text: value } : item)));
        } else {
          remember();
          setComments(list => (list.some(item => item.id === edit.id) ? list
            : [...list, { id: edit.id, x: edit.x, y: edit.y, text: value }]));
        }
      };

      const removeComment = id => {
        setEditing(null);
        if (!comments.some(item => item.id === id)) return;
        remember();
        setComments(list => list.filter(item => item.id !== id));
      };

      // ---- pointer --------------------------------------------------------
      const capture = event => {
        try { stageRef.current.setPointerCapture(event.pointerId); } catch { /* pointer already gone */ }
      };

      const startPan = event => {
        dragRef.current = { kind: 'pan', startX: event.clientX, startY: event.clientY, x: view.x, y: view.y };
        setPanning(true);
        capture(event);
      };

      // Handle drags (move, rotate, resize) start from the overlay but are
      // captured by the stage so they keep tracking outside the handle.
      const startHandle = (kind, shape) => event => {
        if (event.button !== 0) return;
        event.preventDefault();
        event.stopPropagation();
        const box = textBox(shape);
        const center = { x: box.x + box.width / 2, y: box.y + box.height / 2 };
        const point = pointOf(event);
        dragRef.current = {
          kind, id: shape.id, snapshot: snapshot(), changed: false, center, start: point,
          origin: { x: shape.x, y: shape.y, size: shape.size, rotation: shape.rotation || 0 },
          startAngle: Math.atan2(point.y - center.y, point.x - center.x),
          startDistance: Math.max(1, Math.hypot(point.x - center.x, point.y - center.y)),
        };
        setSelected(shape.id);
        capture(event);
      };

      const onPointerDown = event => {
        if (!source) return;
        if (palette) setPalette(false);
        // Clicking away commits an open editor and does nothing else.
        if (textLive.current) { event.preventDefault(); commitText(); return; }
        if (commentLive.current) { event.preventDefault(); commitComment(); return; }
        // Keyboard shortcuts (space-pan, undo, delete) need focus in the panel.
        if (rootRef.current && typeof rootRef.current.focus === 'function') rootRef.current.focus({ preventScroll: true });
        if (event.button === 1 || (event.button === 0 && spaceHeld)) { event.preventDefault(); startPan(event); return; }
        if (event.button !== 0) return;
        const point = pointOf(event);
        const inside = insideImage(point);

        if (menu === 'comment') {
          event.preventDefault();
          if (!inside) return;
          setEditing({ id: annId(), x: point.x, y: point.y, text: '', isNew: true });
          return;
        }
        if (menu !== 'draw') { event.preventDefault(); startPan(event); return; }

        if (tool === 'select' || tool === 'text') {
          const hit = hitText(point);
          if (hit && tool === 'text') { event.preventDefault(); beginTextEdit(hit); return; }
          if (hit) { startHandle('move', hit)(event); return; }
          setSelected(null);
          if (tool === 'select' || !inside) { event.preventDefault(); startPan(event); return; }
          event.preventDefault();
          const size = Math.max(6, Math.round(18 / scale));
          setTextEdit({
            id: annId(), isNew: true, x: point.x, y: point.y - size * 0.62, size, rotation: 0, color, value: '',
          });
          return;
        }

        setSelected(null);
        if (!inside) { event.preventDefault(); startPan(event); return; }
        event.preventDefault();
        const width = Number((stroke / scale).toFixed(2));
        const id = annId();
        const shape = tool === 'pen' || tool === 'eraser'
          ? { id, type: tool === 'eraser' ? 'erase' : 'pen', color, width, points: [point] }
          : { id, type: tool, color, width, x1: point.x, y1: point.y, x2: point.x, y2: point.y };
        dragRef.current = { kind: 'draw', id, snapshot: snapshot(), changed: false };
        // An eraser tap still erases a dot; other tools wait for movement.
        if (tool === 'eraser') { remember(); dragRef.current.changed = true; setShapes(list => [...list, shape]); }
        else dragRef.current.pending = shape;
        capture(event);
      };

      const onPointerMove = event => {
        if (!source || !stageRef.current) return;
        setHover(stagePoint(event));
        const drag = dragRef.current;
        if (!drag) return;
        if (drag.kind === 'pan') {
          setView(current => ({ ...current, x: drag.x + event.clientX - drag.startX, y: drag.y + event.clientY - drag.startY }));
          return;
        }
        const point = pointOf(event);
        const touch = () => {
          if (drag.changed) return;
          drag.changed = true;
          remember(drag.snapshot);
          if (drag.pending) {
            const pending = drag.pending;
            drag.pending = null;
            setShapes(list => [...list, pending]);
          }
        };
        if (drag.kind === 'draw') {
          touch();
          setShapes(list => list.map(item => {
            if (item.id !== drag.id) return item;
            if (item.type === 'pen' || item.type === 'erase') {
              const last = item.points[item.points.length - 1];
              if (last && Math.hypot(last.x - point.x, last.y - point.y) * scale < 1.5) return item;
              return { ...item, points: [...item.points, point] };
            }
            return { ...item, x2: point.x, y2: point.y };
          }));
          return;
        }
        const update = patch => {
          touch();
          setShapes(list => list.map(item => (item.id === drag.id ? { ...item, ...patch } : item)));
        };
        if (drag.kind === 'move') {
          const dx = point.x - drag.start.x;
          const dy = point.y - drag.start.y;
          if (!drag.changed && Math.hypot(dx, dy) * scale < 2) return;
          update({ x: drag.origin.x + dx, y: drag.origin.y + dy });
        } else if (drag.kind === 'rotate') {
          let rotation = drag.origin.rotation
            + ((Math.atan2(point.y - drag.center.y, point.x - drag.center.x) - drag.startAngle) * 180) / Math.PI;
          rotation = ((rotation % 360) + 540) % 360 - 180;
          if (event.shiftKey) rotation = Math.round(rotation / 15) * 15;
          else if (Math.abs(rotation % 90) < 3) rotation = Math.round(rotation / 90) * 90;
          update({ rotation: Number(rotation.toFixed(1)) });
        } else if (drag.kind === 'resize') {
          // Scale about the box center so the text grows in place.
          const ratio = Math.hypot(point.x - drag.center.x, point.y - drag.center.y) / drag.startDistance;
          const size = Math.max(4, Math.round(drag.origin.size * ratio));
          const target = shapes.find(item => item.id === drag.id);
          if (!target || size === target.size) return;
          const after = measureLines(target.text, size);
          update({ size, x: drag.center.x - after.width / 2, y: drag.center.y - after.height / 2 });
        }
      };

      const onPointerUp = () => {
        const drag = dragRef.current;
        dragRef.current = null;
        setPanning(false);
        if (!drag || drag.kind !== 'draw' || !drag.changed) return;
        // Drop a shape too small to see, and its history entry with it.
        const shape = shapes.find(item => item.id === drag.id);
        if (!shape || shape.type === 'erase') return;
        const tiny = shape.type === 'pen'
          ? shape.points.length < 2
          : Math.hypot(shape.x2 - shape.x1, shape.y2 - shape.y1) * scale < 4;
        if (!tiny) return;
        setPast(entries => entries.slice(0, -1));
        setShapes(list => list.filter(item => item.id !== drag.id));
      };

      const onDoubleClick = event => {
        if (!source) return;
        const hit = hitText(pointOf(event));
        if (!hit) return;
        event.preventDefault();
        setMenu('draw');
        beginTextEdit(hit);
      };

      const onKeyDown = event => {
        const typing = event.target instanceof Element && event.target.closest('input,textarea,[contenteditable="true"]');
        if (typing) return;
        const mod = event.ctrlKey || event.metaKey;
        if (event.code === 'Space') { event.preventDefault(); setSpaceHeld(true); return; }
        if (mod && event.key.toLowerCase() === 'z') { event.preventDefault(); if (event.shiftKey) redoLast(); else undo(); return; }
        if (mod && event.key.toLowerCase() === 'y') { event.preventDefault(); redoLast(); return; }
        if ((event.key === 'Delete' || event.key === 'Backspace') && selected) {
          event.preventDefault();
          remember();
          setShapes(list => list.filter(item => item.id !== selected));
          setSelected(null);
          return;
        }
        if (event.key === 'Escape') {
          if (selected) setSelected(null);
          else if (menu !== 'main') setMenu('main');
        }
      };

      // ---- export ---------------------------------------------------------
      const normalizedComments = list => list.filter(entry => entry.text.trim()).map((entry, index) => ({
        id: index + 1,
        text: entry.text.trim(),
        x: Number((entry.x / source.width).toFixed(4)),
        y: Number((entry.y / source.height).toFixed(4)),
      }));

      const commentText = list => {
        if (!list.length) return '';
        const lines = list.map(entry => `${entry.id}. (x: ${(entry.x * 100).toFixed(1)}%, y: ${(entry.y * 100).toFixed(1)}%) ${entry.text}`);
        return `图像 ${(source.draftIndex ?? 0) + 1}：\n${lines.join('\n')}\n`;
      };

      // Comments go into the prompt at the caret, else to the clipboard.
      const ANN_SAVED_KEYS = ['annExported', 'annExportedCopy', 'annExportedSilent'];
      const deliverComments = async (kept, keys = ANN_SAVED_KEYS) => {
        const text = commentText(kept);
        if (!text) { notify(t(keys[2])); return; }
        let inserted = false;
        if (inputActions) {
          try { inserted = inputActions.insertText(text, inputActions.captureInsertion()); } catch { inserted = false; }
        }
        if (!inserted) {
          try { await navigator.clipboard.writeText(text); } catch { /* no clipboard */ }
        }
        notify(inserted ? t(keys[0]) : t(keys[1]));
      };

      const swapActions = () => Boolean(inputActions && idsRef.current
        && ['removeAttachment', 'addAttachments', 'pruneAttachments'].every(name => typeof inputActions[name] === 'function'));

      // Put the marked copy where the original sits. The original is taken
      // off first so the copy fits under the host's per-message image limits;
      // any failure puts the draft back as it was. Returns the copy's URL.
      const swapInComposer = async (file, original, marks) => {
        const before = idsRef.current.slice();
        const slot = railSlot(original.url);
        if (!slot || slot.count !== before.length) return null;
        const originalId = before[slot.index];
        const beforeUrls = new Set(composerImages().map(item => item.url));
        inputActions.removeAttachment(originalId);
        const gone = await annWaitFor(() => idsRef.current && !idsRef.current.includes(originalId)
          && !composerImages().some(item => item.url === original.url), 1500);
        if (!gone) {
          if (idsRef.current && !idsRef.current.includes(originalId)) arrangeAttachments(inputActions, idsRef.current, before);
          return null;
        }
        // Let the composer re-arm its drop handler with the shorter list.
        await annPause(60);
        const dropped = dropIntoComposer(file);
        const addedId = dropped
          ? await annWaitFor(() => (idsRef.current || []).find(id => !before.includes(id)), 2500)
          : null;
        if (!addedId) {
          arrangeAttachments(inputActions, idsRef.current || [], before);
          await annWaitFor(() => composerImages().some(item => item.url === original.url), 1500);
          return null;
        }
        const target = before.slice();
        target[slot.index] = addedId;
        arrangeAttachments(inputActions, idsRef.current, target);
        const url = await annWaitFor(() => {
          const ids = idsRef.current || [];
          if (ids.join('\n') !== target.join('\n')) return null;
          const found = composerImages().find(item => !beforeUrls.has(item.url));
          return found && railSlot(found.url) && railSlot(found.url).index === slot.index ? found.url : null;
        }, 2500);
        if (!url) {
          // The copy arrived but did not settle into place: back to the original.
          arrangeAttachments(inputActions, idsRef.current || [], before);
          return null;
        }
        // Saving a marked copy again still restores to the first original,
        // carrying the marks of every round so far.
        const prior = annReplaced.get(original.url);
        annReplaced.delete(original.url);
        annReplaced.set(url, prior ? {
          ...prior, copyId: addedId,
          shapes: [...prior.shapes, ...marks.shapes], comments: [...prior.comments, ...marks.comments],
        } : {
          originalId, copyId: addedId, originalUrl: original.url, name: original.name,
          draftIndex: original.draftIndex, shapes: marks.shapes, comments: marks.comments,
        });
        return url;
      };

      const restoreOriginal = async () => {
        const entry = source ? annReplaced.get(source.url) : null;
        if (!entry || busy || !swapActions()) return;
        setBusy(true);
        swapping.current = true;
        let restored = false;
        try {
          const ids = idsRef.current.slice();
          const at = ids.indexOf(entry.copyId);
          if (at >= 0) {
            const target = ids.slice();
            target[at] = entry.originalId;
            arrangeAttachments(inputActions, ids, target);
            restored = Boolean(await annWaitFor(() => composerImages().some(item => item.url === entry.originalUrl)
              && !composerImages().some(item => item.url === source.url), 1500));
            if (!restored) arrangeAttachments(inputActions, idsRef.current || [], ids);
          }
          if (restored) {
            annReplaced.delete(source.url);
            writeAnnotation(entry.originalUrl, { shapes: entry.shapes, comments: entry.comments });
            await openSource({ url: entry.originalUrl, name: entry.name, draftIndex: entry.draftIndex });
          }
        } finally {
          swapping.current = false;
          setBusy(false);
        }
        notify(restored ? t('annRestored') : t('annRestoreFailed'), !restored);
      };

      const exportAnnotation = async () => {
        if (!source || busy) return;
        if (source.draftIndex === null) { notify(t('annNeedsComposerImage'), true); return; }
        // 分层/合成 applies to drawn marks only; comments travel as prompt text.
        const marked = shapes.some(shape => shape.type !== 'erase');
        if (marked && mode === 'markup' && source.draftIndex !== 0) {
          notify(t('annMustBeFirst', { n: String(source.draftIndex + 1) }), true);
          return;
        }
        if (!composerImages().some(item => item.url === source.url)) {
          notify(t('annSourceGone'), true);
          setSource(null);
          return;
        }
        // A comment still open in its editor counts: commit it and export it.
        let list = comments;
        const open = commentLive.current;
        if (open) {
          const value = open.text.trim();
          list = comments.some(item => item.id === open.id)
            ? comments.map(item => (item.id === open.id ? { ...item, text: value } : item)).filter(item => item.text)
            : value ? [...comments, { id: open.id, x: open.x, y: open.y, text: value }] : comments;
          commitComment();
        }
        const kept = normalizedComments(list);
        if (!marked && !kept.length) { notify(t('annNothing'), true); return; }
        setBusy(true);
        notify('');
        try {
          if (!marked) {
            // Comments only: no layer file. A layer saved earlier for this
            // upload would still ride the next edit, so withdraw it.
            const earlier = annSaved.get(source.url);
            if (earlier) {
              await request(ANN_ROUTE, { method: 'POST', body: JSON.stringify({ action: 'withdraw', id: earlier.id }) });
              annSaved.delete(source.url);
              annNotify();
            }
            await deliverComments(kept);
            return;
          }
          let keys = ANN_SAVED_KEYS;
          if (mode === 'flatten' && swapActions()) {
            // A layer saved earlier for this upload must not also ride the
            // edit of the marked copy, so withdraw it before the swap.
            const earlier = annSaved.get(source.url);
            if (earlier) {
              await request(ANN_ROUTE, { method: 'POST', body: JSON.stringify({ action: 'withdraw', id: earlier.id }) });
              annSaved.delete(source.url);
              annNotify();
            }
            let url = null;
            swapping.current = true;
            try {
              url = await swapInComposer(await compositeFile(source, shapes), source, { shapes, comments: list });
              if (url) await openSource({ url, name: source.name, draftIndex: source.draftIndex });
            } catch {
              url = null;
            } finally {
              swapping.current = false;
            }
            if (url) {
              await deliverComments(kept, ['annReplaced', 'annReplacedCopy', 'annReplacedSilent']);
              return;
            }
            // Could not swap: the edit flattens the saved layer instead.
            keys = ['annFallback', 'annFallbackCopy', 'annFallbackSilent'];
          }
          const layer = document.createElement('canvas');
          layer.width = source.width;
          layer.height = source.height;
          drawShapes(layer.getContext('2d'), shapes);
          const layerData = await blobToDataUrl(await pngBlobOf(layer));
          // Bind the layer to this picture so it only goes with an edit of it.
          const [sha256, fingerprint] = await Promise.all([
            fileSha256(source.url),
            loadImageElement(source.url).then(annFingerprint, () => null),
          ]);
          const saved = await request(ANN_ROUTE, {
            method: 'POST',
            body: JSON.stringify({
              mode, layer: layerData, width: source.width, height: source.height,
              comments: kept, sourceName: source.name, sha256, fingerprint, upload: source.url,
            }),
          });
          annSaved.set(source.url, {
            id: saved.id, mode, sha256, savedAt: Date.now(), shapes, signature: JSON.stringify(kept),
          });
          watchUploads();
          annNotify();
          await deliverComments(kept, keys);
        } catch (failure) {
          notify(`${t('annFailed')}: ${failure && failure.message ? failure.message : ''}`, true);
        } finally {
          setBusy(false);
        }
      };

      const withdrawSave = async () => {
        const saved = source ? annSaved.get(source.url) : null;
        if (!saved || busy) return;
        setBusy(true);
        try {
          const result = await request(ANN_ROUTE, { method: 'POST', body: JSON.stringify({ action: 'withdraw', id: saved.id }) });
          annSaved.delete(source.url);
          if (result && result.consumedAt) {
            annRecent.unshift({ ...saved, status: 'sent', consumedAt: result.consumedAt });
            notify(t('annWithdrawUsed'), true);
          } else {
            notify(t('annWithdrawn'));
          }
          annNotify();
        } catch (failure) {
          notify(`${t('annFailed')}: ${failure && failure.message ? failure.message : ''}`, true);
        } finally {
          setBusy(false);
        }
      };

      const modeName = value => (value === 'markup' ? t('annModeMarkup') : t('annModeFlatten'));
      const savedEntry = source ? annSaved.get(source.url) : null;
      const savedStale = Boolean(savedEntry) && (savedEntry.shapes !== shapes || savedEntry.mode !== mode
        || savedEntry.signature !== JSON.stringify(normalizedComments(comments)));
      const savedStatus = savedEntry
        ? h('div', { className: `fmg-ann-status${savedStale ? ' is-stale' : ''}`, role: 'status', onPointerDown: event => { event.stopPropagation(); } },
          h('span', { className: 'fmg-ann-status-dot', 'aria-hidden': true }),
          h('span', { className: 'fmg-ann-status-text', title: savedStale ? t('annPendingStaleHint') : t('annPendingHint') },
            savedStale ? t('annPendingStale') : t('annPending', { mode: modeName(savedEntry.mode) })),
          h('button', { type: 'button', disabled: busy, onClick: () => { void withdrawSave(); } }, t('annWithdraw')))
        : null;
      const swappedEntry = source ? annReplaced.get(source.url) : null;
      const swappedStatus = swappedEntry
        ? h('div', { className: 'fmg-ann-status', role: 'status', onPointerDown: event => { event.stopPropagation(); } },
          h('span', { className: 'fmg-ann-status-dot', 'aria-hidden': true }),
          h('span', { className: 'fmg-ann-status-text', title: t('annSwappedHint') }, t('annSwapped')),
          h('button', { type: 'button', disabled: busy, onClick: () => { void restoreOriginal(); } }, t('annRestore')))
        : null;
      const recentText = !recent ? null
        : recent.status === 'sent'
          ? t('annRecentSent', { mode: modeName(recent.mode), time: new Date(recent.consumedAt).toLocaleTimeString() })
          : recent.status === 'released' ? t('annRecentReleased')
            : recent.status === 'expired' ? t('annRecentExpired')
              : t('annRecentWaiting', { mode: modeName(recent.mode) });

      // ---- controls -------------------------------------------------------
      const stopUi = event => { event.stopPropagation(); };

      const strokeFromPointer = event => {
        const rect = sliderRef.current.getBoundingClientRect();
        const rail = { top: rect.top + 16, height: Math.max(1, rect.height - 32) };
        const fromTop = Math.min(1, Math.max(0, (event.clientY - rail.top) / rail.height));
        setStroke(Math.round(ANN_STROKE_MIN + (1 - fromTop) * (ANN_STROKE_MAX - ANN_STROKE_MIN)));
      };
      const strokeRatio = (stroke - ANN_STROKE_MIN) / (ANN_STROKE_MAX - ANN_STROKE_MIN);

      // The slider captures its own pointer, so the knob follows the drag
      // anywhere on screen, not only while the pointer is over the rail.
      const slider = h('div', {
        ref: sliderRef,
        className: `fmg-ann-slider${sliding ? ' is-sliding' : ''}`,
        role: 'slider', tabIndex: 0, title: t('annStroke'), 'aria-label': t('annStroke'),
        'aria-valuemin': ANN_STROKE_MIN, 'aria-valuemax': ANN_STROKE_MAX, 'aria-valuenow': stroke,
        onPointerDown: event => {
          if (event.button !== 0) return;
          event.preventDefault();
          event.stopPropagation();
          try { event.currentTarget.setPointerCapture(event.pointerId); } catch { /* ignore */ }
          setSliding(true);
          strokeFromPointer(event);
        },
        onPointerMove: event => {
          event.stopPropagation();
          setHover(null);
          if (sliding) strokeFromPointer(event);
        },
        onPointerUp: event => { event.stopPropagation(); setSliding(false); },
        onLostPointerCapture: () => setSliding(false),
        onKeyDown: event => {
          const step = event.key === 'ArrowUp' || event.key === 'ArrowRight' ? 1
            : event.key === 'ArrowDown' || event.key === 'ArrowLeft' ? -1 : 0;
          if (!step) return;
          event.preventDefault();
          event.stopPropagation();
          setStroke(value => Math.min(ANN_STROKE_MAX, Math.max(ANN_STROKE_MIN, value + step)));
        },
      },
        h('span', { className: 'fmg-ann-track', 'aria-hidden': true }),
        h('span', {
          className: 'fmg-ann-knob',
          style: { top: `calc(16px + ${(1 - strokeRatio).toFixed(4)} * (100% - 32px))` },
        }, sliding
          ? h('span', { className: 'fmg-ann-bubble' },
            h('i', { style: { width: Math.min(stroke, 22), height: Math.min(stroke, 22), background: tool === 'eraser' ? '#fff' : color } }),
            `${stroke}px`)
          : null));

      const iconButton = (name, label, props) => h('button', {
        type: 'button', className: 'fmg-ann-ib', title: label, 'aria-label': label, ...props,
      }, annGlyph(ANN_ICONS[name], 18));

      const textButton = (name, label, props, className) => h('button', {
        type: 'button', className: className || 'fmg-ann-tb', ...props,
      }, annGlyph(ANN_ICONS[name]), h('span', null, label));

      const modeTitle = !capable ? t('annModeUnsupported')
        : mode === 'markup' ? t('annModeMarkupHint') : t('annModeFlattenHint');

      const mainBar = h('div', { className: 'fmg-ann-bar', role: 'toolbar', onPointerDown: stopUi },
        textButton('annotate', t('annAnnotate'), { onClick: () => { setMenu('draw'); setEditing(null); } }),
        textButton('comment', t('annAddComment'), { onClick: () => { setMenu('comment'); setSelected(null); } }),
        h('button', {
          type: 'button', className: 'fmg-ann-tb fmg-ann-mode', title: modeTitle,
          'aria-label': `${t('annModeFlatten')}/${t('annModeMarkup')}`,
          // Not `disabled`: a disabled button shows no tooltip, and the user
          // should learn why 分层 is unavailable.
          'aria-disabled': !capable,
          onClick: () => {
            if (!capable) { notify(t('annModeUnsupported'), true); return; }
            const next = mode === 'markup' ? 'flatten' : 'markup';
            setPreferredMode(next);
            storeAnnMode(next);
          },
        },
          annGlyph(ANN_ICONS.layers),
          h('span', { className: mode === 'flatten' ? 'is-on' : 'is-off' }, t('annModeFlatten')),
          h('span', { className: 'is-slash', 'aria-hidden': true }, '/'),
          h('span', { className: mode === 'markup' ? 'is-on' : 'is-off' }, t('annModeMarkup'))),
        h('span', { className: 'fmg-ann-sep', 'aria-hidden': true }),
        textButton('save', busy ? t('annExporting') : t('annExport'), {
          disabled: busy, onClick: () => { void exportAnnotation(); },
        }, 'fmg-ann-save'));

      const drawBar = h('div', { className: 'fmg-ann-bar', role: 'toolbar', onPointerDown: stopUi },
        ANN_DRAW_TOOLS.map(([name, labelKey]) => h('button', {
          key: name, type: 'button', className: 'fmg-ann-ib', title: t(labelKey), 'aria-label': t(labelKey),
          'aria-pressed': tool === name, onClick: () => { setTool(name); if (name !== 'select' && name !== 'text') setSelected(null); },
        }, annGlyph(ANN_ICONS[name], 18))),
        h('button', {
          type: 'button', className: 'fmg-ann-dot', title: t('annColor'), 'aria-label': t('annColor'),
          'aria-expanded': palette, onClick: () => setPalette(open => !open),
        }, h('i', { style: { background: color } })),
        iconButton('eraser', t('annToolEraser'), { 'aria-pressed': tool === 'eraser', onClick: () => { setTool('eraser'); setSelected(null); } }),
        h('span', { className: 'fmg-ann-sep', 'aria-hidden': true }),
        iconButton('undo', t('annUndo'), { disabled: !past.length, onClick: undo }),
        iconButton('redo', t('annRedo'), { disabled: !future.length, onClick: redoLast }),
        iconButton('trash', t('annClear'), {
          disabled: !shapes.length,
          onClick: () => { remember(); setShapes([]); setSelected(null); setTextEdit(null); },
        }),
        h('span', { className: 'fmg-ann-sep', 'aria-hidden': true }),
        iconButton('check', t('annDone'), { onClick: () => { if (textEdit) commitText(); setSelected(null); setMenu('main'); } }));

      const commentBar = h('div', { className: 'fmg-ann-bar', role: 'toolbar', onPointerDown: stopUi },
        h('span', { className: 'fmg-ann-prompt' }, annGlyph(ANN_ICONS.comment), t('annCommentHint')),
        textButton('check', t('annDone'), {
          onClick: () => { if (editing) commitComment(); setMenu('main'); },
        }, 'fmg-ann-save'));

      const paletteMenu = palette && menu === 'draw'
        ? h('div', { className: 'fmg-ann-palette', role: 'menu', onPointerDown: stopUi },
          ANN_COLORS.map(value => h('button', {
            key: value, type: 'button', 'aria-label': value, 'aria-pressed': value === color,
            style: { background: value },
            onClick: () => {
              setColor(value);
              setPalette(false);
              if (selected) {
                remember();
                setShapes(list => list.map(item => (item.id === selected ? { ...item, color: value } : item)));
              }
            },
          })))
        : null;

      // ---- overlays -------------------------------------------------------
      const overlays = [];
      if (source && frame) {
        const pinList = comments.map((comment, index) => ({ comment, n: index + 1 }));
        if (editing && editing.isNew) pinList.push({ comment: editing, n: comments.length + 1 });
        pinList.forEach(({ comment, n }) => {
          const at = toScreen(comment.x, comment.y);
          overlays.push(h('button', {
            // Near the right edge the badge flips to the tip's left so it stays visible.
            key: `pin-${comment.id}`, type: 'button',
            className: at.left + 26 > stageSize.width ? 'fmg-ann-pin is-flip' : 'fmg-ann-pin',
            'aria-pressed': Boolean(editing && editing.id === comment.id),
            'aria-label': `${t('annComment')} ${n}`, title: comment.text || undefined,
            style: { left: at.left, top: at.top },
            onPointerDown: stopUi,
            onClick: () => { if (!editing || editing.id !== comment.id) { if (editing) commitComment(); openComment(comment); } },
          }, String(n)));
        });

        if (editing) {
          // The pin's tip is at `at`; its 24px badge sits above-right of it.
          // Put the editor to the right of the badge, else to its left, else
          // below it, so the pin being edited is never covered.
          const at = toScreen(editing.x, editing.y);
          const width = Math.min(editing.isNew ? 280 : 300, stageSize.width - 16);
          const tall = editing.isNew ? 40 : 128;
          let left = at.left + 32;
          let top = at.top - 12;
          if (left + width > stageSize.width - 8) left = at.left - 8 - width;
          if (left < 8) {
            left = Math.max(8, Math.min(at.left - width / 2, stageSize.width - width - 8));
            top = at.top + 10 + tall / 2;
          }
          top = Math.max(60 + tall / 2, Math.min(top, stageSize.height - 8 - tall / 2));
          const update = event => {
            const value = event.target.value;
            setEditing(current => (current ? { ...current, text: value } : current));
          };
          const cancel = () => setEditing(null);
          overlays.push(editing.isNew
            ? h('div', { key: 'comment-editor', className: 'fmg-ann-pill', style: { left, top, width }, onPointerDown: stopUi },
              h('input', {
                autoFocus: true, value: editing.text, placeholder: t('annCommentPlaceholder'), 'aria-label': t('annComment'),
                onChange: update,
                onKeyDown: event => {
                  if (event.key === 'Enter' && !event.nativeEvent.isComposing) { event.preventDefault(); commitComment(); }
                  if (event.key === 'Escape') { event.preventDefault(); cancel(); }
                },
              }),
              h('button', {
                type: 'button', className: 'fmg-ann-send', title: t('annSend'), 'aria-label': t('annSend'),
                onClick: commitComment,
              }, annGlyph(ANN_ICONS.send, 16, 2.4)))
            : h('div', { key: 'comment-editor', className: 'fmg-ann-card', style: { left, top, width }, onPointerDown: stopUi },
              h('textarea', {
                autoFocus: true, value: editing.text, placeholder: t('annCommentPlaceholder'), 'aria-label': t('annComment'),
                onChange: update,
                onFocus: event => { const end = event.target.value.length; event.target.setSelectionRange(end, end); },
                onKeyDown: event => {
                  if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); commitComment(); }
                  if (event.key === 'Escape') { event.preventDefault(); cancel(); }
                },
              }),
              h('div', { className: 'fmg-ann-card-foot' },
                iconButton('trash', t('annRemoveComment'), { onClick: () => removeComment(editing.id) }),
                h('button', { type: 'button', className: 'fmg-ann-ghost', onClick: cancel }, t('annCancel')),
                h('button', { type: 'button', className: 'fmg-ann-confirm', onClick: commitComment }, t('annSaveComment')))));
        }

        const selectedShape = selected && !textEdit ? shapes.find(item => item.id === selected && item.type === 'text') : null;
        if (selectedShape) {
          const box = textBox(selectedShape);
          const pad = 6;
          const at = toScreen(box.x, box.y);
          overlays.push(h('div', {
            key: 'selection', className: 'fmg-ann-sel',
            style: {
              left: at.left - pad, top: at.top - pad,
              width: box.width * scale + pad * 2, height: box.height * scale + pad * 2,
              transform: selectedShape.rotation ? `rotate(${selectedShape.rotation}deg)` : undefined,
            },
            onPointerDown: startHandle('move', selectedShape),
            onDoubleClick: event => { event.stopPropagation(); beginTextEdit(selectedShape); },
          },
            h('span', {
              className: 'fmg-ann-handle is-rotate', title: t('annRotate'), 'aria-label': t('annRotate'), role: 'button',
              onPointerDown: startHandle('rotate', selectedShape),
            }, annGlyph(ANN_ICONS.rotate, 13, 2.6)),
            h('button', {
              type: 'button', className: 'fmg-ann-handle is-del', title: t('annDeleteText'), 'aria-label': t('annDeleteText'),
              onPointerDown: stopUi,
              onClick: () => { remember(); setShapes(list => list.filter(item => item.id !== selectedShape.id)); setSelected(null); },
            }, annGlyph(ANN_ICONS.close, 13, 2.6)),
            h('span', {
              className: 'fmg-ann-handle is-size', title: t('annResize'), 'aria-label': t('annResize'), role: 'button',
              onPointerDown: startHandle('resize', selectedShape),
            }, annGlyph(ANN_ICONS.resize, 13, 2.6)),
            h('span', {
              className: 'fmg-ann-handle is-move', title: t('annMove'), 'aria-label': t('annMove'), role: 'button',
              onPointerDown: startHandle('move', selectedShape),
            }, annGlyph(ANN_ICONS.move, 13, 2.6))));
        }

        if (textEdit) {
          const measured = measureLines(textEdit.value || t('annTextPrompt'), textEdit.size);
          const at = toScreen(textEdit.x, textEdit.y);
          overlays.push(h('textarea', {
            key: `text-${textEdit.id}`, className: 'fmg-ann-textin',
            autoFocus: true, value: textEdit.value, placeholder: t('annTextPrompt'), 'aria-label': t('annTextPrompt'),
            spellCheck: false, wrap: 'off',
            style: {
              left: at.left, top: at.top,
              width: measured.width * scale + 6, height: measured.height * scale,
              fontSize: textEdit.size * scale, fontFamily: ANN_FONT, color: textEdit.color,
              transform: textEdit.rotation ? `rotate(${textEdit.rotation}deg)` : undefined,
              transformOrigin: 'center center',
            },
            onPointerDown: stopUi,
            onFocus: event => { const end = event.target.value.length; event.target.setSelectionRange(end, end); },
            onChange: event => {
              const value = event.target.value;
              setTextEdit(current => (current ? { ...current, value } : current));
            },
            onKeyDown: event => {
              if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); commitText(); }
              if (event.key === 'Escape') { event.preventDefault(); setTextEdit(null); }
            },
            onBlur: () => commitText(),
          }));
        }

        const overImage = hover && hover.x >= frame.left && hover.x <= frame.left + frame.width
          && hover.y >= frame.top && hover.y <= frame.top + frame.height;
        if (menu === 'draw' && ANN_PIP_TOOLS.includes(tool) && overImage && !spaceHeld && !panning && !sliding && !textEdit) {
          overlays.push(h('div', {
            key: 'pip', className: 'fmg-ann-pip',
            style: {
              left: hover.x, top: hover.y, width: Math.max(4, stroke), height: Math.max(4, stroke),
              background: tool === 'eraser' ? 'rgba(255,255,255,.35)' : `${color}55`,
            },
          }));
        }
      }

      const drawCursor = () => {
        if (panning) return 'grabbing';
        if (spaceHeld || menu === 'main') return 'grab';
        if (menu === 'comment') return 'crosshair';
        if (tool === 'text') return 'text';
        if (tool === 'select') return 'default';
        if (ANN_PIP_TOOLS.includes(tool)) return 'none';
        return 'crosshair';
      };

      const hiddenText = textEdit ? textEdit.id : null;
      const stage = source && frame
        ? h('div', {
          ref: stageRef,
          className: 'fmg-ann-stage',
          style: { cursor: drawCursor() },
          onPointerDown, onPointerMove, onPointerUp,
          // Stop the browser from moving focus or selecting text on a canvas
          // press; editors would otherwise blur the instant they open.
          onMouseDown: event => {
            if (!(event.target instanceof Element && event.target.closest('input,textarea'))) event.preventDefault();
          },
          onPointerCancel: onPointerUp,
          onLostPointerCapture: onPointerUp,
          onPointerLeave: () => setHover(null),
          onDoubleClick,
          onContextMenu: event => event.preventDefault(),
        },
          h('div', {
            className: 'fmg-ann-frame',
            style: { left: frame.left, top: frame.top, width: frame.width, height: frame.height },
          },
            h('img', { src: source.url, alt: source.name, draggable: false }),
            h('svg', {
              className: 'fmg-ann-svg', viewBox: `0 0 ${source.width} ${source.height}`, preserveAspectRatio: 'none',
            },
              // One mask per eraser stroke, wrapped around only the shapes
              // drawn before it, so later strokes paint over erased spots.
              h('defs', null,
                shapes.filter(shape => shape.type === 'erase').map(shape => h('mask', {
                  key: shape.id, id: `fmg-ann-erase-${shape.id}`, maskUnits: 'userSpaceOnUse',
                  x: 0, y: 0, width: source.width, height: source.height,
                },
                  h('rect', { x: 0, y: 0, width: source.width, height: source.height, fill: '#fff' }),
                  h('polyline', {
                    points: polyPoints(shape.points.length === 1
                      ? [shape.points[0], { x: shape.points[0].x + 0.01, y: shape.points[0].y }]
                      : shape.points),
                    fill: 'none', stroke: '#000', strokeWidth: shape.width, strokeLinecap: 'round', strokeLinejoin: 'round',
                  })))),
              shapes.reduce((painted, shape) => {
                if (shape.type === 'erase') {
                  return painted.length ? [h('g', { key: `erase-${shape.id}`, mask: `url(#fmg-ann-erase-${shape.id})` }, painted)] : painted;
                }
                return shape.id === hiddenText ? painted : [...painted, renderShape(shape)];
              }, []))),
          h('div', { className: 'fmg-ann-layer' }, overlays),
          menu === 'draw' && ANN_STROKE_TOOLS.includes(tool) ? slider : null,
          swappedStatus || savedStatus,
          h('div', { className: 'fmg-ann-zoom', onPointerDown: stopUi },
            iconButton('minus', t('annZoomOut'), { onClick: () => zoomTo(zoom => zoom / 1.25) }),
            h('button', {
              type: 'button', className: 'fmg-ann-zoom-val', title: t('annZoomFit'),
              onClick: () => setView({ zoom: 1, x: 0, y: 0 }),
            }, `${Math.round(scale * 100)}%`),
            iconButton('plus', t('annZoomIn'), { onClick: () => zoomTo(zoom => zoom * 1.25) })))
        : h('div', { ref: stageRef, className: 'fmg-ann-stage' },
          h('div', { className: 'fmg-ann-empty' },
            h('p', null, t('annEmpty')),
            recentText ? h('p', { className: 'fmg-ann-recent', role: 'status' }, recentText) : null,
            drafts.length
              ? h('div', { className: 'fmg-ann-thumbs' }, drafts.map(item => h('button', {
                key: item.index, type: 'button', title: item.name,
                onClick: () => { void openSource({ ...item, draftIndex: item.index }); },
              }, h('img', { src: item.url, alt: item.name }))))
              : null));

      const acceptsDrop = event => {
        const types = event.dataTransfer ? Array.from(event.dataTransfer.types || []) : [];
        return types.includes('text/uri-list') || types.includes('text/plain');
      };

      const bar = !source ? null : menu === 'draw' ? drawBar : menu === 'comment' ? commentBar : mainBar;

      return h('section', {
        ref: rootRef,
        className: 'fmg-ann',
        tabIndex: -1,
        'aria-label': t('annTitle'),
        onKeyDown,
        onKeyUp: event => { if (event.code === 'Space') setSpaceHeld(false); },
        onDragOver: event => {
          if (!acceptsDrop(event)) return;
          event.preventDefault();
          event.stopPropagation();
          setDragging(true);
        },
        onDragLeave: () => setDragging(false),
        onDrop: async event => {
          if (!acceptsDrop(event)) return;
          event.preventDefault();
          event.stopPropagation();
          setDragging(false);
          const uri = (event.dataTransfer.getData('text/uri-list') || event.dataTransfer.getData('text/plain') || '')
            .split(/\r?\n/).map(line => line.trim()).find(line => line && !line.startsWith('#'));
          if (!uri) { notify(t('annDropUnsupported'), true); return; }
          const known = composerImages().find(item => item.url === uri);
          if (!known) { notify(t('annNeedsComposerImage'), true); return; }
          await openSource({ ...known, draftIndex: known.index });
        },
      },
        bar,
        paletteMenu,
        stage,
        toast ? h('div', { className: `fmg-ann-toast${toast.error ? ' is-error' : ''}`, role: toast.error ? 'alert' : 'status' }, toast.text) : null,
        dragging ? h('div', { className: 'fmg-ann-drop' }, t('annDropHere')) : null);
    }

    // Composer pill: reads the same config route as the settings page and writes
    // only `default_provider`, so the two surfaces cannot disagree.
    let pillText = key => key;
    const portal = node => (createPortal && typeof document !== 'undefined' ? createPortal(node, document.body) : node);
    function ProviderPill() {
      const [view, setView] = useState(null);
      const [open, setOpen] = useState(false);
      const [pending, setPending] = useState(false);
      const [error, setError] = useState('');
      const [menu, setMenu] = useState(null);
      const buttonRef = useRef(null);
      const menuRef = useRef(null);
      const load = useCallback(async () => {
        try { setView(await request(ROUTE)); }
        catch (e) { setError(e.message); }
      }, []);
      useEffect(() => { void load(); }, [load]);
      useEffect(() => {
        if (!open) return undefined;
        const place = () => {
          const rect = buttonRef.current && buttonRef.current.getBoundingClientRect();
          if (!rect) return;
          // Same placement as the host's model picker: above the pill, right
          // edges aligned, 8px gap, 12px from the window edge; below the pill
          // only when the menu does not fit above it.
          const width = menuRef.current ? menuRef.current.offsetWidth : Math.min(350, window.innerWidth - 24);
          const left = Math.max(12, Math.min(rect.right - width, window.innerWidth - 12 - width));
          const height = menuRef.current ? menuRef.current.offsetHeight : 360;
          setMenu(rect.top - 8 - 12 >= height || rect.top > window.innerHeight - rect.bottom
            ? { left, bottom: window.innerHeight - rect.top + 8 }
            : { left, top: rect.bottom + 8 });
        };
        place();
        // Measured again once the list has rendered, so the flip uses its real height.
        const frame = requestAnimationFrame(place);
        const onDown = event => {
          if (buttonRef.current && buttonRef.current.contains(event.target)) return;
          if (event.target instanceof Element && event.target.closest('.fmg-pill-menu')) return;
          setOpen(false);
        };
        const onKey = event => { if (event.key === 'Escape') setOpen(false); };
        document.addEventListener('mousedown', onDown, true);
        document.addEventListener('keydown', onKey, true);
        window.addEventListener('resize', place);
        window.addEventListener('scroll', place, true);
        return () => {
          cancelAnimationFrame(frame);
          document.removeEventListener('mousedown', onDown, true);
          document.removeEventListener('keydown', onKey, true);
          window.removeEventListener('resize', place);
          window.removeEventListener('scroll', place, true);
        };
      }, [open]);

      const t = pillText;
      // Re-read on open so a settings edit made in another surface is visible.
      const toggle = () => { if (!open) void load(); setOpen(!open); };
      const select = async name => {
        if (pending) return;
        if (view && name === view.default_provider) { setOpen(false); return; }
        setPending(true); setError('');
        try {
          const next = await request(ROUTE, { method: 'PUT', body: JSON.stringify({ revision: view.revision, changes: { default_provider: name } }) });
          setView(next); setOpen(false);
        } catch (e) {
          setError(e.message);
          if (e.status === 409) void load();
        } finally { setPending(false); }
      };

      if (!view || view.show_provider_pill === false) return null;
      const enabled = view.providers.filter(provider => provider.enabled);
      if (!enabled.length) return null;
      const current = enabled.find(provider => provider.name === view.default_provider) || enabled[0];
      return h('div', { className: 'fmg-pill' },
        h('button', {
          type: 'button', ref: buttonRef, className: `fmg-pill-button${open ? ' is-open' : ''}`,
          'aria-haspopup': 'menu', 'aria-expanded': open, title: t('pillTitle'), onClick: toggle,
        },
          pillIcon(),
          h('span', { className: 'fmg-pill-label' }, current.name),
          h('span', { className: 'fmg-pill-caret', 'aria-hidden': true },
            h('svg', { width: 10, height: 10, viewBox: '0 0 16 16', fill: 'none', stroke: 'currentColor', strokeWidth: 2, strokeLinecap: 'round', strokeLinejoin: 'round' },
              h('path', { d: 'M4 10l4-4 4 4' })))),
        open && menu ? portal(h('div', { ref: menuRef, className: 'fmg-pill-menu', role: 'menu', 'aria-label': t('pillTitle'), style: menu },
          h('div', { className: 'fmg-pill-list' },
            enabled.map(provider => {
              const active = provider.name === current.name;
              const ready = Boolean(provider.api_key_set || provider.api_key_env);
              return h('button', {
                key: provider.name, type: 'button', role: 'menuitemradio', 'aria-checked': active,
                className: 'fmg-pill-option', disabled: pending,
                title: `${provider.model || '—'} · ${provider.name}${ready ? '' : ` · ${t('pillKeyMissing')}`}`,
                onClick: () => { void select(provider.name); },
              },
                h('span', { className: 'fmg-pill-text' },
                  provider.model || '—',
                  h('span', { className: 'fmg-pill-sep', 'aria-hidden': true }, '·'),
                  h('span', { className: 'fmg-pill-provider' }, provider.name)),
                h('span', { className: 'fmg-pill-check', 'aria-hidden': true }, active
                  ? h('svg', {
                    width: 16, height: 16, viewBox: '0 0 16 16', fill: 'none', stroke: 'currentColor',
                    strokeWidth: 2.2, strokeLinecap: 'round', strokeLinejoin: 'round',
                  }, h('path', { d: 'M2.5 8.5l3.5 3.5 7-8' }))
                  : null));
            })),
          error ? h('div', { className: 'fmg-pill-error', role: 'alert' }, `${t('pillSwitchFailed')}: ${error}`) : null)) : null);
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
        // From load on, not only after a first save: any picture may be edited.
        ctx.effect(() => watchOriginals(), 'fmage: original sidecar');
        const t = ctx.locale.bind(NS);
        pillText = t;
        annText = t;
        ctx.slots.inject('settings.section', () => ctx.slots.register({
          name: 'settings.section', id: 'fmage', order: 18, label: () => t('nav'), locale: NS,
        }, FmageSection));
        // Official composer seat beside the model picker; a host without the
        // conversation UI never declares this slot, so the pill simply never mounts.
        ctx.slots.inject('conversation.input.right', () => ctx.slots.register({
          name: 'conversation.input.right', id: 'fmage-provider', order: 20, label: () => t('pill'),
        }, ProviderPill));
        // Deferred so a host without the right sidebar keeps the rest of the
        // plugin. The tab type carries a guide entry, so the sidebar's start
        // page lists the workbench as a card instead of a composer button.
        if (typeof ctx.inject !== 'function') return;
        ctx.inject(['sidebarRightTabs'], owner => {
          const tabs = owner.get('sidebarRightTabs');
          if (!tabs || typeof tabs.register !== 'function') return;
          owner.effect(() => tabs.register({
            id: ANN_TAB_ID,
            kind: ANN_KIND,
            priority: 'extension',
            title: () => t('annTitle'),
            // Title only: the card reads as a tool name, not a feature blurb.
            guide: [{ id: 'annotation', order: 90, title: () => t('annTitle') }],
          }), 'fmage: annotation tab type');
          owner.slots.inject('sidebar.right.pane.tab', () => owner.slots.register({
            name: 'sidebar.right.pane.tab',
            key: ANN_TAB_ID,
          }, AnnotationPanel));
        });
      },
    };
  },
});
