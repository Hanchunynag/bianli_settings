#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Export compact DFS navigation paths from the settings navigation graph.

This script only builds JSON data. It does not connect to a device, replay
operations, resolve coordinates, verify pages, or save runtime sessions.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

Graph = Dict[str, Any]
Transition = Dict[str, Any]
Target = Dict[str, Any]

TARGET_FIELDS = (
    "type",
    "value",
    "key",
    "component_type",
    "text",
    "key_description",
    "step_prompt",
    "expect",
)


DFS_RECORD_FIELDS = (
    "package_name",
    "main_page_name",
    "page_description",
    "path_snapshot",
)

DFS_TARGET_FIELDS = (
    "type",
    "value",
    "key_description",
    "step_prompt",
)


def safe_priority(value: Any, default: int = 1000) -> int:
    """Malformed legacy priority must not make the whole export fail."""
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


def compact_target(target: Any) -> Target:
    """Keep semantic target fields needed while traversing the graph."""
    if not isinstance(target, dict):
        return {}
    return {
        key: target[key]
        for key in TARGET_FIELDS
        if target.get(key) not in (None, "", [])
    }


def format_path_target(target: Any) -> Target:
    """Convert a path step to the strict compact DFS locator schema.

    A stable key is preferred. When no key exists, visible text is used. The
    exported step contains only ``type``, ``value``, ``key_description`` and
    ``step_prompt``.
    """
    compact = compact_target(target)
    if not compact:
        return {}

    key = str(compact.get("key") or "").strip()
    text = str(compact.get("text") or "").strip()
    legacy_type = str(compact.get("type") or "").strip()
    legacy_value = compact.get("value")

    locator_type = ""
    locator_value: Any = None
    if key:
        locator_type, locator_value = "key", key
    elif text:
        locator_type, locator_value = "text", text
    elif legacy_type in {"key", "text"} and legacy_value not in (None, "", []):
        locator_type, locator_value = legacy_type, legacy_value

    if not locator_type:
        return {}

    description = str(
        compact.get("key_description")
        or compact.get("step_prompt")
        or text
        or locator_value
        or ""
    ).strip()
    step_prompt = str(
        compact.get("step_prompt")
        or compact.get("key_description")
        or text
        or locator_value
        or ""
    ).strip()

    formatted: Target = {
        "type": locator_type,
        "value": locator_value,
    }
    if description:
        formatted["key_description"] = description
    if step_prompt:
        formatted["step_prompt"] = step_prompt
    return formatted


def format_dfs_records(records: List[Dict[str, Any]], graph: Graph) -> List[Dict[str, Any]]:
    """Return only the fixed compact fields allowed in DFS output JSON."""
    del graph  # Kept in the public signature for compatibility with callers.
    formatted_records: List[Dict[str, Any]] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        formatted_records.append({
            "package_name": str(record.get("package_name") or ""),
            "main_page_name": str(record.get("main_page_name") or ""),
            "page_description": str(record.get("page_description") or ""),
            "path_snapshot": [
                formatted_target
                for target in record.get("path_snapshot") or []
                if (formatted_target := format_path_target(target))
            ],
        })
    return formatted_records


def export_dfs_paths(graph: Graph, root_page: str) -> tuple[List[Dict[str, Any]], List[str]]:
    states = graph.get("states")
    if not isinstance(states, dict):
        raise ValueError("navigation graph states must be an object")
    if root_page not in states:
        raise ValueError(f"root page does not exist in graph: {root_page}")

    outgoing: Dict[str, List[Transition]] = {}
    for record_order, transition in enumerate(graph.get("transitions") or []):
        if not isinstance(transition, dict):
            continue
        from_page = str(transition.get("from_page") or "")
        to_page = str(transition.get("to_page") or "")
        if not from_page or not to_page or from_page == to_page:
            continue
        if from_page not in states or to_page not in states:
            continue
        outgoing.setdefault(from_page, []).append({**transition, "_record_order": record_order})
    for transitions in outgoing.values():
        transitions.sort(key=lambda item: (
            safe_priority(item.get("priority")),
            int(item.get("_record_order", 0)),
            str(item.get("transition_id") or ""),
        ))

    visited: Set[str] = set()
    records: List[Dict[str, Any]] = []

    def visit(page_name: str, path_snapshot: List[Target]) -> None:
        if page_name in visited:
            return
        visited.add(page_name)

        if page_name != root_page:
            state = graph.get("states", {}).get(page_name, {})
            fallback = str(state.get("last_title") or state.get("page_description") or page_name) if isinstance(state, dict) else page_name
            labels = [
                str(target.get("step_prompt") or target.get("key_description") or target.get("text") or target.get("value") or target.get("key") or "").strip()
                for target in path_snapshot
            ]
            operations = []
            seen_operation_ids: Set[str] = set()
            for operation in state.get("page_operations", []) if isinstance(state, dict) else []:
                if not isinstance(operation, dict):
                    continue
                operation_id = str(operation.get("operation_id") or "").strip()
                if not operation_id or operation_id in seen_operation_ids:
                    continue
                seen_operation_ids.add(operation_id)
                item = {
                    "operation_id": operation_id,
                    "operate": str(operation.get("operate") or "tap"),
                    "target": compact_target(operation.get("target")),
                }
                if operation.get("effect") not in (None, "", []):
                    item["effect"] = operation["effect"]
                operations.append(item)
            records.append({
                "package_name": str(graph.get("package_name") or ""),
                "main_page_name": str(graph.get("main_page_name") or ""),
                "page_description": "_".join(label for label in labels if label) or fallback,
                "path_snapshot": path_snapshot,
                "special_operate": operations,
            })

        for transition in outgoing.get(page_name, []):
            to_page = str(transition.get("to_page") or "")
            if not to_page or to_page in visited:
                continue
            steps = transition.get("steps")
            if not isinstance(steps, list) or not steps:
                target = transition.get("target")
                steps = [{"operate": transition.get("operate") or "tap", "target": target}] if isinstance(target, dict) and target else []
            targets = [compact_target(step.get("target")) for step in steps if isinstance(step, dict)]
            visit(to_page, [*path_snapshot, *(target for target in targets if target)])

    visit(root_page, [])
    return records, sorted(str(page) for page in states if page not in visited)


class DfsPathExporter:
    """Compatibility wrapper around the simple export function."""

    def __init__(self, graph: Graph, root_page: str) -> None:
        self.graph = graph
        self.root_page = root_page
        self._records: Optional[List[Dict[str, Any]]] = None
        self._unreachable: Optional[List[str]] = None

    def build(self) -> List[Dict[str, Any]]:
        if self._records is None:
            self._records, self._unreachable = export_dfs_paths(self.graph, self.root_page)
        return self._records

    def unreachable_pages(self) -> List[str]:
        if self._unreachable is None:
            self._records, self._unreachable = export_dfs_paths(self.graph, self.root_page)
        return self._unreachable


def main() -> None:
    parser = argparse.ArgumentParser(description="Export compact DFS paths from settings_navigation_graph.json.")
    parser.add_argument("--work-dir", default="demo_settings", help="Project work dir containing outputs/navigation/settings_navigation_graph.json.")
    parser.add_argument("--graph", default="", help="Explicit graph path; overrides --work-dir.")
    parser.add_argument("--root", default="", help="DFS root; defaults to traversal_config.root_page or Pages_root.")
    parser.add_argument("--output", default="", help="Output path; defaults beside the graph.")
    parser.add_argument("--format", choices=["text", "json"], default="text", help="Console output format.")
    args = parser.parse_args()
    work_dir = Path(args.work_dir)
    graph_path = Path(args.graph) if args.graph else work_dir / "outputs" / "navigation" / "settings_navigation_graph.json"
    if not graph_path.exists():
        raise FileNotFoundError(f"navigation graph does not exist: {graph_path}")
    graph = json.loads(graph_path.read_text(encoding="utf-8"))
    if not isinstance(graph, dict):
        raise ValueError("navigation graph root must be a JSON object")
    root_page = str(args.root or graph.get("traversal_config", {}).get("root_page") or "Pages_root")
    exporter = DfsPathExporter(graph, root_page)
    output = format_dfs_records(exporter.build(), graph)
    output_path = Path(args.output) if args.output else graph_path.parent / "settings_navigation_paths.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.format == "json":
        print(json.dumps(output, ensure_ascii=False, indent=2))
    else:
        print(f"DFS 页面路径数量: {len(output)}")
        for index, record in enumerate(output, start=1):
            print(f"{index:03d}. {record['page_description']}")
        print(f"精简路径已保存: {output_path}")
        unreachable = exporter.unreachable_pages()
        if unreachable:
            print(f"警告: {len(unreachable)} 个页面从 {root_page} 不可达")
            for page in unreachable:
                print(f"  - {page}")


if __name__ == "__main__":
    main()
