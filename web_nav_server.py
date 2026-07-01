#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FastAPI 设置导航录制 Web 控制台。"""

import argparse
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
    capture_artifacts,
    extract_navigation_candidates,
    horizontal_target,
    load_json,
    load_navigation_graph,
    navigation_graph_path,
    next_horizontal_view_state,
    now_iso,
    pending_transition_path,
    save_current_path_session,
    save_json,
    save_navigation_graph,
    screen_metrics_from_root,
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


@app.post("/api/tap_candidate")
def api_tap_candidate(req: TapCandidateRequest) -> JSONResponse:
    try:
        current = read_current_state(capture=True)
        candidates = current["candidates"]
        if req.index < 1 or req.index > len(candidates):
            raise ValueError(f"候选编号无效：{req.index}")
        selected = candidates[req.index - 1]
        graph = load_navigation_graph(config.work_dir)
        active_state = active_navigation_state(config.work_dir, graph, current["state"])
        target = dict(selected["suggested_target"])
        if req.expect:
            target["expect"] = req.expect
        pending = {"from_page": active_state["page_name"], "operate": "tap", "target": target, "created_at": now_iso()}
        if req.effect:
            pending["effect"] = req.effect
        save_json(pending, pending_transition_path(config.work_dir), "未完成导航转移")
        execute_tap(config.device_id, selected.get("bounds_center"))
        time.sleep(1.2)
        after = read_current_state(capture=True)
        graph = load_navigation_graph(config.work_dir)
        graph.setdefault("states", {})[after["state"]["page_name"]] = after["state"]
        auto_complete_pending_if_needed(config.work_dir, graph, after["state"])
        return ok_response(**read_current_state(capture=False))
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
