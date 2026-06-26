#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import subprocess
from pathlib import Path
from typing import List, Optional

from settings_json_tree_analyzer import analyze_json_file

DEFAULT_DEVICE_ID = "68Q0223918000004"
DEFAULT_WORK_DIR = Path(r"D:\hanchunyang_6_3\AItest")
HDC = "hd" + "c"
REMOTE_JSON = "/data/local/tmp/current_ui_tree.json"
REMOTE_SCREEN = "/data/local/tmp/current_screen.png"


def run_cmd(cmd: List[str], cwd: Optional[str] = None, timeout: int = 30) -> bool:
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd)
    except Exception as exc:
        print(f"✗ 执行异常: {exc}")
        return False
    if result.returncode != 0:
        print(f"✗ 命令失败: {' '.join(cmd)}")
        if result.stdout.strip():
            print(f"  stdout: {result.stdout.strip()}")
        if result.stderr.strip():
            print(f"  stderr: {result.stderr.strip()}")
        return False
    return True


def check_hdc() -> bool:
    ok = run_cmd([HDC, "version"])
    print("✓ hdc 可用" if ok else "✗ hdc 不可用，请确认 hdc 已加入 PATH")
    return ok


def capture_artifacts(device_id: str, output_dir: Path) -> bool:
    output_dir.mkdir(parents=True, exist_ok=True)
    base = [HDC, "-t", device_id]
    sh = "sh" + "ell"
    steps = [
        (base + [sh, "uitest", "dumpLayout", "-p", REMOTE_JSON], "dumpLayout"),
        (base + ["file", "recv", REMOTE_JSON, "current_ui_tree.json"], "拉取 JSON"),
        (base + [sh, "uitest", "screenCap", "-p", REMOTE_SCREEN], "screenCap"),
        (base + ["file", "recv", REMOTE_SCREEN, "current_screen.png"], "拉取截图"),
    ]
    for cmd, name in steps:
        if not run_cmd(cmd, cwd=str(output_dir)):
            print(f"✗ {name} 失败")
            return False
        print(f"✓ {name} 成功")
    return True


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="PC 端采集 UI JSON 后调用分析器")
    p.add_argument("--device-id", default=DEFAULT_DEVICE_ID)
    p.add_argument("--work-dir", default=str(DEFAULT_WORK_DIR))
    p.add_argument("--output-dir", default="")
    p.add_argument("--graph-dir", default="")
    p.add_argument("--json", default="")
    p.add_argument("--skip-capture", action="store_true")
    p.add_argument("--reset", action="store_true")
    p.add_argument("--parent-node-id", default="")
    p.add_argument("--no-interactive", action="store_true")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    work_dir = Path(args.work_dir)
    output_dir = Path(args.output_dir) if args.output_dir else work_dir / "outputs" / "latest"
    graph_dir = Path(args.graph_dir) if args.graph_dir else work_dir / "outputs" / "graph"
    output_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 60)
    print("脚本2：PC端采集 + 调用脚本1")
    print("=" * 60)
    print(f"工作目录: {work_dir}")
    print(f"输出目录: {output_dir}")
    print(f"图谱目录: {graph_dir}")

    if not args.skip_capture:
        if not check_hdc():
            return
        if not capture_artifacts(args.device_id, output_dir):
            return
    else:
        print("已跳过采集，仅分析已有 JSON")

    json_path = Path(args.json) if args.json else output_dir / "current_ui_tree.json"
    if not json_path.exists():
        print(f"✗ JSON 文件不存在: {json_path}")
        return

    analyze_json_file(
        json_path=json_path,
        graph_dir=graph_dir,
        parent_node_id=args.parent_node_id,
        reset=args.reset,
        interactive=not args.no_interactive,
        current_summary_path=output_dir / "ui_semantic_summary.txt",
    )

    print("执行完成。")


if __name__ == "__main__":
    main()
