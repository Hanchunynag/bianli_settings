#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Export compact DFS navigation paths from the settings navigation graph.

This script only builds JSON data. It does not connect to a device, replay
operations, resolve coordinates, verify pages, or save runtime sessions.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set

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
    "scope",
    "expect",
)


def load_json(path: Path) -> Graph:
    if not path.exists():
        raise FileNotFoundError(f"navigation graph does not exist: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("navigation graph root must be a JSON object")
    return data


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def graph_path_from_work_dir(work_dir: Path) -> Path:
    return work_dir / "outputs" / "navigation" / "settings_navigation_graph.json"


def transition_steps(transition: Transition) -> List[Dict[str, Any]]:
    """Return transition steps while remaining compatible with old single-target records."""
    steps = transition.get("steps")
    if isinstance(steps, list) and steps:
        return [step for step in steps if isinstance(step, dict)]

    target = transition.get("target")
    if isinstance(target, dict) and target:
        return [{
            "operate": transition.get("operate") or "tap",
            "target": target,
        }]
    return []


def outgoing_map(transitions: Iterable[Transition]) -> Dict[str, List[Transition]]:
    """Group transitions by source page and keep the recorder's stable DFS order."""
    outgoing: Dict[str, List[Transition]] = defaultdict(list)

    for record_order, transition in enumerate(transitions):
        if not isinstance(transition, dict):
            continue

        from_page = str(transition.get("from_page") or "")
        to_page = str(transition.get("to_page") or "")
        if not from_page or not to_page or from_page == to_page:
            continue

        item = dict(transition)
        item["_record_order"] = record_order
        outgoing[from_page].append(item)

    for page_transitions in outgoing.values():
        page_transitions.sort(
            key=lambda item: (
                int(item.get("priority", 1000)),
                int(item.get("_record_order", 0)),
                str(item.get("transition_id") or ""),
            )
        )

    return outgoing


def compact_target(target: Any) -> Target:
    """Keep only stable semantic fields required to locate a target later."""
    if not isinstance(target, dict):
        return {}
    return {
        key: target[key]
        for key in TARGET_FIELDS
        if target.get(key) not in (None, "", [])
    }


def compact_transition_targets(transition: Transition) -> List[Target]:
    """Flatten all targets in one transition into path_snapshot entries."""
    targets: List[Target] = []
    for step in transition_steps(transition):
        target = compact_target(step.get("target"))
        if target:
            targets.append(target)
    return targets


def page_title(graph: Graph, page_name: str) -> str:
    state = graph.get("states", {}).get(page_name, {})
    if not isinstance(state, dict):
        return page_name
    return str(
        state.get("last_title")
        or state.get("page_description")
        or page_name
    ).removeprefix("弹窗：")


def target_label(target: Target) -> str:
    return str(
        target.get("step_prompt")
        or target.get("key_description")
        or target.get("text")
        or target.get("value")
        or target.get("key")
        or ""
    ).strip()


def page_description(path_snapshot: List[Target], fallback: str) -> str:
    labels = [target_label(target) for target in path_snapshot]
    return "_".join(label for label in labels if label) or fallback


def compact_page_operation(operation: Any) -> Optional[Dict[str, Any]]:
    """Keep the operation id, action type, target and optional expected effect."""
    if not isinstance(operation, dict):
        return None

    operation_id = str(operation.get("operation_id") or "").strip()
    if not operation_id:
        return None

    result: Dict[str, Any] = {
        "operation_id": operation_id,
        "operate": str(operation.get("operate") or "tap"),
        "target": compact_target(operation.get("target")),
    }

    effect = operation.get("effect")
    if effect not in (None, "", []):
        result["effect"] = effect

    return result


def page_special_operations(graph: Graph, page_name: str) -> List[Dict[str, Any]]:
    state = graph.get("states", {}).get(page_name, {})
    if not isinstance(state, dict):
        return []

    result: List[Dict[str, Any]] = []
    seen_ids: Set[str] = set()

    for operation in state.get("page_operations") or []:
        compact = compact_page_operation(operation)
        if not compact:
            continue

        operation_id = str(compact["operation_id"])
        if operation_id in seen_ids:
            continue

        seen_ids.add(operation_id)
        result.append(compact)

    return result


class DfsPathExporter:
    """Traverse reachable pages once and directly build the compact output."""

    def __init__(self, graph: Graph, root_page: str) -> None:
        states = graph.get("states")
        if not isinstance(states, dict):
            raise ValueError("navigation graph states must be an object")
        if root_page not in states:
            raise ValueError(f"root page does not exist in graph: {root_page}")

        self.graph = graph
        self.root_page = root_page
        self.outgoing = outgoing_map(graph.get("transitions") or [])
        self.visited: Set[str] = set()
        self.records: List[Dict[str, Any]] = []

    def build(self) -> List[Dict[str, Any]]:
        self._visit(self.root_page, path_snapshot=[])
        return self.records

    def unreachable_pages(self) -> List[str]:
        states = self.graph.get("states", {})
        return sorted(str(page) for page in states if page not in self.visited)

    def _visit(self, page_name: str, path_snapshot: List[Target]) -> None:
        if page_name in self.visited:
            return

        self.visited.add(page_name)

        if page_name != self.root_page:
            self.records.append({
                "package_name": str(self.graph.get("package_name") or ""),
                "main_page_name": str(self.graph.get("main_page_name") or ""),
                "page_description": page_description(
                    path_snapshot,
                    fallback=page_title(self.graph, page_name),
                ),
                "path_snapshot": path_snapshot,
                "special_operate": page_special_operations(
                    self.graph,
                    page_name,
                ),
            })

        for transition in self.outgoing.get(page_name, []):
            to_page = str(transition.get("to_page") or "")
            if not to_page or to_page in self.visited:
                continue

            next_path = [
                *path_snapshot,
                *compact_transition_targets(transition),
            ]
            self._visit(to_page, next_path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export compact DFS paths from settings_navigation_graph.json."
    )
    parser.add_argument(
        "--work-dir",
        default="demo_settings",
        help="Project work dir containing outputs/navigation/settings_navigation_graph.json.",
    )
    parser.add_argument(
        "--graph",
        default="",
        help="Explicit navigation graph path; overrides --work-dir.",
    )
    parser.add_argument(
        "--root",
        default="",
        help="DFS root page; defaults to traversal_config.root_page or Pages_root.",
    )
    parser.add_argument(
        "--output",
        default="",
        help="Output JSON path; defaults to settings_navigation_paths.json.",
    )
    parser.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Console output format.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    work_dir = Path(args.work_dir)
    graph_path = (
        Path(args.graph)
        if args.graph
        else graph_path_from_work_dir(work_dir)
    )
    graph = load_json(graph_path)
    root_page = str(
        args.root
        or graph.get("traversal_config", {}).get("root_page")
        or "Pages_root"
    )

    exporter = DfsPathExporter(graph, root_page)
    output = exporter.build()
    output_path = (
        Path(args.output)
        if args.output
        else graph_path.parent / "settings_navigation_paths.json"
    )
    save_json(output_path, output)

    if args.format == "json":
        print(json.dumps(output, ensure_ascii=False, indent=2))
    else:
        print(f"DFS 页面路径数量: {len(output)}")
        for index, record in enumerate(output, start=1):
            operation_count = len(record.get("special_operate") or [])
            print(
                f"{index:03d}. {record['page_description']} "
                f"(special_operate={operation_count})"
            )
        print(f"精简路径已保存: {output_path}")

        unreachable = exporter.unreachable_pages()
        if unreachable:
            print(f"警告: {len(unreachable)} 个页面从 {root_page} 不可达")
            for page in unreachable:
                print(f"  - {page}")


if __name__ == "__main__":
    main()
