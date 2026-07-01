#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FastAPI 设置导航录制 Web 控制台。"""

import argparse
import hashlib
import json
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

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


class SwipeHorizontalRequest(BaseModel):
    direction: str


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


def pending_data() -> Optional[Dict[str, Any]]:
    path = pending_transition_path(config.work_dir)
    return load_json(path) if path.exists() else None


def has_parent_transition(graph: Dict[str, Any], page_name: str) -> bool:
    return any(t.get("to_page") == page_name for t in graph.get("transitions", []))


def warning_for_state(graph: Dict[str, Any], state: Dict[str, Any]) -> str:
    page = state.get("page_name", "")
    if page and page != "Pages_root" and not pending_transition_path(config.work_dir).exists() and not has_parent_transition(graph, page):
        return "当前页面没有 pending transition，且导航图中没有父级来源。说明你可能是手动进入了当前页面，无法自动知道父级页面。请返回父页面后点击候选入口录制。"
    return ""


def read_current_state(capture: bool) -> Dict[str, Any]:
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
        preserve_keys = ["page_operations", "merged_candidates"]
        if existing_state.get("is_overlay"):
            preserve_keys.extend(["state_type", "is_overlay", "overlay_parent", "overlay_title", "page_description"])
        state.update({k: existing_state[k] for k in preserve_keys if k in existing_state})
    graph.setdefault("states", {})[state["page_name"]] = state
    save_navigation_graph(graph, config.work_dir)
    active_state = active_navigation_state(config.work_dir, graph, state)
    candidates = extract_navigation_candidates(root_json)
    return {
        "state": state,
        "active_state": active_state,
        "active_page": active_state.get("page_name"),
        "candidates": candidates,
        "pending": pending_data(),
        "warning": warning_for_state(graph, state),
        "screenshot_url": f"/screen?t={int(time.time() * 1000)}",
        "screen_metrics": screen_metrics_from_root(root_json),
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


def candidate_merge_key(candidate: Dict[str, Any]) -> str:
    key = str(candidate.get("key") or "").strip()
    if key:
        return f"key:{key}"
    text = str(candidate.get("text") or "").strip()
    ctype = str(candidate.get("component_type") or candidate.get("type") or "").strip()
    if text or ctype:
        return f"text_type:{text}|{ctype}"
    return ""


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


def merge_revealed_candidates(state_entry: Dict[str, Any], revealed: List[Dict[str, Any]]) -> None:
    merged = state_entry.setdefault("merged_candidates", [])
    existing = {candidate_merge_key(c) for c in merged if candidate_merge_key(c)}
    for candidate in revealed:
        key = candidate_merge_key(candidate)
        if not key or key in existing:
            continue
        merged.append(candidate)
        existing.add(key)


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
            revealed.append({**item, "source_operation_id": operation_id, "requires_operation_id": operation_id})
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
    merge_revealed_candidates(state_entry, revealed)
    save_navigation_graph(graph, config.work_dir)
    refreshed = read_current_state(capture=False)
    return {**refreshed, "message": f"已记录页面内变化：新增 {len(revealed)} 个控件，消失 {len(hidden)} 个控件。"}


def record_tap_at_point(x: int, y: int, expect: str = "new_page", effect: str = "", manual_label: str = "") -> Dict[str, Any]:
    current = read_current_state(capture=True)
    ensure_page_consistency(current)
    root_json = load_json(config.output_dir / "current_ui_tree.json")
    annotate(root_json)
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
    graph = load_navigation_graph(config.work_dir)
    from_page = current["active_page"]
    execute_tap(config.device_id, [int(x), int(y)])
    time.sleep(1.2)
    after = read_current_state(capture=True)
    to_page = after["state"]["page_name"]
    graph = load_navigation_graph(config.work_dir)
    graph.setdefault("states", {})[after["state"]["page_name"]] = after["state"]
    if to_page == from_page:
        refreshed = read_current_state(capture=False)
        return {**refreshed, "message": "点击后仍停留在当前页面。如该操作用于展开或刷新页面内容，请使用‘录制页面内变化’模式。"}
    transition = {
        "transition_id": transition_id(from_page, "tap", to_page, target, effect),
        "from_page": from_page,
        "to_page": to_page,
        "operate": "tap",
        "target": target,
    }
    if effect:
        transition["effect"] = effect
    add_transition(graph, transition)
    save_navigation_graph(graph, config.work_dir)
    save_current_path_session(config.work_dir, to_page)
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
        return ok_response(pending=None)
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
