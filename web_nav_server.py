#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FastAPI 设置导航录制 Web 控制台。"""

import argparse
import shutil
import time
from pathlib import Path
from typing import Any, Dict, Optional

try:
    from fastapi import FastAPI, Request
    from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
    from fastapi.staticfiles import StaticFiles
    import uvicorn
except ImportError as exc:  # pragma: no cover - 给用户明确安装提示
    raise SystemExit("缺少 FastAPI 依赖，请先执行：pip install fastapi uvicorn") from exc

from settings_ui_manual_recorder import (
    DEFAULT_DEVICE_ID,
    DEFAULT_WORK_DIR,
    ConsoleActionRequest,
    DeleteActionRequest,
    DeleteBranchRequest,
    DeleteCandidateRequest,
    DeleteContinuedCaptureRequest,
    DeletePageOperationRequest,
    DeletePageRequest,
    DeleteTransitionRequest,
    NavigateToPageRequest,
    PageGestureOperationRequest,
    PointRequest,
    RecordActionRequest,
    RenamePageRequest,
    SetActivePageRequest,
    SwipeHorizontalRequest,
    TapCandidateRequest,
    TapPointRequest,
    active_navigation_state,
    add_transition,
    annotate,
    append_web_history,
    build_navigation_state,
    build_page_directory,
    blank_delete_plan,
    bfs_path,
    build_semantic_target_from_node,
    candidate_from_auto,
    candidate_id,
    candidate_merge_key,
    component_summary_from_tree,
    component_changes,
    components_signature,
    clear_pending_action_chain,
    capture_artifacts,
    capture_ui_tree_only,
    contextualize_child_state,
    current_session_page,
    detect_overlay_title,
    execute_back,
    execute_gesture_operation,
    execute_horizontal_swipe,
    execute_tap,
    ensure_page_consistency,
    extract_navigation_candidates,
    find_candidate_center_for_target,
    hit_test_full_ui_tree,
    horizontal_target,
    load_json,
    load_navigation_graph,
    navigation_graph_path,
    next_horizontal_view_state,
    next_page_operation_id,
    now_iso,
    pending_transition_path,
    pending_action_chain,
    pending_data,
    resolve_detected_state,
    save_current_path_session,
    save_json,
    save_navigation_graph,
    save_pending_action_chain,
    screen_metrics_from_root,
    state_matches_graph_page,
    states_represent_same_page,
    transition_id,
    transition_id_for_steps,
    transition_step,
    transition_steps,
    transition_steps_label,
    upsert_candidate,
    upsert_clicked_target_as_candidate,
    upsert_page_variant,
    get_page_merged_candidates,
    page_operation_id,
    plan_delete_transition,
    prune_graph_after_delete,
    rename_page_references,
    step_target,
    validate_page_name_for_rename,
    warning_for_state,
    apply_delete_plan,
)

APP_DIR = Path(__file__).resolve().parent
app = FastAPI(title="Settings Navigation Recorder")


class ServerConfig:
    work_dir: Path = DEFAULT_WORK_DIR
    device_id: str = DEFAULT_DEVICE_ID
    output_dir: Path = DEFAULT_WORK_DIR / "outputs" / "latest"


config = ServerConfig()


def ok_response(**kwargs: Any) -> JSONResponse:
    return JSONResponse({"ok": True, **kwargs})


def error_response(message: str) -> JSONResponse:
    return JSONResponse({"ok": False, "error": message})


@app.exception_handler(Exception)
def api_error(_request: Request, exc: Exception) -> JSONResponse:
    """所有 API 使用统一错误结构，业务路由只保留正常流程。"""
    return error_response(str(exc))


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
    state = resolve_detected_state(graph, state, current_session_page(config.work_dir))
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
        "pending": pending_data(config.work_dir),
        "pending_action_chain": pending_action_chain(config.work_dir),
        "warning": warning_for_state(graph, state, pending_transition_path(config.work_dir).exists() or bool(pending_action_chain(config.work_dir))),
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
    state = resolve_detected_state(graph, state, active_page)
    candidates = extract_navigation_candidates(root_json)
    return {
        "state": state,
        "active_state": graph.get("states", {}).get(active_page, state),
        "active_page": active_page,
        "current_candidates": candidates,
        "candidates": candidates,
        "merged_candidates": get_page_merged_candidates(graph, active_page, []),
        "pending": pending_data(config.work_dir),
        "pending_action_chain": pending_action_chain(config.work_dir),
        "warning": warning_for_state(graph, state, pending_transition_path(config.work_dir).exists() or bool(pending_action_chain(config.work_dir))),
        "screenshot_url": f"/screen?t={int(time.time() * 1000)}",
        "screen_metrics": screen_metrics_from_root(root_json),
        "message": message,
    }


def capture_state_without_graph_write(ui_tree_only: bool = False) -> Dict[str, Any]:
    """采集并解析设备状态，但不修改导航图。"""
    capture = capture_ui_tree_only if ui_tree_only else capture_artifacts
    if not capture(config.device_id, config.output_dir):
        raise RuntimeError("hdc 采集失败，请检查设备连接、hdc PATH 和授权状态")
    root_json = load_json(config.output_dir / "current_ui_tree.json")
    annotate(root_json)
    state = build_navigation_state(root_json)
    graph = load_navigation_graph(config.work_dir)
    state = resolve_detected_state(graph, state, current_session_page(config.work_dir))
    return {"root": root_json, "state": state}


def prepare_operation(
    x: int, y: int, manual_label: str, event: str,
    strict_page: bool = True, debug: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """完成三种页面内录制共用的采集、页面校验和语义命中。"""
    before = capture_state_without_graph_write()
    graph = load_navigation_graph(config.work_dir)
    active = active_navigation_state(config.work_dir, graph, before["state"])
    active_page = str(active.get("page_name") or before["state"].get("page_name") or "")
    if not active_page:
        raise ValueError("无法确定当前页面，不能保存页面操作。")
    if strict_page and before["state"].get("page_name") != active_page:
        raise ValueError(
            f"当前检测页面 {before['state'].get('page_name')} 与 active_page {active_page} 不一致，"
            "请先重新采集或确认当前页面状态后再录制。"
        )
    hit = hit_test_full_ui_tree(before["root"], int(x), int(y))
    target = build_semantic_target_from_node(hit, manual_label=manual_label.strip())
    append_web_history(config.work_dir, {
        "event": event,
        "page_name": active_page,
        "debug": {"point": [int(x), int(y)], "hit_node": hit, "needs_manual_label": bool(target.get("needs_manual_label")), **(debug or {})},
    })
    return {"before": before, "graph": graph, "active_page": active_page, "hit": hit, "target": target}


def record_page_operation(
    x: int,
    y: int,
    *,
    mode: str,
    operate: str = "tap",
    effect: str = "",
    manual_label: str = "",
) -> Dict[str, Any]:
    """统一录制页面内操作；popup 即使识别为 Overlay_xxx 也始终归属点击前的 active_page。"""
    events = {"popup": "popup_tap", "same_page": "tap_same_page_operation", "gesture": "page_gesture_operation"}
    debug = {"operate": operate, "effect": effect} if mode == "gesture" else None
    ctx = prepare_operation(x, y, manual_label, events[mode], strict_page=mode != "popup", debug=debug)
    before, graph, active_page, hit, target = (ctx[k] for k in ("before", "graph", "active_page", "hit", "target"))

    if target.get("needs_manual_label"):
        messages = {
            "popup": "命中控件缺少稳定 key/text，请填写操作描述。",
            "same_page": "命中控件缺少稳定 key/text，请手动填写描述后再保存。",
            "gesture": "命中区域缺少稳定 key/text，请手动填写操作对象描述后再保存。",
        }
        current = (
            state_response_from_capture(before["root"], before["state"], graph, active_page)
            if mode == "popup" else read_current_state(capture=False)
        )
        flags = {"popup": "popup_mode", "same_page": "same_page_mode", "gesture": "page_operation_mode"}
        details = {"operate": operate, "effect": effect} if mode == "gesture" else {}
        return {**current, **details, "needs_manual_label": True, "hit_node": hit, flags[mode]: True, "message": messages[mode]}

    before_components = component_summary_from_tree(before["root"])
    if mode == "gesture":
        execute_gesture_operation(config.device_id, operate, [int(x), int(y)], screen_metrics_from_root(before["root"]))
    else:
        execute_tap(config.device_id, [int(x), int(y)])
    time.sleep(1.0 if mode == "gesture" else 1.2)
    after = capture_state_without_graph_write()
    if mode != "popup" and not states_represent_same_page(after["state"], before["state"]):
        message = (
            "执行后进入了新页面，请使用页面跳转录制，不要保存为页面内操作。"
            if mode == "gesture"
            else "点击后进入了新页面，请使用页面跳转录制模式，不要使用页面内变化模式。"
        )
        return {"ok": False, "error": message}

    after_components = component_summary_from_tree(after["root"])
    revealed, hidden = component_changes(before_components, after_components)
    state = graph.setdefault("states", {}).setdefault(active_page, before["state"])
    state.update(before["state"])
    operation_id = (
        next_page_operation_id(state)
        if mode == "popup"
        else page_operation_id(active_page, {**target, "operate": operate, "effect": effect})
    )
    if mode == "same_page":
        revealed = [{**item, "source": "page_operation", "source_operation_id": operation_id, "requires_operation_id": operation_id} for item in revealed]
        hidden = [{**item, "source_operation_id": operation_id} for item in hidden]
    operation = {
        "operation_id": operation_id,
        "created_at": now_iso(),
        "operate": operate,
        "effect": "open_popup" if mode == "popup" else effect or ("content_changed" if mode == "same_page" else "same_page_state_changed"),
        "target": step_target(target) if mode == "popup" else target,
        "before_signature": components_signature(before_components),
        "after_signature": components_signature(after_components),
    }
    if mode != "gesture":
        operation.update({"revealed_candidates": revealed, "hidden_candidates": hidden})
    operations = state.setdefault("page_operations", [])
    if mode != "popup":
        operations[:] = [item for item in operations if item.get("operation_id") != operation_id]
    operations.append(operation)
    upsert_clicked_target_as_candidate(graph, active_page, target, operation_id=operation_id)
    if mode == "same_page":
        upsert_page_variant(state, operation, revealed, hidden)
        for item in revealed:
            item.setdefault("candidate_id", candidate_merge_key(item))
            upsert_candidate(state, item)
    save_navigation_graph(graph, config.work_dir)

    if mode == "popup":
        message = f"已记录弹窗操作 {operation_id}：新增 {len(revealed)} 个控件，消失 {len(hidden)} 个控件。"
        # 保留点击前页面身份，只用点击后的树刷新截图、候选控件和屏幕尺寸。
        return state_response_from_capture(after["root"], before["state"], graph, active_page, message=message)
    refreshed = read_current_state(capture=False)
    message = (
        f"已记录页面内变化：新增 {len(revealed)} 个控件，消失 {len(hidden)} 个控件。"
        if mode == "same_page"
        else f"已记录页面内操作：{operate} / {operation['effect']}"
    )
    return {**refreshed, "message": message}


def record_popup_operation(x: int, y: int, manual_label: str = "") -> Dict[str, Any]:
    return record_page_operation(x, y, mode="popup", manual_label=manual_label)


def record_same_page_operation(x: int, y: int, manual_label: str = "") -> Dict[str, Any]:
    return record_page_operation(x, y, mode="same_page", manual_label=manual_label)


def record_page_gesture_operation(x: int, y: int, operate: str, effect: str = "", manual_label: str = "") -> Dict[str, Any]:
    return record_page_operation(x, y, mode="gesture", operate=operate, effect=effect, manual_label=manual_label)


def record_tap_at_point(x: int, y: int, expect: str = "new_page", effect: str = "", manual_label: str = "") -> Dict[str, Any]:
    chain = pending_action_chain(config.work_dir)
    if chain:
        current = read_current_state(capture=False)
        root_json = load_json(config.output_dir / "current_ui_tree.json")
        annotate(root_json)
        from_page = str(chain["from_page"])
    else:
        current = read_current_state(capture=False)
        ensure_page_consistency(current)
        root_json = load_json(config.output_dir / "current_ui_tree.json")
        annotate(root_json)
        from_page = str(current["active_page"])
    hit = hit_test_full_ui_tree(root_json, int(x), int(y))
    target = build_semantic_target_from_node(hit, manual_label=manual_label.strip())
    append_web_history(config.work_dir, {"event": "tap_point", "debug": {"point": [int(x), int(y)], "hit_node": hit, "needs_manual_label": target.get("needs_manual_label", False)}})
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
    graph = load_navigation_graph(config.work_dir)
    from_state = graph.get("states", {}).get(
        from_page,
        current.get("active_state") or current.get("state") or {"page_name": from_page},
    )
    same_page = states_represent_same_page(after_capture["state"], from_state)
    if same_page:
        after_capture["state"] = resolve_detected_state(
            graph, after_capture["state"], from_page
        )
    else:
        after_capture["state"] = contextualize_child_state(
            graph, from_page, after_capture["state"]
        )
    after = state_response_from_capture(
        after_capture["root"],
        after_capture["state"],
        graph,
        from_page,
    )
    to_page = after["state"]["page_name"]
    if same_page:
        if ctype in {"Toggle", "Switch", "CheckBox", "Checkbox"}:
            upsert_clicked_target_as_candidate(graph, from_page, target)
            save_navigation_graph(graph, config.work_dir)
            refreshed = read_current_state(capture=False)
            return {**refreshed, "message": "点击后仍停留在当前页面。如该操作用于展开或刷新页面内容，请使用‘录制页面内变化’模式。"}
        steps = list(chain.get("steps", [])) if chain else []
        steps.append(transition_step(target))
        save_pending_action_chain(config.work_dir, {
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
    clear_pending_action_chain(config.work_dir)
    return read_current_state(capture=False)


@app.get("/", response_class=HTMLResponse)
def index() -> FileResponse:
    return FileResponse(APP_DIR / "templates" / "nav.html")


@app.post("/api/capture")
def api_capture() -> JSONResponse:
    return ok_response(**read_current_state(capture=True))


@app.post("/api/console_action")
def api_console_action(req: ConsoleActionRequest) -> JSONResponse:
    action = req.action.strip()
    payload = req.payload or {}
    if action == "capture_current":
        return api_capture()
    if action == "system_back":
        return api_back()
    if action == "clear_pending":
        clear_pending_files()
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


@app.get("/api/state")
def api_state() -> JSONResponse:
    return ok_response(**read_current_state(capture=False))


@app.post("/api/tap_point")
def api_tap_point(req: TapPointRequest) -> JSONResponse:
    return ok_response(**record_tap_at_point(req.x, req.y, expect=req.expect, effect=req.effect, manual_label=req.manual_label))


@app.post("/api/tap_same_page_operation")
def api_tap_same_page_operation(req: PointRequest) -> JSONResponse:
    return operation_response(record_same_page_operation(req.x, req.y, manual_label=req.manual_label))


@app.post("/api/page_gesture_operation")
def api_page_gesture_operation(req: PageGestureOperationRequest) -> JSONResponse:
    return operation_response(record_page_gesture_operation(req.x, req.y, req.operate, effect=req.effect, manual_label=req.manual_label))


def operation_response(data: Dict[str, Any]) -> JSONResponse:
    return JSONResponse(data) if data.get("ok") is False else ok_response(**data)


def record_candidate(index: int, expect: str, effect: str, manual_label: str) -> Dict[str, Any]:
    candidates = read_current_state(capture=True)["candidates"]
    if index < 1 or index > len(candidates):
        raise ValueError(f"候选编号无效：{index}")
    center = candidates[index - 1].get("bounds_center")
    if not isinstance(center, list) or len(center) != 2:
        raise ValueError("候选项缺少 bounds_center，无法作为临时 hit-test 输入")
    return record_tap_at_point(int(center[0]), int(center[1]), expect=expect, effect=effect, manual_label=manual_label)


@app.post("/api/record_action")
def api_record_action(req: RecordActionRequest) -> JSONResponse:
    action = req.action.strip()
    payload = req.payload or {}
    label = str(payload.get("manual_label") or "")
    if action == "tap_candidate":
        return ok_response(**record_candidate(int(payload.get("index")), str(payload.get("expect") or "new_page"), str(payload.get("effect") or ""), label))
    if action not in {"tap_point", "same_page_tap", "popup_tap", "same_page_gesture"}:
        raise ValueError(f"未知录制动作：{action}")
    x, y = int(payload.get("x")), int(payload.get("y"))
    if action == "tap_point":
        data = record_tap_at_point(x, y, expect=str(payload.get("expect") or "new_page"), effect=str(payload.get("effect") or ""), manual_label=label)
    elif action == "same_page_tap":
        data = record_same_page_operation(x, y, manual_label=label)
    elif action == "popup_tap":
        data = record_popup_operation(x, y, manual_label=label)
    elif action == "same_page_gesture":
        data = record_page_gesture_operation(x, y, str(payload.get("operate") or ""), effect=str(payload.get("effect") or ""), manual_label=label)
    return operation_response(data)


@app.post("/api/tap_candidate")
def api_tap_candidate(req: TapCandidateRequest) -> JSONResponse:
    return ok_response(**record_candidate(req.index, req.expect, req.effect, req.manual_label))


@app.post("/api/mark_current_as_overlay")
def api_mark_current_as_overlay() -> JSONResponse:
    current = read_current_state(capture=False)
    root_json = load_json(config.output_dir / "current_ui_tree.json")
    annotate(root_json)
    title = detect_overlay_title(root_json) or current["state"].get("last_title") or "未知弹窗"
    page_name = current["state"].get("page_name") or current.get("active_page")
    graph = load_navigation_graph(config.work_dir)
    parent = current.get("active_page") or (pending_data(config.work_dir) or {}).get("from_page") or ""
    graph.setdefault("states", {}).setdefault(page_name, current["state"]).update({
        "state_type": "overlay", "is_overlay": True, "overlay_parent": parent,
        "overlay_title": title, "page_description": f"弹窗：{title}", "last_title": title,
    })
    save_navigation_graph(graph, config.work_dir)
    return ok_response(**read_current_state(capture=False), message="当前页面已标记为弹窗页面")


@app.get("/api/page_directory")
def api_page_directory() -> JSONResponse:
    return ok_response(**build_page_directory(load_navigation_graph(config.work_dir)))


@app.post("/api/rename_page")
def api_rename_page(req: RenamePageRequest) -> JSONResponse:
    old_name = req.old_page_name.strip()
    new_name = validate_page_name_for_rename(req.new_page_name)
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
    if req.new_title.strip():
        state["last_title"] = req.new_title.strip()
        state["page_description"] = f"{'弹窗：' if state.get('is_overlay') else ''}{req.new_title.strip()}"
    states[new_name] = state
    if new_name != old_name:
        rename_page_references(graph, old_name, new_name)
    save_navigation_graph(graph, config.work_dir)
    rename_session_references(old_name, new_name)
    return ok_response(
        page_name=new_name, old_page_name=old_name,
        new_title=state.get("last_title") or state.get("page_description") or new_name,
        backup=backup, message=f"已重命名页面：{old_name} -> {new_name}",
    )


def rename_session_references(old_name: str, new_name: str) -> None:
    path = config.work_dir / "outputs" / "navigation" / "current_path_session.json"
    if not path.exists():
        return
    try:
        session = load_json(path)
    except Exception:
        return
    changed = False
    for field in ("active_page", "base_page"):
        if session.get(field) == old_name:
            session[field], changed = new_name, True
    if changed:
        save_json(session, path, "当前页面会话")


@app.get("/api/page_detail")
def api_page_detail(page_name: str) -> JSONResponse:
    graph = load_navigation_graph(config.work_dir)
    state = graph.get("states", {}).get(page_name)
    if not isinstance(state, dict):
        raise ValueError(f"页面不存在：{page_name}")
    transitions = graph.get("transitions", [])
    return ok_response(
        page_name=page_name, state=state,
        path_from_root=[] if page_name == "Pages_root" else (bfs_path(graph, page_name) or []),
        incoming_transitions=[item for item in transitions if item.get("to_page") == page_name],
        outgoing_transitions=[item for item in transitions if item.get("from_page") == page_name],
        merged_candidates=get_page_merged_candidates(graph, page_name, []),
        page_operations=state.get("page_operations", []) or [],
        page_variants=state.get("page_variants", []) or [],
        continued_captures=state.get("continued_captures", []) or [],
    )


@app.post("/api/set_active_page")
def api_set_active_page(req: SetActivePageRequest) -> JSONResponse:
    if req.page_name not in load_navigation_graph(config.work_dir).get("states", {}):
        raise ValueError(f"页面不存在：{req.page_name}")
    save_current_path_session(config.work_dir, req.page_name)
    return ok_response(**read_current_state(capture=False))


@app.post("/api/continue_current_page")
def api_continue_current_page() -> JSONResponse:
    current = read_current_state(capture=True)
    graph = load_navigation_graph(config.work_dir)
    page_name = current.get("active_page") or current["state"]["page_name"]
    state = graph.setdefault("states", {}).setdefault(page_name, current["state"])
    captures = state.setdefault("continued_captures", [])
    capture_id = f"{page_name}__continue_{len(captures) + 1:03d}"
    capture_dir = config.work_dir / "outputs" / "navigation" / "continued_captures"
    capture_dir.mkdir(parents=True, exist_ok=True)
    screenshot = capture_dir / f"{capture_id}.png"
    if (source := config.output_dir / "current_screen.png").exists():
        shutil.copy2(source, screenshot)
    for candidate in current.get("current_candidates", []) or []:
        item = candidate_from_auto(candidate, source="continued_capture")
        item["source_capture_id"] = capture_id
        upsert_candidate(state, item)
    captures.append({"capture_id": capture_id, "created_at": now_iso(), "screenshot": str(screenshot), "candidate_count": len(current.get("current_candidates", []) or [])})
    save_navigation_graph(graph, config.work_dir)
    return ok_response(**read_current_state(capture=False), message=f"已续录当前页面：{capture_id}")


@app.post("/api/navigate_to_page")
def api_navigate_to_page(req: NavigateToPageRequest) -> JSONResponse:
    graph = load_navigation_graph(config.work_dir)
    path = bfs_path(graph, req.page_name)
    if path is None:
        raise ValueError(f"找不到 Pages_root 到 {req.page_name} 的路径")
    for transition in path:
        from_page = str(transition.get("from_page") or "")
        for step in transition_steps(transition):
            before = capture_state_without_graph_write(ui_tree_only=True)
            before["state"] = resolve_detected_state(graph, before["state"], from_page)
            detected_page = str(before["state"].get("page_name") or "")
            if from_page and not state_matches_graph_page(graph, before["state"], from_page):
                raise ValueError(f"无法进入 {req.page_name}：当前检测页面是 {detected_page}，但路径下一步要求位于 {from_page}。请先回到正确起始页。")
            center = find_candidate_center_for_target(before["root"], step.get("target") or {})
            if not center:
                raise ValueError(f"无法进入 {req.page_name}：当前页面找不到路径控件「{transition_steps_label([step])}」。请确认该控件仍存在或重新录制这条路径。")
            execute_tap(config.device_id, center)
            time.sleep(0.5)
    captured = capture_state_without_graph_write(ui_tree_only=True)
    captured["state"] = resolve_detected_state(graph, captured["state"], req.page_name)
    if not state_matches_graph_page(graph, captured["state"], req.page_name):
        raise ValueError(f"无法进入 {req.page_name}：最终校验失败，实际位于 {captured['state'].get('page_name')}")
    save_current_path_session(config.work_dir, req.page_name)
    return ok_response(**state_response_from_capture(
        captured["root"], captured["state"], graph, req.page_name,
        message="已按导航图快速跳转：仅拉取 UI 树并解析控件，未重新截图。",
    ))


def backup_graph_file() -> str:
    src = navigation_graph_path(config.work_dir)
    backup_dir = config.work_dir / "outputs" / "navigation" / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    dst = backup_dir / f"settings_navigation_graph_{int(time.time())}.json"
    if src.exists():
        shutil.copy2(src, dst)
    return str(dst)


def finalize_delete(action: str, graph: Dict[str, Any], plan: Dict[str, Any], dry_run: bool) -> Dict[str, Any]:
    if dry_run:
        return {"dry_run": True, "delete_plan": plan}
    backup = backup_graph_file()
    apply_delete_plan(graph, plan)
    prune = prune_graph_after_delete(graph, config.work_dir)
    plan.setdefault("warnings", []).extend(prune.get("warnings", []))
    save_navigation_graph(graph, config.work_dir)
    append_web_history(config.work_dir, {"operation_id": f"{action}_{int(time.time())}", "action": action, "delete_plan": plan, "graph_backup": backup})
    return {"dry_run": False, "delete_plan": plan, "graph_backup": backup}


@app.post("/api/delete_action")
def api_delete_action(req: DeleteActionRequest) -> JSONResponse:
    target_type = req.target_type.strip()
    payload = dict(req.payload or {})
    payload["dry_run"] = bool(req.dry_run)
    handlers = {
        "transition": (api_delete_transition, DeleteTransitionRequest),
        "branch": (api_delete_branch, DeleteBranchRequest),
        "page": (api_delete_page, DeletePageRequest),
        "candidate": (api_delete_candidate, DeleteCandidateRequest),
        "page_operation": (api_delete_page_operation, DeletePageOperationRequest),
        "continued_capture": (api_delete_continued_capture, DeleteContinuedCaptureRequest),
    }
    if target_type not in handlers:
        raise ValueError(f"未知删除目标类型：{target_type}")
    handler, model = handlers[target_type]
    return handler(model(**payload))


@app.post("/api/delete_transition")
def api_delete_transition(req: DeleteTransitionRequest) -> JSONResponse:
    return delete_transition_response(req.transition_id, req.delete_orphan_to_state, req.dry_run, "delete_transition")


@app.post("/api/delete_branch")
def api_delete_branch(req: DeleteBranchRequest) -> JSONResponse:
    return delete_transition_response(req.transition_id, req.delete_descendants, req.dry_run, "delete_branch")


def delete_transition_response(transition_id_value: str, descendants: bool, dry_run: bool, action: str) -> JSONResponse:
    graph = load_navigation_graph(config.work_dir)
    plan = plan_delete_transition(graph, transition_id_value, descendants)
    return ok_response(**finalize_delete(action, graph, plan, dry_run))


@app.post("/api/delete_page")
def api_delete_page(req: DeletePageRequest) -> JSONResponse:
    if req.page_name == "Pages_root":
        raise ValueError("不允许删除 Pages_root")
    graph = load_navigation_graph(config.work_dir)
    plan = blank_delete_plan()
    plan["states"].append(req.page_name)
    plan["transitions"] = [item for item in graph.get("transitions", []) if
        (req.delete_incoming and item.get("to_page") == req.page_name) or
        (req.delete_outgoing and item.get("from_page") == req.page_name)]
    state = graph.get("states", {}).get(req.page_name, {})
    plan["page_operations"] = [{"page_name": req.page_name, "operation_id": item["operation_id"]} for item in state.get("page_operations", []) if item.get("operation_id")]
    plan["continued_captures"] = [{"page_name": req.page_name, "capture_id": item["capture_id"]} for item in state.get("continued_captures", []) if item.get("capture_id")]
    result = finalize_delete("delete_page", graph, plan, req.dry_run)
    if not req.dry_run:
        save_current_path_session(config.work_dir, "Pages_root")
    return ok_response(**result)


@app.post("/api/delete_candidate")
def api_delete_candidate(req: DeleteCandidateRequest) -> JSONResponse:
    graph = load_navigation_graph(config.work_dir)
    state = graph.get("states", {}).get(req.page_name, {})
    candidates = state.get("merged_candidates", []) or []
    candidate = next((item for item in candidates if candidate_id(item) == req.candidate_id), None)
    if not candidate:
        raise ValueError(f"候选不存在：{req.candidate_id}")
    plan = blank_delete_plan()
    plan["candidates"].append({"page_name": req.page_name, "candidate_id": req.candidate_id, "action": "delete_candidate"})
    if candidate.get("transition_ids") and not req.delete_linked_transitions:
        plan["warnings"].append("该候选控件关联了已录制跳转，只删除候选可能造成 transition 缺少控件引用。")
    if req.delete_linked_transitions:
        for transition_id_value in candidate.get("transition_ids", []) or []:
            subplan = plan_delete_transition(graph, transition_id_value, True)
            plan["transitions"].extend(subplan["transitions"])
            plan["states"].extend(page for page in subplan["states"] if page not in plan["states"])
    if req.delete_linked_operations:
        plan["page_operations"].extend({"page_name": req.page_name, "operation_id": operation_id} for operation_id in candidate.get("operation_ids", []) or [])
    if not req.dry_run:
        state["merged_candidates"] = [item for item in candidates if candidate_id(item) != req.candidate_id]
    return ok_response(**finalize_delete("delete_candidate", graph, plan, req.dry_run))


@app.post("/api/delete_page_operation")
def api_delete_page_operation(req: DeletePageOperationRequest) -> JSONResponse:
    graph = load_navigation_graph(config.work_dir)
    plan = blank_delete_plan()
    plan["page_operations"].append({"page_name": req.page_name, "operation_id": req.operation_id})
    for candidate in graph.get("states", {}).get(req.page_name, {}).get("merged_candidates", []) or []:
        references = {candidate.get("requires_operation_id"), candidate.get("source_operation_id"), *(candidate.get("operation_ids") or [])}
        if req.operation_id in references:
            plan["candidates"].append({"page_name": req.page_name, "candidate_id": candidate_id(candidate), "action": "delete_revealed" if req.delete_revealed_candidates else "remove_operation_ref"})
    if not req.delete_revealed_candidates:
        plan["keep_revealed_candidates"] = True
    return ok_response(**finalize_delete("delete_page_operation", graph, plan, req.dry_run))


@app.post("/api/delete_continued_capture")
def api_delete_continued_capture(req: DeleteContinuedCaptureRequest) -> JSONResponse:
    graph = load_navigation_graph(config.work_dir)
    plan = blank_delete_plan()
    state = graph.get("states", {}).get(req.page_name, {})
    capture = next((item for item in state.get("continued_captures", []) or [] if item.get("capture_id") == req.capture_id), None)
    if not capture:
        raise ValueError(f"续录不存在：{req.capture_id}")
    plan["continued_captures"].append({"page_name": req.page_name, "capture_id": req.capture_id})
    if capture.get("screenshot"):
        plan["files"].append(capture["screenshot"])
    for candidate in state.get("merged_candidates", []) or []:
        if candidate.get("source_capture_id") != req.capture_id:
            continue
        if (candidate.get("transition_ids") or candidate.get("operation_ids")) and req.delete_candidates_from_capture:
            plan["warnings"].append(f"候选 {candidate_id(candidate)} 有关联记录，将只移除 source_capture_id")
        plan["candidates"].append({"page_name": req.page_name, "candidate_id": candidate_id(candidate), "action": "delete_from_capture"})
    if not req.delete_candidates_from_capture:
        plan["keep_capture_candidates"] = True
    return ok_response(**finalize_delete("delete_continued_capture", graph, plan, req.dry_run))


@app.post("/api/swipe_horizontal")
def api_swipe_horizontal(req: SwipeHorizontalRequest) -> JSONResponse:
    current = read_current_state(capture=False)
    direction = req.direction.lower()
    execute_horizontal_swipe(config.device_id, direction, current.get("screen_metrics", {}))
    graph = load_navigation_graph(config.work_dir)
    active_page = current["active_page"]
    operate = "swipe_left" if direction == "left" else "swipe_right"
    target = horizontal_target(direction)
    view_state = next_horizontal_view_state(graph, str(current["active_state"].get("base_page") or active_page))
    graph.setdefault("states", {})[view_state["page_name"]] = view_state
    effect = "local_horizontal_view_changed"
    add_transition(graph, {
        "transition_id": transition_id(active_page, operate, view_state["page_name"], target, effect),
        "from_page": active_page, "to_page": view_state["page_name"], "operate": operate,
        "target": target, "effect": effect, "base_page": view_state["base_page"],
    })
    save_navigation_graph(graph, config.work_dir)
    save_current_path_session(config.work_dir, view_state["page_name"], view_state["base_page"])
    time.sleep(1.0)
    return ok_response(**read_current_state(capture=True))


@app.post("/api/back")
def api_back() -> JSONResponse:
    execute_back(config.device_id)
    time.sleep(1.0)
    return ok_response(**read_current_state(capture=True))


def clear_pending_files() -> None:
    path = pending_transition_path(config.work_dir)
    if path.exists():
        path.unlink()
    clear_pending_action_chain(config.work_dir)


@app.post("/api/clear_pending")
def api_clear_pending() -> JSONResponse:
    clear_pending_files()
    return ok_response(pending=None, pending_action_chain=None)


@app.get("/api/graph")
def api_graph() -> JSONResponse:
    path = navigation_graph_path(config.work_dir)
    return JSONResponse(load_json(path) if path.exists() else load_navigation_graph(config.work_dir))


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
