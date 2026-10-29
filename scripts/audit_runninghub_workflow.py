#!/usr/bin/env python3
"""审计一个 RunningHub 工作流与 Fmage 工作流路由（workflow_image）的接洽程度。

它回答三个问题：
1. 这个工作流能不能按 Fmage 现有契约接进来（workflow_id + node_id/field_name 映射 + output_node_ids）；
2. 哪些节点字段是真正值得暴露成输入口的（图片、提示词、采样参数、模型/风格枚举、开关），
   哪些是噪声（连线值、固定模型路径、被 bypass 的中间节点、纯 UI 节点）；
3. 有多少硬阻塞（视频/音频输出、加密工作流、AI App 表单包装、非图片输入）。

用法：
  # 用 providers.json 里的 RunningHub 连接直接拉工作流 JSON
  python scripts/audit_runninghub_workflow.py --workflow-id 2115157784023416834

  # 已经拿到 JSON（页面「API 调用」导出的 workflowJson / prompt 都行）
  python scripts/audit_runninghub_workflow.py --workflow-id 2115157784023416834 \
      --json C:\\path\\workflow.json

  # 额外把建议的 inputs 片段写出来
  python scripts/audit_runninghub_workflow.py --workflow-id ... --emit-mapping out.json

不打印任何密钥。只读 providers.json。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------- 常量：Fmage v1 能力边界

IMAGE_CLASSES = (
    "LoadImage",
    "LoadImageMask",
    "LoadImageOutput",
    "ETN_LoadImageBase64",
    "ImageLoad",
    "VHS_LoadImagePath",
)
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff")

VIDEO_OUT_CLASSES = ("VHS_VideoCombine", "SaveVideo", "SaveAnimatedWEBP", "CreateVideo", "SaveWEBM")
AUDIO_OUT_CLASSES = ("SaveAudio", "PreviewAudio", "SaveAudioMP3", "SaveAudioOpus")
IMAGE_OUT_CLASSES = ("SaveImage", "PreviewImage", "Image Save")

# Fmage v1：输入只收 PNG/JPEG/WebP 图片，输出只收 PNG/JPEG/WebP 图片。
V1_BLOCKERS = {
    "video_output": "工作流输出是视频/动画 → 当前 workflow 路由 v1 只收图片，需要新增视频下载与 result 类型",
    "audio_output": "工作流输出是音频 → v1 不支持",
    "encrypted": "工作流为加密/会员工作流 → v1 不支持加密工作流密码",
}

# 高价值字段的启发式：命中即视为值得暴露的输入口
PROMPT_HINTS = ("text", "prompt", "positive", "negative", "string", "caption")
SEED_HINTS = ("seed", "noise_seed", "rand")
RES_HINTS = ("width", "height", "resolution", "megapixel", "size", "scale", "batch_size")
SAMPLER_HINTS = ("steps", "cfg", "denoise", "sampler_name", "scheduler", "guidance", "strength")
MODEL_HINTS = ("ckpt_name", "unet_name", "lora_name", "vae_name", "model", "style", "preset")


def user_home() -> Path:
    return Path(os.environ.get("USERPROFILE") or os.path.expanduser("~"))


def config_path() -> Path:
    """与 Fmage 相同的解析顺序：FMAGE_CONFIG → CODEX_HOME/fmage → ~/.codex/fmage。"""
    explicit = os.environ.get("FMAGE_CONFIG")
    if explicit:
        return Path(explicit)
    codex_home = os.environ.get("CODEX_HOME")
    base = Path(codex_home) if codex_home else user_home() / ".codex"
    return base / "fmage" / "providers.json"


def load_connection(name: str = "runninghub") -> tuple[str, str]:
    path = config_path()
    if not path.exists():
        raise SystemExit(f"找不到 {path}；用 --json 手工提供工作流 JSON，或先配置 workflow_connections")
    data = json.loads(path.read_text(encoding="utf-8"))
    conn = (data.get("workflow_connections") or {}).get(name) or {}
    base_url = str(conn.get("base_url") or "https://www.runninghub.ai").rstrip("/")
    api_key = str(conn.get("api_key") or "")
    if not api_key:
        env_name = conn.get("api_key_env")
        if isinstance(env_name, str) and env_name:
            api_key = os.environ.get(env_name, "")
    if not api_key:
        raise SystemExit("providers.json 里没有 RunningHub 密钥；可改用 --json 分析已导出的工作流 JSON")
    return base_url, api_key


def post_json(url: str, payload: dict[str, Any], host_header: str) -> Any:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Host": host_header,
        },
    )
    with urllib.request.urlopen(req, timeout=40) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_workflow_json(base_url: str, api_key: str, workflow_id: str) -> dict[str, Any]:
    host = urllib.parse.urlparse(base_url).netloc
    # RunningHub OpenAPI: POST /task/openapi/getJson 返回工作流 prompt/JSON
    candidates = [
        (f"{base_url}/task/openapi/getJson", {"apiKey": api_key, "workflowId": workflow_id}),
        (f"{base_url}/api/openapi/getJson", {"apiKey": api_key, "workflowId": workflow_id}),
    ]
    last: str = ""
    for url, payload in candidates:
        try:
            data = post_json(url, payload, host)
        except urllib.error.HTTPError as exc:  # noqa: PERF203 - 需要逐个降级
            last = f"{url} -> HTTP {exc.code}"
            continue
        except Exception as exc:  # noqa: BLE001 - 网络层统一降级
            last = f"{url} -> {exc}"
            continue
        code = data.get("code") if isinstance(data, dict) else None
        if code in (0, "0", 200, None) and isinstance(data, dict):
            return data
        last = f"{url} -> {json.dumps(data, ensure_ascii=False)[:200]}"
    raise SystemExit(
        "拉取工作流 JSON 失败（%s）。\n"
        "退路：在 RunningHub 页面点「API 调用」，把返回里的 workflowJson / prompt 存成文件，"
        "再用 --json 传给本脚本。" % last
    )


def unwrap(raw: Any) -> dict[str, Any]:
    """把各家返回形状压平成 {class_type 节点}，兼容 API 格式与 UI 格式。"""
    node: Any = raw
    for _ in range(4):
        if not isinstance(node, dict):
            return {}
        if "prompt" in node and isinstance(node["prompt"], (dict, str)):
            node = node["prompt"]
            continue
        if "workflowJson" in node:
            node = node["workflowJson"]
            continue
        if "data" in node and isinstance(node["data"], (dict, str)):
            node = node["data"]
            continue
        if "workflow" in node and isinstance(node["workflow"], (dict, str)):
            node = node["workflow"]
            continue
        break
    if isinstance(node, str):
        node = json.loads(node)

    if isinstance(node, dict) and "nodes" in node and isinstance(node["nodes"], list):
        return to_api_format(node)

    if isinstance(node, dict):
        # 已是 API 格式：{id: {class_type, inputs}}
        if all(isinstance(v, dict) and "class_type" in v for v in node.values() if str(v).startswith("{")):
            return {str(k): v for k, v in node.items() if isinstance(v, dict) and "class_type" in v}
    return {}


def to_api_format(ui: dict[str, Any]) -> dict[str, Any]:
    """UI 格式（nodes/links/widgets_values）→ API 格式的最小可用投影。"""
    out: dict[str, Any] = {}
    for n in ui.get("nodes", []):
        nid = str(n.get("id"))
        cls = str(n.get("type") or "")
        widgets = n.get("widgets_values")
        inputs: dict[str, Any] = {}
        if isinstance(widgets, list):
            for idx, value in enumerate(widgets):
                inputs[f"widget_{idx}"] = value
        elif isinstance(widgets, dict):
            inputs.update(widgets)
        out[nid] = {
            "class_type": cls,
            "inputs": inputs,
            "_meta": {"title": n.get("title") or cls, "mode": n.get("mode", 0)},
        }
    return out


def classify_field(name: str, value: Any, class_type: str) -> str:
    low = name.lower()
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        if any(h in low for h in SEED_HINTS):
            return "seed"
        if any(h in low for h in RES_HINTS):
            return "resolution"
        if any(h in low for h in SAMPLER_HINTS):
            return "sampler"
        return "number"
    if isinstance(value, str):
        if value.lower().endswith(IMAGE_EXTS):
            return "image"
        if any(h in low for h in PROMPT_HINTS) and (len(value) > 12 or "prompt" in low):
            return "prompt"
        if any(h in low for h in MODEL_HINTS):
            return "model"
        if any(h in low for h in SAMPLER_HINTS):
            return "sampler"
        return "string"
    if isinstance(value, list) and len(value) == 2 and all(isinstance(x, (int, str)) for x in value):
        return "link"
    return "other"


def audit(nodes: dict[str, Any]) -> dict[str, Any]:
    total_nodes = len(nodes)
    bypassed = 0
    slots: list[dict[str, Any]] = []
    outputs_image: list[str] = []
    outputs_video: list[str] = []
    outputs_audio: list[str] = []

    for nid, node in nodes.items():
        cls = str(node.get("class_type") or "")
        title = str((node.get("_meta") or {}).get("title") or cls)
        if (node.get("_meta") or {}).get("mode") in (2, 4):  # muted / bypassed
            bypassed += 1
            continue
        if cls in IMAGE_OUT_CLASSES:
            outputs_image.append(nid)
        if cls in VIDEO_OUT_CLASSES:
            outputs_video.append(nid)
        if cls in AUDIO_OUT_CLASSES:
            outputs_audio.append(nid)

        inputs = node.get("inputs") or {}
        for field, value in inputs.items():
            kind = classify_field(str(field), value, cls)
            if kind == "link":
                continue
            if kind == "image":
                # 只有 LoadImage 类节点的 image 字段才是可替换的上传口
                if cls in IMAGE_CLASSES and str(field).lower() in ("image", "image_path", "path", "filename"):
                    slots.append({"node_id": nid, "class_type": cls, "title": title,
                                  "field_name": field, "kind": "image_input", "current": value})
                continue
            slots.append({"node_id": nid, "class_type": cls, "title": title,
                          "field_name": field, "kind": kind, "current": value})

    # 排序：高价值在前
    order = {"image_input": 0, "prompt": 1, "model": 2, "resolution": 3, "sampler": 4,
             "seed": 5, "boolean": 6, "number": 7, "string": 8, "other": 9}
    slots.sort(key=lambda s: (order.get(s["kind"], 9), s["node_id"]))

    valuable = [s for s in slots if s["kind"] in ("image_input", "prompt", "model", "resolution", "sampler", "seed", "boolean")]
    noise = [s for s in slots if s["kind"] in ("string", "other", "number")]

    blockers: list[str] = []
    if outputs_video:
        blockers.append(V1_BLOCKERS["video_output"])
    if outputs_audio:
        blockers.append(V1_BLOCKERS["audio_output"])
    if not outputs_image and (outputs_video or outputs_audio):
        blockers.append("没有任何图片输出节点")

    verdict = "direct" if outputs_image and valuable else ("blocked" if blockers else "needs_adapter")

    return {
        "total_nodes": total_nodes,
        "bypassed_nodes": bypassed,
        "slots_total": len(slots),
        "slots_valuable": valuable,
        "slots_noise_count": len(noise),
        "outputs_image": outputs_image,
        "outputs_video": outputs_video,
        "outputs_audio": outputs_audio,
        "blockers": blockers,
        "verdict": verdict,
    }


def suggest_inputs(report: dict[str, Any]) -> dict[str, Any]:
    """生成可直接粘进 providers.json 的 inputs 片段。"""
    inputs: dict[str, Any] = {}
    image_idx = 0
    used: set[str] = set()

    def key_for(base: str) -> str:
        key, n = base, 2
        while key in used:
            key = f"{base}_{n}"
            n += 1
        used.add(key)
        return key

    for slot in report["slots_valuable"]:
        kind = slot["kind"]
        if kind == "image_input":
            image_idx += 1
            key = "image" if image_idx == 1 else f"image{image_idx}"
            inputs[key] = {
                "node_id": slot["node_id"],
                "field_name": slot["field_name"],
                "type": "image",
                "required": image_idx == 1,
                "description": f"{slot['title']} 的输入图",
            }
        elif kind == "prompt":
            key = key_for("prompt" if "neg" not in str(slot["field_name"]).lower() else "negative_prompt")
            inputs[key] = {
                "node_id": slot["node_id"],
                "field_name": slot["field_name"],
                "type": "text",
                "description": f"{slot['title']}：保持用户原文，不做扩写。",
            }
        elif kind == "seed":
            inputs[key_for("seed")] = {
                "node_id": slot["node_id"], "field_name": slot["field_name"],
                "type": "number", "description": "随机种子；不指定时用配置默认值。",
            }
        elif kind == "resolution":
            inputs[key_for(str(slot["field_name"]))] = {
                "node_id": slot["node_id"], "field_name": slot["field_name"],
                "type": "number", "description": f"{slot['title']} 的尺寸档位。",
            }
        elif kind == "sampler":
            inputs[key_for(str(slot["field_name"]))] = {
                "node_id": slot["node_id"], "field_name": slot["field_name"],
                "type": "number" if isinstance(slot["current"], (int, float)) else "text",
                "description": f"{slot['title']} 的采样参数。",
            }
        elif kind == "model":
            inputs[key_for(str(slot["field_name"]))] = {
                "node_id": slot["node_id"], "field_name": slot["field_name"],
                "type": "text", "description": f"{slot['title']} 的模型/风格枚举。",
            }
        elif kind == "boolean":
            inputs[key_for(str(slot["field_name"]))] = {
                "node_id": slot["node_id"], "field_name": slot["field_name"],
                "type": "boolean", "description": f"{slot['title']} 开关。",
            }
    return inputs


def main() -> int:
    ap = argparse.ArgumentParser(description="审计 RunningHub 工作流与 Fmage workflow 路由的接洽度")
    ap.add_argument("--workflow-id", required=True, help="RunningHub 工作流 ID（URL 最后那段数字）")
    ap.add_argument("--json", help="已导出的工作流 JSON 文件；给了就不联网")
    ap.add_argument("--emit-mapping", help="把建议的 providers.json inputs 片段写到该文件")
    args = ap.parse_args()

    if args.json:
        raw = json.loads(Path(args.json).read_text(encoding="utf-8"))
    else:
        base_url, api_key = load_connection()
        raw = fetch_workflow_json(base_url, api_key, args.workflow_id)

    nodes = unwrap(raw)
    if not nodes:
        print("无法从返回里解析出节点表。原始返回片段：")
        print(json.dumps(raw, ensure_ascii=False)[:800])
        return 2

    report = audit(nodes)
    inputs = suggest_inputs(report)

    print("=" * 70)
    print(f"工作流 {args.workflow_id}")
    print("=" * 70)
    print(f"节点总数        : {report['total_nodes']}（其中 bypass/mute {report['bypassed_nodes']}）")
    print(f"可覆盖字段总数  : {report['slots_total']}")
    print(f"值得做输入口    : {len(report['slots_valuable'])}")
    print(f"噪声字段        : {report['slots_noise_count']}")
    print(f"图片输出节点    : {report['outputs_image'] or '无'}")
    print(f"视频输出节点    : {report['outputs_video'] or '无'}")
    print(f"音频输出节点    : {report['outputs_audio'] or '无'}")
    print()

    print("-- 值得暴露的输入口 " + "-" * 50)
    for s in report["slots_valuable"]:
        cur = str(s["current"])
        cur = cur[:40] + "…" if len(cur) > 40 else cur
        print(f"[{s['kind']:<11}] node {s['node_id']:<4} {s['class_type']:<22} {s['field_name']:<14} = {cur}")
    print()

    verdict_text = {
        "direct": "可直接接洽：有图片输出 + 至少一个高价值输入，按下面的 inputs 片段配置即可跑",
        "needs_adapter": "需要适配：节点表可读，但输入/输出形态超出 v1（或没有明确的图片输出）",
        "blocked": "硬阻塞：输出形态不是图片，需要扩展 workflow 路由",
    }[report["verdict"]]
    print(f"结论：{verdict_text}")
    for b in report["blockers"]:
        print(f"  - {b}")
    print()

    print("-- 建议 inputs 片段 " + "-" * 50)
    print(json.dumps(inputs, ensure_ascii=False, indent=2))
    print()
    print("-- 建议 output_node_ids --")
    print(json.dumps(report["outputs_image"] or ["<图片输出节点 ID>"], ensure_ascii=False))

    if args.emit_mapping:
        Path(args.emit_mapping).write_text(json.dumps(inputs, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n已写出 {args.emit_mapping}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
