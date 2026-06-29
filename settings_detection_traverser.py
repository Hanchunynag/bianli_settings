#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
设置检测遍历器。

它不连接手机，也不执行具体检测动作，只根据已采集的设置树、状态机和
页面组件事实库生成一个稳定的遍历计划：

1. 按设置树/状态机顺序遍历页面；
2. 在每个页面内遍历语义组件；
3. 输出后续检测执行器可以消费的任务 JSONL。
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict, deque
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple


DEFAULT_WORK_DIR = Path(r"D:\hanchunyang_6_3\AItest")


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def load_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8-sig") as f:
        return json.load(f)


def save_json(data: Any, path: Path, desc: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"✓ {desc}: {path}")


def save_text(text: str, path: Path, desc: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        f.write(text.rstrip() + "\n")
    print(f"✓ {desc}: {path}")


def write_jsonl(rows: Iterable[Dict[str, Any]], path: Path, desc: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    print(f"✓ {desc}: {path}")


def node_path_text(node_id: str) -> str:
    if not node_id or node_id == "root":
        return "root"
    return node_id.replace("root/", "root > ").replace("/", " > ")


def tree_walk(root: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    yield root
    for child in root.get("children", []) or []:
        yield from tree_walk(child)


def load_components_jsonl(path: Path) -> Dict[str, Dict[str, Dict[str, Any]]]:
    by_page: Dict[str, Dict[str, Dict[str, Any]]] = defaultdict(dict)
    if not path.exists():
        return by_page
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            item = json.loads(line)
            page_id = str(item.get("page_id") or "")
            component_id = str(item.get("component_id") or "")
            if page_id and component_id:
                by_page[page_id][component_id] = item
    return by_page


def components_for_page(page: Dict[str, Any], jsonl_components: Dict[str, Dict[str, Dict[str, Any]]]) -> List[Dict[str, Any]]:
    page_id = str(page.get("page_id") or "")
    source = jsonl_components.get(page_id) or page.get("components", {}) or {}
    return sorted(
        source.values(),
        key=lambda c: (
            int(c.get("semantic_order", c.get("last_seen_order", c.get("observed_order", 0))) or 0),
            str(c.get("record_group") or ""),
            str(c.get("name") or c.get("text") or ""),
        ),
    )


def transition_sort_key(transition: Dict[str, Any]) -> Tuple[str, str, str]:
    trigger = transition.get("trigger_node", {}) if isinstance(transition.get("trigger_node"), dict) else {}
    tap_target = transition.get("tap_target", {}) if isinstance(transition.get("tap_target"), dict) else {}
    return (
        str(trigger.get("name") or tap_target.get("text") or ""),
        str(transition.get("to_state_id") or ""),
        str(transition.get("transition_id") or ""),
    )


def build_transition_indexes(state_machine: Dict[str, Any]) -> Tuple[Dict[str, List[Dict[str, Any]]], Dict[str, Dict[str, Any]]]:
    by_from: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    by_id: Dict[str, Dict[str, Any]] = {}
    for transition_id, transition in (state_machine.get("transitions", {}) or {}).items():
        if not isinstance(transition, dict):
            continue
        item = dict(transition)
        item["transition_id"] = str(item.get("transition_id") or transition_id)
        by_id[item["transition_id"]] = item
        by_from[str(item.get("from_state_id") or "")].append(item)
    for transitions in by_from.values():
        transitions.sort(key=transition_sort_key)
    return by_from, by_id


def tree_state_order(index: Dict[str, Any], tree: Dict[str, Any], state_machine: Dict[str, Any]) -> List[str]:
    states = state_machine.get("states", {}) or {}
    ordered: List[str] = []
    seen: Set[str] = set()

    if tree.get("root"):
        for node in tree_walk(tree["root"]):
            node_id = str(node.get("node_id") or "")
            if node_id in states and node_id not in seen:
                ordered.append(node_id)
                seen.add(node_id)

    for page in index.get("pages", {}).values():
        state_id = str(page.get("state_id") or "")
        if state_id and state_id in states and state_id not in seen:
            ordered.append(state_id)
            seen.add(state_id)

    for state_id in states.keys():
        if state_id not in seen:
            ordered.append(state_id)
            seen.add(state_id)

    return ordered


def graph_state_order(state_machine: Dict[str, Any], start_state: str = "root") -> List[str]:
    states = state_machine.get("states", {}) or {}
    by_from, _ = build_transition_indexes(state_machine)
    ordered: List[str] = []
    seen: Set[str] = set()
    queue = deque([start_state] if start_state in states else [])

    while queue:
        state_id = queue.popleft()
        if state_id in seen:
            continue
        seen.add(state_id)
        ordered.append(state_id)
        for transition in by_from.get(state_id, []):
            to_state_id = str(transition.get("to_state_id") or "")
            if to_state_id and to_state_id not in seen:
                queue.append(to_state_id)

    for state_id in states.keys():
        if state_id not in seen:
            ordered.append(state_id)
            seen.add(state_id)
    return ordered


def page_order_without_states(index: Dict[str, Any]) -> List[str]:
    return [
        page_id for page_id, _ in sorted(
            (index.get("pages", {}) or {}).items(),
            key=lambda item: (
                str(item[1].get("parent_node_id") or ""),
                str(item[1].get("title") or ""),
                str(item[0]),
            ),
        )
    ]


def component_task_payload(component: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "component_id": component.get("component_id", ""),
        "semantic_component_id": component.get("semantic_component_id", component.get("component_id", "")),
        "name": component.get("name", ""),
        "text": component.get("text", ""),
        "record_group": component.get("record_group", ""),
        "kind": component.get("kind", ""),
        "type": component.get("type", ""),
        "key": component.get("key", ""),
        "locator": component.get("locator", ""),
        "bounds": component.get("bounds", ""),
        "bounds_center": component.get("bounds_center"),
        "normalized_center": component.get("normalized_center"),
        "coordinate_space": component.get("coordinate_space", ""),
        "screen_size": component.get("screen_size"),
        "clickable": component.get("clickable", False),
        "enabled": component.get("enabled", True),
        "visible": component.get("visible", True),
        "value": component.get("value", ""),
        "merged_child_count": component.get("merged_child_count", 0),
        "merged_children": component.get("merged_children", []),
    }


def make_page_visit_task(order: int, state_id: str, page_id: str, page: Dict[str, Any], state: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "task_order": order,
        "task_type": "page_visit",
        "state_id": state_id,
        "page_id": page_id,
        "page_title": page.get("title") or state.get("title", ""),
        "nav_key": page.get("nav_key") or state.get("nav_key", ""),
        "signature_id": page.get("signature_id") or state.get("signature_id", ""),
        "tree_path": node_path_text(state_id),
        "action": "visit_page_placeholder",
    }


def make_component_task(order: int, state_id: str, page_id: str, page: Dict[str, Any], component: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "task_order": order,
        "task_type": "component_check",
        "state_id": state_id,
        "page_id": page_id,
        "page_title": page.get("title", ""),
        "tree_path": node_path_text(state_id),
        "component": component_task_payload(component),
        "action": "component_check_placeholder",
    }


def make_transition_task(order: int, transition: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "task_order": order,
        "task_type": "transition",
        "transition_id": transition.get("transition_id", ""),
        "from_state_id": transition.get("from_state_id", ""),
        "to_state_id": transition.get("to_state_id", ""),
        "event_type": transition.get("event_type", "tap"),
        "trigger_node": transition.get("trigger_node", {}),
        "tap_target": transition.get("tap_target", {}),
        "to_page_id": transition.get("to_page_id", ""),
        "to_page_title": transition.get("to_page_title", ""),
        "signature_id": transition.get("signature_id", ""),
        "action": "transition_replay_placeholder",
    }


def build_traversal_plan(index: Dict[str, Any], tree: Dict[str, Any], state_machine: Dict[str, Any], components_by_page: Dict[str, Dict[str, Dict[str, Any]]], order_mode: str) -> Dict[str, Any]:
    pages = index.get("pages", {}) or {}
    states = state_machine.get("states", {}) or {}
    by_from, _ = build_transition_indexes(state_machine)

    if states:
        state_order = graph_state_order(state_machine) if order_mode == "state-graph" else tree_state_order(index, tree, state_machine)
    else:
        state_order = []

    tasks: List[Dict[str, Any]] = []
    visited_pages: Set[str] = set()

    def append_task(task: Dict[str, Any]) -> None:
        task["task_order"] = len(tasks)
        tasks.append(task)

    for state_id in state_order:
        state = states.get(state_id, {})
        page_id = str(state.get("page_id") or "")
        page = pages.get(page_id, {}) if page_id else {}
        append_task(make_page_visit_task(len(tasks), state_id, page_id, page, state))
        if page_id:
            visited_pages.add(page_id)
        for component in components_for_page(page, components_by_page):
            append_task(make_component_task(len(tasks), state_id, page_id, page, component))
        for transition in by_from.get(state_id, []):
            append_task(make_transition_task(len(tasks), transition))

    for page_id in page_order_without_states(index):
        if page_id in visited_pages:
            continue
        page = pages.get(page_id, {})
        state_id = str(page.get("state_id") or "")
        append_task(make_page_visit_task(len(tasks), state_id, page_id, page, {}))
        for component in components_for_page(page, components_by_page):
            append_task(make_component_task(len(tasks), state_id, page_id, page, component))

    page_count = len([task for task in tasks if task["task_type"] == "page_visit"])
    component_count = len([task for task in tasks if task["task_type"] == "component_check"])
    transition_count = len([task for task in tasks if task["task_type"] == "transition"])

    return {
        "schema_version": "0.1",
        "generated_at": now_iso(),
        "order_mode": order_mode,
        "summary": {
            "page_visit_tasks": page_count,
            "component_check_tasks": component_count,
            "transition_tasks": transition_count,
            "total_tasks": len(tasks),
        },
        "tasks": tasks,
    }


def build_report(plan: Dict[str, Any]) -> str:
    summary = plan.get("summary", {})
    lines = [
        "# 设置检测遍历计划",
        "",
        f"- 生成时间：{plan.get('generated_at', '')}",
        f"- 遍历模式：`{plan.get('order_mode', '')}`",
        f"- 页面任务：{summary.get('page_visit_tasks', 0)}",
        f"- 组件任务：{summary.get('component_check_tasks', 0)}",
        f"- 跳转任务：{summary.get('transition_tasks', 0)}",
        f"- 总任务：{summary.get('total_tasks', 0)}",
        "",
        "## 任务明细",
        "",
    ]

    current_page = ""
    for task in plan.get("tasks", []):
        task_type = task.get("task_type")
        if task_type == "page_visit":
            current_page = str(task.get("page_id") or "")
            lines.extend([
                f"### {task.get('page_title') or current_page or task.get('state_id') or '未命名页面'}",
                "",
                f"- state_id：`{task.get('state_id', '')}`",
                f"- page_id：`{current_page}`",
                f"- signature_id：`{task.get('signature_id', '')}`",
                "",
            ])
        elif task_type == "component_check":
            comp = task.get("component", {})
            label = comp.get("name") or comp.get("text") or comp.get("type") or comp.get("component_id")
            lines.append(
                f"- 组件 `{comp.get('record_group', '')}/{comp.get('kind', '')}` "
                f"{label} center={comp.get('bounds_center')} key={comp.get('key') or '-'}"
            )
        elif task_type == "transition":
            target = task.get("tap_target", {})
            label = target.get("text") or task.get("to_page_title") or task.get("to_state_id")
            lines.append(
                f"- 跳转 `{task.get('transition_id', '')}` tap {label} -> `{task.get('to_state_id', '')}`"
            )
    return "\n".join(lines).rstrip() + "\n"


def handle_page_visit(task: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "status": "skipped",
        "reason": "page navigation is not implemented yet",
        "state_id": task.get("state_id", ""),
        "page_id": task.get("page_id", ""),
    }


def handle_component_check(task: Dict[str, Any]) -> Dict[str, Any]:
    component = task.get("component", {})
    return {
        "status": "skipped",
        "reason": "component check is not implemented yet",
        "component_id": component.get("component_id", ""),
        "component_name": component.get("name") or component.get("text") or component.get("type") or "",
    }


def handle_transition(task: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "status": "skipped",
        "reason": "transition replay is not implemented yet",
        "transition_id": task.get("transition_id", ""),
        "from_state_id": task.get("from_state_id", ""),
        "to_state_id": task.get("to_state_id", ""),
    }


def run_traversal_tasks(tasks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    handlers = {
        "page_visit": handle_page_visit,
        "component_check": handle_component_check,
        "transition": handle_transition,
    }
    results: List[Dict[str, Any]] = []
    for task in tasks:
        task_type = str(task.get("task_type") or "")
        handler = handlers.get(task_type)
        if handler:
            outcome = handler(task)
        else:
            outcome = {"status": "error", "reason": f"unknown task_type: {task_type}"}
        results.append({
            "task_order": task.get("task_order", len(results)),
            "task_type": task_type,
            "task_ref": task.get("transition_id") or task.get("page_id") or task.get("component", {}).get("component_id", ""),
            "outcome": outcome,
        })
    return results


def run_summary(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_status: Dict[str, int] = defaultdict(int)
    by_type: Dict[str, int] = defaultdict(int)
    for result in results:
        by_type[str(result.get("task_type") or "")] += 1
        by_status[str(result.get("outcome", {}).get("status") or "")] += 1
    return {
        "generated_at": now_iso(),
        "total_results": len(results),
        "by_task_type": dict(sorted(by_type.items())),
        "by_status": dict(sorted(by_status.items())),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="根据已采集设置树/状态机/组件库生成检测遍历任务")
    parser.add_argument("--work-dir", default=str(DEFAULT_WORK_DIR))
    parser.add_argument("--graph-dir", default="")
    parser.add_argument("--output-dir", default="")
    parser.add_argument(
        "--order",
        choices=["tree", "state-graph"],
        default="tree",
        help="tree 按设置树顺序；state-graph 按状态机 transition BFS 顺序",
    )
    parser.add_argument("--run", action="store_true", help="按生成的任务顺序执行占位遍历处理函数")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    work_dir = Path(args.work_dir)
    graph_dir = Path(args.graph_dir) if args.graph_dir else work_dir / "outputs" / "graph"
    output_dir = Path(args.output_dir) if args.output_dir else graph_dir

    index_path = graph_dir / "settings_nodes_index.json"
    tree_path = graph_dir / "settings_tree.json"
    state_machine_path = graph_dir / "settings_state_machine.json"
    components_path = graph_dir / "settings_page_components.jsonl"

    index = load_json(index_path, {"pages": {}}) or {"pages": {}}
    tree = load_json(tree_path, {"root": {}}) or {"root": {}}
    state_machine = load_json(state_machine_path, index.get("state_machine", {})) or index.get("state_machine", {})
    components_by_page = load_components_jsonl(components_path)

    plan = build_traversal_plan(index, tree, state_machine, components_by_page, args.order)
    save_json(plan, output_dir / "settings_detection_traversal_plan.json", "检测遍历计划")
    write_jsonl(plan["tasks"], output_dir / "settings_detection_traversal_tasks.jsonl", "检测遍历任务 JSONL")
    save_text(build_report(plan), output_dir / "settings_detection_traversal_report.md", "检测遍历报告")

    if args.run:
        results = run_traversal_tasks(plan["tasks"])
        write_jsonl(results, output_dir / "settings_detection_traversal_run.jsonl", "检测遍历执行结果 JSONL")
        save_json(run_summary(results), output_dir / "settings_detection_traversal_run_summary.json", "检测遍历执行摘要")

    summary = plan["summary"]
    print(
        f"执行完成：页面 {summary['page_visit_tasks']} 个，"
        f"组件任务 {summary['component_check_tasks']} 个，"
        f"跳转任务 {summary['transition_tasks']} 条。"
    )


if __name__ == "__main__":
    main()
