#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FastAPI 设置导航录制 Web 控制台。"""

import argparse
import hashlib
import hmac
import json
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

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
    ActionRequest,
    DeleteActionRequest,
    GraphMaintenance,
    NavigationGraph,
    NavigationGraphRepository,
    RenamePageRequest,
    annotate,
    append_web_history,
    build_navigation_state,
    build_page_directory,
    build_semantic_target_from_node,
    candidate_from_auto,
    candidate_merge_key,
    component_summary_from_tree,
    clear_pending_action_chain,
    capture_device,
    contextualize_child_state,
    current_session_page,
    execute_device_input,
    extract_navigation_candidates,
    hit_test_full_ui_tree,
    load_json,
    now_iso,
    pending_transition_path,
    pending_action_chain,
    resolve_detected_state,
    save_current_path_session,
    save_json,
    screen_metrics_from_root,
    states_represent_same_page,
    upsert_candidate,
    upsert_clicked_target_as_candidate,
    get_page_merged_candidates,
    step_target,
    transition_id_for_pages,
)
from DFS import (
    DFS_MANUAL_FIELD,
    dfs_branch_for_page,
    dfs_record_display_name,
    dfs_record_for_page,
    export_dfs_paths,
    format_dfs_records,
    format_path_target,
    sync_descendant_manual_dfs_prefixes,
)

APP_DIR = Path(__file__).resolve().parent
app = FastAPI(title="Settings Navigation Recorder")
POPUP_TYPES = ("SheetWrapper", "Dialog", "MenuWrapper")
POPUP_TYPE_SET = frozenset(POPUP_TYPES)


class ServerConfig:
    def __init__(
        self,
        work_dir: Path = DEFAULT_WORK_DIR,
        device_id: str = DEFAULT_DEVICE_ID,
        output_dir: Optional[Path] = None,
    ) -> None:
        self.work_dir = Path(work_dir)
        self.device_id = device_id
        self.output_dir = Path(output_dir) if output_dir else self.work_dir / "outputs" / "latest"
        self.graphs = NavigationGraphRepository(self.work_dir)


config = ServerConfig()


def ok_response(**kwargs: Any) -> JSONResponse:
    return JSONResponse({"ok": True, **kwargs})


@app.exception_handler(Exception)
def api_error(_request: Request, exc: Exception) -> JSONResponse:
    """所有 API 使用统一错误结构，业务路由只保留正常流程。"""
    return JSONResponse({"ok": False, "error": str(exc)})


def read_current_state(
    capture: bool = False,
    preferred_page: str = "",
    snapshot: Optional[Dict[str, Any]] = None,
    graph: Optional[Dict[str, Any]] = None,
    active_page: str = "",
    message: str = "",
) -> Dict[str, Any]:
    """读取 latest 或复用刚采集的 snapshot，统一生成前端状态。"""
    graph = graph if graph is not None else config.graphs.load()
    if snapshot:
        root_json, state = snapshot["root"], snapshot["state"]
        state = resolve_detected_state(graph, state, active_page)
        active_state = graph.get("states", {}).get(active_page, state)
        candidates = extract_navigation_candidates(root_json)
        merged_candidates = get_page_merged_candidates(graph, active_page, [])
    else:
        if capture and not capture_device(config.device_id, config.output_dir, include_screen=True):
            raise RuntimeError("hdc 采集失败，请检查设备连接、hdc PATH 和授权状态")
        json_path = config.output_dir / "current_ui_tree.json"
        if not json_path.exists() or not (config.output_dir / "current_screen.png").exists():
            raise FileNotFoundError("outputs/latest/current_ui_tree.json 或 current_screen.png 不存在，请先点击“重新采集”。")
        root_json = load_json(json_path)
        annotate(root_json)
        state = resolve_detected_state(
            graph,
            build_navigation_state(root_json),
            preferred_page or current_session_page(config.work_dir),
        )
        stored_state = graph.get("states", {}).get(state["page_name"], {})
        if isinstance(stored_state, dict):
            state.update({key: stored_state[key] for key in ("page_operations", "page_variants", "merged_candidates") if key in stored_state})
        candidates = extract_navigation_candidates(root_json)
        # GET /api/state 只读。否则删除当前孤儿页后，浏览器刷新会根据仍停留
        # 在该页的旧 UI 树立即把它写回导航图；只有显式“采集当前界面”才落图。
        if capture:
            graph["states"][state["page_name"]] = state
            for candidate in candidates:
                upsert_candidate(state, candidate_from_auto(candidate, source="auto_detected"))
            config.graphs.save(graph)
        active_state = {**stored_state, **state} if isinstance(stored_state, dict) else state
        active_page = str(active_state.get("page_name") or "")
        merged_candidates = get_page_merged_candidates(graph, active_page or state["page_name"], candidates)
    pending_path = pending_transition_path(config.work_dir)
    pending = load_json(pending_path) if pending_path.exists() else None
    action_chain = pending_action_chain(config.work_dir)
    page_name = state.get("page_name", "")
    warning = (
        "当前页面没有 pending transition，且导航图中没有父级来源。说明你可能是手动进入了当前页面，"
        "无法自动知道父级页面。请返回父页面后点击候选入口录制。"
        if page_name and page_name != "Pages_root" and not pending_path.exists()
        and not action_chain and not any(item.get("to_page") == page_name for item in graph.get("transitions", []))
        else ""
    )
    response = {
        "state": state,
        "active_state": active_state,
        "active_page": active_page,
        "popup_types": list(POPUP_TYPES),
        "current_candidates": candidates,
        "candidates": candidates,
        "merged_candidates": merged_candidates,
        "pending": pending,
        "pending_action_chain": action_chain,
        "warning": warning,
        "screenshot_url": f"/screen?t={int(time.time() * 1000)}",
        "screen_metrics": screen_metrics_from_root(root_json),
    }
    if message:
        response["message"] = message
    return response


def capture_state_without_graph_write() -> Dict[str, Any]:
    """采集并解析设备状态，但不修改导航图。"""
    if not capture_device(config.device_id, config.output_dir, include_screen=True):
        raise RuntimeError("hdc 采集失败，请检查设备连接、hdc PATH 和授权状态")
    root_json = load_json(config.output_dir / "current_ui_tree.json")
    annotate(root_json)
    state = build_navigation_state(root_json)
    graph = config.graphs.load()
    state = resolve_detected_state(graph, state, current_session_page(config.work_dir))
    return {"root": root_json, "state": state}


def normalize_popup_type(value: Any) -> str:
    popup_type = str(value or "").strip()
    if popup_type not in POPUP_TYPE_SET:
        allowed = "、".join(POPUP_TYPES)
        raise ValueError(f"弹窗类型无效：{popup_type or '未指定'}；可选类型：{allowed}")
    return popup_type


def page_display_description(
    graph: Dict[str, Any],
    page_name: str,
) -> str:
    state = graph.get("states", {}).get(page_name, {})
    if not isinstance(state, dict):
        return page_name
    manual = state.get(DFS_MANUAL_FIELD)
    display_record = manual if isinstance(manual, dict) else {
        "page_description": (
            state.get("page_description")
            or state.get("last_title")
            or ""
        ),
        "path_snapshot": [],
    }
    return dfs_record_display_name(
        display_record,
        page_name,
    )


def transition_with_descriptions(
    graph: Dict[str, Any],
    transition: Dict[str, Any],
) -> Dict[str, Any]:
    from_page = str(transition.get("from_page") or "")
    to_page = str(transition.get("to_page") or "")
    return {
        **transition,
        "from_page_description": page_display_description(graph, from_page),
        "to_page_description": page_display_description(graph, to_page),
    }


def sync_single_incoming_transition_target(
    graph: Dict[str, Any],
    page_name: str,
    manual_target: Dict[str, Any],
) -> bool:
    """Synchronize the final DFS step when the page has one unambiguous entry."""
    incoming = [
        transition
        for transition in graph.get("transitions", [])
        if isinstance(transition, dict)
        and transition.get("to_page") == page_name
    ]
    if len(incoming) != 1:
        return False

    transition = incoming[0]
    steps = transition.get("steps")
    step_target_value: Optional[Dict[str, Any]] = None
    if isinstance(steps, list) and steps:
        last_step = steps[-1]
        if isinstance(last_step, dict):
            target = last_step.get("target")
            if isinstance(target, dict):
                step_target_value = target
    if step_target_value is None:
        target = transition.get("target")
        if isinstance(target, dict):
            step_target_value = target
    if step_target_value is None:
        return False

    locator_type = str(manual_target.get("type") or "")
    locator_value = manual_target.get("value")
    if locator_type == "key":
        step_target_value["key"] = locator_value
    elif locator_type == "text":
        step_target_value["text"] = locator_value
    else:
        return False
    for field in ("key_description", "step_prompt"):
        value = str(manual_target.get(field) or "").strip()
        if value:
            step_target_value[field] = value

    # 单步 transition 的顶层 target 与 steps[0].target 是两份 JSON 时，
    # 同步两处，保证旧数据和页面详情使用任何一种表示都能看到新描述。
    if (
        isinstance(steps, list)
        and len(steps) == 1
        and isinstance(transition.get("target"), dict)
    ):
        transition["target"].update(step_target_value)
    return True


def maintain_page_dfs(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Save or clear the exact DFS record maintained for one page."""
    original_description_field = "dfs_original_page_description"
    page_name = str(payload.get("page_name") or "").strip()
    graph = config.graphs.load()
    state = graph.get("states", {}).get(page_name)
    if not isinstance(state, dict):
        raise ValueError(f"页面不存在：{page_name}")
    previous_record = dfs_record_for_page(graph, page_name) or {}
    previous_path = previous_record.get("path_snapshot") or []
    updated_descendant_pages: List[str] = []

    if payload.get("clear"):
        backup = config.graphs.backup()
        state.pop(DFS_MANUAL_FIELD, None)
        if original_description_field in state:
            original_description = state.pop(original_description_field)
            if original_description in (None, ""):
                state.pop("page_description", None)
            else:
                state["page_description"] = original_description
        message = "已恢复自动生成 DFS 数据。"
    else:
        package_name = str(payload.get("package_name") or "").strip()
        main_page_name = str(payload.get("main_page_name") or "").strip()
        page_description = str(payload.get("page_description") or "").strip()
        if not package_name:
            raise ValueError("package_name 不能为空")
        if not main_page_name:
            raise ValueError("main_page_name 不能为空")
        if not page_description:
            raise ValueError("page_description 不能为空")
        raw_path = payload.get("path_snapshot")
        if not isinstance(raw_path, list):
            raise ValueError("path_snapshot 必须是 JSON 数组")
        path_snapshot = []
        for index, target in enumerate(raw_path, start=1):
            formatted = format_path_target(target)
            if not formatted:
                raise ValueError(
                    f"path_snapshot 第 {index} 步无效：type 只能是 key/text，且 value 不能为空"
                )
            path_snapshot.append(formatted)
        root_page = str(
            graph.get("traversal_config", {}).get("root_page") or "Pages_root"
        )
        if page_name == root_page and path_snapshot:
            raise ValueError("根页面 path_snapshot 必须为空")
        if page_name != root_page and not path_snapshot:
            raise ValueError("非根页面 path_snapshot 至少需要一个定位步骤")
        backup = config.graphs.backup()
        if DFS_MANUAL_FIELD not in state:
            state[original_description_field] = state.get("page_description")
        state[DFS_MANUAL_FIELD] = {
            "package_name": package_name,
            "main_page_name": main_page_name,
            "page_description": page_description,
            "path_snapshot": path_snapshot,
        }
        state["page_description"] = page_description
        transition_synced = sync_single_incoming_transition_target(
            graph,
            page_name,
            path_snapshot[-1] if path_snapshot else {},
        ) if page_name != root_page else False
        updated_descendant_pages = sync_descendant_manual_dfs_prefixes(
            graph,
            page_name,
            previous_path,
            path_snapshot,
            str(previous_record.get("page_description") or ""),
            page_description,
        )
        descendant_message = (
            f"，并级联更新 {len(updated_descendant_pages)} 个下级页面的人工 DFS 路径"
            if updated_descendant_pages
            else ""
        )
        message = (
            f"已保存 DFS 人工维护数据，并同步当前页面唯一入边的目标描述"
            f"{descendant_message}。"
            if transition_synced
            else f"已保存 DFS 人工维护数据{descendant_message}；"
            "后续导出将优先使用该记录。"
        )

    config.graphs.save(graph)
    compact_result = export_compact_dfs({})
    return {
        "page_name": page_name,
        "dfs_record": dfs_record_for_page(graph, page_name),
        "dfs_manual": state.get(DFS_MANUAL_FIELD),
        "graph_backup": backup,
        "output_path": compact_result["output_path"],
        "record_count": compact_result["record_count"],
        "unreachable_pages": compact_result["unreachable_pages"],
        "updated_descendant_pages": updated_descendant_pages,
        "message": (
            f"{message} 已同步更新 settings_navigation_paths.json，"
            f"共 {compact_result['record_count']} 条路径。"
        ),
    }


def export_compact_dfs(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Generate the complete compact DFS file from the current graph."""
    graph = config.graphs.load()
    root_page = str(
        graph.get("traversal_config", {}).get("root_page") or "Pages_root"
    )
    records, unreachable = export_dfs_paths(graph, root_page)
    output = format_dfs_records(records, graph)
    output_path = (
        config.work_dir
        / "outputs"
        / "navigation"
        / "settings_navigation_paths.json"
    )
    save_json(output, output_path, "DFS 精简路径")
    page_name = str(payload.get("page_name") or "").strip()
    result: Dict[str, Any] = {
        "output_path": str(output_path),
        "record_count": len(output),
        "unreachable_pages": unreachable,
        "message": f"DFS 精简完成，共生成 {len(output)} 条页面路径。",
    }
    if page_name:
        result["dfs_detail"] = dfs_branch_for_page(graph, page_name)
    return result


def record_page_operation(
    x: int,
    y: int,
    *,
    mode: str,
    operate: str = "tap",
    effect: str = "",
    manual_label: str = "",
    popup_type: str = "",
) -> Dict[str, Any]:
    """统一录制页面内操作；popup 始终归属点击前的 active_page。"""
    if mode not in {"popup", "same_page", "gesture"}:
        raise ValueError(f"未知页面内操作模式：{mode}")

    selected_popup_type = normalize_popup_type(popup_type) if mode == "popup" else ""
    before = capture_state_without_graph_write()
    graph = config.graphs.load()
    stored_state = graph.get("states", {}).get(before["state"].get("page_name", ""))
    active = {**stored_state, **before["state"]} if isinstance(stored_state, dict) else before["state"]
    active_page = str(active.get("page_name") or before["state"].get("page_name") or "")
    if not active_page:
        raise ValueError("无法确定当前页面，不能保存页面操作。")
    if mode != "popup" and before["state"].get("page_name") != active_page:
        raise ValueError(
            f"当前检测页面 {before['state'].get('page_name')} 与 active_page {active_page} 不一致，"
            "请先重新采集或确认当前页面状态后再录制。"
        )

    hit = hit_test_full_ui_tree(before["root"], int(x), int(y))
    target = build_semantic_target_from_node(hit, manual_label=manual_label.strip())
    debug = {
        "point": [int(x), int(y)],
        "hit_node": hit,
        "needs_manual_label": bool(target.get("needs_manual_label")),
    }
    if mode == "popup":
        debug["popup_type"] = selected_popup_type
    if mode == "gesture":
        debug.update({"operate": operate, "effect": effect})
    event_name = (
        "popup_tap"
        if mode == "popup"
        else "tap_same_page_operation"
        if mode == "same_page"
        else "page_gesture_operation"
    )
    append_web_history(
        config.work_dir,
        {
            "event": event_name,
            "page_name": active_page,
            "debug": debug,
        },
    )

    if target.get("needs_manual_label"):
        current = (
            read_current_state(snapshot=before, graph=graph, active_page=active_page)
            if mode == "popup"
            else read_current_state(capture=False)
        )
        details = {"operate": operate, "effect": effect} if mode == "gesture" else {}
        return {
            **current,
            **details,
            "needs_manual_label": True,
            "hit_node": hit,
            "popup_mode" if mode == "popup" else "same_page_mode" if mode == "same_page" else "page_operation_mode": True,
            "message": (
                "命中控件缺少稳定 key/text，请填写操作描述。"
                if mode == "popup"
                else "命中控件缺少稳定 key/text，请手动填写描述后再保存。"
                if mode == "same_page"
                else "命中区域缺少稳定 key/text，请手动填写操作对象描述后再保存。"
            ),
        }

    before_components = component_summary_from_tree(before["root"])
    if mode == "gesture":
        execute_device_input(config.device_id, operate, [int(x), int(y)], screen_metrics_from_root(before["root"]))
    else:
        execute_device_input(config.device_id, "tap", [int(x), int(y)])
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
    before_map = {candidate_merge_key(item): item for item in before_components if candidate_merge_key(item)}
    after_map = {candidate_merge_key(item): item for item in after_components if candidate_merge_key(item)}
    before_signature = hashlib.sha256(json.dumps(sorted(before_map), ensure_ascii=False).encode("utf-8")).hexdigest()[:16]
    after_signature = hashlib.sha256(json.dumps(sorted(after_map), ensure_ascii=False).encode("utf-8")).hexdigest()[:16]
    revealed = [item for key, item in after_map.items() if key not in before_map]
    hidden = [item for key, item in before_map.items() if key not in after_map]
    state = graph.setdefault("states", {}).setdefault(active_page, before["state"])
    state.update(before["state"])
    if mode == "popup":
        max_index = 0
        for item in state.get("page_operations", []) or []:
            existing_id = str(item.get("operation_id") or "")
            suffix = existing_id.removeprefix("operation")
            if existing_id.startswith("operation") and suffix.isdigit():
                max_index = max(max_index, int(suffix))
        operation_id = f"operation{max_index + 1}"
    else:
        description = str(target.get("key_description") or target.get("step_prompt") or target.get("value") or "操作")
        safe_description = "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in description).strip("_") or "operation"
        digest = hashlib.sha1(json.dumps([active_page, target.get("type"), target.get("value"), description], ensure_ascii=False).encode("utf-8")).hexdigest()[:8]
        operation_id = f"{active_page}__op__{safe_description}_{digest}"
    if mode == "same_page":
        revealed = [
            {**item, "source": "page_operation", "source_operation_id": operation_id, "requires_operation_id": operation_id}
            for item in revealed
        ]
        hidden = [{**item, "source_operation_id": operation_id} for item in hidden]
    operation_target = dict(target)
    if mode == "popup":
        operation_target["type"] = selected_popup_type
    else:
        component_type = str(hit.get("component_type") if hit else "")
        if component_type:
            operation_target["type"] = component_type
    operation = {
        "operation_id": operation_id,
        "created_at": now_iso(),
        "operate": operate,
        "effect": "open_popup" if mode == "popup" else effect or ("content_changed" if mode == "same_page" else "same_page_state_changed"),
        "target": step_target(operation_target, include_type=True),
        "before_signature": before_signature,
        "after_signature": after_signature,
    }
    if mode == "popup":
        operation["popup_type"] = selected_popup_type
    if mode != "gesture":
        operation.update({"revealed_candidates": revealed, "hidden_candidates": hidden})
    operations = state.setdefault("page_operations", [])
    if mode != "popup":
        operations[:] = [item for item in operations if item.get("operation_id") != operation_id]
    operations.append(operation)
    upsert_clicked_target_as_candidate(graph, active_page, target, operation_id=operation_id)
    if mode == "same_page":
        variant_payload = [
            str(state.get("page_name") or active_page),
            operation.get("operation_id"),
            operation.get("effect"),
            operation.get("after_signature"),
        ]
        variant = {
            "variant_id": (
                f"{state.get('page_name') or active_page}__variant__"
                f"{hashlib.sha1(json.dumps(variant_payload, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()[:10]}"
            ),
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
        variants = state.setdefault("page_variants", [])
        variants[:] = [item for item in variants if item.get("variant_id") != variant["variant_id"]]
        variants.append(variant)
        for item in revealed:
            item.setdefault("candidate_id", candidate_merge_key(item))
            upsert_candidate(state, item)
    config.graphs.save(graph)

    if mode == "popup":
        message = (
            f"已记录弹窗操作 {operation_id}（{selected_popup_type}）："
            f"新增 {len(revealed)} 个控件，消失 {len(hidden)} 个控件。"
        )
        # 保留点击前页面身份，只用点击后的树刷新截图、候选控件和屏幕尺寸。
        return read_current_state(
            snapshot={"root": after["root"], "state": before["state"]},
            graph=graph,
            active_page=active_page,
            message=message,
        )
    refreshed = read_current_state(capture=False)
    message = (
        f"已记录页面内变化：新增 {len(revealed)} 个控件，消失 {len(hidden)} 个控件。"
        if mode == "same_page"
        else f"已记录页面内操作：{operate} / {operation['effect']}"
    )
    return {**refreshed, "message": message}


def record_tap_at_point(x: int, y: int, expect: str = "new_page", effect: str = "", manual_label: str = "") -> Dict[str, Any]:
    chain = pending_action_chain(config.work_dir)
    if chain:
        current = read_current_state(capture=False)
        root_json = load_json(config.output_dir / "current_ui_tree.json")
        annotate(root_json)
        from_page = str(chain["from_page"])
    else:
        current = read_current_state(capture=False)
        detected = current.get("state", {}).get("page_name")
        active = current.get("active_page") or current.get("active_state", {}).get("page_name")
        if detected and active and detected != active:
            raise ValueError(f"当前检测页面 {detected} 与 active_page {active} 不一致，请先重新采集或确认当前页面状态后再录制。")
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
    ctype = str(hit.get("component_type") if hit else "")
    if ctype in {"Toggle", "Switch", "CheckBox", "Checkbox"}:
        target["expect"] = "same_page"
    execute_device_input(config.device_id, "tap", [int(x), int(y)])
    time.sleep(1.2)
    after_capture = capture_state_without_graph_write()
    graph = config.graphs.load()
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
            graph, from_page, after_capture["state"], target
        )
    after = read_current_state(snapshot=after_capture, graph=graph, active_page=from_page)
    to_page = after["state"]["page_name"]
    if same_page:
        if ctype in {"Toggle", "Switch", "CheckBox", "Checkbox"}:
            upsert_clicked_target_as_candidate(graph, from_page, target)
            config.graphs.save(graph)
            refreshed = read_current_state(capture=False)
            return {**refreshed, "message": "点击后仍停留在当前页面。如该操作用于展开或刷新页面内容，请使用‘录制页面内变化’模式。"}
        steps = list(chain.get("steps", [])) if chain else []
        steps.append({"operate": "tap", "target": step_target(target)})
        save_json({
            "from_page": from_page,
            "steps": steps,
            "created_at": chain.get("created_at") if chain else now_iso(),
            "updated_at": now_iso(),
        }, config.work_dir / "outputs" / "navigation" / "pending_action_chain.json", "未完成多步骤跳转")
        message = f"已记录第 {len(steps)} 步，继续点击临时菜单/弹层中的目标控件；进入新页面后会保存为一条多步骤跳转。"
        return read_current_state(
            snapshot=after_capture,
            graph=graph,
            active_page=from_page,
            message=message,
        )

    steps = list(chain.get("steps", [])) if chain else []
    steps.append({"operate": "tap", "target": step_target(target)})
    tid = transition_id_for_pages(from_page, to_page)
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
    NavigationGraph(graph).add_transition(transition)
    if steps:
        upsert_clicked_target_as_candidate(graph, from_page, steps[0].get("target") or target, transition_id=tid)
    config.graphs.save(graph)
    save_current_path_session(config.work_dir, to_page)
    clear_pending_action_chain(config.work_dir)
    return read_current_state(capture=False)


@app.get("/", response_class=HTMLResponse)
def index() -> FileResponse:
    return FileResponse(APP_DIR / "templates" / "nav.html")


@app.post("/api/console_action")
def api_console_action(req: ActionRequest) -> JSONResponse:
    action = req.action.strip()
    payload = req.payload or {}
    if action == "capture_current":
        return ok_response(**read_current_state(capture=True))
    if action == "system_back":
        graph = config.graphs.load()
        active_page = current_session_page(config.work_dir)
        active_state = graph.get("states", {}).get(active_page, {})
        parent_page = str(active_state.get("parent_page") or active_state.get("base_page") or "")
        if not parent_page and active_page:
            incoming_pages = list(dict.fromkeys(
                str(transition.get("from_page") or "")
                for transition in graph.get("transitions", [])
                if transition.get("to_page") == active_page and transition.get("from_page") != active_page
            ))
            incoming_pages = [page for page in incoming_pages if page]
            if len(incoming_pages) == 1:
                parent_page = incoming_pages[0]
        execute_device_input(config.device_id, "back")
        time.sleep(1.0)
        current = read_current_state(capture=True, preferred_page=parent_page)
        resolved_page = str(current.get("active_page") or current.get("state", {}).get("page_name") or "")
        if resolved_page:
            save_current_path_session(config.work_dir, resolved_page)
        return ok_response(**current)
    if action == "clear_pending":
        pending_path = pending_transition_path(config.work_dir)
        if pending_path.exists():
            pending_path.unlink()
        clear_pending_action_chain(config.work_dir)
        return ok_response(**read_current_state(capture=False), message="已清空待确认跳转。")
    if action == "reorder_children":
        parent_page = str(payload.get("parent_page") or "")
        ordered_transition_ids = payload.get("ordered_transition_ids")
        if not isinstance(ordered_transition_ids, list):
            raise ValueError("ordered_transition_ids 必须是数组")
        graph = config.graphs.load()
        persisted_order = NavigationGraph(graph).reorder_children(
            parent_page, ordered_transition_ids,
        )
        config.graphs.save(graph)
        return ok_response(
            parent_page=parent_page,
            ordered_transition_ids=persisted_order,
            message=f"已保存 {parent_page} 的页面顺序，重新生成 DFS 时自动生效。",
        )
    if action == "maintain_page_dfs":
        return ok_response(**maintain_page_dfs(payload))
    if action == "export_dfs_compact":
        return ok_response(**export_compact_dfs(payload))
    if action == "continue_current_page":
        current = read_current_state(capture=True)
        graph = config.graphs.load()
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
        captures.append({
            "capture_id": capture_id,
            "created_at": now_iso(),
            "screenshot": str(screenshot),
            "candidate_count": len(current.get("current_candidates", []) or []),
        })
        config.graphs.save(graph)
        return ok_response(**read_current_state(capture=False), message=f"已续录当前页面：{capture_id}")
    if action == "swipe_horizontal":
        direction = str(payload.get("direction") or "").lower()
        if direction not in {"left", "right"}:
            raise ValueError("direction 必须是 left 或 right")
        current = read_current_state(capture=False)
        execute_device_input(
            config.device_id,
            f"horizontal_{direction}",
            metrics=current.get("screen_metrics", {}),
        )
        graph = config.graphs.load()
        active_page = str(current["active_page"])
        base_page = str(current["active_state"].get("base_page") or active_page)
        prefix = f"{base_page}__view_h"
        indexes = [
            int(str(name).removeprefix(prefix))
            for name in graph.get("states", {})
            if str(name).startswith(prefix) and str(name).removeprefix(prefix).isdigit()
        ]
        page_name = f"{prefix}{max(indexes, default=0) + 1}"
        view_state = {
            "page_name": page_name,
            "page_description": f"{base_page} 横向视图 {max(indexes, default=0) + 1}",
            "base_page": base_page,
            "state_type": "local_view",
            "effect": "local_horizontal_view_changed",
        }
        operate = "swipe_left" if direction == "left" else "swipe_right"
        description = f"横向{'左' if direction == 'left' else '右'}滑"
        target = {"type": "gesture", "value": f"swipe_{direction}", "key_description": description, "step_prompt": description, "axis": "horizontal"}
        effect = "local_horizontal_view_changed"
        tid = transition_id_for_pages(active_page, page_name)
        graph.setdefault("states", {})[page_name] = view_state
        NavigationGraph(graph).add_transition({
            "transition_id": tid,
            "from_page": active_page,
            "to_page": page_name,
            "operate": operate,
            "target": target,
            "effect": effect,
            "base_page": base_page,
        })
        config.graphs.save(graph)
        save_current_path_session(config.work_dir, page_name, base_page)
        time.sleep(1.0)
        return ok_response(**read_current_state(capture=True))
    raise ValueError(f"未知控制台动作：{action}")


@app.get("/api/state")
def api_state() -> JSONResponse:
    return ok_response(**read_current_state(capture=False))


@app.post("/api/record_action")
def api_record_action(req: ActionRequest) -> JSONResponse:
    action = req.action.strip()
    payload = req.payload or {}
    label = str(payload.get("manual_label") or "")
    if action == "tap_candidate":
        candidates = read_current_state(capture=True)["candidates"]
        index = int(payload.get("index"))
        if index < 1 or index > len(candidates):
            raise ValueError(f"候选编号无效：{index}")
        center = candidates[index - 1].get("bounds_center")
        if not isinstance(center, list) or len(center) != 2:
            raise ValueError("候选项缺少 bounds_center，无法作为临时 hit-test 输入")
        return ok_response(**record_tap_at_point(
            int(center[0]),
            int(center[1]),
            expect=str(payload.get("expect") or "new_page"),
            effect=str(payload.get("effect") or ""),
            manual_label=label,
        ))
    if action not in {"tap_point", "same_page_tap", "popup_tap", "same_page_gesture"}:
        raise ValueError(f"未知录制动作：{action}")
    x, y = int(payload.get("x")), int(payload.get("y"))
    if action == "tap_point":
        data = record_tap_at_point(x, y, expect=str(payload.get("expect") or "new_page"), effect=str(payload.get("effect") or ""), manual_label=label)
    elif action == "same_page_tap":
        data = record_page_operation(x, y, mode="same_page", manual_label=label)
    elif action == "popup_tap":
        data = record_page_operation(
            x,
            y,
            mode="popup",
            manual_label=label,
            popup_type=str(payload.get("popup_type") or ""),
        )
    elif action == "same_page_gesture":
        data = record_page_operation(
            x,
            y,
            mode="gesture",
            operate=str(payload.get("operate") or ""),
            effect=str(payload.get("effect") or ""),
            manual_label=label,
        )
    return JSONResponse(data) if data.get("ok") is False else ok_response(**data)


@app.get("/api/page_directory")
def api_page_directory() -> JSONResponse:
    return ok_response(**build_page_directory(config.graphs.load()))


@app.post("/api/rename_page")
def api_rename_page(req: RenamePageRequest) -> JSONResponse:
    old_name = req.old_page_name.strip()
    new_name = req.new_page_name.strip()
    if not new_name:
        raise ValueError("page_name 不能为空")
    if not new_name.startswith("Pages_"):
        raise ValueError("page_name 必须以 Pages_ 开头，例如 Pages_WLAN")
    if any(ch in new_name for ch in ["/", "\\", "\n", "\r", "\t"]):
        raise ValueError("page_name 不能包含路径分隔符或换行符")
    graph = config.graphs.load()
    states = graph.setdefault("states", {})
    if old_name not in states:
        raise ValueError(f"页面不存在：{old_name}")
    if old_name == "Pages_root" and new_name != old_name:
        raise ValueError("不允许修改 Pages_root 的 page_name")
    if new_name != old_name and new_name in states:
        raise ValueError(f"目标 page_name 已存在：{new_name}")

    backup = config.graphs.backup()
    state = NavigationGraph(graph).rename_page(
        old_name,
        new_name,
        new_title=req.new_title,
    )
    config.graphs.save(graph)
    migrated_files = (
        config.graphs.rename_runtime_references(old_name, new_name)
        if new_name != old_name else []
    )
    return ok_response(
        page_name=new_name, old_page_name=old_name,
        new_title=state.get("last_title") or state.get("page_description") or new_name,
        backup=backup, migrated_files=migrated_files,
        message=f"已修改内部页面 ID：{old_name} -> {new_name}",
    )

@app.get("/api/page_detail")
def api_page_detail(page_name: str) -> JSONResponse:
    graph = config.graphs.load()
    state = graph.get("states", {}).get(page_name)
    if not isinstance(state, dict):
        raise ValueError(f"页面不存在：{page_name}")
    transitions = graph.get("transitions", [])
    dfs_record = dfs_record_for_page(graph, page_name) or {
        "package_name": str(graph.get("package_name") or ""),
        "main_page_name": str(graph.get("main_page_name") or ""),
        "page_description": str(
            state.get("last_title") or state.get("page_description") or page_name
        ),
        "path_snapshot": [],
    }
    return ok_response(
        page_name=page_name,
        display_name=dfs_record_display_name(dfs_record, page_name),
        state=state,
        incoming_transitions=[
            transition_with_descriptions(graph, item)
            for item in transitions
            if item.get("to_page") == page_name
        ],
        outgoing_transitions=[
            transition_with_descriptions(graph, item)
            for item in transitions
            if item.get("from_page") == page_name
        ],
        merged_candidates=get_page_merged_candidates(graph, page_name, []),
        page_operations=state.get("page_operations", []) or [],
        page_variants=state.get("page_variants", []) or [],
        continued_captures=state.get("continued_captures", []) or [],
        dfs_record=dfs_record,
        dfs_manual=state.get(DFS_MANUAL_FIELD),
    )


@app.get("/api/page_dfs_detail")
def api_page_dfs_detail(page_name: str) -> JSONResponse:
    return ok_response(**dfs_branch_for_page(config.graphs.load(), page_name))


@app.get("/api/orphan_pages")
def api_orphan_pages() -> JSONResponse:
    maintenance = GraphMaintenance(config.graphs.load())
    pages = maintenance.orphan_pages(current_session_page(config.work_dir))
    return ok_response(orphan_pages=pages, count=len(pages))


@app.post("/api/delete_action")
def api_delete_action(req: DeleteActionRequest) -> JSONResponse:
    target_type = req.target_type.strip()
    graph = config.graphs.load()
    maintenance = GraphMaintenance(graph)
    plan = maintenance.plan_delete(target_type, dict(req.payload or {}))
    preview_token = hashlib.sha256(
        f"{target_type}\n{json.dumps(plan, ensure_ascii=False, sort_keys=True, separators=(',', ':'))}".encode("utf-8")
    ).hexdigest()
    if req.dry_run:
        return ok_response(dry_run=True, delete_plan=plan, preview_token=preview_token)
    if not req.preview_token or not hmac.compare_digest(req.preview_token, preview_token):
        raise ValueError("删除计划已变化或缺少预览确认，请重新预览后再执行。")

    backup = config.graphs.backup()
    runtime_warnings = maintenance.apply_delete(plan)
    config.graphs.save(maintenance.graph)
    append_web_history(config.work_dir, {
        "operation_id": f"{target_type}_{int(time.time())}",
        "action": target_type,
        "delete_plan": plan,
        "graph_backup": backup,
    })
    if current_session_page(config.work_dir) in set(plan.get("states", [])):
        save_current_path_session(config.work_dir, "Pages_root")
    return ok_response(
        dry_run=False,
        delete_plan=plan,
        graph_backup=backup,
        preview_token=preview_token,
        runtime_warnings=runtime_warnings,
    )


@app.get("/api/graph")
def api_graph() -> JSONResponse:
    return JSONResponse(config.graphs.load())


@app.get("/screen")
def screen() -> FileResponse:
    path = config.output_dir / "current_screen.png"
    headers = {"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0", "Pragma": "no-cache"}
    return FileResponse(path, media_type="image/png", headers=headers)


def main() -> None:
    global config
    parser = argparse.ArgumentParser(description="设置导航录制 Web 控制台")
    parser.add_argument("--work-dir", type=Path, default=DEFAULT_WORK_DIR)
    parser.add_argument("--device-id", default=DEFAULT_DEVICE_ID)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    config = ServerConfig(args.work_dir, args.device_id, args.output_dir)
    app.mount("/static", StaticFiles(directory=str(APP_DIR / "static")), name="static")
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
