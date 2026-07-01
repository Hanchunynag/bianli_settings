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
from typing import Any, Dict, List, Optional, Tuple

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
    capture_artifacts,
    current_path_session_path,
    extract_navigation_candidates,
    get_attr,
    get_key,
    get_text,
    get_type,
    is_stable_key_for_navigation,
    is_stable_text_for_navigation,
    is_visible,
    load_current_path_session,
    load_json,
    load_navigation_graph,
    meaningful_texts,
    navigation_dir,
    navigation_graph_path,
    next_horizontal_view_state,
    now_iso,
    parse_rect,
    pending_transition_path,
    save_current_path_session,
    save_json,
    save_navigation_graph,
    screen_metrics_from_root,
    to_bool,
    transition_id,
    walk,
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


class TapPointRequest(BaseModel):
    x: int
    y: int
    normalized_point: List[float]
    expect: str = "new_page"
    effect: str = ""


class SwipePointRequest(BaseModel):
    x: int
    y: int
    normalized_point: List[float]
    direction: str


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


def execute_tap(device_id: str, x: int, y: int) -> None:
    """在设备上点击指定截图坐标，优先 uitest uiInput，失败后回退 input tap。"""
    x, y = int(x), int(y)
    base = ["hdc", "-t", device_id, "shell"]
    run_hdc_with_fallback([
        base + ["uitest", "uiInput", "click", str(x), str(y)],
        base + ["input", "tap", str(x), str(y)],
    ], f"点击 [{x}, {y}]")


def execute_swipe(device_id: str, start_x: int, start_y: int, end_x: int, end_y: int, duration_ms: int = 300) -> None:
    """在设备上执行滑动，优先 uitest uiInput，失败后回退 input swipe。"""
    sx, sy, ex, ey, duration = map(int, [start_x, start_y, end_x, end_y, duration_ms])
    base = ["hdc", "-t", device_id, "shell"]
    run_hdc_with_fallback([
        base + ["uitest", "uiInput", "swipe", str(sx), str(sy), str(ex), str(ey), str(duration)],
        base + ["input", "swipe", str(sx), str(sy), str(ex), str(ey), str(duration)],
    ], f"滑动 [{sx}, {sy}] -> [{ex}, {ey}]")


def execute_back(device_id: str) -> None:
    """执行系统返回键，优先 uitest uiInput，失败后回退 input keyevent。"""
    base = ["hdc", "-t", device_id, "shell"]
    run_hdc_with_fallback([
        base + ["uitest", "uiInput", "keyEvent", "Back"],
        base + ["input", "keyevent", "BACK"],
    ], "返回")


def clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def stable_text_for_node(node: Dict[str, Any]) -> str:
    """返回节点或其子树中适合作为导航描述的稳定文本。"""
    direct = get_text(node)
    if is_stable_text_for_navigation(direct):
        return direct
    return next((t for t in meaningful_texts(node) if is_stable_text_for_navigation(t)), "")


def matched_node_from_raw(node: Dict[str, Any], rect: Dict[str, Any]) -> Dict[str, Any]:
    """把 UI tree 节点压缩成可落盘的命中节点摘要。"""
    raw_key = get_key(node)
    return {
        "type": get_type(node),
        "text": stable_text_for_node(node),
        "key": raw_key if is_stable_key_for_navigation(raw_key) else "",
        "bounds": get_attr(node, "bounds"),
        "bounds_center": rect.get("center"),
        "clickable": to_bool(get_attr(node, "clickable", "false")),
        "area": rect.get("area", 0),
    }


def hit_test_ui_node(root_json: Dict[str, Any], x: int, y: int) -> Optional[Dict[str, Any]]:
    """在 UI tree 中命中包含截图坐标的最合适控件。\n\n    Toggle/Switch/CheckBox 优先于按钮与行容器，避免点击 WLAN 开关时被整行 Row 抢占；\n    同时过滤 Navigation/NavDestination/全屏 Column 这类纯布局大容器。\n    """
    x, y = int(x), int(y)
    root_metrics = screen_metrics_from_root(root_json)
    screen_size = root_metrics.get("screen_size") or [0, 0]
    screen_area = int(screen_size[0] or 0) * int(screen_size[1] or 0)
    hits: List[Tuple[Tuple[int, int, int, int], Dict[str, Any]]] = []
    preferred_containers = {"Row", "ListItem", "Column"}
    layout_types = {"Navigation", "NavDestination", "Scroll", "List", "Flex", "Stack"}

    for node, _, _ in walk(root_json):
        if not is_visible(node):
            continue
        rect = parse_rect(get_attr(node, "bounds"))
        if not rect.get("valid"):
            continue
        if not (rect["left"] <= x <= rect["right"] and rect["top"] <= y <= rect["bottom"]):
            continue
        node_type = get_type(node)
        area = int(rect.get("area") or 0)
        if node_type in layout_types and screen_area and area > screen_area * 0.55:
            continue
        if node_type == "Column" and screen_area and area > screen_area * 0.55:
            continue
        clickable = to_bool(get_attr(node, "clickable", "false"))
        text = stable_text_for_node(node)
        key = get_key(node)
        low_type = node_type.lower()
        if any(t in low_type for t in ["toggle", "switch", "checkbox"]):
            rank = 0
        elif "button" in low_type:
            rank = 1
        elif clickable and node_type in preferred_containers:
            rank = 2
        elif is_stable_key_for_navigation(key):
            rank = 3
        elif text:
            rank = 4
        else:
            rank = 6
        # rank 越小越优先；面积越小越优先；clickable 节点略优先。
        hits.append(((rank, 0 if clickable else 1, area, -len(text)), matched_node_from_raw(node, rect)))

    if not hits:
        return None
    hits.sort(key=lambda item: item[0])
    return hits[0][1]


def build_target_from_hit(x: int, y: int, normalized_point: List[float], matched_node: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """根据截图坐标和 hit-test 结果构造 coordinate_hit target。"""
    point = [int(x), int(y)]
    node = matched_node or {}
    text = str(node.get("text") or "")
    key = str(node.get("key") or "")
    node_type = str(node.get("type") or "")
    if text:
        desc = text
    elif key:
        desc = f"点击 WLAN 开关" if "wlan" in key.lower() and any(t in node_type.lower() for t in ["toggle", "switch"]) else key
    else:
        desc = f"点击坐标 [{point[0]}, {point[1]}]"
    return {
        "type": "coordinate_hit",
        "value": point,
        "point": point,
        "normalized_point": normalized_point,
        "key_description": desc,
        "step_prompt": desc,
        "fallback_locator": "normalized_point",
        "matched_node": node,
    }


def build_swipe_target(x: int, y: int, normalized_point: List[float], direction: str, matched_node: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """构造坐标点选式横向滑动 target。"""
    left = direction == "left"
    return {
        "type": "coordinate_swipe",
        "operate": "swipe_left" if left else "swipe_right",
        "value": "横向列表向左滑动一次" if left else "横向列表向右滑动一次",
        "point": [int(x), int(y)],
        "normalized_point": normalized_point,
        "direction": direction,
        "axis": "horizontal",
        "scope": "local_container",
        "key_description": "横向局部区域",
        "step_prompt": "在点击位置附近向左滑动一次" if left else "在点击位置附近向右滑动一次",
        "matched_node": matched_node or {},
    }


def swipe_coordinates(root_json: Dict[str, Any], x: int, y: int, direction: str, matched_node: Optional[Dict[str, Any]]) -> Tuple[int, int, int, int]:
    """根据命中节点 bounds 或屏幕宽度计算局部横向滑动坐标。"""
    rect = parse_rect((matched_node or {}).get("bounds", ""))
    if rect.get("valid"):
        left, right, top, bottom = int(rect["left"]), int(rect["right"]), int(rect["top"]), int(rect["bottom"])
        width = max(1, right - left)
        yy = clamp(int(y), top + 1, bottom - 1)
        if direction == "left":
            return right - int(width * 0.2), yy, left + int(width * 0.2), yy
        return left + int(width * 0.2), yy, right - int(width * 0.2), yy
    size = screen_metrics_from_root(root_json).get("screen_size") or [1080, 2400]
    width = int(size[0])
    yy = int(y)
    if direction == "left":
        return int(width * 0.8), yy, int(width * 0.2), yy
    return int(width * 0.2), yy, int(width * 0.8), yy


def web_history_path(work_dir: Path) -> Path:
    return navigation_dir(work_dir) / "web_record_history.jsonl"


def load_web_history(work_dir: Path) -> List[Dict[str, Any]]:
    path = web_history_path(work_dir)
    if not path.exists():
        return []
    records: List[Dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def write_web_history(work_dir: Path, records: List[Dict[str, Any]]) -> None:
    path = web_history_path(work_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


def append_web_history(work_dir: Path, record: Dict[str, Any]) -> None:
    """追加网页录制历史，用于 undo_last。"""
    path = web_history_path(work_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def referenced_by_any_transition(graph: Dict[str, Any], page_name: str) -> bool:
    return any(t.get("from_page") == page_name or t.get("to_page") == page_name for t in graph.get("transitions", []))


def undo_last_web_record(work_dir: Path) -> Dict[str, Any]:
    """撤销最后一条未撤销的网页录制历史，并同步更新导航图和当前会话。"""
    records = load_web_history(work_dir)
    last_index = next((i for i in range(len(records) - 1, -1, -1) if not records[i].get("undone")), None)
    if last_index is None:
        raise RuntimeError("没有可撤销的网页录制操作")
    record = records[last_index]
    graph = load_navigation_graph(work_dir)
    transition_id_to_remove = record.get("transition_id")
    graph["transitions"] = [t for t in graph.get("transitions", []) if t.get("transition_id") != transition_id_to_remove]
    created_state = str(record.get("created_state") or "")
    if created_state and created_state != "Pages_root" and not referenced_by_any_transition(graph, created_state):
        graph.get("states", {}).pop(created_state, None)
    session = load_current_path_session(work_dir)
    if session.get("active_page") == created_state:
        save_current_path_session(work_dir, str(record.get("from_page") or "Pages_root"))
    elif current_path_session_path(work_dir).exists():
        # 保持已有会话文件不变，仅确保路径函数被纳入撤销判断。
        pass
    record["undone"] = True
    record["undone_at"] = now_iso()
    records[last_index] = record
    write_web_history(work_dir, records)
    save_navigation_graph(graph, work_dir)
    return record



def candidate_merge_key(candidate: Dict[str, Any]) -> str:
    """为继续录制候选入口生成稳定去重 key。"""
    key = str(candidate.get("key") or "").strip()
    if key:
        return f"key::{key}"
    text = str(candidate.get("text") or "").strip()
    ctype = str(candidate.get("type") or "")
    if text:
        return f"text::{ctype}::{text}"
    center = candidate.get("bounds_center") or ["", ""]
    x = center[0] if isinstance(center, list) and len(center) > 0 else ""
    y = center[1] if isinstance(center, list) and len(center) > 1 else ""
    return f"bounds::{ctype}::{x}::{y}"


def candidates_content_signature(candidates: List[Dict[str, Any]]) -> str:
    """基于当前屏幕候选入口生成内容签名，用于识别重复视图。"""
    pieces = []
    for candidate in candidates:
        pieces.append({
            "text": candidate.get("text") or "",
            "key": candidate.get("key") or "",
            "type": candidate.get("type") or "",
            "bounds_center": candidate.get("bounds_center") or [],
        })
    payload = json.dumps(pieces, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8", errors="ignore")).hexdigest()[:16]


def next_continue_capture_id(state: Dict[str, Any], active_page: str) -> str:
    """为当前页面生成 Pages_xxx__continue_NNN 格式的续录 capture_id。"""
    captures = state.setdefault("continued_captures", [])
    return f"{active_page}__continue_{len(captures) + 1:03d}"


def current_active_page_without_pending(work_dir: Path, fallback_state: Optional[Dict[str, Any]] = None) -> str:
    """读取当前 active_page；不读取或创建 pending，不改变 current_path_session。"""
    session = load_current_path_session(work_dir)
    active_page = str(session.get("active_page") or "")
    if active_page:
        return active_page
    if fallback_state and fallback_state.get("page_name"):
        return str(fallback_state["page_name"])
    return ""


def merge_continue_candidates(state: Dict[str, Any], candidates: List[Dict[str, Any]], capture_id: str, captured_at: str) -> int:
    """把本屏候选入口合并到 state.merged_candidates，并返回新增数量。"""
    merged = state.setdefault("merged_candidates", {})
    added = 0
    for candidate in candidates:
        key = candidate_merge_key(candidate)
        if key not in merged:
            item = {
                "text": candidate.get("text") or "",
                "key": candidate.get("key") or "",
                "type": candidate.get("type") or "",
                "bounds": candidate.get("bounds") or "",
                "bounds_center": candidate.get("bounds_center"),
                "source_capture_id": capture_id,
                "first_seen_at": captured_at,
                "last_seen_capture_id": capture_id,
                "last_seen_at": captured_at,
            }
            merged[key] = item
            added += 1
        else:
            merged[key]["last_seen_capture_id"] = capture_id
            merged[key]["last_seen_at"] = captured_at
    return added


def continue_current_page_capture() -> Dict[str, Any]:
    """手动滚动后的当前页面续录：只合并候选入口，不生成 transition/pending，不修改 active_page。"""
    # 先用 latest 文件识别 fallback active_state；这一步不强制采集，也不读 pending。
    fallback_state: Optional[Dict[str, Any]] = None
    latest_json = config.output_dir / "current_ui_tree.json"
    if latest_json.exists():
        latest_root = load_json(latest_json)
        annotate(latest_root)
        graph_for_fallback = load_navigation_graph(config.work_dir)
        fallback_state = active_navigation_state(config.work_dir, graph_for_fallback, build_navigation_state(latest_root))
    active_page = current_active_page_without_pending(config.work_dir, fallback_state)
    if not active_page:
        raise RuntimeError("无法确定 active_page，请先点击“重新采集”。")

    if not capture_artifacts(config.device_id, config.output_dir):
        raise RuntimeError("hdc 采集失败，请检查设备连接、hdc PATH 和授权状态")
    root_json = load_json(config.output_dir / "current_ui_tree.json")
    annotate(root_json)
    detected_state = build_navigation_state(root_json)
    if detected_state.get("page_name") != active_page:
        raise RuntimeError("当前页面与 active_page 不一致，不能作为当前页面续录。请确认是否误进入新页面。")

    candidates = extract_navigation_candidates(root_json)
    graph = load_navigation_graph(config.work_dir)
    state = graph.setdefault("states", {}).setdefault(active_page, detected_state)
    # 保留原页面身份字段，但用本次识别结果刷新 title/signature 等基础信息。
    for field in ["page_name", "page_description", "last_title", "signature"]:
        state[field] = detected_state.get(field, state.get(field))

    signature = candidates_content_signature(candidates)
    captures = state.setdefault("continued_captures", [])
    duplicate = next((c for c in captures if c.get("content_signature") == signature), None)
    captured_at = now_iso()
    warning = ""
    if duplicate:
        capture_id = str(duplicate.get("capture_id") or next_continue_capture_id(state, active_page))
        warning = "当前视图可能已经录制过，本次未重复追加 capture，但已刷新当前截图和候选入口。"
    else:
        capture_id = next_continue_capture_id(state, active_page)
        rel_screenshot = Path("outputs") / "navigation" / "continued_captures" / f"{capture_id}.png"
        screenshot_path = config.work_dir / rel_screenshot
        screenshot_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(config.output_dir / "current_screen.png", screenshot_path)
        captures.append({
            "capture_id": capture_id,
            "captured_at": captured_at,
            "screenshot": rel_screenshot.as_posix(),
            "candidate_count": len(candidates),
            "texts": [c.get("text") for c in candidates if c.get("text")][:20],
            "content_signature": signature,
        })

    added = merge_continue_candidates(state, candidates, capture_id, captured_at)
    save_navigation_graph(graph, config.work_dir)
    screen_metrics = screen_metrics_from_root(root_json)
    return {
        "state": detected_state,
        "active_state": state,
        "active_page": active_page,
        "candidates": candidates,
        "continued_captures_count": len(state.get("continued_captures", [])),
        "merged_candidates_count": len(state.get("merged_candidates", {})),
        "added_merged_candidates_count": added,
        "pending": None,
        "warning": warning,
        "screenshot_url": f"/screen?t={int(time.time() * 1000)}",
        "screen_metrics": screen_metrics,
        "screen_size": screen_metrics.get("screen_size"),
    }


def pending_data() -> Optional[Dict[str, Any]]:
    path = pending_transition_path(config.work_dir)
    return load_json(path) if path.exists() else None


def has_parent_transition(graph: Dict[str, Any], page_name: str) -> bool:
    return any(t.get("to_page") == page_name for t in graph.get("transitions", []))


def warning_for_state(graph: Dict[str, Any], state: Dict[str, Any]) -> str:
    page = state.get("page_name", "")
    if page and page != "Pages_root" and not pending_transition_path(config.work_dir).exists() and not has_parent_transition(graph, page):
        return "当前页面没有 pending transition，且导航图中没有父级来源。说明你可能是手动进入了当前页面，无法自动知道父级页面。请返回父页面后点击截图位置录制。"
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
    graph.setdefault("states", {})[state["page_name"]] = state
    save_navigation_graph(graph, config.work_dir)
    active_state = active_navigation_state(config.work_dir, graph, state)
    graph_state = graph.get("states", {}).get(active_state.get("page_name"), {})
    if isinstance(graph_state, dict):
        if "continued_captures" in graph_state:
            active_state.setdefault("continued_captures", graph_state.get("continued_captures", []))
        if "merged_candidates" in graph_state:
            active_state.setdefault("merged_candidates", graph_state.get("merged_candidates", {}))
    candidates = extract_navigation_candidates(root_json)
    screen_metrics = screen_metrics_from_root(root_json)
    return {
        "state": state,
        "active_state": active_state,
        "active_page": active_state.get("page_name"),
        "candidates": candidates,
        "continued_captures_count": len(active_state.get("continued_captures", [])),
        "merged_candidates_count": len(active_state.get("merged_candidates", {})),
        "pending": pending_data(),
        "warning": warning_for_state(graph, state),
        "screenshot_url": f"/screen?t={int(time.time() * 1000)}",
        "screen_metrics": screen_metrics,
        "screen_size": screen_metrics.get("screen_size"),
    }


def transition_from_graph(graph: Dict[str, Any], tid: str) -> Optional[Dict[str, Any]]:
    return next((t for t in graph.get("transitions", []) if t.get("transition_id") == tid), None)


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
        current = read_current_state(capture=True)
        root_json = load_json(config.output_dir / "current_ui_tree.json")
        annotate(root_json)
        graph = load_navigation_graph(config.work_dir)
        state = current["state"]
        active_state = active_navigation_state(config.work_dir, graph, state)
        active_page = active_state["page_name"]
        matched = hit_test_ui_node(root_json, req.x, req.y)
        target = build_target_from_hit(req.x, req.y, req.normalized_point, matched)
        if req.expect:
            target["expect"] = req.expect
        pending = {"from_page": active_page, "operate": "tap", "target": target, "created_at": now_iso()}
        if req.effect:
            pending["effect"] = req.effect
        save_json(pending, pending_transition_path(config.work_dir), "未完成导航转移")
        execute_tap(config.device_id, req.x, req.y)
        time.sleep(1.2)
        after = read_current_state(capture=True)
        new_state = after["state"]
        expected_tid = transition_id(active_page, "tap", new_state["page_name"], target, req.effect or "")
        graph = load_navigation_graph(config.work_dir)
        graph.setdefault("states", {})[new_state["page_name"]] = new_state
        completed = auto_complete_pending_if_needed(config.work_dir, graph, new_state)
        graph = load_navigation_graph(config.work_dir)
        transition = transition_from_graph(graph, expected_tid)
        if completed and transition:
            append_web_history(config.work_dir, {
                "operation_id": f"web_{int(time.time() * 1000)}",
                "created_at": now_iso(),
                "action": "tap_point",
                "from_page": active_page,
                "to_page": new_state["page_name"],
                "transition_id": expected_tid,
                "created_state": new_state["page_name"],
                "target": target,
                "undone": False,
            })
        return ok_response(**read_current_state(capture=False))
    except Exception as exc:
        return error_response(str(exc))


@app.post("/api/tap_candidate")
def api_tap_candidate(req: TapCandidateRequest) -> JSONResponse:
    try:
        current = read_current_state(capture=True)
        candidates = current["candidates"]
        if req.index < 1 or req.index > len(candidates):
            raise ValueError(f"候选编号无效：{req.index}")
        center = candidates[req.index - 1].get("bounds_center")
        if not isinstance(center, list) or len(center) != 2:
            raise ValueError("候选入口没有有效 bounds_center，无法按坐标录制")
        return api_tap_point(TapPointRequest(x=int(center[0]), y=int(center[1]), normalized_point=normalize_from_screen(center, current), expect=req.expect, effect=req.effect))
    except Exception as exc:
        return error_response(str(exc))


def normalize_from_screen(point: List[int], state_payload: Dict[str, Any]) -> List[float]:
    size = state_payload.get("screen_size") or state_payload.get("screen_metrics", {}).get("screen_size") or [1, 1]
    return [round(float(point[0]) / float(size[0] or 1), 6), round(float(point[1]) / float(size[1] or 1), 6)]


@app.post("/api/swipe_point")
def api_swipe_point(req: SwipePointRequest) -> JSONResponse:
    try:
        direction = req.direction.lower()
        if direction not in {"left", "right"}:
            raise ValueError("direction 必须是 left 或 right")
        current = read_current_state(capture=True)
        root_json = load_json(config.output_dir / "current_ui_tree.json")
        annotate(root_json)
        matched = hit_test_ui_node(root_json, req.x, req.y)
        sx, sy, ex, ey = swipe_coordinates(root_json, req.x, req.y, direction, matched)
        execute_swipe(config.device_id, sx, sy, ex, ey, 300)
        graph = load_navigation_graph(config.work_dir)
        active_state = active_navigation_state(config.work_dir, graph, current["state"])
        active_page = active_state["page_name"]
        base_page = str(active_state.get("base_page") or active_page)
        operate = "swipe_left" if direction == "left" else "swipe_right"
        target = build_swipe_target(req.x, req.y, req.normalized_point, direction, matched)
        view_state = next_horizontal_view_state(graph, base_page)
        graph.setdefault("states", {})[view_state["page_name"]] = view_state
        effect = "local_horizontal_view_changed"
        tid = transition_id(active_page, operate, view_state["page_name"], target, effect)
        add_transition(graph, {
            "transition_id": tid,
            "from_page": active_page,
            "to_page": view_state["page_name"],
            "operate": operate,
            "target": target,
            "effect": effect,
            "base_page": view_state["base_page"],
        })
        save_navigation_graph(graph, config.work_dir)
        save_current_path_session(config.work_dir, view_state["page_name"], view_state["base_page"])
        append_web_history(config.work_dir, {
            "operation_id": f"web_{int(time.time() * 1000)}",
            "created_at": now_iso(),
            "action": "swipe_point",
            "from_page": active_page,
            "to_page": view_state["page_name"],
            "transition_id": tid,
            "created_state": view_state["page_name"],
            "target": target,
            "undone": False,
        })
        time.sleep(1.0)
        return ok_response(**read_current_state(capture=True))
    except Exception as exc:
        return error_response(str(exc))


@app.post("/api/swipe_horizontal")
def api_swipe_horizontal(req: SwipeHorizontalRequest) -> JSONResponse:
    try:
        current = read_current_state(capture=False)
        size = current.get("screen_size") or [1080, 2400]
        x, y = int(size[0] / 2), int(size[1] * 0.55)
        return api_swipe_point(SwipePointRequest(x=x, y=y, normalized_point=normalize_from_screen([x, y], current), direction=req.direction))
    except Exception as exc:
        return error_response(str(exc))


@app.post("/api/continue_current_page")
def api_continue_current_page() -> JSONResponse:
    try:
        return ok_response(**continue_current_page_capture())
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


@app.post("/api/undo_last")
def api_undo_last() -> JSONResponse:
    try:
        undone = undo_last_web_record(config.work_dir)
        return ok_response(undone=undone, **read_current_state(capture=False))
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
