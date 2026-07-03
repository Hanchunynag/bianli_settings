#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FastAPI 设置导航录制 Web 控制台。"""

import argparse
import hashlib
import json
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

try:
    from fastapi import FastAPI
    from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
    from fastapi.staticfiles import StaticFiles
    from pydantic import BaseModel
    import uvicorn
except ImportError as exc:  # pragma: no cover - 给用户明确安装提示
    raise SystemExit("缺少 FastAPI 依赖，请先执行：pip install fastapi uvicorn") from exc

from settings_ui_manual_recorder import (
    DEFAULT_DEVICE_ID,
    DEFAULT_WORK_DIR,
    active_navigation_state,
    add_transition,
    annotate,
    auto_complete_pending_if_needed,
    build_navigation_state,
    build_semantic_target_from_node,
    capture_artifacts,
    capture_ui_tree_only,
    detect_overlay_title,
    extract_navigation_candidates,
    hit_test_full_ui_tree,
    horizontal_target,
    load_json,
    load_navigation_graph,
    navigation_graph_path,
    next_horizontal_view_state,
    node_semantic_summary,
    now_iso,
    pending_transition_path,
    save_current_path_session,
    save_json,
    save_navigation_graph,
    screen_metrics_from_root,
    walk,
    transition_id,
)

APP_DIR = Path(__file__).resolve().parent
app = FastAPI(title="Settings Navigation Recorder")


class ServerConfig:
    work_dir: Path = DEFAULT_WORK_DIR
    device_id: str = DEFAULT_DEVICE_ID
    output_dir: Path = DEFAULT_WORK_DIR / "outputs" / "latest"


config = ServerConfig()


class TapCandidateRequest(BaseModel):
    index: int
    expect: str = "new_page"
    effect: str = ""
    manual_label: str = ""


class TapPointRequest(BaseModel):
    x: int
    y: int
    expect: str = "new_page"
    effect: str = ""
    manual_label: str = ""


class TapSamePageOperationRequest(BaseModel):
    x: int
    y: int
    manual_label: str = ""


class PageGestureOperationRequest(BaseModel):
    x: int
    y: int
    operate: str
    effect: str = ""
    manual_label: str = ""


class SwipeHorizontalRequest(BaseModel):
    direction: str


class NavigateToPageRequest(BaseModel):
    page_name: str


class SetActivePageRequest(BaseModel):
    page_name: str


class RenamePageRequest(BaseModel):
    old_page_name: str
    new_page_name: str
    new_title: str = ""


class ConsoleActionRequest(BaseModel):
    action: str
    payload: Optional[Dict[str, Any]] = None


class RecordActionRequest(BaseModel):
    action: str
    payload: Optional[Dict[str, Any]] = None


class DeleteActionRequest(BaseModel):
    target_type: str
    payload: Optional[Dict[str, Any]] = None
    dry_run: bool = True


class DeleteBranchRequest(BaseModel):
    transition_id: str
    delete_descendants: bool = True
    dry_run: bool = True


class DeleteTransitionRequest(BaseModel):
    transition_id: str
    delete_orphan_to_state: bool = True
    dry_run: bool = True


class DeletePageRequest(BaseModel):
    page_name: str
    delete_incoming: bool = True
    delete_outgoing: bool = True
    dry_run: bool = True


class DeleteCandidateRequest(BaseModel):
    page_name: str
    candidate_id: str
    delete_linked_transitions: bool = False
    delete_linked_operations: bool = False
    dry_run: bool = True


class DeletePageOperationRequest(BaseModel):
    page_name: str
    operation_id: str
    delete_revealed_candidates: bool = True
    dry_run: bool = True


class DeleteContinuedCaptureRequest(BaseModel):
    page_name: str
    capture_id: str
    delete_candidates_from_capture: bool = True
    dry_run: bool = True


def ok_response(**kwargs: Any) -> JSONResponse:
    return JSONResponse({"ok": True, **kwargs})


def error_response(message: str) -> JSONResponse:
    return JSONResponse({"ok": False, "error": message})


def run_hdc_with_fallback(commands: List[List[str]], action: str) -> None:
    """按顺序执行 hdc 命令，全部失败时抛出包含 stdout/stderr 的错误。"""
    errors = []
    for cmd in commands:
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        except Exception as exc:
            errors.append(f"{' '.join(cmd)} -> {exc}")
            continue
        if result.returncode == 0:
            return
        errors.append(
            f"{' '.join(cmd)} -> code={result.returncode}, stdout={result.stdout.strip()}, stderr={result.stderr.strip()}"
        )
    raise RuntimeError(f"{action} 失败：" + " | ".join(errors))


def execute_tap(device_id: str, center: List[int]) -> None:
    """在设备上点击候选控件中心点，优先 uitest uiInput，失败后回退 input tap。"""
    if not isinstance(center, list) or len(center) != 2:
        raise ValueError("candidate.bounds_center 必须是 [x, y]")
    x, y = int(center[0]), int(center[1])
    base = ["hdc", "-t", device_id, "shell"]
    run_hdc_with_fallback([
        base + ["uitest", "uiInput", "click", str(x), str(y)],
        base + ["input", "tap", str(x), str(y)],
    ], f"点击 [{x}, {y}]")


def execute_back(device_id: str) -> None:
    """执行系统返回键，优先 uitest uiInput，失败后回退 input keyevent。"""
    base = ["hdc", "-t", device_id, "shell"]
    run_hdc_with_fallback([
        base + ["uitest", "uiInput", "keyEvent", "Back"],
        base + ["input", "keyevent", "BACK"],
    ], "返回")


def execute_horizontal_swipe(device_id: str, direction: str, metrics: Dict[str, Any]) -> None:
    """执行一次横向滑动，用屏幕中部区域作为通用滑动坐标。"""
    size = metrics.get("screen_size") or [1080, 2400]
    width, height = int(size[0]), int(size[1])
    y = int(height * 0.55)
    if direction == "left":
        x1, x2 = int(width * 0.78), int(width * 0.22)
    elif direction == "right":
        x1, x2 = int(width * 0.22), int(width * 0.78)
    else:
        raise ValueError("direction 必须是 left 或 right")
    base = ["hdc", "-t", device_id, "shell"]
    run_hdc_with_fallback([
        base + ["uitest", "uiInput", "swipe", str(x1), str(y), str(x2), str(y), "600"],
        base + ["input", "swipe", str(x1), str(y), str(x2), str(y), "600"],
    ], f"横向{direction}滑动")


def execute_gesture_operation(device_id: str, operate: str, center: List[int], metrics: Dict[str, Any]) -> None:
    if not isinstance(center, list) or len(center) != 2:
        raise ValueError("center 必须是 [x, y]")
    x, y = int(center[0]), int(center[1])
    size = metrics.get("screen_size") or [1080, 2400]
    width, height = int(size[0]), int(size[1])
    dx = max(160, int(width * 0.22))
    dy = max(180, int(height * 0.12))
    if operate == "tap":
        execute_tap(device_id, [x, y])
        return
    if operate == "long_press":
        x1, y1, x2, y2, duration = x, y, x, y, "900"
    elif operate == "swipe_left":
        x1, y1, x2, y2, duration = x + dx // 2, y, x - dx // 2, y, "600"
    elif operate == "swipe_right":
        x1, y1, x2, y2, duration = x - dx // 2, y, x + dx // 2, y, "600"
    elif operate == "swipe_up":
        x1, y1, x2, y2, duration = x, y + dy // 2, x, y - dy // 2, "600"
    elif operate == "swipe_down":
        x1, y1, x2, y2, duration = x, y - dy // 2, x, y + dy // 2, "600"
    else:
        raise ValueError("operate 必须是 tap/long_press/swipe_left/swipe_right/swipe_up/swipe_down")
    base = ["hdc", "-t", device_id, "shell"]
    run_hdc_with_fallback([
        base + ["uitest", "uiInput", "swipe", str(x1), str(y1), str(x2), str(y2), duration],
        base + ["input", "swipe", str(x1), str(y1), str(x2), str(y2), duration],
    ], f"{operate} 手势")


def pending_data() -> Optional[Dict[str, Any]]:
    path = pending_transition_path(config.work_dir)
    return load_json(path) if path.exists() else None


def pending_action_chain_path() -> Path:
    return config.work_dir / "outputs" / "navigation" / "pending_action_chain.json"


def pending_action_chain() -> Optional[Dict[str, Any]]:
    path = pending_action_chain_path()
    return load_json(path) if path.exists() else None


def save_pending_action_chain(chain: Dict[str, Any]) -> None:
    save_json(chain, pending_action_chain_path(), "未完成多步骤跳转")


def clear_pending_action_chain() -> None:
    path = pending_action_chain_path()
    if path.exists():
        path.unlink()


def has_parent_transition(graph: Dict[str, Any], page_name: str) -> bool:
    return any(t.get("to_page") == page_name for t in graph.get("transitions", []))


def warning_for_state(graph: Dict[str, Any], state: Dict[str, Any]) -> str:
    page = state.get("page_name", "")
    if page and page != "Pages_root" and not pending_transition_path(config.work_dir).exists() and not pending_action_chain() and not has_parent_transition(graph, page):
        return "当前页面没有 pending transition，且导航图中没有父级来源。说明你可能是手动进入了当前页面，无法自动知道父级页面。请返回父页面后点击候选入口录制。"
    return ""


def read_current_state(capture: bool, persist_candidates: bool = True) -> Dict[str, Any]:
    """采集或读取 latest 文件，更新导航图状态并返回前端需要的数据。"""
    if capture and not capture_artifacts(config.device_id, config.output_dir):
        raise RuntimeError("hdc 采集失败，请检查设备连接、hdc PATH 和授权状态")
    json_path = config.output_dir / "current_ui_tree.json"
    screen_path = config.output_dir / "current_screen.png"
    if not json_path.exists() or not screen_path.exists():
        raise FileNotFoundError("outputs/latest/current_ui_tree.json 或 current_screen.png 不存在，请先点击“重新采集”。")
    root_json = load_json(json_path)
    annotate(root_json)
    state = build_navigation_state(root_json)
    graph = load_navigation_graph(config.work_dir)
    existing_state = graph.get("states", {}).get(state["page_name"], {})
    if isinstance(existing_state, dict):
        preserve_keys = ["page_operations", "page_variants", "merged_candidates"]
        if existing_state.get("is_overlay"):
            preserve_keys.extend(["state_type", "is_overlay", "overlay_parent", "overlay_title", "page_description"])
        state.update({k: existing_state[k] for k in preserve_keys if k in existing_state})
    if persist_candidates:
        graph.setdefault("states", {})[state["page_name"]] = state
    candidates = extract_navigation_candidates(root_json)
    if persist_candidates:
        state_entry = graph.setdefault("states", {}).setdefault(state["page_name"], state)
        for c in candidates:
            upsert_candidate(state_entry, candidate_from_auto(c, source="auto_detected"))
        save_navigation_graph(graph, config.work_dir)
    active_state = active_navigation_state(config.work_dir, graph, state)
    active_page = active_state.get("page_name")
    merged_candidates = get_page_merged_candidates(graph, str(active_page or state["page_name"]), candidates)
    return {
        "state": state,
        "active_state": active_state,
        "active_page": active_page,
        "current_candidates": candidates,
        "candidates": candidates,
        "merged_candidates": merged_candidates,
        "pending": pending_data(),
        "pending_action_chain": pending_action_chain(),
        "warning": warning_for_state(graph, state),
        "screenshot_url": f"/screen?t={int(time.time() * 1000)}",
        "screen_metrics": screen_metrics_from_root(root_json),
    }


def state_response_from_capture(
    root_json: Dict[str, Any],
    state: Dict[str, Any],
    graph: Dict[str, Any],
    active_page: str,
    message: str = "",
) -> Dict[str, Any]:
    candidates = extract_navigation_candidates(root_json)
    return {
        "state": state,
        "active_state": graph.get("states", {}).get(active_page, state),
        "active_page": active_page,
        "current_candidates": candidates,
        "candidates": candidates,
        "merged_candidates": get_page_merged_candidates(graph, active_page, []),
        "pending": pending_data(),
        "pending_action_chain": pending_action_chain(),
        "warning": warning_for_state(graph, state),
        "screenshot_url": f"/screen?t={int(time.time() * 1000)}",
        "screen_metrics": screen_metrics_from_root(root_json),
        "message": message,
    }


def web_record_history_path() -> Path:
    return config.work_dir / "outputs" / "navigation" / "web_record_history.jsonl"


def append_web_history(event: Dict[str, Any]) -> None:
    path = web_record_history_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"created_at": now_iso(), **event}, ensure_ascii=False) + "\n")


def ensure_page_consistency(current: Dict[str, Any]) -> None:
    detected = current.get("state", {}).get("page_name")
    active = current.get("active_page") or current.get("active_state", {}).get("page_name")
    if detected and active and detected != active:
        raise ValueError(f"当前检测页面 {detected} 与 active_page {active} 不一致，请先重新采集或确认当前页面状态后再录制。")


def capture_state_without_graph_write() -> Dict[str, Any]:
    """采集设备并构建 state，但不写 navigation graph，避免误生成状态。"""
    if not capture_artifacts(config.device_id, config.output_dir):
        raise RuntimeError("hdc 采集失败，请检查设备连接、hdc PATH 和授权状态")
    root_json = load_json(config.output_dir / "current_ui_tree.json")
    annotate(root_json)
    return {"root": root_json, "state": build_navigation_state(root_json)}


def capture_ui_tree_state_without_graph_write() -> Dict[str, Any]:
    """只拉取当前 UI tree 并构建 state；用于快速路径执行，不刷新截图。"""
    if not capture_ui_tree_only(config.device_id, config.output_dir):
        raise RuntimeError("hdc 拉取 UI 树失败，请检查设备连接、hdc PATH 和授权状态")
    root_json = load_json(config.output_dir / "current_ui_tree.json")
    annotate(root_json)
    return {"root": root_json, "state": build_navigation_state(root_json)}


def candidate_merge_key(candidate: Dict[str, Any]) -> str:
    value = str(candidate.get("value") or "").strip()
    key = str(candidate.get("key") or (value if candidate.get("type") in {"key", "button"} else "")).strip()
    if key:
        return f"key::{key}"
    ctype = str(candidate.get("type") or "").strip()
    text = str(candidate.get("text") or candidate.get("key_description") or (value if ctype in {"text", "button_text"} else "")).strip()
    if ctype and text:
        return f"type_text::{ctype}::{text}"
    component_type = str(candidate.get("component_type") or "").strip()
    if component_type and text:
        return f"component_text::{component_type}::{text}"
    stable = json.dumps({k: candidate.get(k) for k in ("type", "value", "component_type", "key_description", "step_prompt") if candidate.get(k)}, ensure_ascii=False, sort_keys=True)
    return "hash::" + hashlib.sha1(stable.encode("utf-8")).hexdigest()[:12] if stable != "{}" else ""


def candidate_id(candidate: Dict[str, Any]) -> str:
    return str(candidate.get("candidate_id") or candidate_merge_key(candidate))


def candidate_from_auto(c: Dict[str, Any], source: str = "auto_detected") -> Dict[str, Any]:
    target = dict(c.get("suggested_target") or {})
    text = str(c.get("text") or target.get("text") or target.get("key_description") or "")
    item = {
        "candidate_id": "",
        "type": str(target.get("type") or c.get("type") or ""),
        "value": target.get("value") or c.get("key") or text,
        "component_type": str(target.get("component_type") or c.get("type") or ""),
        "text": text,
        "key": str(c.get("key") or (target.get("value") if target.get("type") == "key" else "") or ""),
        "key_description": str(target.get("key_description") or text or target.get("value") or ""),
        "step_prompt": str(target.get("step_prompt") or text or target.get("value") or ""),
        "source": source,
        "transition_ids": list(c.get("transition_ids") or []),
        "operation_ids": list(c.get("operation_ids") or []),
    }
    item["candidate_id"] = candidate_merge_key(item)
    return item


def candidate_from_target(target: Dict[str, Any], source: str = "hit_test_click") -> Dict[str, Any]:
    clean = {k: v for k, v in target.items() if k not in {"point", "normalized_point", "coordinate_hit", "bounds_center", "fallback_locator"}}
    item = {
        "candidate_id": "",
        "type": str(clean.get("type") or ""),
        "value": clean.get("value", ""),
        "component_type": str(clean.get("component_type") or ""),
        "text": str(clean.get("text") or (clean.get("value") if clean.get("type") in {"text", "button_text"} else "") or ""),
        "key": str(clean.get("value") if clean.get("type") in {"key", "button"} else clean.get("key", "") or ""),
        "key_description": str(clean.get("key_description") or clean.get("text") or clean.get("value") or ""),
        "step_prompt": str(clean.get("step_prompt") or clean.get("key_description") or clean.get("value") or ""),
        "source": source,
        "transition_ids": [],
        "operation_ids": [],
    }
    item["candidate_id"] = candidate_merge_key(item)
    return item


def step_target(target: Dict[str, Any]) -> Dict[str, Any]:
    allowed = {
        "type",
        "value",
        "component_type",
        "text",
        "key",
        "key_description",
        "step_prompt",
        "scope",
        "expect",
    }
    clean = {k: v for k, v in target.items() if k in allowed and v not in (None, "", [])}
    if target.get("value") and "key" not in clean and target.get("type") in {"key", "button"}:
        clean["key"] = target.get("value")
    return clean


def transition_step(target: Dict[str, Any], operate: str = "tap") -> Dict[str, Any]:
    return {"operate": operate, "target": step_target(target)}


def transition_steps(transition: Dict[str, Any]) -> List[Dict[str, Any]]:
    steps = transition.get("steps")
    if isinstance(steps, list) and steps:
        return [s for s in steps if isinstance(s, dict)]
    target = transition.get("target") or {}
    operate = str(transition.get("operate") or "tap")
    return [transition_step(target, operate)] if target else []


def transition_steps_label(steps: List[Dict[str, Any]]) -> str:
    labels = []
    for step in steps:
        target = step.get("target") or {}
        label = target.get("step_prompt") or target.get("key_description") or target.get("text") or target.get("value") or target.get("key") or step.get("operate")
        labels.append(str(label))
    return " -> ".join(labels)


def transition_id_for_steps(from_page: str, to_page: str, steps: List[Dict[str, Any]], effect: str = "") -> str:
    payload = {
        "from_page": from_page,
        "to_page": to_page,
        "steps": steps,
        "effect": effect,
    }
    digest = hashlib.sha1(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:12]
    return f"{from_page}__to__{to_page}__steps_{digest}"


def component_summary_from_tree(root_json: Dict[str, Any]) -> List[Dict[str, Any]]:
    """提取可用于对比/合并的稳定控件摘要；不把坐标作为正式 key。"""
    components: List[Dict[str, Any]] = []
    seen = set()
    for node, _, _ in walk(root_json):
        item = node_semantic_summary(node)
        ctype = str(item.get("component_type") or "")
        if ctype in {"Root", "Page", "Navigation", "NavDestination", "RelativeContainer"}:
            continue
        if not ctype or not item.get("enabled", True):
            continue
        if not item.get("key") and not item.get("text"):
            continue
        merge_key = candidate_merge_key(item)
        if not merge_key or merge_key in seen:
            continue
        seen.add(merge_key)
        components.append({
            "text": item.get("text", ""),
            "key": item.get("key", ""),
            "component_type": ctype,
            "clickable": bool(item.get("clickable", False)),
            "enabled": bool(item.get("enabled", True)),
        })
    return components


def components_signature(components: List[Dict[str, Any]]) -> str:
    keys = sorted(candidate_merge_key(c) for c in components if candidate_merge_key(c))
    return hashlib.sha256(json.dumps(keys, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


def page_operation_id(page_name: str, target: Dict[str, Any]) -> str:
    desc = str(target.get("key_description") or target.get("step_prompt") or target.get("value") or "操作")
    safe = "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in desc).strip("_") or "operation"
    digest = hashlib.sha1(json.dumps([page_name, target.get("type"), target.get("value"), desc], ensure_ascii=False).encode("utf-8")).hexdigest()[:8]
    return f"{page_name}__op__{safe}_{digest}"


def page_variant_id(page_name: str, operation: Dict[str, Any], after_signature: str) -> str:
    payload = [page_name, operation.get("operation_id"), operation.get("effect"), after_signature]
    digest = hashlib.sha1(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:10]
    return f"{page_name}__variant__{digest}"


def upsert_page_variant(state_entry: Dict[str, Any], operation: Dict[str, Any], revealed: List[Dict[str, Any]], hidden: List[Dict[str, Any]]) -> None:
    variant = {
        "variant_id": page_variant_id(str(state_entry.get("page_name") or ""), operation, str(operation.get("after_signature") or "")),
        "created_at": now_iso(),
        "trigger_operation_id": operation.get("operation_id"),
        "trigger": operation.get("target") or {},
        "operate": operation.get("operate") or "tap",
        "effect": operation.get("effect") or "same_page_state_changed",
        "before_signature": operation.get("before_signature"),
        "after_signature": operation.get("after_signature"),
        "revealed_candidates": revealed,
        "hidden_candidates": hidden,
        "is_mutually_exclusive": bool(revealed and hidden),
    }
    variants = state_entry.setdefault("page_variants", [])
    variants[:] = [item for item in variants if item.get("variant_id") != variant["variant_id"]]
    variants.append(variant)


def merge_revealed_candidates(state_entry: Dict[str, Any], revealed: List[Dict[str, Any]]) -> None:
    merged = state_entry.setdefault("merged_candidates", [])
    existing = {candidate_merge_key(c) for c in merged if candidate_merge_key(c)}
    for candidate in revealed:
        key = candidate_merge_key(candidate)
        if not key or key in existing:
            continue
        merged.append(candidate)
        existing.add(key)


def transition_lookup(graph: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {str(t.get("transition_id")): t for t in graph.get("transitions", []) if t.get("transition_id")}


def all_operation_ids(state: Dict[str, Any]) -> Set[str]:
    return {str(op.get("operation_id")) for op in state.get("page_operations", []) if op.get("operation_id")}


def candidate_record_status(graph: Dict[str, Any], page_name: str, candidate: Dict[str, Any]) -> Dict[str, Any]:
    tids = [tid for tid in candidate.get("transition_ids", []) if tid]
    oids = [oid for oid in candidate.get("operation_ids", []) if oid]
    ctype = str(candidate.get("component_type") or "")
    if ctype in {"Toggle", "Switch", "CheckBox", "Checkbox"}:
        return {"status": "same_page_control", "label": "同页控件，不建议录制为页面跳转", "transition_ids": tids, "operation_ids": oids}
    lookup = transition_lookup(graph)
    valid_tids = [tid for tid in tids if tid in lookup]
    if valid_tids:
        to_page = lookup[valid_tids[0]].get("to_page", "")
        return {"status": "recorded_transition", "label": f"已录制跳转 -> {to_page}", "transition_ids": valid_tids, "operation_ids": oids}
    if oids:
        return {"status": "page_operation", "label": "页面内操作", "transition_ids": [], "operation_ids": oids}
    if ctype == "Button":
        return {"status": "unrecorded", "label": "Button / 未录制", "transition_ids": [], "operation_ids": []}
    return {"status": "unrecorded", "label": "未录制", "transition_ids": [], "operation_ids": []}


def enrich_candidate(graph: Dict[str, Any], page_name: str, candidate: Dict[str, Any]) -> Dict[str, Any]:
    item = dict(candidate)
    item.setdefault("candidate_id", candidate_id(item))
    item.update(candidate_record_status(graph, page_name, item))
    return item


def get_page_merged_candidates(graph: Dict[str, Any], page_name: str, current_candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    state = graph.get("states", {}).get(page_name, {}) if page_name else {}
    merged: Dict[str, Dict[str, Any]] = {}
    for c in current_candidates:
        item = candidate_from_auto(c, source=str(c.get("source") or "auto_detected"))
        key = candidate_merge_key(item)
        if key:
            merged[key] = {**item, **merged.get(key, {})}
    for c in state.get("merged_candidates", []) or []:
        key = candidate_merge_key(c)
        if not key:
            continue
        prev = merged.get(key, {})
        item = {**prev, **c}
        item.setdefault("candidate_id", key)
        item.setdefault("source", c.get("source") or "hit_test_click")
        item.setdefault("transition_ids", [])
        item.setdefault("operation_ids", [])
        merged[key] = item
    return [enrich_candidate(graph, page_name, c) for c in merged.values()]


def upsert_candidate(state_entry: Dict[str, Any], candidate: Dict[str, Any]) -> Dict[str, Any]:
    merged = state_entry.setdefault("merged_candidates", [])
    key = candidate_merge_key(candidate)
    if not key:
        return candidate
    candidate = dict(candidate)
    candidate.setdefault("candidate_id", key)
    for existing in merged:
        if candidate_merge_key(existing) == key:
            for field in ("transition_ids", "operation_ids"):
                ids = list(dict.fromkeys(list(existing.get(field) or []) + list(candidate.get(field) or [])))
                existing[field] = ids
            existing.update({k: v for k, v in candidate.items() if k not in {"transition_ids", "operation_ids"} and v not in (None, "", [])})
            return existing
    merged.append(candidate)
    return candidate


def upsert_clicked_target_as_candidate(graph: Dict[str, Any], page_name: str, target: Dict[str, Any], transition_id: Optional[str] = None, operation_id: Optional[str] = None) -> Dict[str, Any]:
    state_entry = graph.setdefault("states", {}).setdefault(page_name, {"page_name": page_name})
    item = candidate_from_target(target, source="hit_test_click")
    item["clicked_count"] = 1
    item["last_clicked_at"] = now_iso()
    if transition_id:
        item["transition_ids"] = [transition_id]
    if operation_id:
        item["operation_ids"] = [operation_id]
    existing = None
    for c in state_entry.setdefault("merged_candidates", []):
        if candidate_merge_key(c) == candidate_merge_key(item):
            existing = c
            break
    if existing:
        existing["clicked_count"] = int(existing.get("clicked_count") or 0) + 1
        existing["last_clicked_at"] = item["last_clicked_at"]
        existing.setdefault("source", "hit_test_click")
        existing.setdefault("transition_ids", [])
        existing.setdefault("operation_ids", [])
        if transition_id and transition_id not in existing["transition_ids"]:
            existing["transition_ids"].append(transition_id)
        if operation_id and operation_id not in existing["operation_ids"]:
            existing["operation_ids"].append(operation_id)
        for k, v in item.items():
            if k not in {"clicked_count", "last_clicked_at", "transition_ids", "operation_ids"} and v not in (None, "", []):
                existing[k] = v
        return existing
    return upsert_candidate(state_entry, item)


def transition_label(t: Dict[str, Any]) -> str:
    steps = transition_steps(t)
    if len(steps) > 1:
        return transition_steps_label(steps)
    target = t.get("target") or {}
    return str(target.get("step_prompt") or target.get("key_description") or target.get("value") or t.get("operate") or "")


def state_title(state: Dict[str, Any], page_name: str) -> str:
    return str(state.get("last_title") or state.get("page_description") or page_name).replace("弹窗：", "")


def build_page_directory(graph: Dict[str, Any]) -> Dict[str, Any]:
    states = graph.get("states", {})
    transitions = [t for t in graph.get("transitions", []) if t.get("from_page") != t.get("to_page")]
    incoming: Dict[str, List[Dict[str, Any]]] = {}
    outgoing: Dict[str, List[Dict[str, Any]]] = {}
    for t in transitions:
        incoming.setdefault(str(t.get("to_page")), []).append(t)
        outgoing.setdefault(str(t.get("from_page")), []).append(t)
    def node(page: str, seen: Set[str]) -> Dict[str, Any]:
        st = states.get(page, {})
        children = []
        for t in outgoing.get(page, []):
            child = str(t.get("to_page"))
            if child in seen or states.get(child, {}).get("is_overlay"):
                continue
            steps = transition_steps(t)
            children.append({**node(child, seen | {child}), "via": {
                "from_page": page,
                "target_label": transition_label(t),
                "transition_id": t.get("transition_id"),
                "step_count": len(steps),
                "steps": steps,
            }})
        return {"page_name": page, "title": state_title(st, page), "children": children}
    flat = []
    for page, st in states.items():
        flat.append({
            "page_name": page,
            "title": state_title(st, page),
            "incoming_count": len(incoming.get(page, [])),
            "outgoing_count": len(outgoing.get(page, [])),
            "candidate_count": len(st.get("merged_candidates", []) or []),
            "operation_count": len(st.get("page_operations", []) or []),
            "continued_capture_count": len(st.get("continued_captures", []) or []),
            "is_overlay": bool(st.get("is_overlay")),
            "state_type": st.get("state_type") or ("overlay" if st.get("is_overlay") else "page"),
        })
    return {"root": "Pages_root", "items": [node("Pages_root", {"Pages_root"})] if "Pages_root" in states else [], "flat_pages": sorted(flat, key=lambda x: x["page_name"])}


def record_same_page_operation(x: int, y: int, manual_label: str = "") -> Dict[str, Any]:
    before = capture_state_without_graph_write()
    graph = load_navigation_graph(config.work_dir)
    active_state = active_navigation_state(config.work_dir, graph, before["state"])
    active_page = active_state.get("page_name")
    before_page = before["state"].get("page_name")
    if before_page != active_page:
        raise ValueError(f"当前检测页面 {before_page} 与 active_page {active_page} 不一致，请先重新采集或确认当前页面状态后再录制。")
    hit = hit_test_full_ui_tree(before["root"], int(x), int(y))
    target = build_semantic_target_from_node(hit, manual_label=manual_label.strip())
    append_web_history({"event": "tap_same_page_operation", "debug": {"point": [int(x), int(y)], "hit_node": hit, "needs_manual_label": target.get("needs_manual_label", False)}})
    if target.get("needs_manual_label"):
        current = read_current_state(capture=False)
        return {**current, "needs_manual_label": True, "hit_node": hit, "same_page_mode": True, "message": "命中控件缺少稳定 key/text，请手动填写描述后再保存。"}
    before_components = component_summary_from_tree(before["root"])
    execute_tap(config.device_id, [int(x), int(y)])
    time.sleep(1.2)
    after = capture_state_without_graph_write()
    after_page = after["state"].get("page_name")
    if after_page != before_page:
        return {"ok": False, "error": "点击后进入了新页面，请使用页面跳转录制模式，不要使用页面内变化模式。"}
    after_components = component_summary_from_tree(after["root"])
    before_keys = {candidate_merge_key(c) for c in before_components if candidate_merge_key(c)}
    after_keys = {candidate_merge_key(c) for c in after_components if candidate_merge_key(c)}
    operation_id = page_operation_id(active_page, target)
    revealed = []
    for item in after_components:
        if candidate_merge_key(item) in after_keys - before_keys:
            revealed.append({**item, "source": "page_operation", "source_operation_id": operation_id, "requires_operation_id": operation_id})
    hidden = []
    for item in before_components:
        if candidate_merge_key(item) in before_keys - after_keys:
            hidden.append({**item, "source_operation_id": operation_id})
    state_entry = graph.setdefault("states", {}).setdefault(active_page, before["state"])
    state_entry.update(before["state"])
    operation = {
        "operation_id": operation_id,
        "created_at": now_iso(),
        "operate": "tap",
        "target": target,
        "effect": "content_changed",
        "before_signature": components_signature(before_components),
        "after_signature": components_signature(after_components),
        "revealed_candidates": revealed,
        "hidden_candidates": hidden,
    }
    ops = state_entry.setdefault("page_operations", [])
    ops[:] = [op for op in ops if op.get("operation_id") != operation_id]
    ops.append(operation)
    upsert_page_variant(state_entry, operation, revealed, hidden)
    upsert_clicked_target_as_candidate(graph, active_page, target, operation_id=operation_id)
    for item in revealed:
        item.setdefault("candidate_id", candidate_merge_key(item))
        upsert_candidate(state_entry, item)
    save_navigation_graph(graph, config.work_dir)
    refreshed = read_current_state(capture=False)
    return {**refreshed, "message": f"已记录页面内变化：新增 {len(revealed)} 个控件，消失 {len(hidden)} 个控件。"}


def record_page_gesture_operation(x: int, y: int, operate: str, effect: str = "", manual_label: str = "") -> Dict[str, Any]:
    before = capture_state_without_graph_write()
    graph = load_navigation_graph(config.work_dir)
    active_state = active_navigation_state(config.work_dir, graph, before["state"])
    active_page = str(active_state.get("page_name") or "")
    before_page = before["state"].get("page_name")
    if before_page != active_page:
        raise ValueError(f"当前检测页面 {before_page} 与 active_page {active_page} 不一致，请先重新采集或确认当前页面状态后再录制。")
    hit = hit_test_full_ui_tree(before["root"], int(x), int(y))
    target = build_semantic_target_from_node(hit, manual_label=manual_label.strip())
    append_web_history({"event": "page_gesture_operation", "debug": {"operate": operate, "effect": effect, "point": [int(x), int(y)], "hit_node": hit, "needs_manual_label": target.get("needs_manual_label", False)}})
    if target.get("needs_manual_label"):
        current = read_current_state(capture=False)
        return {**current, "needs_manual_label": True, "hit_node": hit, "page_operation_mode": True, "operate": operate, "effect": effect, "message": "命中区域缺少稳定 key/text，请手动填写操作对象描述后再保存。"}
    before_components = component_summary_from_tree(before["root"])
    metrics = screen_metrics_from_root(before["root"])
    execute_gesture_operation(config.device_id, operate, [int(x), int(y)], metrics)
    time.sleep(1.0)
    after = capture_state_without_graph_write()
    if after["state"].get("page_name") != before_page:
        return {"ok": False, "error": "执行后进入了新页面，请使用页面跳转录制，不要保存为页面内操作。"}
    after_components = component_summary_from_tree(after["root"])
    operation_id = page_operation_id(active_page, {**target, "operate": operate, "effect": effect})
    operation = {
        "operation_id": operation_id,
        "created_at": now_iso(),
        "operate": operate,
        "target": target,
        "effect": effect or "same_page_state_changed",
        "before_signature": components_signature(before_components),
        "after_signature": components_signature(after_components),
    }
    state_entry = graph.setdefault("states", {}).setdefault(active_page, before["state"])
    state_entry.update(before["state"])
    ops = state_entry.setdefault("page_operations", [])
    ops[:] = [op for op in ops if op.get("operation_id") != operation_id]
    ops.append(operation)
    upsert_clicked_target_as_candidate(graph, active_page, target, operation_id=operation_id)
    save_navigation_graph(graph, config.work_dir)
    refreshed = read_current_state(capture=False)
    return {**refreshed, "message": f"已记录页面内操作：{operate} / {operation.get('effect')}"}


def record_tap_at_point(x: int, y: int, expect: str = "new_page", effect: str = "", manual_label: str = "") -> Dict[str, Any]:
    chain = pending_action_chain()
    if chain:
        before = capture_state_without_graph_write()
        root_json = before["root"]
        current = state_response_from_capture(root_json, before["state"], load_navigation_graph(config.work_dir), str(chain["from_page"]))
        from_page = str(chain["from_page"])
    else:
        current = read_current_state(capture=True)
        ensure_page_consistency(current)
        root_json = load_json(config.output_dir / "current_ui_tree.json")
        annotate(root_json)
        from_page = str(current["active_page"])
    hit = hit_test_full_ui_tree(root_json, int(x), int(y))
    target = build_semantic_target_from_node(hit, manual_label=manual_label.strip())
    append_web_history({"event": "tap_point", "debug": {"point": [int(x), int(y)], "hit_node": hit, "needs_manual_label": target.get("needs_manual_label", False)}})
    if target.get("needs_manual_label"):
        return {**current, "needs_manual_label": True, "hit_node": hit, "message": "命中控件缺少稳定 key/text，请手动填写描述后再保存。"}
    if expect:
        target["expect"] = expect
    ctype = str(target.get("component_type") or hit.get("component_type") if hit else "")
    if ctype in {"Toggle", "Switch", "CheckBox", "Checkbox"}:
        target["expect"] = "same_page"
    execute_tap(config.device_id, [int(x), int(y)])
    time.sleep(1.2)
    after_capture = capture_state_without_graph_write()
    after = state_response_from_capture(after_capture["root"], after_capture["state"], load_navigation_graph(config.work_dir), from_page)
    to_page = after["state"]["page_name"]
    graph = load_navigation_graph(config.work_dir)
    if to_page == from_page:
        if ctype in {"Toggle", "Switch", "CheckBox", "Checkbox"}:
            upsert_clicked_target_as_candidate(graph, from_page, target)
            save_navigation_graph(graph, config.work_dir)
            refreshed = read_current_state(capture=False)
            return {**refreshed, "message": "点击后仍停留在当前页面。如该操作用于展开或刷新页面内容，请使用‘录制页面内变化’模式。"}
        steps = list(chain.get("steps", [])) if chain else []
        steps.append(transition_step(target))
        save_pending_action_chain({
            "from_page": from_page,
            "steps": steps,
            "created_at": chain.get("created_at") if chain else now_iso(),
            "updated_at": now_iso(),
        })
        message = f"已记录第 {len(steps)} 步，继续点击临时菜单/弹层中的目标控件；进入新页面后会保存为一条多步骤跳转。"
        return state_response_from_capture(after_capture["root"], after_capture["state"], graph, from_page, message=message)

    steps = list(chain.get("steps", [])) if chain else []
    steps.append(transition_step(target))
    tid = transition_id_for_steps(from_page, str(to_page), steps, effect)
    transition = {
        "transition_id": tid,
        "from_page": from_page,
        "to_page": to_page,
        "operate": "tap",
        "target": steps[0].get("target") or step_target(target),
        "steps": steps,
    }
    if effect:
        transition["effect"] = effect
    graph.setdefault("states", {})[after["state"]["page_name"]] = after["state"]
    add_transition(graph, transition)
    if steps:
        upsert_clicked_target_as_candidate(graph, from_page, steps[0].get("target") or target, transition_id=tid)
    save_navigation_graph(graph, config.work_dir)
    save_current_path_session(config.work_dir, to_page)
    clear_pending_action_chain()
    return read_current_state(capture=False)


@app.get("/", response_class=HTMLResponse)
def index() -> FileResponse:
    return FileResponse(APP_DIR / "templates" / "nav.html")


@app.post("/api/capture")
def api_capture() -> JSONResponse:
    try:
        return ok_response(**read_current_state(capture=True))
    except Exception as exc:
        return error_response(str(exc))


@app.post("/api/console_action")
def api_console_action(req: ConsoleActionRequest) -> JSONResponse:
    action = req.action.strip()
    payload = req.payload or {}
    try:
        if action == "capture_current":
            return api_capture()
        if action == "system_back":
            return api_back()
        if action == "clear_pending":
            path = pending_transition_path(config.work_dir)
            if path.exists():
                path.unlink()
            clear_pending_action_chain()
            return ok_response(**read_current_state(capture=False), message="已清空待确认跳转。")
        if action == "mark_overlay":
            return api_mark_current_as_overlay()
        if action == "continue_current_page":
            return api_continue_current_page()
        if action == "swipe_horizontal":
            return api_swipe_horizontal(SwipeHorizontalRequest(direction=str(payload.get("direction") or "")))
        if action == "navigate_to_page":
            return api_navigate_to_page(NavigateToPageRequest(page_name=str(payload.get("page_name") or "")))
        raise ValueError(f"未知控制台动作：{action}")
    except Exception as exc:
        return error_response(str(exc))


@app.get("/api/state")
def api_state() -> JSONResponse:
    try:
        return ok_response(**read_current_state(capture=False))
    except Exception as exc:
        return error_response(str(exc))


@app.post("/api/tap_point")
def api_tap_point(req: TapPointRequest) -> JSONResponse:
    try:
        data = record_tap_at_point(req.x, req.y, expect=req.expect, effect=req.effect, manual_label=req.manual_label)
        return ok_response(**data)
    except Exception as exc:
        return error_response(str(exc))


@app.post("/api/tap_same_page_operation")
def api_tap_same_page_operation(req: TapSamePageOperationRequest) -> JSONResponse:
    try:
        data = record_same_page_operation(req.x, req.y, manual_label=req.manual_label)
        if data.get("ok") is False:
            return JSONResponse(data)
        return ok_response(**data)
    except Exception as exc:
        return error_response(str(exc))


@app.post("/api/page_gesture_operation")
def api_page_gesture_operation(req: PageGestureOperationRequest) -> JSONResponse:
    try:
        data = record_page_gesture_operation(req.x, req.y, req.operate, effect=req.effect, manual_label=req.manual_label)
        if data.get("ok") is False:
            return JSONResponse(data)
        return ok_response(**data)
    except Exception as exc:
        return error_response(str(exc))


@app.post("/api/record_action")
def api_record_action(req: RecordActionRequest) -> JSONResponse:
    action = req.action.strip()
    payload = req.payload or {}
    try:
        if action == "tap_point":
            data = record_tap_at_point(
                int(payload.get("x")),
                int(payload.get("y")),
                expect=str(payload.get("expect") or "new_page"),
                effect=str(payload.get("effect") or ""),
                manual_label=str(payload.get("manual_label") or ""),
            )
            return ok_response(**data)
        if action == "same_page_tap":
            data = record_same_page_operation(
                int(payload.get("x")),
                int(payload.get("y")),
                manual_label=str(payload.get("manual_label") or ""),
            )
            return JSONResponse(data) if data.get("ok") is False else ok_response(**data)
        if action == "same_page_gesture":
            data = record_page_gesture_operation(
                int(payload.get("x")),
                int(payload.get("y")),
                str(payload.get("operate") or ""),
                effect=str(payload.get("effect") or ""),
                manual_label=str(payload.get("manual_label") or ""),
            )
            return JSONResponse(data) if data.get("ok") is False else ok_response(**data)
        if action == "tap_candidate":
            current = read_current_state(capture=True)
            candidates = current["candidates"]
            index = int(payload.get("index"))
            if index < 1 or index > len(candidates):
                raise ValueError(f"候选编号无效：{index}")
            selected = candidates[index - 1]
            center = selected.get("bounds_center")
            if not isinstance(center, list) or len(center) != 2:
                raise ValueError("候选项缺少 bounds_center，无法作为临时 hit-test 输入")
            data = record_tap_at_point(
                int(center[0]),
                int(center[1]),
                expect=str(payload.get("expect") or "new_page"),
                effect=str(payload.get("effect") or ""),
                manual_label=str(payload.get("manual_label") or ""),
            )
            return ok_response(**data)
        raise ValueError(f"未知录制动作：{action}")
    except Exception as exc:
        return error_response(str(exc))


@app.post("/api/tap_candidate")
def api_tap_candidate(req: TapCandidateRequest) -> JSONResponse:
    try:
        current = read_current_state(capture=True)
        candidates = current["candidates"]
        if req.index < 1 or req.index > len(candidates):
            raise ValueError(f"候选编号无效：{req.index}")
        selected = candidates[req.index - 1]
        center = selected.get("bounds_center")
        if not isinstance(center, list) or len(center) != 2:
            raise ValueError("候选项缺少 bounds_center，无法作为临时 hit-test 输入")
        data = record_tap_at_point(int(center[0]), int(center[1]), expect=req.expect, effect=req.effect, manual_label=req.manual_label)
        return ok_response(**data)
    except Exception as exc:
        return error_response(str(exc))


@app.post("/api/mark_current_as_overlay")
def api_mark_current_as_overlay() -> JSONResponse:
    try:
        current = read_current_state(capture=False)
        root_json = load_json(config.output_dir / "current_ui_tree.json")
        annotate(root_json)
        title = detect_overlay_title(root_json) or current["state"].get("last_title") or "未知弹窗"
        page_name = current["state"].get("page_name") or current.get("active_page")
        graph = load_navigation_graph(config.work_dir)
        pending = pending_data() or {}
        parent = current.get("active_page") or pending.get("from_page") or ""
        state_entry = graph.setdefault("states", {}).setdefault(page_name, current["state"])
        state_entry.update({
            "state_type": "overlay",
            "is_overlay": True,
            "overlay_parent": parent,
            "overlay_title": title,
            "page_description": f"弹窗：{title}",
            "last_title": title,
        })
        save_navigation_graph(graph, config.work_dir)
        refreshed = read_current_state(capture=False)
        refreshed["message"] = "当前页面已标记为弹窗页面"
        return ok_response(**refreshed)
    except Exception as exc:
        return error_response(str(exc))


def bfs_path(graph: Dict[str, Any], target_page: str) -> Optional[List[Dict[str, Any]]]:
    queue: List[Tuple[str, List[Dict[str, Any]]]] = [("Pages_root", [])]
    seen = {"Pages_root"}
    while queue:
        page, path = queue.pop(0)
        if page == target_page:
            return path
        for t in graph.get("transitions", []):
            if t.get("from_page") != page or t.get("from_page") == t.get("to_page"):
                continue
            nxt = str(t.get("to_page"))
            if nxt in seen:
                continue
            seen.add(nxt)
            queue.append((nxt, path + [t]))
    return None


def find_candidate_center_for_target(root_json: Dict[str, Any], target: Dict[str, Any]) -> Optional[List[int]]:
    candidates = extract_navigation_candidates(root_json)
    want_type = str(target.get("type") or "")
    want_value = str(target.get("value") or target.get("key") or "")
    want_text = str(target.get("text") or target.get("key_description") or target.get("step_prompt") or "")
    for c in candidates:
        ct = c.get("suggested_target") or {}
        values = {str(c.get("key") or ""), str(c.get("text") or ""), str(ct.get("value") or ""), str(ct.get("key_description") or ""), str(ct.get("step_prompt") or "")}
        if (want_value and want_value in values) or (want_text and want_text in values) or (want_type == ct.get("type") and want_value == str(ct.get("value") or "")):
            center = c.get("bounds_center")
            return center if isinstance(center, list) and len(center) == 2 else None
    return None


@app.get("/api/page_directory")
def api_page_directory() -> JSONResponse:
    try:
        return ok_response(**build_page_directory(load_navigation_graph(config.work_dir)))
    except Exception as exc:
        return error_response(str(exc))


def validate_page_name_for_rename(page_name: str) -> str:
    page_name = page_name.strip()
    if not page_name:
        raise ValueError("page_name 不能为空")
    if not page_name.startswith("Pages_"):
        raise ValueError("page_name 必须以 Pages_ 开头，例如 Pages_WLAN")
    if any(ch in page_name for ch in ["/", "\\", "\n", "\r", "\t"]):
        raise ValueError("page_name 不能包含路径分隔符或换行符")
    return page_name


@app.post("/api/rename_page")
def api_rename_page(req: RenamePageRequest) -> JSONResponse:
    try:
        old_name = req.old_page_name.strip()
        new_name = validate_page_name_for_rename(req.new_page_name)
        new_title = req.new_title.strip()
        graph = load_navigation_graph(config.work_dir)
        states = graph.setdefault("states", {})
        if old_name not in states:
            raise ValueError(f"页面不存在：{old_name}")
        if old_name == "Pages_root" and new_name != old_name:
            raise ValueError("不允许修改 Pages_root 的 page_name")
        if new_name != old_name and new_name in states:
            raise ValueError(f"目标 page_name 已存在：{new_name}")

        backup = backup_graph_file()
        state = states.pop(old_name)
        state["page_name"] = new_name
        if new_title:
            state["last_title"] = new_title
            prefix = "弹窗：" if state.get("is_overlay") else ""
            state["page_description"] = f"{prefix}{new_title}"
        states[new_name] = state

        if new_name != old_name:
            for transition in graph.get("transitions", []):
                if transition.get("from_page") == old_name:
                    transition["from_page"] = new_name
                if transition.get("to_page") == old_name:
                    transition["to_page"] = new_name
            traversal = graph.setdefault("traversal_config", {})
            if traversal.get("root_page") == old_name:
                traversal["root_page"] = new_name
            if graph.get("main_page_name") == old_name:
                graph["main_page_name"] = new_name

        save_navigation_graph(graph, config.work_dir)

        session_path = config.work_dir / "outputs" / "navigation" / "current_path_session.json"
        if session_path.exists():
            try:
                session = load_json(session_path)
            except Exception:
                session = {}
            changed = False
            if session.get("active_page") == old_name:
                session["active_page"] = new_name
                changed = True
            if session.get("base_page") == old_name:
                session["base_page"] = new_name
                changed = True
            if changed:
                save_json(session, session_path, "当前页面会话")

        return ok_response(
            page_name=new_name,
            old_page_name=old_name,
            new_title=state.get("last_title") or state.get("page_description") or new_name,
            backup=backup,
            message=f"已重命名页面：{old_name} -> {new_name}",
        )
    except Exception as exc:
        return error_response(str(exc))


@app.get("/api/page_detail")
def api_page_detail(page_name: str) -> JSONResponse:
    try:
        graph = load_navigation_graph(config.work_dir)
        state = graph.get("states", {}).get(page_name)
        if not isinstance(state, dict):
            raise ValueError(f"页面不存在：{page_name}")
        incoming = [t for t in graph.get("transitions", []) if t.get("to_page") == page_name]
        outgoing = [t for t in graph.get("transitions", []) if t.get("from_page") == page_name]
        path_from_root = [] if page_name == "Pages_root" else (bfs_path(graph, page_name) or [])
        return ok_response(
            page_name=page_name,
            state=state,
            path_from_root=path_from_root,
            incoming_transitions=incoming,
            outgoing_transitions=outgoing,
            merged_candidates=get_page_merged_candidates(graph, page_name, []),
            page_operations=state.get("page_operations", []) or [],
            page_variants=state.get("page_variants", []) or [],
            continued_captures=state.get("continued_captures", []) or [],
        )
    except Exception as exc:
        return error_response(str(exc))


@app.post("/api/set_active_page")
def api_set_active_page(req: SetActivePageRequest) -> JSONResponse:
    try:
        graph = load_navigation_graph(config.work_dir)
        if req.page_name not in graph.get("states", {}):
            raise ValueError(f"页面不存在：{req.page_name}")
        save_current_path_session(config.work_dir, req.page_name)
        return ok_response(**read_current_state(capture=False))
    except Exception as exc:
        return error_response(str(exc))


@app.post("/api/continue_current_page")
def api_continue_current_page() -> JSONResponse:
    try:
        current = read_current_state(capture=True)
        graph = load_navigation_graph(config.work_dir)
        page_name = current.get("active_page") or current["state"]["page_name"]
        state_entry = graph.setdefault("states", {}).setdefault(page_name, current["state"])
        captures = state_entry.setdefault("continued_captures", [])
        capture_id = f"{page_name}__continue_{len(captures) + 1:03d}"
        capture_dir = config.work_dir / "outputs" / "navigation" / "continued_captures"
        capture_dir.mkdir(parents=True, exist_ok=True)
        src = config.output_dir / "current_screen.png"
        dst = capture_dir / f"{capture_id}.png"
        if src.exists():
            shutil.copy2(src, dst)
        for c in current.get("current_candidates", []) or []:
            item = candidate_from_auto(c, source="continued_capture")
            item["source_capture_id"] = capture_id
            upsert_candidate(state_entry, item)
        captures.append({"capture_id": capture_id, "created_at": now_iso(), "screenshot": str(dst), "candidate_count": len(current.get("current_candidates", []) or [])})
        save_navigation_graph(graph, config.work_dir)
        return ok_response(**read_current_state(capture=False), message=f"已续录当前页面：{capture_id}")
    except Exception as exc:
        return error_response(str(exc))


@app.post("/api/navigate_to_page")
def api_navigate_to_page(req: NavigateToPageRequest) -> JSONResponse:
    try:
        graph = load_navigation_graph(config.work_dir)
        path = bfs_path(graph, req.page_name)
        if path is None:
            raise ValueError(f"找不到 Pages_root 到 {req.page_name} 的路径")
        last_capture: Optional[Dict[str, Any]] = None
        for t in path:
            from_page = str(t.get("from_page") or "")
            for step in transition_steps(t):
                before = capture_ui_tree_state_without_graph_write()
                last_capture = before
                detected_page = str(before["state"].get("page_name") or "")
                if from_page and detected_page != from_page:
                    raise ValueError(f"无法进入 {req.page_name}：当前检测页面是 {detected_page}，但路径下一步要求位于 {from_page}。请先回到正确起始页。")
                root_json = before["root"]
                center = find_candidate_center_for_target(root_json, step.get("target") or {})
                if not center:
                    raise ValueError(f"无法进入 {req.page_name}：当前页面找不到路径控件「{transition_steps_label([step])}」。请确认该控件仍存在或重新录制这条路径。")
                execute_tap(config.device_id, center)
                time.sleep(0.5)
        last_capture = capture_ui_tree_state_without_graph_write()
        if last_capture["state"].get("page_name") != req.page_name:
            raise ValueError(f"无法进入 {req.page_name}：最终校验失败，实际位于 {last_capture['state'].get('page_name')}")
        save_current_path_session(config.work_dir, req.page_name)
        if last_capture:
            return ok_response(**state_response_from_capture(
                last_capture["root"],
                last_capture["state"],
                graph,
                req.page_name,
                message="已按导航图快速跳转：仅拉取 UI 树并解析控件，未重新截图。",
            ))
        return ok_response(**read_current_state(capture=False))
    except Exception as exc:
        return error_response(str(exc))


def blank_delete_plan() -> Dict[str, Any]:
    return {"transitions": [], "states": [], "candidates": [], "page_operations": [], "continued_captures": [], "files": [], "warnings": []}


def backup_graph_file() -> str:
    src = navigation_graph_path(config.work_dir)
    backup_dir = config.work_dir / "outputs" / "navigation" / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    dst = backup_dir / f"settings_navigation_graph_{int(time.time())}.json"
    if src.exists():
        shutil.copy2(src, dst)
    return str(dst)


def cleanup_candidate_refs(graph: Dict[str, Any], plan: Dict[str, Any]) -> None:
    tids = {str(t.get("transition_id") or t) for t in plan.get("transitions", [])}
    oids = {str(o.get("operation_id") or o) for o in plan.get("page_operations", [])}
    for page, state in graph.get("states", {}).items():
        kept = []
        for c in state.get("merged_candidates", []) or []:
            before_tid = set(c.get("transition_ids") or [])
            before_oid = set(c.get("operation_ids") or [])
            c["transition_ids"] = [x for x in c.get("transition_ids", []) if x not in tids]
            c["operation_ids"] = [x for x in c.get("operation_ids", []) if x not in oids]
            if before_tid - set(c["transition_ids"]) or before_oid - set(c["operation_ids"]):
                plan["candidates"].append({"page_name": page, "candidate_id": candidate_id(c), "action": "remove_refs"})
            if c.get("source") == "hit_test_click" and not c.get("transition_ids") and not c.get("operation_ids") and (before_tid or before_oid):
                plan["candidates"].append({"page_name": page, "candidate_id": candidate_id(c), "action": "delete_orphan_clicked_candidate"})
                continue
            kept.append(c)
        state["merged_candidates"] = kept


def incoming_count(graph: Dict[str, Any], page: str, ignore_tids: Optional[Set[str]] = None) -> int:
    ignore_tids = ignore_tids or set()
    return sum(1 for t in graph.get("transitions", []) if t.get("to_page") == page and t.get("transition_id") not in ignore_tids)


def collect_descendant_delete(graph: Dict[str, Any], start_page: str, plan: Dict[str, Any], protected_tids: Set[str]) -> None:
    if start_page == "Pages_root" or incoming_count(graph, start_page, protected_tids) > 0:
        return
    if start_page not in plan["states"]:
        plan["states"].append(start_page)
    for op in graph.get("states", {}).get(start_page, {}).get("page_operations", []) or []:
        if op.get("operation_id"):
            plan["page_operations"].append({"page_name": start_page, "operation_id": op.get("operation_id")})
    for cap in graph.get("states", {}).get(start_page, {}).get("continued_captures", []) or []:
        plan["continued_captures"].append({"page_name": start_page, "capture_id": cap.get("capture_id")})
    outgoing = [t for t in graph.get("transitions", []) if t.get("from_page") == start_page]
    for t in outgoing:
        tid = t.get("transition_id")
        if tid and tid not in {x.get("transition_id") for x in plan["transitions"]}:
            plan["transitions"].append(t)
            protected_tids.add(tid)
        collect_descendant_delete(graph, str(t.get("to_page")), plan, protected_tids)


def apply_delete_plan(graph: Dict[str, Any], plan: Dict[str, Any]) -> None:
    tids = {str(t.get("transition_id") or t) for t in plan.get("transitions", [])}
    states = {str(s) for s in plan.get("states", [])}
    graph["transitions"] = [t for t in graph.get("transitions", []) if t.get("transition_id") not in tids and t.get("from_page") not in states and t.get("to_page") not in states]
    for page in states:
        graph.get("states", {}).pop(page, None)
    cleanup_candidate_refs(graph, plan)
    for op_ref in plan.get("page_operations", []):
        page, oid = op_ref.get("page_name"), op_ref.get("operation_id")
        state = graph.get("states", {}).get(page, {})
        state["page_operations"] = [op for op in state.get("page_operations", []) if op.get("operation_id") != oid]
        if plan.get("keep_revealed_candidates"):
            for c in state.get("merged_candidates", []) or []:
                if c.get("requires_operation_id") == oid:
                    c.pop("requires_operation_id", None)
                c["operation_ids"] = [x for x in c.get("operation_ids", []) if x != oid]
        else:
            state["merged_candidates"] = [c for c in state.get("merged_candidates", []) if c.get("requires_operation_id") != oid and c.get("source_operation_id") != oid]
    for cap_ref in plan.get("continued_captures", []):
        page, cid = cap_ref.get("page_name"), cap_ref.get("capture_id")
        state = graph.get("states", {}).get(page, {})
        state["continued_captures"] = [c for c in state.get("continued_captures", []) if c.get("capture_id") != cid]
        if plan.get("keep_capture_candidates"):
            for c in state.get("merged_candidates", []) or []:
                if c.get("source_capture_id") == cid:
                    c.pop("source_capture_id", None)
        else:
            state["merged_candidates"] = [c for c in state.get("merged_candidates", []) if c.get("source_capture_id") != cid or c.get("transition_ids") or c.get("operation_ids")]
    for f in plan.get("files", []):
        try:
            path = Path(f)
            if path.exists():
                path.unlink()
        except Exception as exc:
            plan.setdefault("warnings", []).append(f"删除文件失败 {f}: {exc}")


def prune_graph_after_delete(graph: Dict[str, Any]) -> Dict[str, Any]:
    warnings: List[str] = []
    states = graph.setdefault("states", {})
    valid_pages = set(states)
    graph["transitions"] = [t for t in graph.get("transitions", []) if t.get("from_page") in valid_pages and t.get("to_page") in valid_pages]
    active_page = ""
    session_path = config.work_dir / "outputs" / "navigation" / "current_path_session.json"
    if session_path.exists():
        try:
            active_page = str(load_json(session_path).get("active_page") or "")
        except Exception:
            active_page = ""
    for page in list(states.keys()):
        if page != "Pages_root" and page != active_page and incoming_count(graph, page) == 0 and not states[page].get("explicit_keep"):
            states.pop(page, None)
            warnings.append(f"删除孤儿 state：{page}")
    valid_pages = set(states)
    graph["transitions"] = [t for t in graph.get("transitions", []) if t.get("from_page") in valid_pages and t.get("to_page") in valid_pages]
    valid_tids = {t.get("transition_id") for t in graph.get("transitions", [])}
    for page, st in list(states.items()):
        valid_oids = all_operation_ids(st)
        kept = []
        for c in st.get("merged_candidates", []) or []:
            c["transition_ids"] = [tid for tid in c.get("transition_ids", []) if tid in valid_tids]
            c["operation_ids"] = [oid for oid in c.get("operation_ids", []) if oid in valid_oids]
            if c.get("requires_operation_id") and c.get("requires_operation_id") not in valid_oids:
                continue
            kept.append(c)
        st["merged_candidates"] = kept
        caps = []
        for cap in st.get("continued_captures", []) or []:
            f = cap.get("screenshot")
            if f and not Path(f).exists():
                warnings.append(f"续录截图缺失：{f}")
            caps.append(cap)
        st["continued_captures"] = caps
        st["incoming_count"] = incoming_count(graph, page)
        st["outgoing_count"] = sum(1 for t in graph.get("transitions", []) if t.get("from_page") == page)
        st["candidate_count"] = len(st.get("merged_candidates", []) or [])
        st["operation_count"] = len(st.get("page_operations", []) or [])
        st["continued_capture_count"] = len(st.get("continued_captures", []) or [])
    return {"warnings": warnings}


def finalize_delete(action: str, graph: Dict[str, Any], plan: Dict[str, Any], dry_run: bool) -> Dict[str, Any]:
    if dry_run:
        return {"dry_run": True, "delete_plan": plan}
    backup = backup_graph_file()
    apply_delete_plan(graph, plan)
    prune = prune_graph_after_delete(graph)
    plan.setdefault("warnings", []).extend(prune.get("warnings", []))
    save_navigation_graph(graph, config.work_dir)
    append_web_history({"operation_id": f"{action}_{int(time.time())}", "action": action, "delete_plan": plan, "graph_backup": backup})
    return {"dry_run": False, "delete_plan": plan, "graph_backup": backup}


def plan_delete_transition(graph: Dict[str, Any], transition_id_value: str, delete_orphan_to_state: bool) -> Dict[str, Any]:
    plan = blank_delete_plan()
    t = transition_lookup(graph).get(transition_id_value)
    if not t:
        raise ValueError(f"transition 不存在：{transition_id_value}")
    plan["transitions"].append(t)
    if delete_orphan_to_state:
        collect_descendant_delete(graph, str(t.get("to_page")), plan, {transition_id_value})
    return plan


@app.post("/api/delete_action")
def api_delete_action(req: DeleteActionRequest) -> JSONResponse:
    target_type = req.target_type.strip()
    payload = dict(req.payload or {})
    payload["dry_run"] = bool(req.dry_run)
    try:
        if target_type == "transition":
            return api_delete_transition(DeleteTransitionRequest(**payload))
        if target_type == "branch":
            return api_delete_branch(DeleteBranchRequest(**payload))
        if target_type == "page":
            return api_delete_page(DeletePageRequest(**payload))
        if target_type == "candidate":
            return api_delete_candidate(DeleteCandidateRequest(**payload))
        if target_type == "page_operation":
            return api_delete_page_operation(DeletePageOperationRequest(**payload))
        if target_type == "continued_capture":
            return api_delete_continued_capture(DeleteContinuedCaptureRequest(**payload))
        raise ValueError(f"未知删除目标类型：{target_type}")
    except Exception as exc:
        return error_response(str(exc))


@app.post("/api/delete_transition")
def api_delete_transition(req: DeleteTransitionRequest) -> JSONResponse:
    try:
        graph = load_navigation_graph(config.work_dir)
        plan = plan_delete_transition(graph, req.transition_id, req.delete_orphan_to_state)
        return ok_response(**finalize_delete("delete_transition", graph, plan, req.dry_run))
    except Exception as exc:
        return error_response(str(exc))


@app.post("/api/delete_branch")
def api_delete_branch(req: DeleteBranchRequest) -> JSONResponse:
    try:
        graph = load_navigation_graph(config.work_dir)
        plan = plan_delete_transition(graph, req.transition_id, req.delete_descendants)
        return ok_response(**finalize_delete("delete_branch", graph, plan, req.dry_run))
    except Exception as exc:
        return error_response(str(exc))


@app.post("/api/delete_page")
def api_delete_page(req: DeletePageRequest) -> JSONResponse:
    try:
        if req.page_name == "Pages_root":
            raise ValueError("不允许删除 Pages_root")
        graph = load_navigation_graph(config.work_dir)
        plan = blank_delete_plan()
        plan["states"].append(req.page_name)
        for t in graph.get("transitions", []):
            if (req.delete_incoming and t.get("to_page") == req.page_name) or (req.delete_outgoing and t.get("from_page") == req.page_name):
                plan["transitions"].append(t)
        st = graph.get("states", {}).get(req.page_name, {})
        plan["page_operations"] = [{"page_name": req.page_name, "operation_id": op.get("operation_id")} for op in st.get("page_operations", []) if op.get("operation_id")]
        plan["continued_captures"] = [{"page_name": req.page_name, "capture_id": c.get("capture_id")} for c in st.get("continued_captures", []) if c.get("capture_id")]
        result = finalize_delete("delete_page", graph, plan, req.dry_run)
        if not req.dry_run:
            save_current_path_session(config.work_dir, "Pages_root")
        return ok_response(**result)
    except Exception as exc:
        return error_response(str(exc))


@app.post("/api/delete_candidate")
def api_delete_candidate(req: DeleteCandidateRequest) -> JSONResponse:
    try:
        graph = load_navigation_graph(config.work_dir)
        state = graph.get("states", {}).get(req.page_name, {})
        candidates = state.get("merged_candidates", []) or []
        cand = next((c for c in candidates if candidate_id(c) == req.candidate_id), None)
        if not cand:
            raise ValueError(f"候选不存在：{req.candidate_id}")
        plan = blank_delete_plan()
        plan["candidates"].append({"page_name": req.page_name, "candidate_id": req.candidate_id, "action": "delete_candidate"})
        if cand.get("transition_ids") and not req.delete_linked_transitions:
            plan["warnings"].append("该候选控件关联了已录制跳转，只删除候选可能造成 transition 缺少控件引用。")
        if req.delete_linked_transitions:
            for tid in cand.get("transition_ids", []) or []:
                sub = plan_delete_transition(graph, tid, True)
                plan["transitions"].extend(sub["transitions"])
                plan["states"].extend(x for x in sub["states"] if x not in plan["states"])
        if req.delete_linked_operations:
            plan["page_operations"].extend({"page_name": req.page_name, "operation_id": oid} for oid in cand.get("operation_ids", []) or [])
        if not req.dry_run:
            state["merged_candidates"] = [c for c in candidates if candidate_id(c) != req.candidate_id]
        return ok_response(**finalize_delete("delete_candidate", graph, plan, req.dry_run))
    except Exception as exc:
        return error_response(str(exc))


@app.post("/api/delete_page_operation")
def api_delete_page_operation(req: DeletePageOperationRequest) -> JSONResponse:
    try:
        graph = load_navigation_graph(config.work_dir)
        plan = blank_delete_plan()
        plan["page_operations"].append({"page_name": req.page_name, "operation_id": req.operation_id})
        state = graph.get("states", {}).get(req.page_name, {})
        for c in state.get("merged_candidates", []) or []:
            if c.get("requires_operation_id") == req.operation_id or c.get("source_operation_id") == req.operation_id or req.operation_id in (c.get("operation_ids") or []):
                plan["candidates"].append({"page_name": req.page_name, "candidate_id": candidate_id(c), "action": "delete_revealed" if req.delete_revealed_candidates else "remove_operation_ref"})
        if not req.delete_revealed_candidates:
            plan["keep_revealed_candidates"] = True
        return ok_response(**finalize_delete("delete_page_operation", graph, plan, req.dry_run))
    except Exception as exc:
        return error_response(str(exc))


@app.post("/api/delete_continued_capture")
def api_delete_continued_capture(req: DeleteContinuedCaptureRequest) -> JSONResponse:
    try:
        graph = load_navigation_graph(config.work_dir)
        plan = blank_delete_plan()
        state = graph.get("states", {}).get(req.page_name, {})
        cap = next((c for c in state.get("continued_captures", []) or [] if c.get("capture_id") == req.capture_id), None)
        if not cap:
            raise ValueError(f"续录不存在：{req.capture_id}")
        plan["continued_captures"].append({"page_name": req.page_name, "capture_id": req.capture_id})
        if cap.get("screenshot"):
            plan["files"].append(cap["screenshot"])
        for c in state.get("merged_candidates", []) or []:
            if c.get("source_capture_id") == req.capture_id:
                if (c.get("transition_ids") or c.get("operation_ids")) and req.delete_candidates_from_capture:
                    plan["warnings"].append(f"候选 {candidate_id(c)} 有关联记录，将只移除 source_capture_id")
                plan["candidates"].append({"page_name": req.page_name, "candidate_id": candidate_id(c), "action": "delete_from_capture"})
        if not req.delete_candidates_from_capture:
            plan["keep_capture_candidates"] = True
        return ok_response(**finalize_delete("delete_continued_capture", graph, plan, req.dry_run))
    except Exception as exc:
        return error_response(str(exc))


@app.post("/api/swipe_horizontal")
def api_swipe_horizontal(req: SwipeHorizontalRequest) -> JSONResponse:
    try:
        current = read_current_state(capture=False)
        direction = req.direction.lower()
        execute_horizontal_swipe(config.device_id, direction, current.get("screen_metrics", {}))
        graph = load_navigation_graph(config.work_dir)
        active_page = current["active_page"]
        active_state = current["active_state"]
        operate = "swipe_left" if direction == "left" else "swipe_right"
        target = horizontal_target(direction)
        view_state = next_horizontal_view_state(graph, str(active_state.get("base_page") or active_page))
        graph.setdefault("states", {})[view_state["page_name"]] = view_state
        effect = "local_horizontal_view_changed"
        add_transition(graph, {
            "transition_id": transition_id(active_page, operate, view_state["page_name"], target, effect),
            "from_page": active_page,
            "to_page": view_state["page_name"],
            "operate": operate,
            "target": target,
            "effect": effect,
            "base_page": view_state["base_page"],
        })
        save_navigation_graph(graph, config.work_dir)
        save_current_path_session(config.work_dir, view_state["page_name"], view_state["base_page"])
        time.sleep(1.0)
        return ok_response(**read_current_state(capture=True))
    except Exception as exc:
        return error_response(str(exc))


@app.post("/api/back")
def api_back() -> JSONResponse:
    try:
        execute_back(config.device_id)
        time.sleep(1.0)
        return ok_response(**read_current_state(capture=True))
    except Exception as exc:
        return error_response(str(exc))


@app.post("/api/clear_pending")
def api_clear_pending() -> JSONResponse:
    try:
        path = pending_transition_path(config.work_dir)
        if path.exists():
            path.unlink()
        clear_pending_action_chain()
        return ok_response(pending=None, pending_action_chain=None)
    except Exception as exc:
        return error_response(str(exc))


@app.get("/api/graph")
def api_graph() -> JSONResponse:
    try:
        path = navigation_graph_path(config.work_dir)
        return JSONResponse(load_json(path) if path.exists() else load_navigation_graph(config.work_dir))
    except Exception as exc:
        return error_response(str(exc))


@app.get("/screen")
def screen() -> FileResponse:
    path = config.output_dir / "current_screen.png"
    headers = {"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0", "Pragma": "no-cache"}
    return FileResponse(path, media_type="image/png", headers=headers)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="设置导航录制 Web 控制台")
    parser.add_argument("--work-dir", type=Path, default=DEFAULT_WORK_DIR)
    parser.add_argument("--device-id", default=DEFAULT_DEVICE_ID)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config.work_dir = args.work_dir
    config.device_id = args.device_id
    config.output_dir = args.output_dir or (args.work_dir / "outputs" / "latest")
    app.mount("/static", StaticFiles(directory=str(APP_DIR / "static")), name="static")
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
