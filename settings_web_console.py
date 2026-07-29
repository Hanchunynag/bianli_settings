#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Web console entrypoint with page-level DFS record maintenance."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Dict, Optional

import uvicorn
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import web_nav_server as core
from DFS import DFS_OVERRIDE_FIELD, build_page_dfs_preview, normalize_dfs_override


class DfsOverrideRequest(BaseModel):
    page_name: str
    record: Optional[Dict[str, Any]] = None
    reset: bool = False


app = core.app


@app.get("/api/dfs_record")
def api_dfs_record(page_name: str) -> JSONResponse:
    graph = core.config.graphs.load()
    return core.ok_response(**build_page_dfs_preview(graph, page_name.strip()))


@app.post("/api/dfs_override")
def api_dfs_override(req: DfsOverrideRequest) -> JSONResponse:
    page_name = req.page_name.strip()
    if not page_name:
        raise ValueError("page_name 不能为空")

    graph = core.config.graphs.load()
    state = graph.get("states", {}).get(page_name)
    if not isinstance(state, dict):
        raise ValueError(f"页面不存在：{page_name}")

    normalized = None if req.reset else normalize_dfs_override(req.record)
    backup = core.config.graphs.backup()
    if req.reset:
        state.pop(DFS_OVERRIDE_FIELD, None)
        message = f"已恢复 {page_name} 的自动 DFS 数据"
    else:
        state[DFS_OVERRIDE_FIELD] = normalized
        message = f"已保存 {page_name} 的 DFS 人工维护数据"
    core.config.graphs.save(graph)

    return core.ok_response(
        **build_page_dfs_preview(graph, page_name),
        graph_backup=backup,
        message=message,
    )


if not any(getattr(route, "path", "") == "/static" for route in app.routes):
    app.mount(
        "/static",
        StaticFiles(directory=str(core.APP_DIR / "static")),
        name="static",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="设置导航录制 Web 控制台（含 DFS 人工维护）")
    parser.add_argument("--work-dir", type=Path, default=Path("settings_workspace"))
    parser.add_argument("--device-id", default=core.DEFAULT_DEVICE_ID)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    core.config = core.ServerConfig(args.work_dir, args.device_id, args.output_dir)
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
