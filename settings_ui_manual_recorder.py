#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shared UI-tree and navigation-graph helpers for the Web recorder and DFS.


Shared domain layer for device input, UI-tree parsing, navigation-graph rules,
request contracts and graph maintenance. The Web server only orchestrates
these helpers and exposes HTTP routes.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import uuid
from collections import defaultdict, deque
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from pydantic import BaseModel


Node = Dict[str, Any]

DEFAULT_DEVICE_ID = "68Q0223918000004"
DEFAULT_WORK_DIR = Path("demo_settings")
PACKAGE_NAME = "com.huawei.hmos.settings"
MAIN_PAGE_NAME = "com.huawei.hmos.settings.MainAbility"
DEFAULT_SETTINGS_PROFILE_ID = "default"
SETTINGS_PROFILE_REGISTRY = "settings_profiles.json"

NOISE_TEXTS = {"tab_unlock"}
NON_INTERACTION_TYPES = {"Navigation", "NavDestination", "Page", "Root", "WindowScene"}

TITLE_KEYS = {
    "title_id",
    "singletitletext",
    "titlecompid",
}

COORDINATE_RECORD_KEYS = {
    "bounds",
    "bounds_center",
    "container_bounds",
    "coordinate_space",
    "coordinate_hit",
    "fallback_locator",
    "normalized_center",
    "normalized_point",
    "point",
    "root_bounds",
    "screen_size",
}
COORDINATE_RECORD_VALUES = {
    "bounds",
    "bounds_center",
    "coordinate",
    "point",
    "normalized_center",
    "normalized_point",
}
LEGACY_LOCATOR_TYPES = {"key", "text", "button", "button_text", "manual"}


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def load_json(path: Path) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(data: Any, path: Path, label: str = "JSON") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"✓ {label}: {path}")


def run_cmd(cmd: List[str], cwd: Optional[str] = None, timeout: int = 30) -> bool:
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd)
    except Exception as exc:
        print(f"✗ 执行异常: {exc}")
        return False
    if result.returncode == 0:
        return True
    print(f"✗ 命令失败: {' '.join(cmd)}")
    if result.stdout.strip():
        print(f"  stdout: {result.stdout.strip()}")
    if result.stderr.strip():
        print(f"  stderr: {result.stderr.strip()}")
    return False


# Device interaction
def run_hdc_with_fallback(commands: List[List[str]], action: str) -> None:
    errors = []
    for command in commands:
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=15)
        except Exception as exc:
            errors.append(f"{' '.join(command)} -> {exc}")
            continue
        if result.returncode == 0:
            return
        errors.append(f"{' '.join(command)} -> code={result.returncode}, stdout={result.stdout.strip()}, stderr={result.stderr.strip()}")
    raise RuntimeError(f"{action} 失败：" + " | ".join(errors))


def execute_device_input(
    device_id: str,
    action: str,
    center: Optional[List[int]] = None,
    metrics: Optional[Dict[str, Any]] = None,
) -> None:
    base = ["hdc", "-t", device_id, "shell"]
    if action == "back":
        commands = [
            base + ["uitest", "uiInput", "keyEvent", "Back"],
            base + ["input", "keyevent", "BACK"],
        ]
        description = "返回"
    else:
        width, height = map(int, (metrics or {}).get("screen_size") or [1080, 2400])
        if action in {"horizontal_left", "horizontal_right"}:
            left = action == "horizontal_left"
            x1, x2, y = (
                int(width * (0.78 if left else 0.22)),
                int(width * (0.22 if left else 0.78)),
                int(height * 0.55),
            )
            gesture = (x1, y, x2, y, "600")
        else:
            if not isinstance(center, list) or len(center) != 2:
                raise ValueError("center 必须是 [x, y]")
            x, y = map(int, center)
            if action == "tap":
                commands = [
                    base + ["uitest", "uiInput", "click", str(x), str(y)],
                    base + ["input", "tap", str(x), str(y)],
                ]
                return run_hdc_with_fallback(commands, f"点击 [{x}, {y}]")
            dx, dy = max(160, int(width * 0.22)), max(180, int(height * 0.12))
            gestures = {
                "long_press": (x, y, x, y, "900"),
                "swipe_left": (x + dx // 2, y, x - dx // 2, y, "600"),
                "swipe_right": (x - dx // 2, y, x + dx // 2, y, "600"),
                "swipe_up": (x, y + dy // 2, x, y - dy // 2, "600"),
                "swipe_down": (x, y - dy // 2, x, y + dy // 2, "600"),
            }
            if action not in gestures:
                raise ValueError(f"未知设备操作：{action}")
            gesture = gestures[action]
        x1, y1, x2, y2, duration = gesture
        commands = [
            base + ["uitest", "uiInput", "swipe", str(x1), str(y1), str(x2), str(y2), duration],
            base + ["input", "swipe", str(x1), str(y1), str(x2), str(y2), duration],
        ]
        description = f"横向{action.removeprefix('horizontal_')}滑动" if action.startswith("horizontal_") else f"{action} 手势"
    run_hdc_with_fallback(commands, description)


def capture_device(device_id: str, output_dir: Path, include_screen: bool) -> bool:
    """执行共用采集流程，可选择是否同时拉取截图。"""
    output_dir.mkdir(parents=True, exist_ok=True)
    if not run_cmd(["hdc", "version"]):
        print("✗ hdc 不可用，请确认 hdc 已加入 PATH")
        return False
    base = ["hdc"] + (["-t", device_id] if device_id else [])
    commands: List[Tuple[List[str], str]] = [
        (base + ["shell", "uitest", "dumpLayout", "-p", "/data/local/tmp/current_ui_tree.json"], "dumpLayout"),
        (base + ["file", "recv", "/data/local/tmp/current_ui_tree.json", "current_ui_tree.json"], "拉取 JSON"),
    ]
    if include_screen:
        commands += [
            (base + ["shell", "uitest", "screenCap", "-p", "/data/local/tmp/current_screen.png"], "screenCap"),
            (base + ["file", "recv", "/data/local/tmp/current_screen.png", "current_screen.png"], "拉取截图"),
        ]
    for cmd, name in commands:
        if not run_cmd(cmd, cwd=str(output_dir)):
            print(f"✗ {name} 失败")
            return False
        if name == "拉取 JSON":
            path = output_dir / "current_ui_tree.json"
            try:
                data = load_json(path)
                path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                print(f"✓ 格式化 UI JSON: {path}")
            except Exception as exc:
                print(f"⚠ UI JSON 格式化失败，继续采集流程: {path} ({exc})")
    return True


def navigation_graph_path(work_dir: Path) -> Path:
    return work_dir / "outputs" / "navigation" / "settings_navigation_graph.json"


def pending_transition_path(work_dir: Path) -> Path:
    return work_dir / "outputs" / "navigation" / "pending_transition.json"


def strip_coordinate_fields(value: Any) -> None:
    if isinstance(value, dict):
        for key in list(value.keys()):
            if key in COORDINATE_RECORD_KEYS:
                value.pop(key, None)
                continue
            strip_coordinate_fields(value[key])
            if key in {"locator", "preferred", "identity_strategy"} and value.get(key) in COORDINATE_RECORD_VALUES:
                value.pop(key, None)
            elif key == "fallback_order" and isinstance(value.get(key), list):
                value[key] = [item for item in value[key] if item not in COORDINATE_RECORD_VALUES]
                if not value[key]:
                    value.pop(key, None)
        if value.get("type") in {"bounds", "coordinate", "point", "normalized_point"}:
            value.pop("type", None)
            if isinstance(value.get("value"), list):
                value.pop("value", None)
    elif isinstance(value, list):
        for item in value:
            strip_coordinate_fields(item)


def normalize_semantic_target_types(target: Any, preserve_type: bool = False) -> None:
    """迁移正式 target：跳转只留 key/text，特殊操作额外保留真实组件 type。"""
    if not isinstance(target, dict):
        return
    legacy_type = str(target.get("type") or "")
    component_type = str(target.get("component_type") or "")
    raw_value = target.get("value")
    if legacy_type in {"key", "button"} and raw_value not in (None, "", []):
        target.setdefault("key", raw_value)
    elif legacy_type in {"text", "button_text"} and raw_value not in (None, "", []):
        target.setdefault("text", raw_value)
    if preserve_type:
        if legacy_type in LEGACY_LOCATOR_TYPES:
            if component_type:
                target["type"] = component_type
            elif legacy_type == "button" and not target.get("key") and not target.get("text"):
                target["type"] = "button"
            else:
                target.pop("type", None)
    elif legacy_type == "button" and not target.get("key") and not target.get("text"):
        target["type"] = "button"
    else:
        target.pop("type", None)
    target.pop("component_type", None)
    target.pop("value", None)
    if "key" in target and not is_stable_key_for_navigation(target.get("key")):
        target.pop("key", None)


def normalize_navigation_graph_targets(graph: Dict[str, Any]) -> None:
    for transition in graph.get("transitions", []) or []:
        if not isinstance(transition, dict):
            continue
        normalize_semantic_target_types(transition.get("target"), preserve_type=False)
        for step in transition.get("steps", []) or []:
            if isinstance(step, dict):
                normalize_semantic_target_types(step.get("target"), preserve_type=False)
    for state in (graph.get("states") or {}).values():
        if not isinstance(state, dict):
            continue
        for operation in state.get("page_operations", []) or []:
            if isinstance(operation, dict):
                normalize_semantic_target_types(operation.get("target"), preserve_type=True)


def transition_id_for_pages(from_page: Any, to_page: Any) -> str:
    """Return the canonical identity for the one edge allowed per page pair."""
    return f"{str(from_page or '')}__to__{str(to_page or '')}"


def _rewrite_transition_id_lists(value: Any, id_map: Dict[str, str]) -> None:
    """Keep candidate-to-transition references valid after canonical ID migration."""
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "transition_ids" and isinstance(item, list):
                rewritten: List[Any] = []
                for transition_id in item:
                    mapped = id_map.get(str(transition_id), transition_id)
                    if mapped not in rewritten:
                        rewritten.append(mapped)
                value[key] = rewritten
            else:
                _rewrite_transition_id_lists(item, id_map)
    elif isinstance(value, list):
        for item in value:
            _rewrite_transition_id_lists(item, id_map)


def normalize_navigation_graph_transitions(graph: Dict[str, Any]) -> None:
    """Keep the last transition per page pair and canonicalize its ID."""
    transitions = graph.get("transitions")
    if not isinstance(transitions, list):
        return
    seen_pairs: Set[Tuple[str, str]] = set()
    kept_reversed: List[Any] = []
    id_map: Dict[str, str] = {}
    for transition in reversed(transitions):
        if not isinstance(transition, dict):
            kept_reversed.append(transition)
            continue
        from_page = str(transition.get("from_page") or "")
        to_page = str(transition.get("to_page") or "")
        if not from_page or not to_page:
            kept_reversed.append(transition)
            continue
        pair = (from_page, to_page)
        canonical_id = transition_id_for_pages(from_page, to_page)
        old_id = str(transition.get("transition_id") or "")
        if old_id:
            id_map[old_id] = canonical_id
        transition["transition_id"] = canonical_id
        if pair in seen_pairs:
            continue
        seen_pairs.add(pair)
        kept_reversed.append(transition)
    transitions[:] = reversed(kept_reversed)
    if id_map:
        _rewrite_transition_id_lists(graph, id_map)


def load_navigation_graph(work_dir: Path) -> Dict[str, Any]:
    path = navigation_graph_path(work_dir)
    if not path.exists():
        return {
            "package_name": PACKAGE_NAME,
            "main_page_name": MAIN_PAGE_NAME,
            "updated_at": now_iso(),
            "traversal_config": {
                "strategy": "dfs",
                "root_page": "Pages_root",
                "default_return_policy": {"type": "system_back"},
            },
            "states": {},
            "transitions": [],
        }
    graph = load_json(path)
    graph.setdefault("package_name", PACKAGE_NAME)
    graph.setdefault("main_page_name", MAIN_PAGE_NAME)
    graph.setdefault("states", {})
    graph.setdefault("transitions", [])
    graph.setdefault("traversal_config", {"strategy": "dfs", "root_page": "Pages_root", "default_return_policy": {"type": "system_back"}})
    strip_coordinate_fields(graph)
    normalize_navigation_graph_targets(graph)
    normalize_navigation_graph_transitions(graph)
    return graph


def save_navigation_graph(graph: Dict[str, Any], work_dir: Path) -> None:
    strip_coordinate_fields(graph)
    normalize_navigation_graph_targets(graph)
    normalize_navigation_graph_transitions(graph)
    graph["updated_at"] = now_iso()
    save_json(graph, navigation_graph_path(work_dir), "轻量导航状态图")


def save_current_path_session(work_dir: Path, active_page: str, base_page: str = "") -> None:
    data = {"active_page": active_page}
    if base_page:
        data["base_page"] = base_page
    save_json(data, work_dir / "outputs" / "navigation" / "current_path_session.json", "当前页面会话")


def safe_priority(value: Any, default: int = 1000) -> int:
    """兼容手工编辑或旧数据中的空值、非数字 priority。"""
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


def _replace_page_references(value: Any, old_name: str, new_name: str) -> bool:
    """只迁移结构化页面引用，不误改 target.text 等用户可见内容。"""
    reference_fields = {
        "active_page",
        "base_page",
        "from_page",
        "main_page_name",
        "page_name",
        "parent_page",
        "root_page",
        "to_page",
    }
    changed = False
    if isinstance(value, dict):
        for key, item in list(value.items()):
            if key in reference_fields and item == old_name:
                value[key] = new_name
                changed = True
            elif (
                key == "context_key"
                and isinstance(item, str)
                and item.startswith(f"{old_name}::")
            ):
                value[key] = f"{new_name}{item[len(old_name):]}"
                changed = True
            elif isinstance(item, (dict, list)):
                changed = _replace_page_references(item, old_name, new_name) or changed
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, (dict, list)):
                changed = _replace_page_references(item, old_name, new_name) or changed
    return changed


class NavigationGraph:
    """导航图领域对象；集中维护边顺序、页面引用与兼容规则。"""

    def __init__(self, graph: Dict[str, Any]) -> None:
        if not isinstance(graph, dict):
            raise ValueError("navigation graph 必须是对象")
        self.graph = graph
        self.states = graph.setdefault("states", {})
        self.transitions = graph.setdefault("transitions", [])
        if not isinstance(self.states, dict):
            raise ValueError("navigation graph states 必须是对象")
        if not isinstance(self.transitions, list):
            raise ValueError("navigation graph transitions 必须是数组")

    def add_transition(self, transition: Dict[str, Any]) -> None:
        from_page = str(transition.get("from_page") or "")
        to_page = str(transition.get("to_page") or "")
        if not from_page or not to_page:
            raise ValueError("transition 缺少 from_page 或 to_page")
        pair = (from_page, to_page)
        old_transitions = [
            item
            for item in self.transitions
            if isinstance(item, dict)
            and (
                str(item.get("from_page") or ""),
                str(item.get("to_page") or ""),
            ) == pair
        ]
        if transition.get("priority") in (None, "", []):
            inherited_priority = next((
                item.get("priority")
                for item in reversed(old_transitions)
                if item.get("priority") not in (None, "", [])
            ), None)
            if inherited_priority is not None:
                transition["priority"] = inherited_priority
        tid = transition_id_for_pages(from_page, to_page)
        transition["transition_id"] = tid
        old_ids = {
            str(item.get("transition_id") or "")
            for item in old_transitions
            if item.get("transition_id")
        }
        self.transitions[:] = [
            item
            for item in self.transitions
            if not isinstance(item, dict)
            or (
                str(item.get("from_page") or ""),
                str(item.get("to_page") or ""),
            ) != pair
        ]
        if old_ids:
            _rewrite_transition_id_lists(
                self.graph,
                {old_id: tid for old_id in old_ids},
            )
        self.transitions.append(transition)

    def reorder_children(
        self,
        parent_page: str,
        ordered_transition_ids: List[str],
    ) -> List[str]:
        """按完整同级 transition ID 顺序写 priority 并物理重排。"""
        parent_page = str(parent_page or "").strip()
        if parent_page not in self.states:
            raise ValueError(f"父页面不存在：{parent_page}")
        transitions = [
            transition
            for transition in self.transitions
            if isinstance(transition, dict)
            and transition.get("from_page") == parent_page
            and transition.get("to_page") != parent_page
        ]
        current_ids = [
            str(transition.get("transition_id") or "")
            for transition in transitions
        ]
        if any(not transition_id for transition_id in current_ids):
            raise ValueError(f"{parent_page} 存在缺少 transition_id 的跳转，无法持久化顺序")
        if len(current_ids) != len(set(current_ids)):
            raise ValueError(f"{parent_page} 存在重复 transition_id，无法持久化顺序")
        requested_ids = [
            str(transition_id or "").strip()
            for transition_id in ordered_transition_ids
        ]
        if not requested_ids or any(not transition_id for transition_id in requested_ids):
            raise ValueError("同级 transition 顺序不能为空")
        if len(requested_ids) != len(set(requested_ids)):
            raise ValueError("同级 transition 顺序包含重复 transition_id")
        missing = [item for item in current_ids if item not in requested_ids]
        extra = [item for item in requested_ids if item not in current_ids]
        if missing or extra:
            raise ValueError(f"同级 transition 集合不一致：缺少 {missing}，多出 {extra}")

        priorities = {
            transition_id: (index + 1) * 10
            for index, transition_id in enumerate(requested_ids)
        }
        ordered = {
            str(transition.get("transition_id") or ""): transition
            for transition in transitions
        }
        for transition_id, priority in priorities.items():
            ordered[transition_id]["priority"] = priority

        sibling_object_ids = {id(transition) for transition in transitions}
        ordered_iter = iter(ordered[transition_id] for transition_id in requested_ids)
        for index, transition in enumerate(self.transitions):
            if id(transition) in sibling_object_ids:
                self.transitions[index] = next(ordered_iter)
        return requested_ids

    def ordered_outgoing(
        self,
        *,
        valid_states_only: bool = True,
    ) -> Dict[str, List[Dict[str, Any]]]:
        outgoing: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        record_orders: Dict[int, int] = {}
        for index, transition in enumerate(self.transitions):
            if not isinstance(transition, dict):
                continue
            source = str(transition.get("from_page") or "")
            target = str(transition.get("to_page") or "")
            if not source or not target or source == target:
                continue
            if valid_states_only and (source not in self.states or target not in self.states):
                continue
            outgoing[source].append(transition)
            record_orders[id(transition)] = index
        for siblings in outgoing.values():
            siblings.sort(key=lambda transition: (
                safe_priority(transition.get("priority")),
                record_orders[id(transition)],
                str(transition.get("transition_id") or ""),
            ))
        return outgoing

    def rename_page(
        self,
        old_name: str,
        new_name: str,
        *,
        new_title: str = "",
    ) -> Dict[str, Any]:
        if old_name not in self.states:
            raise ValueError(f"页面不存在：{old_name}")
        if new_name != old_name and new_name in self.states:
            raise ValueError(f"目标 page_name 已存在：{new_name}")
        state = self.states[old_name]
        if not isinstance(state, dict):
            raise ValueError(f"页面数据不是对象：{old_name}")
        if new_name != old_name:
            self.states.pop(old_name)
            self.states[new_name] = state
            _replace_page_references(self.graph, old_name, new_name)
            normalize_navigation_graph_transitions(self.graph)
        state["page_name"] = new_name
        if new_title.strip():
            state["last_title"] = new_title.strip()
            state["page_description"] = new_title.strip()
        return state


class NavigationGraphRepository:
    """统一导航图文件、唯一备份及录制期引用迁移。"""

    RUNTIME_REFERENCE_FILES = (
        "current_path_session.json",
        "pending_transition.json",
        "pending_action_chain.json",
    )

    def __init__(self, work_dir: Path) -> None:
        self.work_dir = Path(work_dir)
        self.path = navigation_graph_path(self.work_dir)

    def load(self) -> Dict[str, Any]:
        return load_navigation_graph(self.work_dir)

    def save(self, graph: Dict[str, Any]) -> None:
        save_navigation_graph(graph, self.work_dir)

    def backup(self) -> str:
        backup_dir = self.path.parent / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_path = backup_dir / (
            f"settings_navigation_graph_{datetime.now().strftime('%Y%m%dT%H%M%S%f')}_"
            f"{uuid.uuid4().hex[:8]}.json"
        )
        if self.path.exists():
            shutil.copy2(self.path, backup_path)
        return str(backup_path)

    def rename_runtime_references(self, old_name: str, new_name: str) -> List[str]:
        changed_files: List[str] = []
        for filename in self.RUNTIME_REFERENCE_FILES:
            path = self.path.parent / filename
            if not path.exists():
                continue
            try:
                document = load_json(path)
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                continue
            if _replace_page_references(document, old_name, new_name):
                save_json(document, path, f"页面引用迁移 {filename}")
                changed_files.append(str(path))
        return changed_files


class SettingsProfileManager:
    """维护设置版本/机型配置；默认配置继续使用原 work_dir。"""

    def __init__(self, base_work_dir: Path) -> None:
        self.base_work_dir = Path(base_work_dir)
        self.navigation_dir = self.base_work_dir / "outputs" / "navigation"
        self.registry_path = self.navigation_dir / SETTINGS_PROFILE_REGISTRY
        self.profiles_dir = self.base_work_dir / "config_profiles"

    def _default_profile(self) -> Dict[str, Any]:
        return {
            "profile_id": DEFAULT_SETTINGS_PROFILE_ID,
            "name": "默认配置",
            "settings_version": "",
            "device_model": "",
            "parent_profile_id": "",
            "created_at": "",
            "updated_at": "",
            "is_default": True,
        }

    def _load_profiles(self) -> List[Dict[str, Any]]:
        profiles: List[Dict[str, Any]] = []
        if self.registry_path.exists():
            document = load_json(self.registry_path)
            raw_profiles = document.get("profiles", [])
            if isinstance(raw_profiles, list):
                profiles = [
                    dict(item)
                    for item in raw_profiles
                    if isinstance(item, dict) and str(item.get("profile_id") or "")
                ]
        by_id = {
            str(item["profile_id"]): item
            for item in profiles
        }
        default = by_id.get(DEFAULT_SETTINGS_PROFILE_ID, self._default_profile())
        default["profile_id"] = DEFAULT_SETTINGS_PROFILE_ID
        default["is_default"] = True
        ordered = [default]
        ordered.extend(
            item
            for item in profiles
            if str(item.get("profile_id")) != DEFAULT_SETTINGS_PROFILE_ID
        )
        return ordered

    def _save_profiles(self, profiles: List[Dict[str, Any]]) -> None:
        save_json({
            "schema_version": 1,
            "updated_at": now_iso(),
            "profiles": profiles,
        }, self.registry_path, "设置版本/机型配置索引")

    def profile_work_dir(self, profile_id: str) -> Path:
        profile_id = str(profile_id or DEFAULT_SETTINGS_PROFILE_ID)
        if profile_id == DEFAULT_SETTINGS_PROFILE_ID:
            return self.base_work_dir
        if not re.fullmatch(r"profile_[0-9a-f]{12}", profile_id):
            raise ValueError(f"配置 ID 非法：{profile_id}")
        return self.profiles_dir / profile_id

    def get_profile(self, profile_id: str) -> Dict[str, Any]:
        for profile in self._load_profiles():
            if str(profile.get("profile_id")) == profile_id:
                return profile
        raise ValueError(f"配置不存在：{profile_id}")

    def list_profiles(self) -> List[Dict[str, Any]]:
        result: List[Dict[str, Any]] = []
        for profile in self._load_profiles():
            profile_id = str(profile["profile_id"])
            work_dir = self.profile_work_dir(profile_id)
            graph = load_navigation_graph(work_dir)
            result.append({
                **profile,
                "work_dir": str(work_dir),
                "graph_path": str(navigation_graph_path(work_dir)),
                "page_count": len(graph.get("states", {}) or {}),
                "transition_count": len(graph.get("transitions", []) or []),
            })
        return result

    def create(
        self,
        *,
        name: str,
        settings_version: str,
        device_model: str,
        parent_profile_id: str,
    ) -> Dict[str, Any]:
        name = str(name or "").strip()
        settings_version = str(settings_version or "").strip()
        device_model = str(device_model or "").strip()
        parent_profile_id = str(
            parent_profile_id or DEFAULT_SETTINGS_PROFILE_ID
        ).strip()
        if not settings_version:
            raise ValueError("设置版本不能为空")
        if not device_model:
            raise ValueError("机型不能为空")
        profiles = self._load_profiles()
        if not any(
            str(item.get("profile_id")) == parent_profile_id
            for item in profiles
        ):
            raise ValueError(f"继承来源配置不存在：{parent_profile_id}")
        duplicate = next((
            item for item in profiles
            if str(item.get("settings_version") or "").casefold()
            == settings_version.casefold()
            and str(item.get("device_model") or "").casefold()
            == device_model.casefold()
        ), None)
        if duplicate:
            raise ValueError(
                f"该设置版本和机型已经存在：{duplicate.get('name') or duplicate['profile_id']}"
            )

        profile_id = f"profile_{uuid.uuid4().hex[:12]}"
        created_at = now_iso()
        profile = {
            "profile_id": profile_id,
            "name": name or f"{settings_version} · {device_model}",
            "settings_version": settings_version,
            "device_model": device_model,
            "parent_profile_id": parent_profile_id,
            "created_at": created_at,
            "updated_at": created_at,
            "is_default": False,
        }
        source_work_dir = self.profile_work_dir(parent_profile_id)
        target_work_dir = self.profile_work_dir(profile_id)
        source_graph_path = navigation_graph_path(source_work_dir)
        if not source_graph_path.exists():
            raise ValueError(
                f"继承来源还没有配置文件：{source_graph_path}"
            )
        source_graph = load_navigation_graph(source_work_dir)
        inherited_graph = json.loads(json.dumps(source_graph, ensure_ascii=False))
        inherited_graph["settings_profile"] = {
            key: profile[key]
            for key in (
                "profile_id",
                "name",
                "settings_version",
                "device_model",
                "parent_profile_id",
            )
        }
        save_navigation_graph(inherited_graph, target_work_dir)

        source_paths = (
            source_work_dir
            / "outputs"
            / "navigation"
            / "settings_navigation_paths.json"
        )
        target_paths = (
            target_work_dir
            / "outputs"
            / "navigation"
            / "settings_navigation_paths.json"
        )
        if source_paths.exists():
            target_paths.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_paths, target_paths)
        root_page = str(
            inherited_graph.get("traversal_config", {}).get("root_page")
            or "Pages_root"
        )
        save_current_path_session(target_work_dir, root_page)
        profiles.append(profile)
        self._save_profiles(profiles)
        return profile

    def import_graph(
        self,
        *,
        name: str,
        settings_version: str,
        device_model: str,
        graph: Optional[Dict[str, Any]] = None,
        source_filename: str = "",
        source_work_dir: str = "",
    ) -> Dict[str, Any]:
        """Import one recorded work_dir as an independent profile, including capture assets."""
        name = str(name or "").strip()
        settings_version = str(settings_version or "").strip()
        device_model = str(device_model or "").strip()
        source_filename = Path(str(source_filename or "")).name
        source_dir: Optional[Path] = None
        source_work_dir = str(source_work_dir or "").strip()
        if source_work_dir:
            source_dir = Path(source_work_dir).expanduser()
            if not source_dir.is_absolute():
                source_dir = (self.base_work_dir.resolve().parent / source_dir).resolve()
            else:
                source_dir = source_dir.resolve()
            if not source_dir.exists() or not source_dir.is_dir():
                raise ValueError(f"源采集目录不存在：{source_dir}")
            source_graph_path = navigation_graph_path(source_dir)
            if not source_graph_path.exists():
                raise ValueError(
                    "源采集目录缺少 outputs/navigation/settings_navigation_graph.json："
                    f"{source_graph_path}"
                )
            graph = load_navigation_graph(source_dir)
            source_filename = source_graph_path.name
        if not settings_version:
            raise ValueError("设置版本不能为空")
        if not device_model:
            raise ValueError("机型不能为空")
        if not isinstance(graph, dict):
            raise ValueError("导入 Graph 必须是 JSON 对象；推荐填写源采集目录进行完整导入")

        imported_graph = json.loads(json.dumps(graph, ensure_ascii=False))
        states = imported_graph.get("states")
        transitions = imported_graph.get("transitions")
        if not isinstance(states, dict) or not states:
            raise ValueError("导入 Graph 缺少有效 states，至少需要一个页面")
        if not isinstance(transitions, list):
            raise ValueError("导入 Graph 的 transitions 必须是数组")
        traversal = imported_graph.get("traversal_config")
        if not isinstance(traversal, dict):
            traversal = {
                "strategy": "dfs",
                "root_page": "Pages_root",
                "default_return_policy": {"type": "system_back"},
            }
            imported_graph["traversal_config"] = traversal
        root_page = str(traversal.get("root_page") or "Pages_root").strip()
        traversal["root_page"] = root_page
        if root_page not in states:
            raise ValueError(f"导入 Graph 的根页面不存在于 states：{root_page}")

        invalid_transitions = []
        for index, transition in enumerate(transitions, start=1):
            if not isinstance(transition, dict):
                invalid_transitions.append(f"#{index}: 不是对象")
                continue
            from_page = str(transition.get("from_page") or "").strip()
            to_page = str(transition.get("to_page") or "").strip()
            if not from_page or not to_page:
                invalid_transitions.append(f"#{index}: 缺少 from_page/to_page")
            elif from_page not in states or to_page not in states:
                invalid_transitions.append(f"#{index}: {from_page} -> {to_page} 引用了不存在页面")
        if invalid_transitions:
            preview = "；".join(invalid_transitions[:5])
            suffix = "；..." if len(invalid_transitions) > 5 else ""
            raise ValueError(f"导入 Graph 含无效 transition：{preview}{suffix}")

        profiles = self._load_profiles()
        duplicate = next((
            item for item in profiles
            if str(item.get("settings_version") or "").casefold()
            == settings_version.casefold()
            and str(item.get("device_model") or "").casefold()
            == device_model.casefold()
        ), None)
        reuse_imported_profile = bool(
            duplicate
            and source_dir is not None
            and str(duplicate.get("source") or "") in {"imported_graph", "imported_work_dir"}
        )
        if duplicate and not reuse_imported_profile:
            raise ValueError(
                f"该设置版本和机型已经存在：{duplicate.get('name') or duplicate['profile_id']}"
            )

        profile_id = (
            str(duplicate["profile_id"])
            if reuse_imported_profile
            else f"profile_{uuid.uuid4().hex[:12]}"
        )
        created_at = str(duplicate.get("created_at") or now_iso()) if reuse_imported_profile else now_iso()
        profile = {
            "profile_id": profile_id,
            "name": name or f"{settings_version} · {device_model}",
            "settings_version": settings_version,
            "device_model": device_model,
            "parent_profile_id": "",
            "source": "imported_work_dir" if source_dir is not None else "imported_graph",
            "source_filename": source_filename,
            "source_work_dir": str(source_dir) if source_dir is not None else "",
            "created_at": created_at,
            "updated_at": now_iso(),
            "is_default": False,
        }
        imported_graph["settings_profile"] = {
            key: profile[key]
            for key in (
                "profile_id",
                "name",
                "settings_version",
                "device_model",
                "parent_profile_id",
                "source",
                "source_filename",
                "source_work_dir",
            )
        }
        target_work_dir = self.profile_work_dir(profile_id)

        if source_dir is not None:
            source_outputs = source_dir / "outputs"
            target_outputs = target_work_dir / "outputs"
            if source_outputs.exists():
                target_outputs.mkdir(parents=True, exist_ok=True)

                def copy_entry(source: Path, target: Path) -> None:
                    if source.is_dir():
                        shutil.copytree(source, target, dirs_exist_ok=True)
                    else:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(source, target)

                for source_entry in source_outputs.iterdir():
                    if source_entry.name != "navigation":
                        copy_entry(source_entry, target_outputs / source_entry.name)
                        continue
                    target_navigation = target_outputs / "navigation"
                    target_navigation.mkdir(parents=True, exist_ok=True)
                    skip_navigation = {
                        SETTINGS_PROFILE_REGISTRY,
                        "settings_navigation_graph.json",
                        "settings_navigation_paths.json",
                        "current_path_session.json",
                        "pending_transition.json",
                        "pending_action_chain.json",
                    }
                    for navigation_entry in source_entry.iterdir():
                        if navigation_entry.name in skip_navigation:
                            continue
                        copy_entry(
                            navigation_entry,
                            target_navigation / navigation_entry.name,
                        )

            capture_dir = (
                target_work_dir / "outputs" / "navigation" / "continued_captures"
            )
            for state in imported_graph.get("states", {}).values():
                if not isinstance(state, dict):
                    continue
                for capture in state.get("continued_captures", []) or []:
                    if not isinstance(capture, dict) or not capture.get("screenshot"):
                        continue
                    copied = capture_dir / Path(str(capture["screenshot"])).name
                    if copied.exists():
                        capture["screenshot"] = str(copied)

        save_navigation_graph(imported_graph, target_work_dir)
        save_current_path_session(target_work_dir, root_page)
        if reuse_imported_profile and duplicate is not None:
            duplicate.update(profile)
        else:
            profiles.append(profile)
        self._save_profiles(profiles)
        return profile


def add_transition(graph: Dict[str, Any], transition: Dict[str, Any]) -> None:
    """兼容旧调用；新代码通过 NavigationGraph 实例维护。"""
    NavigationGraph(graph).add_transition(transition)


def reorder_child_transitions(
    graph: Dict[str, Any],
    parent_page: str,
    ordered_transition_ids: List[str],
) -> List[str]:
    """兼容旧调用；顺序规则由 NavigationGraph 统一实现。"""
    return NavigationGraph(graph).reorder_children(parent_page, ordered_transition_ids)


def get_type(node: Node) -> str:
    a = node.get("attributes", node)
    return str(a.get("type") or a.get("className") or a.get("componentType") or "")


def get_key(node: Node) -> str:
    a = node.get("attributes", node)
    return str(a.get("key") or a.get("id") or "")


def get_text(node: Node) -> str:
    a = node.get("attributes", node)
    return str(a.get("text") or a.get("originalText") or "").strip()


def get_attr(node: Node, name: str, default: str = "") -> str:
    return str(node.get("attributes", node).get(name, default) or "")


def walk(node: Node, depth: int = 0, parent: Optional[Node] = None):
    yield node, depth, parent
    for child in node.get("children", []) or []:
        yield from walk(child, depth + 1, node)


def annotate(root: Node) -> None:
    def rec(node: Node, parent: Optional[Node], type_path: str, index_path: str) -> None:
        node["__parent"] = parent
        node["__type_path"] = type_path
        node["__index_path"] = index_path
        counts: Dict[str, int] = defaultdict(int)
        for index, child in enumerate(node.get("children", []) or []):
            ctype = get_type(child) or "Node"
            counts[ctype] += 1
            rec(child, node, f"{type_path}/{ctype}[{counts[ctype]}]", f"{index_path}/{index}")

    rec(root, None, get_type(root) or "Root", "0")


def parent_chain(node: Node, limit: int = 6) -> List[Node]:
    out: List[Node] = []
    cur = node.get("__parent")
    while isinstance(cur, dict) and len(out) < limit:
        out.append(cur)
        cur = cur.get("__parent")
    return out


def parse_rect(bounds: Any) -> Dict[str, Any]:
    empty = {"left": 0, "top": 0, "right": 0, "bottom": 0, "width": 0, "height": 0, "center": None, "area": 0, "valid": False}
    nums = re.findall(r"-?\d+", str(bounds or ""))
    if len(nums) < 4:
        return empty
    left, top, right, bottom = map(int, nums[:4])
    if right <= left or bottom <= top:
        return empty
    return {
        "left": left,
        "top": top,
        "right": right,
        "bottom": bottom,
        "width": right - left,
        "height": bottom - top,
        "center": [(left + right) // 2, (top + bottom) // 2],
        "area": (right - left) * (bottom - top),
        "valid": True,
    }


def screen_metrics_from_root(root: Node) -> Dict[str, Any]:
    rect = parse_rect(get_attr(root, "bounds"))
    if not rect["valid"]:
        max_right = 0
        max_bottom = 0
        for node, _, _ in walk(root):
            node_rect = parse_rect(get_attr(node, "bounds"))
            if node_rect["valid"]:
                max_right = max(max_right, int(node_rect["right"]))
                max_bottom = max(max_bottom, int(node_rect["bottom"]))
        rect = parse_rect(f"[0,0][{max_right},{max_bottom}]")
    return {
        "coordinate_space": "screen_absolute_px",
        "screen_size": [int(rect["width"]), int(rect["height"])] if rect["valid"] else None,
        "root_bounds": get_attr(root, "bounds") if rect["valid"] else "",
    }


def clean_label(text: Any) -> str:
    raw = str(text or "").strip()
    if not raw:
        return ""
    parts = [part.strip() for part in re.split(r"[,，]", raw) if part.strip()]
    parts = [part for part in parts if part not in NOISE_TEXTS]
    return parts[0] if parts else raw


def meaningful_texts(root: Node, include_numeric: bool = False) -> List[str]:
    out: List[str] = []
    for node, _, _ in walk(root):
        text = clean_label(get_text(node))
        if not text or text in NOISE_TEXTS:
            continue
        if not include_numeric and re.fullmatch(r"\d+(\.\d+)?", text):
            continue
        if text not in out:
            out.append(text)
    return out


def is_stable_key_for_navigation(key: Any) -> bool:
    text = str(key or "").strip()
    if not text or "*" in text or "AvailableDeviceGroup" in text:
        return False
    if re.fullmatch(r"\d+_Inner", text, flags=re.IGNORECASE):
        return False
    if re.search(r"\d{8,}", text):
        return False
    if re.fullmatch(r"[0-9a-fA-F\-]{16,}", text):
        return False
    return True


def is_stable_text_for_navigation(text: Any) -> bool:
    value = clean_label(text)
    if not value or value in NOISE_TEXTS:
        return False
    if len(value) > 40:
        return False
    if re.fullmatch(r"\d+(\.\d+)?", value):
        return False
    return True


def state_name_from_title(title: str) -> str:
    value = clean_label(title) or "page"
    if value == "设置":
        return "Pages_root"
    safe = re.sub(r"\s+", "_", value)
    safe = re.sub(r"[^\w\u4e00-\u9fff]+", "_", safe).strip("_") or "page"
    return "Pages_" + safe


def build_navigation_state(root: Node) -> Dict[str, Any]:
    explicit_titles: List[Tuple[int, str]] = []
    titlebar_texts: List[Tuple[int, str]] = []
    nav_candidates: List[Tuple[int, str]] = []
    for node, depth, _ in walk(root):
        if get_type(node) == "NavDestination" and get_attr(node, "visible", "true").lower() != "false" and get_key(node):
            nav_candidates.append((depth, get_key(node)))
        text = clean_label(get_text(node))
        if not text:
            continue
        key = get_key(node).strip().lower()
        if key in TITLE_KEYS or key.rsplit(".", 1)[-1] in TITLE_KEYS:
            explicit_titles.append((depth, text))
            continue
        if (
            get_type(node).lower() in {"text", "label"}
            and text not in {"返回", "返回按钮"}
            and any(
                "titlebar" in (get_key(parent) or get_type(parent)).lower()
                for parent in parent_chain(node)
            )
        ):
            titlebar_texts.append((depth, text))
    title = (
        explicit_titles[0][1]
        if explicit_titles
        else titlebar_texts[0][1] if titlebar_texts else ""
    )
    nav_key = sorted(nav_candidates, reverse=True)[0][1] if nav_candidates else ""

    page_name = state_name_from_title(title or "page")
    texts = [
        text
        for text in meaningful_texts(root)
        if is_stable_text_for_navigation(text)
    ][:8]
    return {
        "page_name": page_name,
        "raw_page_name": page_name,
        "page_description": title or page_name,
        "last_title": title,
        "page_id": nav_key or f"title::{title}",
        "nav_key": nav_key,
        "signature": {
            "title": title,
            "texts_any": texts,
        },
    }


def is_recordable_clickable_area(node: Node, screen_area: int = 0) -> bool:
    clickable = node.get("attributes", node).get("clickable", False)
    if not (
        (
            (isinstance(clickable, (int, float)) and clickable != 0)
            or str(clickable).strip().lower() in {"true", "1", "yes"}
        )
        and get_attr(node, "visible", "true").lower() != "false"
        and get_attr(node, "enabled", "true").lower() != "false"
    ):
        return False
    if get_type(node) in NON_INTERACTION_TYPES:
        return False
    rect = parse_rect(get_attr(node, "bounds"))
    if not rect["valid"] or rect["area"] <= 0:
        return False
    if screen_area and rect["area"] > screen_area * 0.85:
        return False
    return True


def extract_navigation_candidates(root: Node) -> List[Dict[str, Any]]:
    screen = screen_metrics_from_root(root).get("screen_size") or [0, 0]
    screen_area = int(screen[0] or 0) * int(screen[1] or 0) if isinstance(screen, list) and len(screen) == 2 else 0
    nodes = [n for n, _, _ in walk(root) if is_recordable_clickable_area(n, screen_area)]
    nodes.sort(key=lambda n: (parse_rect(get_attr(n, "bounds"))["top"], parse_rect(get_attr(n, "bounds"))["left"]))
    candidates: List[Dict[str, Any]] = []
    seen = set()
    for node in nodes:
        labels = meaningful_texts(node)
        label = labels[0] if labels else ""
        if not label:
            for parent in parent_chain(node):
                if get_type(parent) in {"Row", "Column", "ListItem", "Button", "MenuItem"}:
                    parent_labels = meaningful_texts(parent)
                    if parent_labels:
                        label = parent_labels[0]
                        break
        label = label or get_text(node)
        if label in {"返回", "返回按钮"}:
            continue
        rect = parse_rect(get_attr(node, "bounds"))
        text = next((item for item in meaningful_texts(node) if is_stable_text_for_navigation(item)), "")
        key = get_key(node)
        component_type = get_type(node)
        if is_stable_key_for_navigation(key):
            description = text or label or key
            target = {"key": key, "text": text, "key_description": description, "step_prompt": description}
        elif text:
            target = {"text": text, "key_description": text, "step_prompt": text}
        elif component_type.lower() == "button":
            target = {"type": "button", "key_description": "Button", "step_prompt": "Button"}
        else:
            target = {"needs_manual_label": True, "component_type": component_type}
        sig = json.dumps([target.get("key"), target.get("text"), target.get("type"), rect["center"]], ensure_ascii=False)
        if sig in seen:
            continue
        seen.add(sig)
        candidates.append({
            "index": len(candidates) + 1,
            "text": text,
            "key": key if is_stable_key_for_navigation(key) else "",
            "type": component_type,
            "bounds": get_attr(node, "bounds"),
            "bounds_center": rect["center"],
            "suggested_target": target,
            "clickable_area": True,
        })
    return candidates


def hit_test_full_ui_tree(
    root: Node,
    x: int,
    y: int,
) -> Optional[Dict[str, Any]]:
    """优先命中最深 Item 内的首个 clickable，否则选择覆盖点的最小 clickable。"""
    stable_key_counts: Dict[str, int] = defaultdict(int)
    for tree_node, _, _ in walk(root):
        tree_key = get_key(tree_node).strip()
        if is_stable_key_for_navigation(tree_key):
            stable_key_counts[tree_key] += 1

    item_types = {"ListItem", "GridItem"}
    screen = screen_metrics_from_root(root).get("screen_size") or [0, 0]
    screen_area = int(screen[0] or 0) * int(screen[1] or 0) if len(screen) == 2 else 0
    item_hits = []
    for node, depth, _ in walk(root):
        rect = parse_rect(get_attr(node, "bounds"))
        if (
            get_type(node) in item_types
            and rect["valid"]
            and rect["left"] <= x <= rect["right"]
            and rect["top"] <= y <= rect["bottom"]
        ):
            item_hits.append((depth, rect["area"], node))
    item_node = min(item_hits, key=lambda item: (-item[0], item[1]))[2] if item_hits else None
    clickable_node = None
    if item_node:
        stack = [item_node]
        while stack:
            node = stack.pop()
            if node is not item_node and get_type(node) in item_types:
                continue
            if is_recordable_clickable_area(node, screen_area):
                clickable_node = node
                break
            stack.extend(reversed(node.get("children", []) or []))
    else:
        hits = []
        for node, depth, _ in walk(root):
            rect = parse_rect(get_attr(node, "bounds"))
            if (
                rect["valid"]
                and rect["left"] <= x <= rect["right"]
                and rect["top"] <= y <= rect["bottom"]
                and is_recordable_clickable_area(node, screen_area)
            ):
                hits.append((rect["area"], -depth, node))
        if hits:
            clickable_node = min(hits, key=lambda item: (item[0], item[1]))[2]
    if clickable_node is None:
        return None
    key = get_key(clickable_node)
    text = clean_label(get_text(clickable_node))
    if not is_stable_key_for_navigation(key) or stable_key_counts.get(key.strip(), 0) != 1:
        key = ""
    if not is_stable_text_for_navigation(text):
        text = ""
    if not key or not text:
        stack = list(reversed(clickable_node.get("children", []) or []))
        while stack:
            node = stack.pop()
            clickable = node.get("attributes", node).get("clickable", False)
            if (
                (isinstance(clickable, (int, float)) and clickable != 0)
                or str(clickable).strip().lower() in {"true", "1", "yes"}
            ):
                continue
            if not key:
                child_key = get_key(node)
                if (
                    is_stable_key_for_navigation(child_key)
                    and stable_key_counts.get(child_key.strip(), 0) == 1
                ):
                    key = child_key
            if not text:
                child_text = clean_label(get_text(node))
                if is_stable_text_for_navigation(child_text):
                    text = child_text
            if key and text:
                break
            stack.extend(reversed(node.get("children", []) or []))
    return {
        "component_type": get_type(clickable_node),
        "key": key,
        "text": text,
        "bounds": get_attr(clickable_node, "bounds"),
        "clickable": True,
        "enabled": get_attr(clickable_node, "enabled", "true").lower() != "false",
        "item_type": get_type(item_node) if item_node else "",
        "item_bounds": get_attr(item_node, "bounds") if item_node else "",
    }


def build_semantic_target_from_node(hit_node: Optional[Dict[str, Any]], manual_label: str = "") -> Dict[str, Any]:
    if not hit_node:
        return {"needs_manual_label": True}
    ctype = str(hit_node.get("component_type") or "")
    text = clean_label(hit_node.get("text") or "")
    raw_key = str(hit_node.get("key") or "")
    key = raw_key if is_stable_key_for_navigation(raw_key) else ""
    if manual_label:
        target = {
            "key": key,
            "text": text,
            "key_description": manual_label,
            "step_prompt": manual_label,
        }
        if not key and not text and ctype.lower() == "button":
            target["type"] = "button"
        return {field: value for field, value in target.items() if value not in (None, "", [])}
    if key:
        desc = text or key
        return {field: value for field, value in {"key": key, "text": text, "key_description": desc, "step_prompt": desc}.items() if value}
    if text:
        return {"text": text, "key_description": text, "step_prompt": text}
    if ctype.lower() == "button":
        return {"type": "button", "key_description": "Button", "step_prompt": "Button"}
    return {"needs_manual_label": True, "component_type": ctype}


# Contextual page identity
def state_raw_page_name(state: Dict[str, Any], page_name: str = "") -> str:
    raw_name = str(state.get("raw_page_name") or "").strip()
    if raw_name:
        return raw_name
    title = str(state.get("last_title") or "").strip()
    return state_name_from_title(title) if title else str(state.get("page_name") or page_name or "").strip()


def current_session_page(work_dir: Path) -> str:
    path = work_dir / "outputs" / "navigation" / "current_path_session.json"
    if not path.exists():
        return ""
    try:
        return str(load_json(path).get("active_page") or "")
    except Exception:
        return ""


def copy_stored_page_context(detected: Dict[str, Any], stored: Dict[str, Any], page_name: str) -> Dict[str, Any]:
    state = {**detected, "page_name": page_name, "raw_page_name": state_raw_page_name(detected)}
    for key in ("parent_page", "parent_title", "context_key", "entry_identity"):
        if key in stored:
            state[key] = stored[key]
    if stored.get("page_description"):
        state["page_description"] = stored["page_description"]
    return state


def resolve_detected_state(graph: Dict[str, Any], detected: Dict[str, Any], preferred_page: str = "") -> Dict[str, Any]:
    state = dict(detected)
    raw_name = state["raw_page_name"] = state_raw_page_name(state)
    current_name = str(state.get("page_name") or "")
    if current_name and current_name != raw_name and state.get("parent_page"):
        return state
    states = graph.get("states", {})
    preferred = states.get(preferred_page, {}) if preferred_page else {}
    preferred_is_contextual = (
        isinstance(preferred, dict)
        and bool(preferred)
        and preferred_page != raw_name
    )
    if (
        preferred_is_contextual
        and states_represent_same_page(state, preferred)
    ):
        return copy_stored_page_context(state, preferred, preferred_page)

    matches = [
        (str(name), stored)
        for name, stored in states.items()
        if isinstance(stored, dict)
        and state_raw_page_name(stored, str(name)) == raw_name
    ]
    scored = []
    left_nav = str(state.get("nav_key") or "")
    for name, stored in matches:
        right_nav = str(stored.get("nav_key") or "")
        score = (
            10000 if left_nav == right_nav else -1
        ) if left_nav and right_nav else state_structure_score(state, stored)
        scored.append((score, name, stored))
    scored.sort()
    if scored and scored[-1][0] >= 3:
        best_score = scored[-1][0]
        best = [item for item in scored if item[0] == best_score]
        if len(best) == 1:
            _, name, stored = best[0]
            return copy_stored_page_context(state, stored, name)
        preferred_match = next(
            (item for item in best if item[1] == preferred_page),
            None,
        )
        if preferred_match:
            _, name, stored = preferred_match
            return copy_stored_page_context(state, stored, name)

    if raw_name == "Pages_root":
        root_state = states.get("Pages_root", {})
        if preferred_is_contextual:
            preferred_score = state_structure_score(state, preferred)
            root_score = (
                state_structure_score(state, root_state)
                if isinstance(root_state, dict) and root_state
                else -1
            )
            if preferred_score >= root_score and preferred_score >= 1:
                return copy_stored_page_context(state, preferred, preferred_page)
        state["page_name"] = raw_name
        return state
    return copy_stored_page_context(state, matches[0][1], matches[0][0]) if len(matches) == 1 else state


def state_signature_texts(state: Dict[str, Any]) -> Set[str]:
    signature = state.get("signature") or {}
    title = clean_label(signature.get("title") or state.get("last_title"))
    return {
        text
        for value in signature.get("texts_any") or []
        if (text := clean_label(value)) and text != title
    }


def state_structure_score(left: Dict[str, Any], right: Dict[str, Any]) -> int:
    shared_texts = len(state_signature_texts(left) & state_signature_texts(right))
    same_title = clean_label(left.get("last_title")) == clean_label(right.get("last_title"))
    return shared_texts * 2 + int(bool(same_title))


def states_represent_same_page(left: Dict[str, Any], right: Dict[str, Any]) -> bool:
    left_name = state_raw_page_name(left)
    if not left_name or left_name != state_raw_page_name(right):
        return False
    left_nav, right_nav = str(left.get("nav_key") or ""), str(right.get("nav_key") or "")
    if left_nav and right_nav:
        return left_nav == right_nav
    if left_name != "Pages_root":
        return True

    # “设置”不只会出现在真正的设置首页标题中。双方都被标题规则暂时
    # 命名为 Pages_root 时，继续比较页面内稳定文本，避免把同名子页面
    # 当成仍停留在根页或临时弹层。
    left_texts = state_signature_texts(left)
    right_texts = state_signature_texts(right)
    if not left_texts or not right_texts:
        return True
    shared = len(left_texts & right_texts)
    return shared >= max(1, (min(len(left_texts), len(right_texts)) + 1) // 2)


def contextualize_child_state(
    graph: Dict[str, Any],
    from_page: str,
    detected: Dict[str, Any],
    via_target: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    state = dict(detected)
    raw_name = state_raw_page_name(state)
    parent = graph.get("states", {}).get(from_page, {"page_name": from_page})
    parent_title = str(parent.get("last_title") or parent.get("page_description") or "").strip()
    parent_title = parent_title or ("设置" if from_page == "Pages_root" else from_page.removeprefix("Pages_") or "page")
    child_title = str(state.get("last_title") or state.get("page_description") or "").strip()
    child_title = child_title or ("设置" if raw_name == "Pages_root" else raw_name.removeprefix("Pages_") or "page")
    target_title = clean_label(
        (via_target or {}).get("step_prompt")
        or (via_target or {}).get("key_description")
        or (via_target or {}).get("text")
        or (via_target or {}).get("value")
        or (via_target or {}).get("key")
    )
    entry_identity = candidate_merge_key(via_target or {})
    context_key = f"{from_page}::{raw_name}::{entry_identity}"
    route_title = target_title if child_title == parent_title and target_title else child_title
    contextual_title = f"{parent_title} to{route_title}"
    contextual_name = state_name_from_title(contextual_title)
    known_name = next((
        str(name) for name, stored in graph.get("states", {}).items()
        if isinstance(stored, dict) and stored.get("context_key") == context_key
    ), "")
    if known_name:
        contextual_name = known_name
    matching = []
    for transition in graph.get("transitions", []):
        child_name = str(transition.get("to_page") or "")
        child = graph.get("states", {}).get(child_name, {})
        if transition.get("from_page") == from_page and isinstance(child, dict) and state_raw_page_name(child, child_name) == raw_name:
            matching.append(child_name)
    if not known_name and len(matching) == 1 and matching[0] != contextual_name:
        old_name = matching[0]
        states = graph.setdefault("states", {})
        if old_name in states and contextual_name not in states:
            NavigationGraph(graph).rename_page(old_name, contextual_name)
    existing = graph.get("states", {}).get(contextual_name, {})
    if isinstance(existing, dict) and existing:
        state = copy_stored_page_context(state, existing, contextual_name)
    return {
        **state,
        "page_name": contextual_name,
        "raw_page_name": raw_name,
        "parent_page": from_page,
        "parent_title": parent_title,
        "entry_identity": entry_identity,
        "context_key": context_key,
        "page_description": contextual_title,
    }

# Navigation graph records and directory
def candidate_merge_key(candidate: Dict[str, Any]) -> str:
    value = str(candidate.get("value") or "").strip()
    key = str(candidate.get("key") or (value if candidate.get("type") in {"key", "button"} else "")).strip()
    if key:
        return f"key::{key}"
    ctype = str(candidate.get("type") or "").strip()
    text = str(candidate.get("text") or candidate.get("key_description") or (value if ctype in {"text", "button_text"} else "")).strip()
    if ctype and text:
        return f"type_text::{ctype}::{text}"
    component_type = str(candidate.get("component_type") or "").strip()
    if component_type and text:
        return f"component_text::{component_type}::{text}"
    if text:
        return f"text::{text}"
    stable = json.dumps({k: candidate.get(k) for k in ("type", "value", "component_type", "key_description", "step_prompt") if candidate.get(k)}, ensure_ascii=False, sort_keys=True)
    return "hash::" + hashlib.sha1(stable.encode("utf-8")).hexdigest()[:12] if stable != "{}" else ""


def candidate_from_auto(c: Dict[str, Any], source: str = "auto_detected") -> Dict[str, Any]:
    target = dict(c.get("suggested_target") or {})
    text = str(c.get("text") or target.get("text") or target.get("key_description") or "")
    item = {
        "candidate_id": "",
        "type": str(target.get("type") or c.get("type") or ""),
        "value": target.get("value") or c.get("key") or text,
        "component_type": str(target.get("component_type") or c.get("type") or ""),
        "text": text,
        "key": str(c.get("key") or target.get("key") or (target.get("value") if target.get("type") == "key" else "") or ""),
        "key_description": str(target.get("key_description") or text or target.get("value") or ""),
        "step_prompt": str(target.get("step_prompt") or text or target.get("value") or ""),
        "source": source,
        "transition_ids": list(c.get("transition_ids") or []),
        "operation_ids": list(c.get("operation_ids") or []),
    }
    item["candidate_id"] = candidate_merge_key(item)
    return item


def step_target(target: Dict[str, Any], include_type: bool = False) -> Dict[str, Any]:
    legacy_type = str(target.get("type") or "")
    allowed = {
        "text",
        "key",
        "key_description",
        "step_prompt",
        "expect",
    }
    if include_type:
        allowed.add("type")
    clean = {k: v for k, v in target.items() if k in allowed and v not in (None, "", [])}
    if target.get("value") and "key" not in clean and legacy_type in {"key", "button"}:
        clean["key"] = target.get("value")
    if not include_type and not (clean.get("key") or clean.get("text")) and legacy_type == "button":
        clean["type"] = "button"
    elif not include_type:
        clean.pop("type", None)
    return clean


def component_summary_from_tree(root_json: Dict[str, Any]) -> List[Dict[str, Any]]:
    """提取可用于对比/合并的稳定控件摘要；不把坐标作为正式 key。"""
    components: List[Dict[str, Any]] = []
    seen = set()
    for node, _, _ in walk(root_json):
        ctype = get_type(node)
        if ctype in {"Root", "Page", "Navigation", "NavDestination", "RelativeContainer"}:
            continue
        if not ctype or get_attr(node, "enabled", "true").lower() == "false":
            continue
        text = next((item for item in meaningful_texts(node) if is_stable_text_for_navigation(item)), "")
        key = get_key(node)
        key = key if is_stable_key_for_navigation(key) else ""
        if not key and not text:
            continue
        item = {"text": text, "key": key, "component_type": ctype}
        merge_key = candidate_merge_key(item)
        if not merge_key or merge_key in seen:
            continue
        seen.add(merge_key)
        clickable = node.get("attributes", node).get("clickable", False)
        components.append({
            "text": item.get("text", ""),
            "key": item.get("key", ""),
            "component_type": ctype,
            "clickable": (
                (isinstance(clickable, (int, float)) and clickable != 0)
                or str(clickable).strip().lower() in {"true", "1", "yes"}
            ),
            "enabled": True,
        })
    return components


def get_page_merged_candidates(graph: Dict[str, Any], page_name: str, current_candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    state = graph.get("states", {}).get(page_name, {}) if page_name else {}
    merged: Dict[str, Dict[str, Any]] = {}
    for c in current_candidates:
        item = candidate_from_auto(c, source=str(c.get("source") or "auto_detected"))
        key = candidate_merge_key(item)
        if key:
            merged[key] = {**item, **merged.get(key, {})}
    for c in state.get("merged_candidates", []) or []:
        key = candidate_merge_key(c)
        if not key:
            continue
        prev = merged.get(key, {})
        item = {**prev, **c}
        item.setdefault("candidate_id", key)
        item.setdefault("source", c.get("source") or "hit_test_click")
        item.setdefault("transition_ids", [])
        item.setdefault("operation_ids", [])
        merged[key] = item
    transitions = {
        str(item.get("transition_id")): item
        for item in graph.get("transitions", [])
        if item.get("transition_id")
    }
    result = []
    for candidate in merged.values():
        item = dict(candidate)
        item.setdefault("candidate_id", str(item.get("candidate_id") or candidate_merge_key(item)))
        raw_tids = [tid for tid in item.get("transition_ids", []) if tid]
        tids = [tid for tid in raw_tids if tid in transitions]
        oids = [oid for oid in item.get("operation_ids", []) if oid]
        ctype = str(item.get("component_type") or "")
        if ctype in {"Toggle", "Switch", "CheckBox", "Checkbox"}:
            status, label = "same_page_control", "同页控件，不建议录制为页面跳转"
            tids = raw_tids
        elif tids:
            status, label = "recorded_transition", f"已录制跳转 -> {transitions[tids[0]].get('to_page', '')}"
        elif oids:
            status, label = "page_operation", "页面内操作"
        else:
            status = "unrecorded"
            label = "Button / 未录制" if ctype == "Button" else "未录制"
        item.update({"status": status, "label": label, "transition_ids": tids, "operation_ids": oids})
        result.append(item)
    return result


def upsert_candidate(state_entry: Dict[str, Any], candidate: Dict[str, Any]) -> Dict[str, Any]:
    merged = state_entry.setdefault("merged_candidates", [])
    key = candidate_merge_key(candidate)
    if not key:
        return candidate
    candidate = dict(candidate)
    candidate.setdefault("candidate_id", key)
    for existing in merged:
        if candidate_merge_key(existing) == key:
            for field in ("transition_ids", "operation_ids"):
                ids = list(dict.fromkeys(list(existing.get(field) or []) + list(candidate.get(field) or [])))
                existing[field] = ids
            existing.update({k: v for k, v in candidate.items() if k not in {"transition_ids", "operation_ids"} and v not in (None, "", [])})
            return existing
    merged.append(candidate)
    return candidate


def upsert_clicked_target_as_candidate(graph: Dict[str, Any], page_name: str, target: Dict[str, Any], transition_id: Optional[str] = None, operation_id: Optional[str] = None) -> Dict[str, Any]:
    state_entry = graph.setdefault("states", {}).setdefault(page_name, {"page_name": page_name})
    clean = {key: value for key, value in target.items() if key not in {"point", "normalized_point", "coordinate_hit", "bounds_center", "fallback_locator"}}
    item = {
        "type": str(clean.get("type") or ""),
        "value": clean.get("value", ""),
        "component_type": str(clean.get("component_type") or ""),
        "text": str(clean.get("text") or (clean.get("value") if clean.get("type") in {"text", "button_text"} else "") or ""),
        "key": str(clean.get("key") or (clean.get("value") if clean.get("type") in {"key", "button"} else "") or ""),
        "key_description": str(clean.get("key_description") or clean.get("text") or clean.get("value") or ""),
        "step_prompt": str(clean.get("step_prompt") or clean.get("key_description") or clean.get("value") or ""),
        "source": "hit_test_click",
        "transition_ids": [],
        "operation_ids": [],
    }
    item["candidate_id"] = candidate_merge_key(item)
    item["clicked_count"] = 1
    item["last_clicked_at"] = now_iso()
    if transition_id:
        item["transition_ids"] = [transition_id]
    if operation_id:
        item["operation_ids"] = [operation_id]
    existing = None
    for c in state_entry.setdefault("merged_candidates", []):
        if candidate_merge_key(c) == candidate_merge_key(item):
            existing = c
            break
    if existing:
        existing["clicked_count"] = int(existing.get("clicked_count") or 0) + 1
        existing["last_clicked_at"] = item["last_clicked_at"]
        existing.setdefault("source", "hit_test_click")
        existing.setdefault("transition_ids", [])
        existing.setdefault("operation_ids", [])
        if transition_id and transition_id not in existing["transition_ids"]:
            existing["transition_ids"].append(transition_id)
        if operation_id and operation_id not in existing["operation_ids"]:
            existing["operation_ids"].append(operation_id)
        for k, v in item.items():
            if k not in {"clicked_count", "last_clicked_at", "transition_ids", "operation_ids"} and v not in (None, "", []):
                existing[k] = v
        return existing
    return upsert_candidate(state_entry, item)


def build_page_directory(graph: Dict[str, Any]) -> Dict[str, Any]:
    navigation = NavigationGraph(graph)
    states = navigation.states
    outgoing = navigation.ordered_outgoing()

    def local_page_label(value: Any) -> str:
        label = str(value or "").strip()
        if label.startswith("Pages_"):
            label = label.removeprefix("Pages_")
        for separator in ("_to", " to"):
            if separator in label:
                label = label.rsplit(separator, 1)[-1].strip()
        if "_" in label:
            label = label.rsplit("_", 1)[-1].strip()
        return label

    def page_title(page: str, state: Any) -> str:
        if not isinstance(state, dict):
            return local_page_label(page) or page
        manual = state.get("dfs_manual")
        if isinstance(manual, dict):
            manual_description = local_page_label(manual.get("page_description"))
            if manual_description and any(
                char.isalnum() or "\u4e00" <= char <= "\u9fff"
                for char in manual_description
            ):
                return manual_description
            for target in reversed(manual.get("path_snapshot") or []):
                if not isinstance(target, dict):
                    continue
                label = str(
                    target.get("step_prompt")
                    or target.get("key_description")
                    or target.get("text")
                    or target.get("value")
                    or target.get("key")
                    or ""
                ).strip()
                if label:
                    return label
        for value in (
            state.get("page_description"),
            state.get("last_title"),
            page,
        ):
            if label := local_page_label(value):
                return label
        return page

    def node(page: str, seen: Set[str]) -> Dict[str, Any]:
        st = states.get(page, {})
        children = []
        for t in outgoing.get(page, []):
            child = str(t.get("to_page"))
            if child in seen:
                continue
            steps = t.get("steps")
            if not isinstance(steps, list) or not steps:
                target = t.get("target") or {}
                steps = [{"operate": str(t.get("operate") or "tap"), "target": step_target(target)}] if target else []
            else:
                steps = [step for step in steps if isinstance(step, dict)]
            target = t.get("target") or {}
            step_labels = [
                str(
                    (step.get("target") or {}).get("step_prompt")
                    or (step.get("target") or {}).get("key_description")
                    or (step.get("target") or {}).get("text")
                    or (step.get("target") or {}).get("value")
                    or (step.get("target") or {}).get("key")
                    or step.get("operate")
                    or "tap"
                )
                for step in steps
            ]
            label = " -> ".join(step_labels) if len(step_labels) > 1 else str(
                target.get("step_prompt")
                or target.get("key_description")
                or target.get("text")
                or target.get("value")
                or target.get("key")
                or t.get("operate")
                or ""
            )
            via = {
                "from_page": page,
                "target_label": label,
                "transition_id": t.get("transition_id"),
                "priority": safe_priority(t.get("priority")),
                "step_count": len(steps),
                "steps": steps,
            }
            children.append({**node(child, seen | {child}), "via": via})
        title = page_title(page, st)
        return {"page_name": page, "title": title, "children": children}
    flat = []
    for page, st in states.items():
        title = page_title(page, st)
        flat.append({
            "page_name": page,
            "title": title,
            "incoming_count": sum(
                1
                for siblings in outgoing.values()
                for transition in siblings
                if transition.get("to_page") == page
            ),
            "outgoing_count": len(outgoing.get(page, [])),
            "candidate_count": len(st.get("merged_candidates", []) or []),
            "operation_count": len(st.get("page_operations", []) or []),
            "continued_capture_count": len(st.get("continued_captures", []) or []),
        })
    return {"root": "Pages_root", "items": [node("Pages_root", {"Pages_root"})] if "Pages_root" in states else [], "flat_pages": sorted(flat, key=lambda x: x["page_name"])}


# Web API request contracts
class RenamePageRequest(BaseModel):
    old_page_name: str
    new_page_name: str
    new_title: str = ""


class ActionRequest(BaseModel):
    action: str
    payload: Optional[Dict[str, Any]] = None


class DeleteActionRequest(BaseModel):
    target_type: str
    payload: Optional[Dict[str, Any]] = None
    dry_run: bool = True
    preview_token: str = ""


class CreateSettingsProfileRequest(BaseModel):
    name: str = ""
    settings_version: str
    device_model: str
    parent_profile_id: str = DEFAULT_SETTINGS_PROFILE_ID


class ImportSettingsProfileRequest(BaseModel):
    name: str = ""
    settings_version: str
    device_model: str
    source_work_dir: str = ""
    source_filename: str = ""
    graph: Optional[Dict[str, Any]] = None


# Web session and graph maintenance
def pending_action_chain(work_dir: Path) -> Optional[Dict[str, Any]]:
    path = work_dir / "outputs" / "navigation" / "pending_action_chain.json"
    return load_json(path) if path.exists() else None


def clear_pending_action_chain(work_dir: Path) -> None:
    path = work_dir / "outputs" / "navigation" / "pending_action_chain.json"
    if path.exists():
        path.unlink()


def append_web_history(work_dir: Path, event: Dict[str, Any]) -> None:
    path = work_dir / "outputs" / "navigation" / "web_record_history.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"created_at": now_iso(), **event}, ensure_ascii=False) + "\n")


class GraphMaintenance(NavigationGraph):
    """集中维护一张导航图；删除只执行 ``plan_delete`` 明示的影响。"""

    def reachable_pages(self, excluded_transition_ids: Optional[Set[str]] = None) -> Set[str]:
        excluded_transition_ids = excluded_transition_ids or set()
        reachable = {"Pages_root"} if "Pages_root" in self.states else set()
        queue = deque(reachable)
        outgoing: Dict[str, List[str]] = defaultdict(list)
        for transition in self.transitions:
            source, target = str(transition.get("from_page") or ""), str(transition.get("to_page") or "")
            if (
                str(transition.get("transition_id") or "") not in excluded_transition_ids
                and source in self.states
                and target in self.states
                and source != target
            ):
                outgoing[source].append(target)
        while queue:
            source = queue.popleft()
            for target in outgoing.get(source, []):
                if target in reachable:
                    continue
                reachable.add(target)
                queue.append(target)
        return reachable

    def orphan_pages(self, active_page: str = "") -> List[Dict[str, Any]]:
        unreachable = set(self.states) - self.reachable_pages()
        pages = []
        for page in sorted(unreachable):
            state = self.states[page]
            pages.append({
                "page_name": page,
                "title": str(state.get("last_title") or state.get("page_description") or page),
                "parent_page": state.get("parent_page") or "",
                "incoming_count": sum(1 for t in self.transitions if t.get("to_page") == page),
                "outgoing_count": sum(1 for t in self.transitions if t.get("from_page") == page),
                "candidate_count": len(state.get("merged_candidates", []) or []),
                "operation_count": len(state.get("page_operations", []) or []),
                "continued_capture_count": len(state.get("continued_captures", []) or []),
                "is_active": page == active_page,
            })
        return pages

    def plan_delete(self, target_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """一次生成完整删除计划，包含 state 带走的边、操作、续录和候选引用。"""
        plan: Dict[str, Any] = {
            "transitions": [],
            "states": [],
            "candidates": [],
            "page_operations": [],
            "continued_captures": [],
            "files": [],
            "warnings": [],
        }
        target_type = target_type.strip()
        transition_by_id = {
            str(item.get("transition_id")): item
            for item in self.transitions
            if item.get("transition_id")
        }
        deleted_pages: Set[str] = set()
        deleted_tids: Set[str] = set()
        delete_newly_unreachable = False

        if target_type in {"transition", "branch"}:
            tid = str(payload.get("transition_id") or "")
            if tid not in transition_by_id:
                raise ValueError(f"transition 不存在：{tid}")
            deleted_tids.add(tid)
            delete_newly_unreachable = bool(payload.get(
                "delete_descendants" if target_type == "branch" else "delete_orphan_to_state",
                True,
            ))
        elif target_type == "page":
            page = str(payload.get("page_name") or "")
            if page == "Pages_root":
                raise ValueError("不允许删除 Pages_root")
            if page not in self.states:
                raise ValueError(f"页面不存在：{page}")
            # state 被删除后所有相连边都必然失效，因此不提供“保留半边”的虚假选项。
            deleted_pages.add(page)
        elif target_type == "orphan_pages":
            requested = {str(page) for page in payload.get("page_names", []) if str(page)}
            orphan_names = {item["page_name"] for item in self.orphan_pages()}
            if not requested:
                raise ValueError("请选择至少一个孤儿页面")
            invalid = requested - orphan_names
            if invalid:
                raise ValueError(f"这些页面不是孤儿页面：{', '.join(sorted(invalid))}")
            deleted_pages.update(requested)
        elif target_type == "candidate":
            page, cid = str(payload.get("page_name") or ""), str(payload.get("candidate_id") or "")
            state = self.states.get(page, {})
            candidates = state.get("merged_candidates", []) or []
            candidate = next((item for item in candidates if str(item.get("candidate_id") or candidate_merge_key(item)) == cid), None)
            if not candidate:
                raise ValueError(f"候选不存在：{cid}")
            plan["candidates"].append({"page_name": page, "candidate_id": cid, "action": "delete_candidate"})
            if candidate.get("transition_ids") and not payload.get("delete_linked_transitions", False):
                plan["warnings"].append("该候选控件关联了已录制跳转，只删除候选可能造成 transition 缺少控件引用。")
            if payload.get("delete_linked_transitions", False):
                linked_tids = {str(item) for item in candidate.get("transition_ids", []) or []}
                missing = linked_tids - set(transition_by_id)
                if missing:
                    raise ValueError(f"关联 transition 不存在：{', '.join(sorted(missing))}")
                deleted_tids.update(linked_tids)
                delete_newly_unreachable = True
            if payload.get("delete_linked_operations", False):
                plan["page_operations"].extend(
                    {"page_name": page, "operation_id": operation_id}
                    for operation_id in candidate.get("operation_ids", []) or []
                )
        elif target_type == "page_operation":
            page, oid = str(payload.get("page_name") or ""), str(payload.get("operation_id") or "")
            state = self.states.get(page, {})
            operations = state.get("page_operations", []) or []
            if not any(item.get("operation_id") == oid for item in operations):
                raise ValueError(f"页面操作不存在：{oid}")
            plan["page_operations"].append({"page_name": page, "operation_id": oid})
            delete_revealed = bool(payload.get("delete_revealed_candidates", True))
            if not delete_revealed:
                plan["keep_revealed_candidates"] = True
            for candidate in state.get("merged_candidates", []) or []:
                references = {
                    candidate.get("requires_operation_id"),
                    candidate.get("source_operation_id"),
                    *(candidate.get("operation_ids") or []),
                }
                if oid in references:
                    plan["candidates"].append({
                        "page_name": page,
                        "candidate_id": str(candidate.get("candidate_id") or candidate_merge_key(candidate)),
                        "action": "delete_revealed" if delete_revealed else "remove_operation_ref",
                })
        elif target_type == "continued_capture":
            page, capture_id = str(payload.get("page_name") or ""), str(payload.get("capture_id") or "")
            state = self.states.get(page, {})
            captures = state.get("continued_captures", []) or []
            capture = next((item for item in captures if item.get("capture_id") == capture_id), None)
            if not capture:
                raise ValueError(f"续录不存在：{capture_id}")
            plan["continued_captures"].append({"page_name": page, "capture_id": capture_id})
            if capture.get("screenshot"):
                plan["files"].append(capture["screenshot"])
            delete_candidates = bool(payload.get("delete_candidates_from_capture", True))
            if not delete_candidates:
                plan["keep_capture_candidates"] = True
            for candidate in state.get("merged_candidates", []) or []:
                if candidate.get("source_capture_id") == capture_id:
                    if delete_candidates and (candidate.get("transition_ids") or candidate.get("operation_ids")):
                        plan["warnings"].append(
                            f"候选 {candidate.get('candidate_id') or candidate_merge_key(candidate)} 有关联记录，将只移除 source_capture_id"
                        )
                    plan["candidates"].append({
                        "page_name": page,
                        "candidate_id": str(candidate.get("candidate_id") or candidate_merge_key(candidate)),
                        "action": "delete_from_capture",
                    })
        else:
            raise ValueError(f"未知删除目标类型：{target_type}")

        if delete_newly_unreachable:
            # 用 root 可达性差集处理整条孤儿链和脱离 root 的循环；历史孤儿不夹带删除。
            reachable_pages = self.reachable_pages()
            reachable_without_deleted = self.reachable_pages(deleted_tids)
            deleted_pages.update(reachable_pages - reachable_without_deleted)
            deleted_pages.discard("Pages_root")

        plan["states"] = sorted(deleted_pages)
        for page in plan["states"]:
            state = self.states[page]
            plan["page_operations"].extend(
                {"page_name": page, "operation_id": operation["operation_id"]}
                for operation in state.get("page_operations", []) or []
                if operation.get("operation_id")
            )
            for capture in state.get("continued_captures", []) or []:
                if capture.get("capture_id"):
                    plan["continued_captures"].append({
                        "page_name": page,
                        "capture_id": capture["capture_id"],
                    })
                if capture.get("screenshot"):
                    plan["files"].append(capture["screenshot"])

        # 删除 state 会必然删除所有相连边；这些边必须全部出现在预览里。
        plan["transitions"] = [
            transition for transition in self.transitions
            if str(transition.get("transition_id") or "") in deleted_tids
            or transition.get("from_page") in deleted_pages
            or transition.get("to_page") in deleted_pages
        ]
        deleted_tids = {
            str(item.get("transition_id") or "")
            for item in plan["transitions"]
            if item.get("transition_id")
        }

        deleted_oids: Dict[str, Set[str]] = defaultdict(set)
        for item in plan["page_operations"]:
            deleted_oids[str(item.get("page_name") or "")].add(str(item.get("operation_id") or ""))
        planned_candidates = {
            (item.get("page_name"), item.get("candidate_id"))
            for item in plan["candidates"]
        }
        for page, state in self.states.items():
            if page in deleted_pages:
                continue
            page_oids = deleted_oids.get(page, set())
            for candidate in state.get("merged_candidates", []) or []:
                old_tids = set(candidate.get("transition_ids") or [])
                old_oids = set(candidate.get("operation_ids") or [])
                if not (old_tids & deleted_tids or old_oids & page_oids):
                    continue
                cid = str(candidate.get("candidate_id") or candidate_merge_key(candidate))
                if (page, cid) in planned_candidates:
                    continue
                action = "remove_refs"
                if (
                    candidate.get("source") == "hit_test_click"
                    and not (old_tids - deleted_tids)
                    and not (old_oids - page_oids)
                ):
                    action = "delete_orphan_clicked_candidate"
                plan["candidates"].append({
                    "page_name": page,
                    "candidate_id": cid,
                    "action": action,
                })
                planned_candidates.add((page, cid))
        return plan

    def apply_delete(self, plan: Dict[str, Any]) -> List[str]:
        runtime_warnings: List[str] = []
        deleted_tids = {str(item.get("transition_id") or "") for item in plan.get("transitions", [])}
        deleted_pages = {str(page) for page in plan.get("states", [])}
        deleted_oids: Dict[str, Set[str]] = defaultdict(set)
        for item in plan.get("page_operations", []):
            deleted_oids[str(item.get("page_name") or "")].add(str(item.get("operation_id") or ""))
        # plan_delete 已把删除 state 带走的所有边列入预览；这里不做额外孤儿清理。
        self.graph["transitions"] = [
            transition for transition in self.transitions
            if transition.get("transition_id") not in deleted_tids
            and transition.get("from_page") not in deleted_pages
            and transition.get("to_page") not in deleted_pages
        ]
        self.transitions = self.graph["transitions"]
        for page in deleted_pages:
            self.states.pop(page, None)

        candidate_actions = {
            (item.get("page_name"), item.get("candidate_id")): item.get("action")
            for item in plan.get("candidates", [])
        }
        capture_ids = {
            (item.get("page_name"), item.get("capture_id"))
            for item in plan.get("continued_captures", [])
        }
        for page, state in self.states.items():
            page_oids = deleted_oids.get(page, set())
            operations = state.get("page_operations", []) or []
            captures = state.get("continued_captures", []) or []
            state["page_operations"] = [
                operation for operation in operations
                if operation.get("operation_id") not in page_oids
            ]
            state["continued_captures"] = [
                capture for capture in captures
                if (page, capture.get("capture_id")) not in capture_ids
            ]
            kept_candidates = []
            for candidate in state.get("merged_candidates", []) or []:
                cid = str(candidate.get("candidate_id") or candidate_merge_key(candidate))
                action = candidate_actions.get((page, cid))
                transition_ids = [item for item in candidate.get("transition_ids", []) if item not in deleted_tids]
                operation_ids = [item for item in candidate.get("operation_ids", []) if item not in page_oids]
                candidate["transition_ids"] = transition_ids
                candidate["operation_ids"] = operation_ids
                if action in {"delete_candidate", "delete_orphan_clicked_candidate", "delete_revealed"}:
                    continue
                if action == "remove_operation_ref":
                    if candidate.get("requires_operation_id") in page_oids:
                        candidate.pop("requires_operation_id", None)
                    if candidate.get("source_operation_id") in page_oids:
                        candidate.pop("source_operation_id", None)
                if action == "delete_from_capture":
                    capture_id = str(candidate.get("source_capture_id") or "")
                    has_links = bool(transition_ids or operation_ids)
                    if not plan.get("keep_capture_candidates") and not has_links:
                        continue
                    if (page, capture_id) in capture_ids:
                        candidate.pop("source_capture_id", None)
                kept_candidates.append(candidate)
            state["merged_candidates"] = kept_candidates

        for file_name in plan.get("files", []):
            try:
                path = Path(file_name)
                if path.exists():
                    path.unlink()
            except Exception as exc:
                runtime_warnings.append(f"删除文件失败 {file_name}: {exc}")
        # 只刷新派生计数；不顺手清理计划外的旧坏引用。
        for page, state in self.states.items():
            state["incoming_count"] = sum(1 for t in self.transitions if t.get("to_page") == page)
            state["outgoing_count"] = sum(1 for t in self.transitions if t.get("from_page") == page)
            state["candidate_count"] = len(state.get("merged_candidates", []) or [])
            state["operation_count"] = len(state.get("page_operations", []) or [])
            state["continued_capture_count"] = len(state.get("continued_captures", []) or [])
        return runtime_warnings


# Keep request-scoped profile management in this baseline domain module.
# web_nav_server historically imports the profile names from a standalone
# module; expose this module under that import name so no extra production
# file is required.
import sys as _sys
_sys.modules.setdefault("settings_profiles", _sys.modules[__name__])
del _sys
