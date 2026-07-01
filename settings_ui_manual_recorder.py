#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shared UI-tree and navigation-graph helpers for the Web recorder and DFS.

This module intentionally stays small. It keeps only the pieces needed by the
current product path:
- capture the current UI tree/screenshot
- infer the current settings page
- extract clickable=True navigation candidates
- persist the lightweight navigation graph without device-specific coordinates
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple


Node = Dict[str, Any]

DEFAULT_DEVICE_ID = "68Q0223918000004"
DEFAULT_WORK_DIR = Path("demo_settings")
PACKAGE_NAME = "com.huawei.hmos.settings"
MAIN_PAGE_NAME = "com.huawei.hmos.settings.MainAbility"

NOISE_TEXTS = {"tab_unlock"}
NON_INTERACTION_TYPES = {"Navigation", "NavDestination", "Page", "Root", "WindowScene"}

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


def capture_artifacts(device_id: str, output_dir: Path) -> bool:
    output_dir.mkdir(parents=True, exist_ok=True)
    if not run_cmd(["hdc", "version"]):
        print("✗ hdc 不可用，请确认 hdc 已加入 PATH")
        return False
    base = ["hdc"] + (["-t", device_id] if device_id else [])
    commands = [
        (base + ["shell", "uitest", "dumpLayout", "-p", "/data/local/tmp/current_ui_tree.json"], "dumpLayout"),
        (base + ["file", "recv", "/data/local/tmp/current_ui_tree.json", "current_ui_tree.json"], "拉取 JSON"),
        (base + ["shell", "uitest", "screenCap", "-p", "/data/local/tmp/current_screen.png"], "screenCap"),
        (base + ["file", "recv", "/data/local/tmp/current_screen.png", "current_screen.png"], "拉取截图"),
    ]
    for cmd, name in commands:
        if not run_cmd(cmd, cwd=str(output_dir)):
            print(f"✗ {name} 失败")
            return False
    return True


def navigation_dir(work_dir: Path) -> Path:
    return work_dir / "outputs" / "navigation"


def navigation_graph_path(work_dir: Path) -> Path:
    return navigation_dir(work_dir) / "settings_navigation_graph.json"


def pending_transition_path(work_dir: Path) -> Path:
    return navigation_dir(work_dir) / "pending_transition.json"


def current_path_session_path(work_dir: Path) -> Path:
    return navigation_dir(work_dir) / "current_path_session.json"


def empty_navigation_graph() -> Dict[str, Any]:
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


def sanitize_navigation_graph_records(graph: Dict[str, Any]) -> None:
    strip_coordinate_fields(graph)


def load_navigation_graph(work_dir: Path) -> Dict[str, Any]:
    path = navigation_graph_path(work_dir)
    if not path.exists():
        return empty_navigation_graph()
    graph = load_json(path)
    graph.setdefault("package_name", PACKAGE_NAME)
    graph.setdefault("main_page_name", MAIN_PAGE_NAME)
    graph.setdefault("states", {})
    graph.setdefault("transitions", [])
    graph.setdefault("traversal_config", {"strategy": "dfs", "root_page": "Pages_root", "default_return_policy": {"type": "system_back"}})
    sanitize_navigation_graph_records(graph)
    return graph


def save_navigation_graph(graph: Dict[str, Any], work_dir: Path) -> None:
    sanitize_navigation_graph_records(graph)
    graph["updated_at"] = now_iso()
    save_json(graph, navigation_graph_path(work_dir), "轻量导航状态图")


def save_current_path_session(work_dir: Path, active_page: str, base_page: str = "") -> None:
    data = {"active_page": active_page}
    if base_page:
        data["base_page"] = base_page
    save_json(data, current_path_session_path(work_dir), "当前页面会话")


def active_navigation_state(work_dir: Path, graph: Dict[str, Any], detected_state: Dict[str, Any]) -> Dict[str, Any]:
    path = current_path_session_path(work_dir)
    if path.exists():
        try:
            session = load_json(path)
            active_page = str(session.get("active_page") or "")
            state = graph.get("states", {}).get(active_page)
            if isinstance(state, dict):
                active = dict(state)
                if session.get("base_page"):
                    active["base_page"] = session.get("base_page")
                return active
        except Exception:
            pass
    return detected_state


def add_transition(graph: Dict[str, Any], transition: Dict[str, Any]) -> None:
    tid = str(transition.get("transition_id") or "")
    if not tid:
        raise ValueError("transition 缺少 transition_id")
    graph["transitions"] = [t for t in graph.get("transitions", []) if t.get("transition_id") != tid]
    graph.setdefault("transitions", []).append(transition)


def transition_id(from_page: str, operate: str, to_page: str, target: Dict[str, Any], effect: str = "") -> str:
    payload = {
        "from_page": from_page,
        "operate": operate,
        "to_page": to_page,
        "target": {k: target.get(k) for k in ("type", "value", "key", "component_type", "key_description", "step_prompt") if target.get(k)},
        "effect": effect,
    }
    digest = hashlib.sha1(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:12]
    return f"{from_page}__to__{to_page}__{operate}_{digest}"


def attrs(node: Node) -> Dict[str, Any]:
    return node.get("attributes", node)


def children(node: Node) -> List[Node]:
    return node.get("children", []) or []


def to_bool(value: Any) -> bool:
    if value is True:
        return True
    if value in (False, None):
        return False
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() in {"true", "1", "yes"}


def get_type(node: Node) -> str:
    a = attrs(node)
    return str(a.get("type") or a.get("className") or a.get("componentType") or "")


def get_key(node: Node) -> str:
    a = attrs(node)
    return str(a.get("key") or a.get("id") or "")


def get_text(node: Node) -> str:
    a = attrs(node)
    return str(a.get("text") or a.get("originalText") or "").strip()


def get_attr(node: Node, name: str, default: str = "") -> str:
    return str(attrs(node).get(name, default) or "")


def is_visible(node: Node) -> bool:
    return get_attr(node, "visible", "true").lower() != "false"


def is_enabled(node: Node) -> bool:
    return get_attr(node, "enabled", "true").lower() != "false"


def walk(node: Node, depth: int = 0, parent: Optional[Node] = None):
    yield node, depth, parent
    for child in children(node):
        yield from walk(child, depth + 1, node)


def find_all(root: Node, pred: Callable[[Node], bool]) -> List[Node]:
    return [node for node, _, _ in walk(root) if pred(node)]


def annotate(root: Node) -> None:
    def rec(node: Node, parent: Optional[Node], type_path: str, index_path: str) -> None:
        node["__parent"] = parent
        node["__type_path"] = type_path
        node["__index_path"] = index_path
        counts: Dict[str, int] = defaultdict(int)
        for index, child in enumerate(children(node)):
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


def nearest_label(node: Node) -> str:
    texts = meaningful_texts(node)
    if texts:
        return texts[0]
    for parent in parent_chain(node):
        if get_type(parent) in {"Row", "Column", "ListItem", "Button", "MenuItem"}:
            texts = meaningful_texts(parent)
            if texts:
                return texts[0]
    return ""


def find_page_title(root: Node) -> str:
    for node, _, _ in walk(root):
        key = get_key(node)
        text = get_text(node)
        if key == "page.title_id" and text:
            return clean_label(text)
    title_candidates: List[Tuple[int, str]] = []
    for node, depth, _ in walk(root):
        if get_type(node) == "Text" and is_visible(node):
            text = clean_label(get_text(node))
            rect = parse_rect(get_attr(node, "bounds"))
            if text and rect["valid"] and rect["top"] <= 220:
                title_candidates.append((depth, text))
    return title_candidates[-1][1] if title_candidates else ""


def find_nav_destination_key(root: Node) -> str:
    candidates: List[Tuple[int, str]] = []
    for node, depth, _ in walk(root):
        if get_type(node) == "NavDestination" and is_visible(node):
            key = get_key(node)
            if key:
                candidates.append((depth, key))
    return sorted(candidates, reverse=True)[0][1] if candidates else ""


def page_identity(root: Node) -> Dict[str, str]:
    title = find_page_title(root)
    nav_key = find_nav_destination_key(root)
    page_id = nav_key or (f"title::{title}" if title else "unknown::page")
    return {"page_id": page_id, "title": title or page_id, "nav_key": nav_key}


def state_name_from_title(title: str, overlay: bool = False) -> str:
    value = clean_label(title) or "page"
    if value == "设置":
        return "Pages_root"
    safe = re.sub(r"\s+", "_", value)
    safe = re.sub(r"[^\w\u4e00-\u9fff]+", "_", safe).strip("_") or "page"
    return ("Overlay_" if overlay else "Pages_") + safe


def detect_dialog_root(root: Node) -> Optional[Node]:
    # Transient popup menus are part of a transition step, not standalone pages.
    # Only explicit dialogs are promoted to overlay states.
    candidates = find_all(root, lambda n: is_visible(n) and "dialog" in (get_type(n) + get_key(n)).lower())
    if not candidates:
        return None
    return max(candidates, key=lambda n: parse_rect(get_attr(n, "bounds"))["area"])


def detect_overlay_title(root: Node) -> str:
    dialog = detect_dialog_root(root)
    return nearest_label(dialog) if dialog else ""


def build_navigation_state(root: Node) -> Dict[str, Any]:
    dialog_root = detect_dialog_root(root)
    page = page_identity(root)
    scope = dialog_root or root
    overlay_title = detect_overlay_title(root) if dialog_root else ""
    title = overlay_title or page.get("title") or nearest_label(scope)
    page_name = state_name_from_title(title or page.get("page_id", "page"), overlay=bool(dialog_root))
    texts = [text for text in meaningful_texts(scope) if is_stable_text_for_navigation(text)][:8]
    state: Dict[str, Any] = {
        "page_name": page_name,
        "page_description": ("弹窗：" if dialog_root else "") + (title or page_name),
        "last_title": title,
        "signature": {"title": title, "texts_any": texts},
    }
    if dialog_root:
        state.update({"state_type": "overlay", "is_overlay": True, "overlay_title": title})
    return state


def node_semantic_summary(node: Node) -> Dict[str, Any]:
    text = next((t for t in meaningful_texts(node) if is_stable_text_for_navigation(t)), "")
    key = get_key(node)
    return {
        "component_type": get_type(node),
        "text": text,
        "key": key if is_stable_key_for_navigation(key) else "",
        "bounds": get_attr(node, "bounds"),
        "clickable": to_bool(attrs(node).get("clickable", False)),
        "enabled": is_enabled(node),
    }


def is_recordable_clickable_area(node: Node, screen_area: int = 0) -> bool:
    if not (to_bool(attrs(node).get("clickable", False)) and is_visible(node) and is_enabled(node)):
        return False
    if get_type(node) in NON_INTERACTION_TYPES:
        return False
    rect = parse_rect(get_attr(node, "bounds"))
    if not rect["valid"] or rect["area"] <= 0:
        return False
    if screen_area and rect["area"] > screen_area * 0.85:
        return False
    return True


def target_from_node(node: Node, dialog: bool = False) -> Dict[str, Any]:
    text = next((t for t in meaningful_texts(node) if is_stable_text_for_navigation(t)), "")
    key = get_key(node)
    label = text or nearest_label(node) or key
    if is_stable_key_for_navigation(key):
        target = {"type": "key", "value": key, "key": key, "component_type": get_type(node), "text": text, "key_description": label, "step_prompt": label}
    elif text:
        target = {"type": "text", "value": text, "component_type": get_type(node), "text": text, "key_description": text, "step_prompt": text}
    else:
        target = {"needs_manual_label": True, "component_type": get_type(node)}
    if dialog:
        target["scope"] = "dialog"
    return target


def extract_navigation_candidates(root: Node) -> List[Dict[str, Any]]:
    dialog_root = detect_dialog_root(root)
    scope = dialog_root or root
    screen = screen_metrics_from_root(root).get("screen_size") or [0, 0]
    screen_area = int(screen[0] or 0) * int(screen[1] or 0) if isinstance(screen, list) and len(screen) == 2 else 0
    nodes = [n for n, _, _ in walk(scope) if is_recordable_clickable_area(n, screen_area)]
    nodes.sort(key=lambda n: (parse_rect(get_attr(n, "bounds"))["top"], parse_rect(get_attr(n, "bounds"))["left"]))
    candidates: List[Dict[str, Any]] = []
    seen = set()
    for node in nodes:
        label = nearest_label(node) or get_text(node)
        if label in {"返回", "返回按钮"}:
            continue
        rect = parse_rect(get_attr(node, "bounds"))
        target = target_from_node(node, dialog=bool(dialog_root))
        sig = json.dumps([target.get("type"), target.get("value"), rect["center"]], ensure_ascii=False)
        if sig in seen:
            continue
        seen.add(sig)
        candidates.append({
            "index": len(candidates) + 1,
            "text": next((t for t in meaningful_texts(node) if is_stable_text_for_navigation(t)), ""),
            "key": get_key(node) if is_stable_key_for_navigation(get_key(node)) else "",
            "type": get_type(node),
            "bounds": get_attr(node, "bounds"),
            "bounds_center": rect["center"],
            "suggested_target": target,
            "clickable_area": True,
        })
    return candidates


def hit_test_full_ui_tree(root: Node, x: int, y: int) -> Optional[Dict[str, Any]]:
    screen = screen_metrics_from_root(root).get("screen_size") or [0, 0]
    screen_area = int(screen[0] or 0) * int(screen[1] or 0) if isinstance(screen, list) and len(screen) == 2 else 0
    hits: List[Tuple[Tuple[int, int], Node]] = []
    for node, depth, _ in walk(root):
        rect = parse_rect(get_attr(node, "bounds"))
        if not rect["valid"] or not (rect["left"] <= x <= rect["right"] and rect["top"] <= y <= rect["bottom"]):
            continue
        if is_recordable_clickable_area(node, screen_area):
            hits.append(((int(rect["area"]), -depth), node))
    if not hits:
        return None
    hits.sort(key=lambda item: item[0])
    return node_semantic_summary(hits[0][1])


def build_semantic_target_from_node(hit_node: Optional[Dict[str, Any]], manual_label: str = "") -> Dict[str, Any]:
    if manual_label:
        return {"type": "manual", "value": manual_label, "key_description": manual_label, "step_prompt": manual_label}
    if not hit_node:
        return {"needs_manual_label": True}
    ctype = str(hit_node.get("component_type") or "")
    text = clean_label(hit_node.get("text") or "")
    key = str(hit_node.get("key") or "")
    if key:
        desc = text or key
        return {"type": "key", "value": key, "key": key, "component_type": ctype, "text": text, "key_description": desc, "step_prompt": desc}
    if text:
        return {"type": "text", "value": text, "component_type": ctype, "text": text, "key_description": text, "step_prompt": text}
    return {"needs_manual_label": True, "component_type": ctype}


def horizontal_target(direction: str) -> Dict[str, Any]:
    return {
        "type": "gesture",
        "value": f"swipe_{direction}",
        "key_description": f"横向{'左' if direction == 'left' else '右'}滑",
        "step_prompt": f"横向{'左' if direction == 'left' else '右'}滑",
        "axis": "horizontal",
    }


def next_horizontal_view_state(graph: Dict[str, Any], base_page: str) -> Dict[str, Any]:
    max_index = 0
    pattern = re.compile(rf"^{re.escape(base_page)}__view_h(\d+)$")
    for name in graph.get("states", {}):
        match = pattern.match(str(name))
        if match:
            max_index = max(max_index, int(match.group(1)))
    page_name = f"{base_page}__view_h{max_index + 1}"
    return {
        "page_name": page_name,
        "page_description": f"{base_page} 横向视图 {max_index + 1}",
        "base_page": base_page,
        "state_type": "local_view",
        "effect": "local_horizontal_view_changed",
    }


def auto_complete_pending_if_needed(*_args: Any, **_kwargs: Any) -> None:
    """Kept for import compatibility; pending completion is handled by web_nav_server."""
    return None
