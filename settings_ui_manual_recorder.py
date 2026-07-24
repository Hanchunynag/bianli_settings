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
import subprocess
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from pydantic import BaseModel


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


def execute_tap(device_id: str, center: List[int]) -> None:
    if not isinstance(center, list) or len(center) != 2:
        raise ValueError("candidate.bounds_center 必须是 [x, y]")
    x, y = map(int, center)
    base = ["hdc", "-t", device_id, "shell"]
    run_hdc_with_fallback([
        base + ["uitest", "uiInput", "click", str(x), str(y)],
        base + ["input", "tap", str(x), str(y)],
    ], f"点击 [{x}, {y}]")


def execute_back(device_id: str) -> None:
    base = ["hdc", "-t", device_id, "shell"]
    run_hdc_with_fallback([
        base + ["uitest", "uiInput", "keyEvent", "Back"],
        base + ["input", "keyevent", "BACK"],
    ], "返回")


def execute_horizontal_swipe(device_id: str, direction: str, metrics: Dict[str, Any]) -> None:
    width, height = map(int, metrics.get("screen_size") or [1080, 2400])
    y = int(height * 0.55)
    if direction not in {"left", "right"}:
        raise ValueError("direction 必须是 left 或 right")
    x1, x2 = (int(width * 0.78), int(width * 0.22)) if direction == "left" else (int(width * 0.22), int(width * 0.78))
    base = ["hdc", "-t", device_id, "shell"]
    run_hdc_with_fallback([
        base + ["uitest", "uiInput", "swipe", str(x1), str(y), str(x2), str(y), "600"],
        base + ["input", "swipe", str(x1), str(y), str(x2), str(y), "600"],
    ], f"横向{direction}滑动")


def execute_gesture_operation(device_id: str, operate: str, center: List[int], metrics: Dict[str, Any]) -> None:
    if not isinstance(center, list) or len(center) != 2:
        raise ValueError("center 必须是 [x, y]")
    x, y = map(int, center)
    width, height = map(int, metrics.get("screen_size") or [1080, 2400])
    dx, dy = max(160, int(width * 0.22)), max(180, int(height * 0.12))
    if operate == "tap":
        return execute_tap(device_id, [x, y])
    gestures = {
        "long_press": (x, y, x, y, "900"),
        "swipe_left": (x + dx // 2, y, x - dx // 2, y, "600"),
        "swipe_right": (x - dx // 2, y, x + dx // 2, y, "600"),
        "swipe_up": (x, y + dy // 2, x, y - dy // 2, "600"),
        "swipe_down": (x, y - dy // 2, x, y + dy // 2, "600"),
    }
    if operate not in gestures:
        raise ValueError("operate 必须是 tap/long_press/swipe_left/swipe_right/swipe_up/swipe_down")
    x1, y1, x2, y2, duration = gestures[operate]
    base = ["hdc", "-t", device_id, "shell"]
    run_hdc_with_fallback([
        base + ["uitest", "uiInput", "swipe", str(x1), str(y1), str(x2), str(y2), duration],
        base + ["input", "swipe", str(x1), str(y1), str(x2), str(y2), duration],
    ], f"{operate} 手势")


def format_ui_tree_json(path: Path) -> None:
    try:
        data = load_json(path)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"✓ 格式化 UI JSON: {path}")
    except Exception as exc:
        print(f"⚠ UI JSON 格式化失败，继续采集流程: {path} ({exc})")


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
            format_ui_tree_json(output_dir / "current_ui_tree.json")
    return True


def capture_artifacts(device_id: str, output_dir: Path) -> bool:
    return capture_device(device_id, output_dir, include_screen=True)


def capture_ui_tree_only(device_id: str, output_dir: Path) -> bool:
    return capture_device(device_id, output_dir, include_screen=False)


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
    # The Web recorder should operate on the page currently visible in the UI
    # tree. Historical session state is still saved for path bookkeeping, but it
    # must not override the detected page shown in the current screenshot.
    state = graph.get("states", {}).get(detected_state.get("page_name", ""))
    if isinstance(state, dict):
        active = dict(state)
        active.update(detected_state)
        return active
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


def is_status_bar_noise_text(text: str) -> bool:
    if text in NOISE_TEXTS:
        return True
    if re.fullmatch(r"\d+", text):
        return True
    if re.fullmatch(r"\d{1,2}:\d{2}", text):
        return True
    if re.fullmatch(r"\d+%", text):
        return True
    if re.fullmatch(r"[\d.]+\s*[KMG]?B/s", text, flags=re.IGNORECASE):
        return True
    return False


def is_page_title_candidate(node: Node) -> bool:
    if get_type(node) != "Text" or not is_visible(node):
        return False
    text = clean_label(get_text(node))
    if not text or is_status_bar_noise_text(text):
        return False
    rect = parse_rect(get_attr(node, "bounds"))
    return bool(rect["valid"] and 100 <= rect["top"] <= 250)


def find_page_title(root: Node) -> str:
    for node, _, _ in walk(root):
        key = get_key(node)
        text = get_text(node)
        if key == "page.title_id" and text:
            return clean_label(text)
    title_candidates: List[Tuple[int, int, str]] = []
    for node, depth, _ in walk(root):
        if is_page_title_candidate(node):
            rect = parse_rect(get_attr(node, "bounds"))
            title_candidates.append((int(rect["top"]), depth, clean_label(get_text(node))))
    return sorted(title_candidates)[0][2] if title_candidates else ""


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
    # A full-screen Dialog may only be the container of a normal settings page,
    # so only dialogs that are clearly smaller than the screen are overlays.
    screen = screen_metrics_from_root(root).get("screen_size") or [0, 0]
    screen_area = (
        int(screen[0] or 0) * int(screen[1] or 0)
        if isinstance(screen, list) and len(screen) == 2
        else 0
    )
    candidates = []
    for node in find_all(
        root,
        lambda n: is_visible(n)
        and "dialog" in (get_type(n) + get_key(n)).lower(),
    ):
        rect = parse_rect(get_attr(node, "bounds"))
        if not rect["valid"]:
            continue
        if screen_area and rect["area"] >= screen_area * 0.85:
            continue
        candidates.append(node)
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda n: parse_rect(get_attr(n, "bounds"))["area"],
    )


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
        "raw_page_name": page_name,
        "page_description": ("弹窗：" if dialog_root else "") + (title or page_name),
        "last_title": title,
        "page_id": page.get("page_id", ""),
        "nav_key": page.get("nav_key", ""),
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


def hit_test_full_ui_tree(
    root: Node,
    x: int,
    y: int,
) -> Optional[Dict[str, Any]]:
    """
    点击命中规则：

    1. 如果点击点位于 ListItem/GridItem 中：
       - 选择最深层 Item；
       - 在该 Item 内找第一个 clickable；
       - 不进入其他嵌套 ListItem/GridItem。

    2. clickable 自身有稳定 key/text 时直接使用；
       否则分别从 clickable 内部补充缺失的 key/text。

    3. 如果点击点不属于任何 Item，则使用原来的坐标命中逻辑。
    """
    item_types = {"ListItem", "GridItem"}

    screen = screen_metrics_from_root(root).get("screen_size") or [0, 0]
    screen_area = (
        int(screen[0] or 0) * int(screen[1] or 0)
        if len(screen) == 2
        else 0
    )

    item_hits = []
    for node, depth, _ in walk(root):
        if get_type(node) not in item_types:
            continue

        rect = parse_rect(get_attr(node, "bounds"))
        if not rect["valid"]:
            continue

        if (
            rect["left"] <= x <= rect["right"]
            and rect["top"] <= y <= rect["bottom"]
        ):
            item_hits.append((depth, rect["area"], node))

    item_node = None
    if item_hits:
        item_hits.sort(key=lambda item: (-item[0], item[1]))
        item_node = item_hits[0][2]

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

            stack.extend(reversed(children(node)))

    if clickable_node is None and item_node is None:
        hits = []

        for node, depth, _ in walk(root):
            rect = parse_rect(get_attr(node, "bounds"))
            if not rect["valid"]:
                continue

            if not (
                rect["left"] <= x <= rect["right"]
                and rect["top"] <= y <= rect["bottom"]
            ):
                continue

            if is_recordable_clickable_area(node, screen_area):
                hits.append((rect["area"], -depth, node))

        if hits:
            hits.sort(key=lambda item: (item[0], item[1]))
            clickable_node = hits[0][2]

    if clickable_node is None:
        return None

    key = get_key(clickable_node)
    text = clean_label(get_text(clickable_node))

    if not is_stable_key_for_navigation(key):
        key = ""

    if not is_stable_text_for_navigation(text):
        text = ""

    if not key or not text:
        stack = list(reversed(children(clickable_node)))

        while stack:
            node = stack.pop()

            if get_type(node) in item_types:
                continue

            if not key:
                child_key = get_key(node)
                if is_stable_key_for_navigation(child_key):
                    key = child_key

            if not text:
                child_text = clean_label(get_text(node))
                if is_stable_text_for_navigation(child_text):
                    text = child_text

            if key and text:
                break

            stack.extend(reversed(children(node)))

    return {
        "component_type": get_type(clickable_node),
        "key": key,
        "text": text,
        "bounds": get_attr(clickable_node, "bounds"),
        "clickable": True,
        "enabled": is_enabled(clickable_node),
        "item_type": get_type(item_node) if item_node else "",
        "item_bounds": get_attr(item_node, "bounds") if item_node else "",
    }


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
    """兼容旧调用；待确认跳转现在由 Web 服务完成。"""


# Contextual page identity
def state_raw_page_name(state: Dict[str, Any], page_name: str = "") -> str:
    raw_name = str(state.get("raw_page_name") or "").strip()
    if raw_name:
        return raw_name
    title = str(state.get("last_title") or "").strip()
    return state_name_from_title(title, overlay=bool(state.get("is_overlay"))) if title else str(state.get("page_name") or page_name or "").strip()


def state_display_title(state: Dict[str, Any], page_name: str = "") -> str:
    title = str(state.get("last_title") or state.get("page_description") or "").strip().removeprefix("弹窗：")
    if title:
        return title
    return "设置" if page_name == "Pages_root" else page_name.removeprefix("Pages_").removeprefix("Overlay_") or "page"


def current_session_page(work_dir: Path) -> str:
    path = current_path_session_path(work_dir)
    if not path.exists():
        return ""
    try:
        return str(load_json(path).get("active_page") or "")
    except Exception:
        return ""


def copy_stored_page_context(detected: Dict[str, Any], stored: Dict[str, Any], page_name: str) -> Dict[str, Any]:
    state = {**detected, "page_name": page_name, "raw_page_name": state_raw_page_name(detected)}
    for key in ("parent_page", "parent_title", "page_description", "state_type", "is_overlay", "overlay_parent", "overlay_title"):
        if key in stored:
            state[key] = stored[key]
    return state


def resolve_detected_state(graph: Dict[str, Any], detected: Dict[str, Any], preferred_page: str = "") -> Dict[str, Any]:
    state = dict(detected)
    raw_name = state["raw_page_name"] = state_raw_page_name(state)
    current_name = str(state.get("page_name") or "")
    if current_name and current_name != raw_name and state.get("parent_page"):
        return state
    states = graph.get("states", {})
    preferred = states.get(preferred_page, {}) if preferred_page else {}
    if (
        isinstance(preferred, dict)
        and preferred
        and states_represent_same_page(state, preferred)
    ):
        return copy_stored_page_context(state, preferred, preferred_page)
    if raw_name == "Pages_root":
        state["page_name"] = raw_name
        return state
    matches = [(str(name), stored) for name, stored in states.items() if isinstance(stored, dict) and state_raw_page_name(stored, str(name)) == raw_name]
    return copy_stored_page_context(state, matches[0][1], matches[0][0]) if len(matches) == 1 else state


def state_signature_texts(state: Dict[str, Any]) -> Set[str]:
    signature = state.get("signature") or {}
    title = clean_label(signature.get("title") or state.get("last_title"))
    return {
        text
        for value in signature.get("texts_any") or []
        if (text := clean_label(value)) and text != title
    }


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


def state_matches_graph_page(graph: Dict[str, Any], detected: Dict[str, Any], page_name: str) -> bool:
    return states_represent_same_page(detected, graph.get("states", {}).get(page_name, {"page_name": page_name}))


def rename_graph_page(graph: Dict[str, Any], old_name: str, new_name: str) -> None:
    states = graph.setdefault("states", {})
    if not old_name or old_name == new_name or old_name not in states or new_name in states:
        return
    state = states.pop(old_name)
    state["page_name"] = new_name
    states[new_name] = state
    for transition in graph.get("transitions", []):
        for field in ("from_page", "to_page"):
            if transition.get(field) == old_name:
                transition[field] = new_name


def contextualize_child_state(
    graph: Dict[str, Any],
    from_page: str,
    detected: Dict[str, Any],
    via_target: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    state = dict(detected)
    raw_name = state_raw_page_name(state)
    parent_title = state_display_title(graph.get("states", {}).get(from_page, {"page_name": from_page}), from_page)
    child_title = state_display_title(state, raw_name)
    target_title = clean_label(
        (via_target or {}).get("step_prompt")
        or (via_target or {}).get("key_description")
        or (via_target or {}).get("text")
        or (via_target or {}).get("value")
        or (via_target or {}).get("key")
    )
    route_title = target_title if child_title == parent_title and target_title else child_title
    contextual_title = f"{parent_title} to{route_title}"
    contextual_name = state_name_from_title(contextual_title, overlay=bool(state.get("is_overlay")))
    matching = []
    for transition in graph.get("transitions", []):
        child_name = str(transition.get("to_page") or "")
        child = graph.get("states", {}).get(child_name, {})
        if transition.get("from_page") == from_page and isinstance(child, dict) and state_raw_page_name(child, child_name) == raw_name:
            matching.append(child_name)
    if len(matching) == 1 and matching[0] != contextual_name:
        rename_graph_page(graph, matching[0], contextual_name)
    existing = graph.get("states", {}).get(contextual_name, {})
    if isinstance(existing, dict) and existing:
        state = copy_stored_page_context(state, existing, contextual_name)
    return {**state, "page_name": contextual_name, "raw_page_name": raw_name, "parent_page": from_page, "parent_title": parent_title, "page_description": contextual_title}

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
    stable = json.dumps({k: candidate.get(k) for k in ("type", "value", "component_type", "key_description", "step_prompt") if candidate.get(k)}, ensure_ascii=False, sort_keys=True)
    return "hash::" + hashlib.sha1(stable.encode("utf-8")).hexdigest()[:12] if stable != "{}" else ""


def candidate_id(candidate: Dict[str, Any]) -> str:
    return str(candidate.get("candidate_id") or candidate_merge_key(candidate))


def candidate_from_auto(c: Dict[str, Any], source: str = "auto_detected") -> Dict[str, Any]:
    target = dict(c.get("suggested_target") or {})
    text = str(c.get("text") or target.get("text") or target.get("key_description") or "")
    item = {
        "candidate_id": "",
        "type": str(target.get("type") or c.get("type") or ""),
        "value": target.get("value") or c.get("key") or text,
        "component_type": str(target.get("component_type") or c.get("type") or ""),
        "text": text,
        "key": str(c.get("key") or (target.get("value") if target.get("type") == "key" else "") or ""),
        "key_description": str(target.get("key_description") or text or target.get("value") or ""),
        "step_prompt": str(target.get("step_prompt") or text or target.get("value") or ""),
        "source": source,
        "transition_ids": list(c.get("transition_ids") or []),
        "operation_ids": list(c.get("operation_ids") or []),
    }
    item["candidate_id"] = candidate_merge_key(item)
    return item


def candidate_from_target(target: Dict[str, Any], source: str = "hit_test_click") -> Dict[str, Any]:
    clean = {k: v for k, v in target.items() if k not in {"point", "normalized_point", "coordinate_hit", "bounds_center", "fallback_locator"}}
    item = {
        "candidate_id": "",
        "type": str(clean.get("type") or ""),
        "value": clean.get("value", ""),
        "component_type": str(clean.get("component_type") or ""),
        "text": str(clean.get("text") or (clean.get("value") if clean.get("type") in {"text", "button_text"} else "") or ""),
        "key": str(clean.get("value") if clean.get("type") in {"key", "button"} else clean.get("key", "") or ""),
        "key_description": str(clean.get("key_description") or clean.get("text") or clean.get("value") or ""),
        "step_prompt": str(clean.get("step_prompt") or clean.get("key_description") or clean.get("value") or ""),
        "source": source,
        "transition_ids": [],
        "operation_ids": [],
    }
    item["candidate_id"] = candidate_merge_key(item)
    return item


def step_target(target: Dict[str, Any]) -> Dict[str, Any]:
    allowed = {
        "type",
        "value",
        "component_type",
        "text",
        "key",
        "key_description",
        "step_prompt",
        "scope",
        "expect",
    }
    clean = {k: v for k, v in target.items() if k in allowed and v not in (None, "", [])}
    if target.get("value") and "key" not in clean and target.get("type") in {"key", "button"}:
        clean["key"] = target.get("value")
    return clean


def transition_step(target: Dict[str, Any], operate: str = "tap") -> Dict[str, Any]:
    return {"operate": operate, "target": step_target(target)}


def transition_steps(transition: Dict[str, Any]) -> List[Dict[str, Any]]:
    steps = transition.get("steps")
    if isinstance(steps, list) and steps:
        return [s for s in steps if isinstance(s, dict)]
    target = transition.get("target") or {}
    operate = str(transition.get("operate") or "tap")
    return [transition_step(target, operate)] if target else []


def transition_steps_label(steps: List[Dict[str, Any]]) -> str:
    labels = []
    for step in steps:
        target = step.get("target") or {}
        label = target.get("step_prompt") or target.get("key_description") or target.get("text") or target.get("value") or target.get("key") or step.get("operate")
        labels.append(str(label))
    return " -> ".join(labels)


def transition_id_for_steps(from_page: str, to_page: str, steps: List[Dict[str, Any]], effect: str = "") -> str:
    payload = {
        "from_page": from_page,
        "to_page": to_page,
        "steps": steps,
        "effect": effect,
    }
    digest = hashlib.sha1(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:12]
    return f"{from_page}__to__{to_page}__steps_{digest}"


def component_summary_from_tree(root_json: Dict[str, Any]) -> List[Dict[str, Any]]:
    """提取可用于对比/合并的稳定控件摘要；不把坐标作为正式 key。"""
    components: List[Dict[str, Any]] = []
    seen = set()
    for node, _, _ in walk(root_json):
        item = node_semantic_summary(node)
        ctype = str(item.get("component_type") or "")
        if ctype in {"Root", "Page", "Navigation", "NavDestination", "RelativeContainer"}:
            continue
        if not ctype or not item.get("enabled", True):
            continue
        if not item.get("key") and not item.get("text"):
            continue
        merge_key = candidate_merge_key(item)
        if not merge_key or merge_key in seen:
            continue
        seen.add(merge_key)
        components.append({
            "text": item.get("text", ""),
            "key": item.get("key", ""),
            "component_type": ctype,
            "clickable": bool(item.get("clickable", False)),
            "enabled": bool(item.get("enabled", True)),
        })
    return components


def components_signature(components: List[Dict[str, Any]]) -> str:
    keys = sorted(candidate_merge_key(c) for c in components if candidate_merge_key(c))
    return hashlib.sha256(json.dumps(keys, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


def page_operation_id(page_name: str, target: Dict[str, Any]) -> str:
    desc = str(target.get("key_description") or target.get("step_prompt") or target.get("value") or "操作")
    safe = "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in desc).strip("_") or "operation"
    digest = hashlib.sha1(json.dumps([page_name, target.get("type"), target.get("value"), desc], ensure_ascii=False).encode("utf-8")).hexdigest()[:8]
    return f"{page_name}__op__{safe}_{digest}"


def next_page_operation_id(state_entry: Dict[str, Any]) -> str:
    """生成当前页面内部顺序编号：operation1、operation2……"""
    operations = state_entry.get("page_operations", []) or []

    max_index = 0

    for operation in operations:
        operation_id = str(operation.get("operation_id") or "")

        if not operation_id.startswith("operation"):
            continue

        number = operation_id[len("operation"):]

        if number.isdigit():
            max_index = max(max_index, int(number))

    return f"operation{max_index + 1}"


def page_variant_id(page_name: str, operation: Dict[str, Any], after_signature: str) -> str:
    payload = [page_name, operation.get("operation_id"), operation.get("effect"), after_signature]
    digest = hashlib.sha1(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:10]
    return f"{page_name}__variant__{digest}"


def upsert_page_variant(state_entry: Dict[str, Any], operation: Dict[str, Any], revealed: List[Dict[str, Any]], hidden: List[Dict[str, Any]]) -> None:
    variant = {
        "variant_id": page_variant_id(str(state_entry.get("page_name") or ""), operation, str(operation.get("after_signature") or "")),
        "created_at": now_iso(),
        "trigger_operation_id": operation.get("operation_id"),
        "trigger": operation.get("target") or {},
        "operate": operation.get("operate") or "tap",
        "effect": operation.get("effect") or "same_page_state_changed",
        "before_signature": operation.get("before_signature"),
        "after_signature": operation.get("after_signature"),
        "revealed_candidates": revealed,
        "hidden_candidates": hidden,
        "is_mutually_exclusive": bool(revealed and hidden),
    }
    variants = state_entry.setdefault("page_variants", [])
    variants[:] = [item for item in variants if item.get("variant_id") != variant["variant_id"]]
    variants.append(variant)


def transition_lookup(graph: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {str(t.get("transition_id")): t for t in graph.get("transitions", []) if t.get("transition_id")}


def all_operation_ids(state: Dict[str, Any]) -> Set[str]:
    return {str(op.get("operation_id")) for op in state.get("page_operations", []) if op.get("operation_id")}


def candidate_record_status(graph: Dict[str, Any], page_name: str, candidate: Dict[str, Any]) -> Dict[str, Any]:
    tids = [tid for tid in candidate.get("transition_ids", []) if tid]
    oids = [oid for oid in candidate.get("operation_ids", []) if oid]
    ctype = str(candidate.get("component_type") or "")
    if ctype in {"Toggle", "Switch", "CheckBox", "Checkbox"}:
        return {"status": "same_page_control", "label": "同页控件，不建议录制为页面跳转", "transition_ids": tids, "operation_ids": oids}
    lookup = transition_lookup(graph)
    valid_tids = [tid for tid in tids if tid in lookup]
    if valid_tids:
        to_page = lookup[valid_tids[0]].get("to_page", "")
        return {"status": "recorded_transition", "label": f"已录制跳转 -> {to_page}", "transition_ids": valid_tids, "operation_ids": oids}
    if oids:
        return {"status": "page_operation", "label": "页面内操作", "transition_ids": [], "operation_ids": oids}
    if ctype == "Button":
        return {"status": "unrecorded", "label": "Button / 未录制", "transition_ids": [], "operation_ids": []}
    return {"status": "unrecorded", "label": "未录制", "transition_ids": [], "operation_ids": []}


def enrich_candidate(graph: Dict[str, Any], page_name: str, candidate: Dict[str, Any]) -> Dict[str, Any]:
    item = dict(candidate)
    item.setdefault("candidate_id", candidate_id(item))
    item.update(candidate_record_status(graph, page_name, item))
    return item


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
    return [enrich_candidate(graph, page_name, c) for c in merged.values()]


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
    item = candidate_from_target(target, source="hit_test_click")
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


def transition_label(t: Dict[str, Any]) -> str:
    steps = transition_steps(t)
    if len(steps) > 1:
        return transition_steps_label(steps)
    target = t.get("target") or {}
    return str(target.get("step_prompt") or target.get("key_description") or target.get("value") or t.get("operate") or "")


def state_title(state: Dict[str, Any], page_name: str) -> str:
    return str(state.get("last_title") or state.get("page_description") or page_name).replace("弹窗：", "")


def build_page_directory(graph: Dict[str, Any]) -> Dict[str, Any]:
    states = graph.get("states", {})
    transitions = [t for t in graph.get("transitions", []) if t.get("from_page") != t.get("to_page")]
    incoming: Dict[str, List[Dict[str, Any]]] = {}
    outgoing: Dict[str, List[Dict[str, Any]]] = {}
    for t in transitions:
        incoming.setdefault(str(t.get("to_page")), []).append(t)
        outgoing.setdefault(str(t.get("from_page")), []).append(t)
    def node(page: str, seen: Set[str]) -> Dict[str, Any]:
        st = states.get(page, {})
        children = []
        for t in outgoing.get(page, []):
            child = str(t.get("to_page"))
            if child in seen or states.get(child, {}).get("is_overlay"):
                continue
            steps = transition_steps(t)
            children.append({**node(child, seen | {child}), "via": {
                "from_page": page,
                "target_label": transition_label(t),
                "transition_id": t.get("transition_id"),
                "step_count": len(steps),
                "steps": steps,
            }})
        return {"page_name": page, "title": state_title(st, page), "children": children}
    flat = []
    for page, st in states.items():
        flat.append({
            "page_name": page,
            "title": state_title(st, page),
            "incoming_count": len(incoming.get(page, [])),
            "outgoing_count": len(outgoing.get(page, [])),
            "candidate_count": len(st.get("merged_candidates", []) or []),
            "operation_count": len(st.get("page_operations", []) or []),
            "continued_capture_count": len(st.get("continued_captures", []) or []),
            "is_overlay": bool(st.get("is_overlay")),
            "state_type": st.get("state_type") or ("overlay" if st.get("is_overlay") else "page"),
        })
    return {"root": "Pages_root", "items": [node("Pages_root", {"Pages_root"})] if "Pages_root" in states else [], "flat_pages": sorted(flat, key=lambda x: x["page_name"])}

def bfs_path(graph: Dict[str, Any], target_page: str) -> Optional[List[Dict[str, Any]]]:
    queue: List[Tuple[str, List[Dict[str, Any]]]] = [("Pages_root", [])]
    seen = {"Pages_root"}
    while queue:
        page, path = queue.pop(0)
        if page == target_page:
            return path
        for t in graph.get("transitions", []):
            if t.get("from_page") != page or t.get("from_page") == t.get("to_page"):
                continue
            nxt = str(t.get("to_page"))
            if nxt in seen:
                continue
            seen.add(nxt)
            queue.append((nxt, path + [t]))
    return None


def find_candidate_center_for_target(root_json: Dict[str, Any], target: Dict[str, Any]) -> Optional[List[int]]:
    candidates = extract_navigation_candidates(root_json)
    want_type = str(target.get("type") or "")
    want_value = str(target.get("value") or target.get("key") or "")
    want_text = str(target.get("text") or target.get("key_description") or target.get("step_prompt") or "")
    for c in candidates:
        ct = c.get("suggested_target") or {}
        values = {str(c.get("key") or ""), str(c.get("text") or ""), str(ct.get("value") or ""), str(ct.get("key_description") or ""), str(ct.get("step_prompt") or "")}
        if (want_value and want_value in values) or (want_text and want_text in values) or (want_type == ct.get("type") and want_value == str(ct.get("value") or "")):
            center = c.get("bounds_center")
            return center if isinstance(center, list) and len(center) == 2 else None
    return None


def validate_page_name_for_rename(page_name: str) -> str:
    page_name = page_name.strip()
    if not page_name:
        raise ValueError("page_name 不能为空")
    if not page_name.startswith("Pages_"):
        raise ValueError("page_name 必须以 Pages_ 开头，例如 Pages_WLAN")
    if any(ch in page_name for ch in ["/", "\\", "\n", "\r", "\t"]):
        raise ValueError("page_name 不能包含路径分隔符或换行符")
    return page_name


def rename_page_references(graph: Dict[str, Any], old_name: str, new_name: str) -> None:
    for transition in graph.get("transitions", []):
        for field in ("from_page", "to_page"):
            if transition.get(field) == old_name:
                transition[field] = new_name
    traversal = graph.setdefault("traversal_config", {})
    if traversal.get("root_page") == old_name:
        traversal["root_page"] = new_name
    if graph.get("main_page_name") == old_name:
        graph["main_page_name"] = new_name

# Web API request contracts
class PointRequest(BaseModel):
    x: int
    y: int
    manual_label: str = ""


class TapPointRequest(PointRequest):
    expect: str = "new_page"
    effect: str = ""


class PageGestureOperationRequest(PointRequest):
    operate: str
    effect: str = ""


class TapCandidateRequest(BaseModel):
    index: int
    expect: str = "new_page"
    effect: str = ""
    manual_label: str = ""


class SwipeHorizontalRequest(BaseModel):
    direction: str


class NavigateToPageRequest(BaseModel):
    page_name: str


class SetActivePageRequest(BaseModel):
    page_name: str


class RenamePageRequest(BaseModel):
    old_page_name: str
    new_page_name: str
    new_title: str = ""


class ConsoleActionRequest(BaseModel):
    action: str
    payload: Optional[Dict[str, Any]] = None


class RecordActionRequest(BaseModel):
    action: str
    payload: Optional[Dict[str, Any]] = None


class DeleteActionRequest(BaseModel):
    target_type: str
    payload: Optional[Dict[str, Any]] = None
    dry_run: bool = True


class DeleteRequest(BaseModel):
    dry_run: bool = True


class DeleteBranchRequest(DeleteRequest):
    transition_id: str
    delete_descendants: bool = True


class DeleteTransitionRequest(DeleteRequest):
    transition_id: str
    delete_orphan_to_state: bool = True


class DeletePageRequest(DeleteRequest):
    page_name: str
    delete_incoming: bool = True
    delete_outgoing: bool = True


class DeleteCandidateRequest(DeleteRequest):
    page_name: str
    candidate_id: str
    delete_linked_transitions: bool = False
    delete_linked_operations: bool = False


class DeletePageOperationRequest(DeleteRequest):
    page_name: str
    operation_id: str
    delete_revealed_candidates: bool = True


class DeleteContinuedCaptureRequest(DeleteRequest):
    page_name: str
    capture_id: str
    delete_candidates_from_capture: bool = True


# Web session and graph maintenance
def warning_for_state(graph: Dict[str, Any], state: Dict[str, Any], has_pending: bool = False) -> str:
    page = state.get("page_name", "")
    if page and page != "Pages_root" and not has_pending and not any(item.get("to_page") == page for item in graph.get("transitions", [])):
        return "当前页面没有 pending transition，且导航图中没有父级来源。说明你可能是手动进入了当前页面，无法自动知道父级页面。请返回父页面后点击候选入口录制。"
    return ""


def ensure_page_consistency(current: Dict[str, Any]) -> None:
    detected = current.get("state", {}).get("page_name")
    active = current.get("active_page") or current.get("active_state", {}).get("page_name")
    if detected and active and detected != active:
        raise ValueError(f"当前检测页面 {detected} 与 active_page {active} 不一致，请先重新采集或确认当前页面状态后再录制。")


def component_changes(before: List[Dict[str, Any]], after: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    before_map = {candidate_merge_key(item): item for item in before if candidate_merge_key(item)}
    after_map = {candidate_merge_key(item): item for item in after if candidate_merge_key(item)}
    return ([item for key, item in after_map.items() if key not in before_map], [item for key, item in before_map.items() if key not in after_map])


def pending_data(work_dir: Path) -> Optional[Dict[str, Any]]:
    path = pending_transition_path(work_dir)
    return load_json(path) if path.exists() else None


def pending_action_chain_path(work_dir: Path) -> Path:
    return navigation_dir(work_dir) / "pending_action_chain.json"


def pending_action_chain(work_dir: Path) -> Optional[Dict[str, Any]]:
    path = pending_action_chain_path(work_dir)
    return load_json(path) if path.exists() else None


def save_pending_action_chain(work_dir: Path, chain: Dict[str, Any]) -> None:
    save_json(chain, pending_action_chain_path(work_dir), "未完成多步骤跳转")


def clear_pending_action_chain(work_dir: Path) -> None:
    path = pending_action_chain_path(work_dir)
    if path.exists():
        path.unlink()


def append_web_history(work_dir: Path, event: Dict[str, Any]) -> None:
    path = navigation_dir(work_dir) / "web_record_history.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"created_at": now_iso(), **event}, ensure_ascii=False) + "\n")



def blank_delete_plan() -> Dict[str, Any]:
    return {"transitions": [], "states": [], "candidates": [], "page_operations": [], "continued_captures": [], "files": [], "warnings": []}


def cleanup_candidate_refs(graph: Dict[str, Any], plan: Dict[str, Any]) -> None:
    tids = {str(t.get("transition_id") or t) for t in plan.get("transitions", [])}
    oids = {str(o.get("operation_id") or o) for o in plan.get("page_operations", [])}
    for page, state in graph.get("states", {}).items():
        kept = []
        for c in state.get("merged_candidates", []) or []:
            before_tid = set(c.get("transition_ids") or [])
            before_oid = set(c.get("operation_ids") or [])
            c["transition_ids"] = [x for x in c.get("transition_ids", []) if x not in tids]
            c["operation_ids"] = [x for x in c.get("operation_ids", []) if x not in oids]
            if before_tid - set(c["transition_ids"]) or before_oid - set(c["operation_ids"]):
                plan["candidates"].append({"page_name": page, "candidate_id": candidate_id(c), "action": "remove_refs"})
            if c.get("source") == "hit_test_click" and not c.get("transition_ids") and not c.get("operation_ids") and (before_tid or before_oid):
                plan["candidates"].append({"page_name": page, "candidate_id": candidate_id(c), "action": "delete_orphan_clicked_candidate"})
                continue
            kept.append(c)
        state["merged_candidates"] = kept


def incoming_count(graph: Dict[str, Any], page: str, ignore_tids: Optional[Set[str]] = None) -> int:
    ignore_tids = ignore_tids or set()
    return sum(1 for t in graph.get("transitions", []) if t.get("to_page") == page and t.get("transition_id") not in ignore_tids)


def collect_descendant_delete(graph: Dict[str, Any], start_page: str, plan: Dict[str, Any], protected_tids: Set[str]) -> None:
    if start_page == "Pages_root" or incoming_count(graph, start_page, protected_tids) > 0:
        return
    if start_page not in plan["states"]:
        plan["states"].append(start_page)
    for op in graph.get("states", {}).get(start_page, {}).get("page_operations", []) or []:
        if op.get("operation_id"):
            plan["page_operations"].append({"page_name": start_page, "operation_id": op.get("operation_id")})
    for cap in graph.get("states", {}).get(start_page, {}).get("continued_captures", []) or []:
        plan["continued_captures"].append({"page_name": start_page, "capture_id": cap.get("capture_id")})
    outgoing = [t for t in graph.get("transitions", []) if t.get("from_page") == start_page]
    for t in outgoing:
        tid = t.get("transition_id")
        if tid and tid not in {x.get("transition_id") for x in plan["transitions"]}:
            plan["transitions"].append(t)
            protected_tids.add(tid)
        collect_descendant_delete(graph, str(t.get("to_page")), plan, protected_tids)


def apply_delete_plan(graph: Dict[str, Any], plan: Dict[str, Any]) -> None:
    tids = {str(t.get("transition_id") or t) for t in plan.get("transitions", [])}
    states = {str(s) for s in plan.get("states", [])}
    graph["transitions"] = [t for t in graph.get("transitions", []) if t.get("transition_id") not in tids and t.get("from_page") not in states and t.get("to_page") not in states]
    for page in states:
        graph.get("states", {}).pop(page, None)
    cleanup_candidate_refs(graph, plan)
    for op_ref in plan.get("page_operations", []):
        page, oid = op_ref.get("page_name"), op_ref.get("operation_id")
        state = graph.get("states", {}).get(page, {})
        state["page_operations"] = [op for op in state.get("page_operations", []) if op.get("operation_id") != oid]
        if plan.get("keep_revealed_candidates"):
            for c in state.get("merged_candidates", []) or []:
                if c.get("requires_operation_id") == oid:
                    c.pop("requires_operation_id", None)
                c["operation_ids"] = [x for x in c.get("operation_ids", []) if x != oid]
        else:
            state["merged_candidates"] = [c for c in state.get("merged_candidates", []) if c.get("requires_operation_id") != oid and c.get("source_operation_id") != oid]
    for cap_ref in plan.get("continued_captures", []):
        page, cid = cap_ref.get("page_name"), cap_ref.get("capture_id")
        state = graph.get("states", {}).get(page, {})
        state["continued_captures"] = [c for c in state.get("continued_captures", []) if c.get("capture_id") != cid]
        if plan.get("keep_capture_candidates"):
            for c in state.get("merged_candidates", []) or []:
                if c.get("source_capture_id") == cid:
                    c.pop("source_capture_id", None)
        else:
            state["merged_candidates"] = [c for c in state.get("merged_candidates", []) if c.get("source_capture_id") != cid or c.get("transition_ids") or c.get("operation_ids")]
    for f in plan.get("files", []):
        try:
            path = Path(f)
            if path.exists():
                path.unlink()
        except Exception as exc:
            plan.setdefault("warnings", []).append(f"删除文件失败 {f}: {exc}")


def prune_graph_after_delete(graph: Dict[str, Any], work_dir: Path) -> Dict[str, Any]:
    warnings: List[str] = []
    states = graph.setdefault("states", {})
    valid_pages = set(states)
    graph["transitions"] = [t for t in graph.get("transitions", []) if t.get("from_page") in valid_pages and t.get("to_page") in valid_pages]
    active_page = ""
    session_path = work_dir / "outputs" / "navigation" / "current_path_session.json"
    if session_path.exists():
        try:
            active_page = str(load_json(session_path).get("active_page") or "")
        except Exception:
            active_page = ""
    for page in list(states.keys()):
        if page != "Pages_root" and page != active_page and incoming_count(graph, page) == 0 and not states[page].get("explicit_keep"):
            states.pop(page, None)
            warnings.append(f"删除孤儿 state：{page}")
    valid_pages = set(states)
    graph["transitions"] = [t for t in graph.get("transitions", []) if t.get("from_page") in valid_pages and t.get("to_page") in valid_pages]
    valid_tids = {t.get("transition_id") for t in graph.get("transitions", [])}
    for page, st in list(states.items()):
        valid_oids = all_operation_ids(st)
        kept = []
        for c in st.get("merged_candidates", []) or []:
            c["transition_ids"] = [tid for tid in c.get("transition_ids", []) if tid in valid_tids]
            c["operation_ids"] = [oid for oid in c.get("operation_ids", []) if oid in valid_oids]
            if c.get("requires_operation_id") and c.get("requires_operation_id") not in valid_oids:
                continue
            kept.append(c)
        st["merged_candidates"] = kept
        caps = []
        for cap in st.get("continued_captures", []) or []:
            f = cap.get("screenshot")
            if f and not Path(f).exists():
                warnings.append(f"续录截图缺失：{f}")
            caps.append(cap)
        st["continued_captures"] = caps
        st["incoming_count"] = incoming_count(graph, page)
        st["outgoing_count"] = sum(1 for t in graph.get("transitions", []) if t.get("from_page") == page)
        st["candidate_count"] = len(st.get("merged_candidates", []) or [])
        st["operation_count"] = len(st.get("page_operations", []) or [])
        st["continued_capture_count"] = len(st.get("continued_captures", []) or [])
    return {"warnings": warnings}


def plan_delete_transition(graph: Dict[str, Any], transition_id_value: str, delete_orphan_to_state: bool) -> Dict[str, Any]:
    plan = blank_delete_plan()
    t = transition_lookup(graph).get(transition_id_value)
    if not t:
        raise ValueError(f"transition 不存在：{transition_id_value}")
    plan["transitions"].append(t)
    if delete_orphan_to_state:
        collect_descendant_delete(graph, str(t.get("to_page")), plan, {transition_id_value})
    return plan
