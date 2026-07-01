#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Plan and optionally execute a DFS traversal from the settings navigation graph.

The persistent graph does not store device coordinates. During real execution,
this script captures the current UI tree, resolves each recorded target against
clickable areas in that tree, uses the resolved center only temporarily, and
then verifies that the expected page is reached.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from settings_ui_manual_recorder import (
    DEFAULT_DEVICE_ID,
    annotate,
    attrs,
    build_navigation_state,
    children,
    extract_navigation_candidates,
    get_attr,
    get_key,
    get_text,
    get_type,
    is_enabled,
    is_visible,
    meaningful_texts,
    parse_rect,
    screen_metrics_from_root,
    to_bool,
    walk,
)


Graph = Dict[str, Any]
Transition = Dict[str, Any]
PlanEvent = Dict[str, Any]
Target = Dict[str, Any]


def load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def graph_path_from_work_dir(work_dir: Path) -> Path:
    return work_dir / "outputs" / "navigation" / "settings_navigation_graph.json"


def transition_label(transition: Transition) -> str:
    labels: List[str] = []
    for step in transition_steps(transition):
        target = step.get("target") or {}
        label = (
            target.get("step_prompt")
            or target.get("key_description")
            or target.get("text")
            or target.get("value")
            or target.get("key")
            or step.get("operate")
            or "操作"
        )
        labels.append(str(label))
    return " -> ".join(labels) if labels else str(transition.get("transition_id") or "未命名跳转")


def transition_steps(transition: Transition) -> List[Dict[str, Any]]:
    steps = transition.get("steps")
    if isinstance(steps, list) and steps:
        return [step for step in steps if isinstance(step, dict)]
    target = transition.get("target")
    if isinstance(target, dict) and target:
        return [{"operate": transition.get("operate") or "tap", "target": target}]
    return []


def outgoing_map(transitions: Iterable[Transition]) -> Dict[str, List[Transition]]:
    outgoing: Dict[str, List[Transition]] = defaultdict(list)
    for index, transition in enumerate(transitions):
        from_page = str(transition.get("from_page") or "")
        to_page = str(transition.get("to_page") or "")
        if not from_page or not to_page or from_page == to_page:
            continue
        item = dict(transition)
        item["_record_order"] = index
        outgoing[from_page].append(item)
    for page in outgoing:
        outgoing[page].sort(key=lambda t: (int(t.get("priority", 1000)), int(t.get("_record_order", 0)), str(t.get("transition_id") or "")))
    return outgoing


def page_title(graph: Graph, page_name: str) -> str:
    state = graph.get("states", {}).get(page_name, {})
    return str(state.get("last_title") or state.get("page_description") or page_name)


def page_operations(graph: Graph, page_name: str) -> List[Dict[str, Any]]:
    state = graph.get("states", {}).get(page_name, {})
    ops = state.get("page_operations") or []
    return [op for op in ops if isinstance(op, dict)]


def return_policy_for(graph: Graph, from_child: str, to_parent: str) -> Dict[str, Any]:
    state = graph.get("states", {}).get(from_child, {})
    policy = state.get("return_policy")
    if isinstance(policy, dict) and policy:
        clean = dict(policy)
        clean.setdefault("target_page", to_parent)
        return clean
    default_policy = graph.get("traversal_config", {}).get("default_return_policy")
    if isinstance(default_policy, dict) and default_policy:
        clean = dict(default_policy)
        clean.setdefault("target_page", to_parent)
        return clean
    return {"type": "system_back", "target_page": to_parent}


class DfsPlanner:
    def __init__(self, graph: Graph, root_page: str) -> None:
        self.graph = graph
        self.root_page = root_page
        self.outgoing = outgoing_map(graph.get("transitions", []))
        self.visited_pages: Set[str] = set()
        self.visited_transitions: Set[str] = set()
        self.events: List[PlanEvent] = []
        self.path_stack: List[str] = []
        self.transition_stack: List[str] = []

    def build(self) -> Dict[str, Any]:
        if self.root_page not in self.graph.get("states", {}):
            raise ValueError(f"root page does not exist in graph: {self.root_page}")
        self.path_stack = [self.root_page]
        self._visit_page(self.root_page, depth=0)
        unreachable = sorted(set(self.graph.get("states", {})) - self.visited_pages)
        return {
            "strategy": "dfs",
            "root_page": self.root_page,
            "visited_pages": list(self.visited_pages),
            "visited_transitions": list(self.visited_transitions),
            "unreachable_pages": unreachable,
            "events": self.events,
            "summary": {
                "page_count": len(self.graph.get("states", {})),
                "visited_page_count": len(self.visited_pages),
                "transition_count": len([t for t in self.graph.get("transitions", []) if t.get("from_page") != t.get("to_page")]),
                "visited_transition_count": len(self.visited_transitions),
                "event_count": len(self.events),
            },
        }

    def _snapshot_path(self) -> Dict[str, Any]:
        return {
            "page_path": list(self.path_stack),
            "transition_path": list(self.transition_stack),
        }

    def _append(self, event: PlanEvent) -> None:
        event["order"] = len(self.events) + 1
        self.events.append(event)

    def _visit_page(self, page: str, depth: int) -> None:
        first_visit = page not in self.visited_pages
        self.visited_pages.add(page)
        self._append({
            "event": "visit_page",
            "page": page,
            "title": page_title(self.graph, page),
            "depth": depth,
            "first_visit": first_visit,
            **self._snapshot_path(),
        })

        ops = page_operations(self.graph, page)
        if ops:
            self._append({
                "event": "record_page_operations",
                "page": page,
                "depth": depth,
                "operations": ops,
                **self._snapshot_path(),
            })

        for transition in self.outgoing.get(page, []):
            transition_id = str(transition.get("transition_id") or "")
            if not transition_id:
                continue
            if transition_id in self.visited_transitions:
                self._append({
                    "event": "skip_transition",
                    "reason": "transition_already_visited",
                    "transition_id": transition_id,
                    "from_page": transition.get("from_page"),
                    "to_page": transition.get("to_page"),
                    "depth": depth,
                    **self._snapshot_path(),
                })
                continue

            to_page = str(transition.get("to_page") or "")
            self.visited_transitions.add(transition_id)
            self.transition_stack.append(transition_id)
            self.path_stack.append(to_page)

            self._append({
                "event": "enter_transition",
                "transition_id": transition_id,
                "from_page": page,
                "to_page": to_page,
                "label": transition_label(transition),
                "steps": transition_steps(transition),
                "depth": depth,
                **self._snapshot_path(),
            })

            if to_page not in self.visited_pages:
                self._visit_page(to_page, depth + 1)
            else:
                self._append({
                    "event": "already_seen_page",
                    "page": to_page,
                    "title": page_title(self.graph, to_page),
                    "depth": depth + 1,
                    **self._snapshot_path(),
                })

            self._append({
                "event": "return_to_parent",
                "from_page": to_page,
                "to_page": page,
                "return_policy": return_policy_for(self.graph, to_page, page),
                "depth": depth,
                **self._snapshot_path(),
            })

            self.path_stack.pop()
            self.transition_stack.pop()


def print_text_plan(plan: Dict[str, Any]) -> None:
    summary = plan.get("summary", {})
    print(f"DFS root: {plan.get('root_page')}")
    print(
        "visited pages/transitions: "
        f"{summary.get('visited_page_count')}/{summary.get('page_count')} pages, "
        f"{summary.get('visited_transition_count')}/{summary.get('transition_count')} transitions"
    )
    if plan.get("unreachable_pages"):
        print("unreachable pages: " + ", ".join(plan["unreachable_pages"]))
    print("")

    for event in plan.get("events", []):
        depth = int(event.get("depth") or 0)
        indent = "  " * depth
        name = event.get("event")
        order = event.get("order")
        if name == "visit_page":
            marker = "首次访问" if event.get("first_visit") else "再次到达"
            print(f"{order:02d}. {indent}访问页面: {event.get('page')} ({event.get('title')}) [{marker}]")
        elif name == "record_page_operations":
            print(f"{order:02d}. {indent}记录页面内操作: {event.get('page')}")
            for op in event.get("operations", []):
                target = op.get("target") or {}
                label = target.get("step_prompt") or target.get("key_description") or target.get("value") or "操作对象"
                print(f"{indent}    - {op.get('operate')}: {label} => {op.get('effect')}")
        elif name == "enter_transition":
            print(f"{order:02d}. {indent}进入: {event.get('from_page')} -> {event.get('to_page')} ({event.get('transition_id')})")
            for idx, step in enumerate(event.get("steps", []), start=1):
                target = step.get("target") or {}
                label = target.get("step_prompt") or target.get("key_description") or target.get("value") or target.get("key") or "操作对象"
                print(f"{indent}    step {idx}: {step.get('operate', 'tap')} {label}")
        elif name == "already_seen_page":
            print(f"{order:02d}. {indent}目标页面已访问过: {event.get('page')}，本次只验证入口后返回")
        elif name == "return_to_parent":
            policy = event.get("return_policy") or {}
            print(f"{order:02d}. {indent}返回: {event.get('from_page')} -> {event.get('to_page')} ({policy.get('type', 'system_back')})")
        elif name == "skip_transition":
            print(f"{order:02d}. {indent}跳过 transition: {event.get('transition_id')} ({event.get('reason')})")


def now_ms() -> int:
    return int(time.time() * 1000)


def hdc_base(device_id: str) -> List[str]:
    return ["hdc"] + (["-t", device_id] if device_id else [])


def run_command(cmd: List[str], cwd: Optional[Path] = None, timeout: int = 20) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=str(cwd) if cwd else None, capture_output=True, text=True, timeout=timeout)


def require_command_success(cmds: List[List[str]], action: str, cwd: Optional[Path] = None, timeout: int = 20) -> None:
    errors: List[str] = []
    for cmd in cmds:
        try:
            result = run_command(cmd, cwd=cwd, timeout=timeout)
        except Exception as exc:
            errors.append(f"{' '.join(cmd)} -> {exc}")
            continue
        if result.returncode == 0:
            return
        errors.append(
            f"{' '.join(cmd)} -> code={result.returncode}, "
            f"stdout={result.stdout.strip()}, stderr={result.stderr.strip()}"
        )
    raise RuntimeError(f"{action} failed: " + " | ".join(errors))


class DeviceDriver:
    def __init__(self, device_id: str, output_dir: Path, dry_run: bool = False, capture_screen: bool = False) -> None:
        self.device_id = device_id
        self.output_dir = output_dir
        self.dry_run = dry_run
        self.capture_screen = capture_screen

    def capture_ui_tree(self) -> Dict[str, Any]:
        path = self.output_dir / "current_ui_tree.json"
        if self.dry_run:
            if not path.exists():
                raise FileNotFoundError(f"dry-run needs an existing UI tree: {path}")
            root = load_json(path)
            annotate(root)
            return root

        self.output_dir.mkdir(parents=True, exist_ok=True)
        version = run_command(["hdc", "version"], timeout=10)
        if version.returncode != 0:
            raise RuntimeError("hdc is unavailable. Put hdc in PATH or use --dry-run.")

        base = hdc_base(self.device_id)
        require_command_success([
            base + ["shell", "uitest", "dumpLayout", "-p", "/data/local/tmp/current_ui_tree.json"],
        ], "dump current UI tree", timeout=30)
        require_command_success([
            base + ["file", "recv", "/data/local/tmp/current_ui_tree.json", "current_ui_tree.json"],
        ], "pull current UI tree", cwd=self.output_dir, timeout=30)

        if self.capture_screen:
            require_command_success([
                base + ["shell", "uitest", "screenCap", "-p", "/data/local/tmp/current_screen.png"],
            ], "capture screen", timeout=30)
            require_command_success([
                base + ["file", "recv", "/data/local/tmp/current_screen.png", "current_screen.png"],
            ], "pull screen", cwd=self.output_dir, timeout=30)

        root = load_json(path)
        annotate(root)
        return root

    def tap(self, center: List[int]) -> None:
        x, y = int(center[0]), int(center[1])
        if self.dry_run:
            print(f"    dry-run tap [{x}, {y}]")
            return
        base = hdc_base(self.device_id) + ["shell"]
        require_command_success([
            base + ["uitest", "uiInput", "click", str(x), str(y)],
            base + ["input", "tap", str(x), str(y)],
        ], f"tap [{x}, {y}]")

    def back(self) -> None:
        if self.dry_run:
            print("    dry-run system_back")
            return
        base = hdc_base(self.device_id) + ["shell"]
        require_command_success([
            base + ["uitest", "uiInput", "keyEvent", "Back"],
            base + ["input", "keyevent", "BACK"],
        ], "system back")

    def gesture(self, operate: str, center: List[int], metrics: Dict[str, Any]) -> None:
        if operate == "tap":
            self.tap(center)
            return
        x, y = int(center[0]), int(center[1])
        size = metrics.get("screen_size") or [1080, 2400]
        width, height = int(size[0]), int(size[1])
        dx = max(160, int(width * 0.22))
        dy = max(180, int(height * 0.12))
        if operate == "long_press":
            x1, y1, x2, y2, duration = x, y, x, y, "900"
        elif operate == "swipe_left":
            x1, y1, x2, y2, duration = x + dx // 2, y, x - dx // 2, y, "600"
        elif operate == "swipe_right":
            x1, y1, x2, y2, duration = x - dx // 2, y, x + dx // 2, y, "600"
        elif operate == "swipe_up":
            x1, y1, x2, y2, duration = x, y + dy // 2, x, y - dy // 2, "600"
        elif operate == "swipe_down":
            x1, y1, x2, y2, duration = x, y - dy // 2, x, y + dy // 2, "600"
        else:
            raise ValueError(f"unsupported operate: {operate}")

        if self.dry_run:
            print(f"    dry-run {operate} [{x1}, {y1}] -> [{x2}, {y2}] duration={duration}")
            return
        base = hdc_base(self.device_id) + ["shell"]
        require_command_success([
            base + ["uitest", "uiInput", "swipe", str(x1), str(y1), str(x2), str(y2), duration],
            base + ["input", "swipe", str(x1), str(y1), str(x2), str(y2), duration],
        ], f"{operate} gesture")


def text_values(value: Any) -> Set[str]:
    if value in (None, "", []):
        return set()
    if isinstance(value, list):
        return {str(item).strip() for item in value if str(item).strip()}
    text = str(value).strip()
    return {text} if text else set()


def target_values(target: Target) -> Set[str]:
    values: Set[str] = set()
    for key in ("key", "value", "text", "key_description", "step_prompt"):
        values.update(text_values(target.get(key)))
    return {v for v in values if v}


def candidate_values(candidate: Dict[str, Any]) -> Set[str]:
    values: Set[str] = set()
    suggested = candidate.get("suggested_target") or {}
    for source in (candidate, suggested):
        for key in ("key", "value", "text", "key_description", "step_prompt"):
            values.update(text_values(source.get(key)))
    return {v for v in values if v}


class LocatorResolver:
    """Resolve graph targets against the current UI tree.

    Coordinates are computed from the current UI dump only and are never written
    back to the graph.
    """

    def resolve_center(self, root: Dict[str, Any], target: Target) -> Tuple[List[int], Dict[str, Any]]:
        candidates = extract_navigation_candidates(root)
        wanted = target_values(target)
        wanted_type = str(target.get("type") or "")
        scored: List[Tuple[int, int, Dict[str, Any]]] = []

        for index, candidate in enumerate(candidates):
            center = candidate.get("bounds_center")
            if not isinstance(center, list) or len(center) != 2:
                continue
            score = self._score_candidate(candidate, target, wanted, wanted_type)
            if score > 0:
                scored.append((score, -index, candidate))

        if scored:
            scored.sort(reverse=True, key=lambda item: item[:2])
            selected = scored[0][2]
            return [int(selected["bounds_center"][0]), int(selected["bounds_center"][1])], selected

        center = self._resolve_from_raw_nodes(root, target, wanted)
        if center:
            return center, {"source": "raw_ui_tree_fallback", "target": target}
        raise RuntimeError(f"cannot resolve target on current page: {json.dumps(target, ensure_ascii=False)}")

    def _score_candidate(self, candidate: Dict[str, Any], target: Target, wanted: Set[str], wanted_type: str) -> int:
        values = candidate_values(candidate)
        suggested = candidate.get("suggested_target") or {}
        score = 0
        target_key = str(target.get("key") or "")
        target_value = str(target.get("value") or "")
        if target_key and target_key == str(candidate.get("key") or ""):
            score += 100
        if target_value and target_value == str(candidate.get("key") or ""):
            score += 90
        if target_value and target_value == str(suggested.get("value") or ""):
            score += 80
        if wanted.intersection(values):
            score += 50
        if wanted_type and wanted_type == str(suggested.get("type") or ""):
            score += 10
        component_type = str(target.get("component_type") or "")
        if component_type and component_type == str(candidate.get("type") or candidate.get("component_type") or ""):
            score += 5
        return score

    def _resolve_from_raw_nodes(self, root: Dict[str, Any], target: Target, wanted: Set[str]) -> Optional[List[int]]:
        stable_key = str(target.get("key") or (target.get("value") if target.get("type") in {"key", "button"} else "") or "")
        best: List[Tuple[Tuple[int, int], List[int]]] = []
        for node, depth, _ in walk(root):
            if not is_visible(node) or not is_enabled(node):
                continue
            if not to_bool(attrs(node).get("clickable", False)):
                continue
            rect = parse_rect(get_attr(node, "bounds"))
            if not rect["valid"] or not rect["center"]:
                continue
            key = get_key(node)
            texts = set(meaningful_texts(node))
            matched = bool(stable_key and stable_key == key) or bool(wanted.intersection(texts))
            if not matched:
                continue
            best.append(((int(rect["area"]), -depth), rect["center"]))
        if not best:
            return None
        best.sort(key=lambda item: item[0])
        return [int(best[0][1][0]), int(best[0][1][1])]


class PageMatcher:
    def __init__(self, graph: Graph) -> None:
        self.graph = graph

    def current_state(self, root: Dict[str, Any]) -> Dict[str, Any]:
        return build_navigation_state(root)

    def matches(self, root: Dict[str, Any], expected_page: str) -> Tuple[bool, Dict[str, Any], str]:
        current = self.current_state(root)
        current_page = str(current.get("page_name") or "")
        if current_page == expected_page:
            return True, current, "page_name"

        expected = self.graph.get("states", {}).get(expected_page, {})
        expected_title = str(expected.get("last_title") or expected.get("page_description") or "").removeprefix("弹窗：")
        current_title = str(current.get("last_title") or current.get("page_description") or "").removeprefix("弹窗：")
        if expected_title and current_title and expected_title == current_title:
            return True, current, "title"

        expected_texts = set(expected.get("signature", {}).get("texts_any") or [])
        current_texts = set(current.get("signature", {}).get("texts_any") or [])
        if expected_title and expected_title in current_texts:
            return True, current, "title_in_texts"
        if expected_texts and len(expected_texts.intersection(current_texts)) >= min(2, len(expected_texts)):
            return True, current, "signature_texts"

        reason = f"expected {expected_page}/{expected_title or '-'}, got {current_page}/{current_title or '-'}"
        return False, current, reason


class TraversalExecutor:
    def __init__(
        self,
        graph: Graph,
        plan: Dict[str, Any],
        driver: DeviceDriver,
        session_path: Path,
        step_delay: float = 0.8,
        verify: bool = True,
        execute_page_operations: bool = False,
    ) -> None:
        self.graph = graph
        self.plan = plan
        self.driver = driver
        self.session_path = session_path
        self.step_delay = step_delay
        self.verify = verify
        self.execute_page_operations = execute_page_operations
        self.resolver = LocatorResolver()
        self.matcher = PageMatcher(graph)
        self.session: Dict[str, Any] = {
            "status": "running",
            "started_at_ms": now_ms(),
            "completed_events": [],
            "failed_event": None,
            "dry_run": driver.dry_run,
        }

    def execute(self) -> Dict[str, Any]:
        self._save_session()
        try:
            for event in self.plan.get("events", []):
                self._execute_event(event)
                self.session["completed_events"].append(event.get("order"))
                self.session["last_event"] = event
                self._save_session()
            self.session["status"] = "completed"
            self.session["finished_at_ms"] = now_ms()
            self._save_session()
            return self.session
        except Exception as exc:
            self.session["status"] = "failed"
            self.session["failed_at_ms"] = now_ms()
            self.session["error"] = str(exc)
            self._save_session()
            raise

    def _save_session(self) -> None:
        save_json(self.session_path, self.session)

    def _capture(self) -> Dict[str, Any]:
        return self.driver.capture_ui_tree()

    def _verify_page(self, expected_page: str) -> Dict[str, Any]:
        if self.driver.dry_run and not self.verify:
            state = self.graph.get("states", {}).get(expected_page, {})
            return {
                "page_name": expected_page,
                "last_title": state.get("last_title") or state.get("page_description") or expected_page,
            }
        root = self._capture()
        if not self.verify:
            return self.matcher.current_state(root)
        ok, state, reason = self.matcher.matches(root, expected_page)
        if not ok:
            raise RuntimeError(f"page verification failed: {reason}")
        return state

    def _execute_event(self, event: PlanEvent) -> None:
        name = event.get("event")
        order = event.get("order")
        print(f"[{order}] {name}")
        if name == "visit_page":
            state = self._verify_page(str(event.get("page") or ""))
            print(f"    verified page: {state.get('page_name')} / {state.get('last_title')}")
        elif name == "enter_transition":
            self._execute_transition(event)
        elif name == "return_to_parent":
            self._execute_return(event)
        elif name == "record_page_operations":
            self._handle_page_operations(event)
        elif name in {"already_seen_page", "skip_transition"}:
            return

    def _execute_transition(self, event: PlanEvent) -> None:
        if self.verify:
            self._verify_page(str(event.get("from_page") or ""))
        for step in event.get("steps", []):
            self._execute_step(step)
            time.sleep(self.step_delay)
        state = self._verify_page(str(event.get("to_page") or ""))
        print(f"    reached: {state.get('page_name')} / {state.get('last_title')}")

    def _execute_step(self, step: Dict[str, Any]) -> None:
        operate = str(step.get("operate") or "tap")
        target = step.get("target") or {}
        label = target.get("step_prompt") or target.get("key_description") or target.get("value") or target.get("key") or "target"
        if self.driver.dry_run:
            print(f"    dry-run {operate}: {label}")
            self.driver.gesture(operate, [540, 1200], {"screen_size": [1080, 2400]})
            return
        root = self._capture()
        metrics = screen_metrics_from_root(root)
        center, selected = self.resolver.resolve_center(root, target)
        print(f"    {operate}: {label} -> temp_center={center}, matched={selected.get('key') or selected.get('text') or selected.get('source')}")
        self.driver.gesture(operate, center, metrics)

    def _execute_return(self, event: PlanEvent) -> None:
        policy = event.get("return_policy") or {"type": "system_back"}
        ptype = str(policy.get("type") or "system_back")
        if ptype == "system_back":
            self.driver.back()
        elif ptype == "tap":
            target = policy.get("target") or {}
            self._execute_step({"operate": "tap", "target": target})
        elif ptype == "steps":
            for step in policy.get("steps", []) or []:
                self._execute_step(step)
                time.sleep(self.step_delay)
        elif ptype in {"none", "manual"}:
            print(f"    return policy is {ptype}; no automatic action")
        else:
            raise ValueError(f"unsupported return_policy.type: {ptype}")
        time.sleep(self.step_delay)
        state = self._verify_page(str(event.get("to_page") or policy.get("target_page") or ""))
        print(f"    returned: {state.get('page_name')} / {state.get('last_title')}")

    def _handle_page_operations(self, event: PlanEvent) -> None:
        operations = event.get("operations", []) or []
        print(f"    page operations: {len(operations)}")
        if not self.execute_page_operations:
            print("    skipped execution; add --execute-page-operations to run them")
            return
        self._verify_page(str(event.get("page") or ""))
        for operation in operations:
            step = {"operate": operation.get("operate") or "tap", "target": operation.get("target") or {}}
            self._execute_step(step)
            time.sleep(self.step_delay)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build or execute a DFS traversal for the settings navigation graph.")
    parser.add_argument("--work-dir", default="demo_settings", help="Project work dir that contains outputs/navigation/settings_navigation_graph.json.")
    parser.add_argument("--graph", default="", help="Explicit navigation graph JSON path. Overrides --work-dir.")
    parser.add_argument("--root", default="", help="Root page name. Defaults to graph.main_page or Pages_root.")
    parser.add_argument("--output", default="", help="Write the generated plan to this JSON path.")
    parser.add_argument("--format", choices=["text", "json"], default="text", help="Print format.")
    parser.add_argument("--execute", action="store_true", help="Execute the DFS plan on a connected device.")
    parser.add_argument("--dry-run", action="store_true", help="Simulate executor actions without hdc input. Reads the existing output-dir UI tree.")
    parser.add_argument("--device-id", default=DEFAULT_DEVICE_ID, help="hdc device id. Use an empty string to omit -t.")
    parser.add_argument("--output-dir", default="", help="Directory for current_ui_tree.json/current_screen.png. Defaults to work-dir/outputs/latest.")
    parser.add_argument("--session-output", default="", help="Runtime session JSON path.")
    parser.add_argument("--step-delay", type=float, default=0.8, help="Seconds to wait after each device action.")
    parser.add_argument("--no-verify", action="store_true", help="Do not verify current page after actions.")
    parser.add_argument("--capture-screen", action="store_true", help="Also capture current_screen.png during real execution.")
    parser.add_argument("--execute-page-operations", action="store_true", help="Actually execute same-page operations such as swipe/delete.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    work_dir = Path(args.work_dir)
    graph_path = Path(args.graph) if args.graph else graph_path_from_work_dir(work_dir)
    graph = load_json(graph_path)
    root_page = args.root or graph.get("traversal_config", {}).get("root_page") or "Pages_root"
    plan = DfsPlanner(graph, str(root_page)).build()

    output_path = Path(args.output) if args.output else graph_path.parent / "dfs_traversal_plan.json"
    save_json(output_path, plan)

    if args.format == "json":
        print(json.dumps(plan, ensure_ascii=False, indent=2))
    else:
        print_text_plan(plan)
        print("")
        print(f"plan saved: {output_path}")

    if args.execute:
        output_dir = Path(args.output_dir) if args.output_dir else work_dir / "outputs" / "latest"
        session_path = Path(args.session_output) if args.session_output else graph_path.parent / "dfs_runtime_session.json"
        driver = DeviceDriver(
            device_id=str(args.device_id or ""),
            output_dir=output_dir,
            dry_run=bool(args.dry_run),
            capture_screen=bool(args.capture_screen),
        )
        executor = TraversalExecutor(
            graph=graph,
            plan=plan,
            driver=driver,
            session_path=session_path,
            step_delay=float(args.step_delay),
            verify=(not bool(args.no_verify) and not bool(args.dry_run)),
            execute_page_operations=bool(args.execute_page_operations),
        )
        session = executor.execute()
        print("")
        print(f"execution status: {session.get('status')}")
        print(f"runtime session saved: {session_path}")


if __name__ == "__main__":
    main()
