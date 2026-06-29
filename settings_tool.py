#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
统一入口：设置 UI 采集、遍历、视觉检测工具箱。

底层实现脚本继续保留用于调试；日常使用优先从这里进入：

  python settings_tool.py record
  python settings_tool.py traverse
  python settings_tool.py crop
  python settings_tool.py visual-prepare
  python settings_tool.py yolo-match
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Dict, List


ROOT = Path(__file__).resolve().parent
BUNDLED_PYTHON = Path("/Users/hanchunyang/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3")


COMMANDS: Dict[str, Dict[str, object]] = {
    "record": {
        "script": "settings_ui_manual_recorder.py",
        "prefix": [],
        "desc": "采集/解析当前页面，更新页面树、状态机、组件库",
    },
    "traverse": {
        "script": "settings_detection_traverser.py",
        "prefix": [],
        "desc": "根据页面树/状态机/组件库生成检测遍历任务",
    },
    "crop": {
        "script": "settings_component_cropper.py",
        "prefix": [],
        "desc": "按组件 bounds 从页面截图裁剪组件小图",
        "prefer_bundled_python": True,
    },
    "visual-prepare": {
        "script": "settings_visual_review_interface.py",
        "prefix": [],
        "desc": "生成规则检测、VLM 请求、LLM 裁决请求",
        "prefer_bundled_python": True,
    },
    "yolo-match": {
        "script": "settings_yolo_backup_detector.py",
        "prefix": [],
        "desc": "运行/接入 YOLO 备份检测，并和 UI tree 组件匹配",
        "prefer_bundled_python": True,
    },
    "sm-reset": {
        "script": "settings_ui_manual_recorder.py",
        "prefix": ["--sm-reset"],
        "desc": "只清空状态机，保留页面树和组件库",
    },
    "sm-prune": {
        "script": "settings_ui_manual_recorder.py",
        "prefix": ["--sm-prune"],
        "desc": "清理状态机孤儿 verify/signature 和页面失效引用",
    },
    "sm-delete-state": {
        "script": "settings_ui_manual_recorder.py",
        "prefix": ["--sm-delete-state"],
        "takes_value": True,
        "desc": "删除指定 state 及相关 transition/verify",
    },
    "sm-delete-transition": {
        "script": "settings_ui_manual_recorder.py",
        "prefix": ["--sm-delete-transition"],
        "takes_value": True,
        "desc": "删除指定 transition 及对应 verify",
    },
    "tree-delete": {
        "script": "settings_ui_manual_recorder.py",
        "prefix": ["--delete"],
        "takes_value": True,
        "desc": "按编号删除页面树分支，并同步清理状态机",
    },
    "tree-clear": {
        "script": "settings_ui_manual_recorder.py",
        "prefix": ["--clear"],
        "takes_value": True,
        "desc": "按编号清空页面树分支子节点，并同步清理状态机",
    },
    "tree-reset": {
        "script": "settings_ui_manual_recorder.py",
        "prefix": ["--reset"],
        "optional_value": True,
        "desc": "清空整棵树，或按编号删除/清空分支",
    },
}


ALIASES = {
    "capture": "record",
    "detect-plan": "traverse",
    "visual": "visual-prepare",
    "yolo": "yolo-match",
    "delete": "tree-delete",
    "clear": "tree-clear",
}


def has_pillow(python_exe: str) -> bool:
    code = "import PIL"
    result = subprocess.run([python_exe, "-c", code], capture_output=True, text=True)
    return result.returncode == 0


def choose_python(spec: Dict[str, object]) -> str:
    if not spec.get("prefer_bundled_python"):
        return sys.executable
    if has_pillow(sys.executable):
        return sys.executable
    if BUNDLED_PYTHON.exists():
        return str(BUNDLED_PYTHON)
    return sys.executable


def print_help() -> None:
    print("设置 UI 工具箱")
    print("")
    print("用法:")
    print("  python settings_tool.py <command> [args...]")
    print("")
    print("常用命令:")
    for name in [
        "record",
        "traverse",
        "crop",
        "visual-prepare",
        "yolo-match",
        "sm-prune",
        "sm-reset",
        "tree-delete",
        "tree-clear",
    ]:
        spec = COMMANDS[name]
        print(f"  {name:<20} {spec['desc']}")
    print("")
    print("示例:")
    print("  python settings_tool.py record --skip-capture")
    print("  python settings_tool.py traverse --run")
    print("  python settings_tool.py crop --page-id \"title::流量管理\"")
    print("  python settings_tool.py visual-prepare --vlm-all")
    print("  python settings_tool.py yolo-match --model custom_ui.pt")
    print("  python settings_tool.py sm-delete-state root/WLAN")
    print("  python settings_tool.py tree-delete 7.3")
    print("")
    print("说明: 日常优先使用这个统一入口；底层脚本仅作为实现模块和调试入口。")


def split_value_and_passthrough(rest: List[str]) -> List[str]:
    if not rest:
        raise SystemExit("这个命令需要一个值，例如编号、state_id 或 transition_id。")
    value = rest[0]
    if value.startswith("-"):
        raise SystemExit("这个命令需要先给值，再给其它参数。")
    return [value] + rest[1:]


def build_args(command: str, rest: List[str]) -> List[str]:
    spec = COMMANDS[command]
    prefix = list(spec.get("prefix", []))
    if spec.get("takes_value"):
        return prefix + split_value_and_passthrough(rest)
    if spec.get("optional_value") and rest and not rest[0].startswith("-"):
        return prefix + [rest[0]] + rest[1:]
    return prefix + rest


def run_command(command: str, rest: List[str]) -> int:
    command = ALIASES.get(command, command)
    if command not in COMMANDS:
        print(f"未知命令: {command}\n")
        print_help()
        return 2

    spec = COMMANDS[command]
    script = ROOT / str(spec["script"])
    if not script.exists():
        print(f"脚本不存在: {script}")
        return 2

    python_exe = choose_python(spec)
    args = build_args(command, rest)
    cmd = [python_exe, str(script)] + args
    print(f"→ {' '.join(cmd)}")
    return subprocess.run(cmd, cwd=str(ROOT)).returncode


def main() -> None:
    if len(sys.argv) <= 1 or sys.argv[1] in {"-h", "--help", "help"}:
        print_help()
        return
    command = sys.argv[1]
    rest = sys.argv[2:]
    raise SystemExit(run_command(command, rest))


if __name__ == "__main__":
    main()
