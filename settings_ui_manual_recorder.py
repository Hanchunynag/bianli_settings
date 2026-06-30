#!/usr/bin/env python3
# -*- coding: utf-8 -*-

r"""
HarmonyOS 设置 UI 采集记录器。

日常入口：
  python settings_tool.py record

本文件负责：
1. 采集或解析 current_ui_tree.json / current_screen.png；
2. 提取当前页面身份、可操作控件、语义组件；
3. 维护页面树、状态机、页面组件树和组件 JSONL 清单；
4. 提供页面树与状态机的删除、清理、重置能力。

每次交互采集时只让用户输入 -1 或数字 0-x：
-1 表示返回父节点并重新选择；
0 表示继续记录当前 active_node_id；
1-x 表示把本次采集页面挂到对应候选分支下面。
"""

import argparse
import json
import re
import shutil
import subprocess
import hashlib
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

Node = Dict[str, Any]

DEFAULT_DEVICE_ID = "68Q0223918000004"
DEFAULT_WORK_DIR = Path(r"D:\hanchunyang_6_3\AItest")
PACKAGE_NAME = "com.huawei.hmos.settings"
MAIN_PAGE_NAME = "com.huawei.hmos.settings.MainAbility"
LIGHT_PATH_KEYS = ["type", "operate", "value", "key_description", "step_prompt", "scope", "expect", "axis"]
NOISE_TEXTS = {"tab_unlock"}
PRIVACY_VALUE_TEXTS = {"加密"}
SENSITIVE_KEY_DISPLAY = "(敏感 key 已隐藏)"


def is_sensitive_key(key: Any) -> bool:
    """HarmonyOS WLAN/蓝牙等动态设备 key 中常带 *，视为敏感定位信息。"""
    return "*" in str(key or "")


def key_fingerprint(key: Any) -> str:
    text = str(key or "")
    if not text:
        return ""
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()[:16]


def sanitize_value(value: Any, key: Any = "") -> str:
    """隐私值脱敏。只要 key 含 *，该控件的状态 value 不展示。"""
    text = str(value or "").strip()
    if is_sensitive_key(key):
        return ""
    if text in PRIVACY_VALUE_TEXTS:
        return ""
    return text


def storage_key(key: Any) -> str:
    """写入输出 JSON 的 key。敏感 key 不落盘。"""
    text = str(key or "")
    return "" if is_sensitive_key(text) else text


def display_key(ctrl_or_key: Any) -> str:
    """终端和可读摘要中使用的 key 文本。"""
    if isinstance(ctrl_or_key, dict):
        if ctrl_or_key.get("sensitive_key"):
            return SENSITIVE_KEY_DISPLAY
        key = str(ctrl_or_key.get("key") or "")
    else:
        key = str(ctrl_or_key or "")
    if is_sensitive_key(key):
        return SENSITIVE_KEY_DISPLAY
    return key or "(无 key)"


def scrub_control_privacy(ctrl: Dict[str, Any]) -> Dict[str, Any]:
    """清理已经写入数据库的历史敏感字段。"""
    key = str(ctrl.get("key") or "")
    # 旧版本可能已经把敏感 key 替换为空，但保留了 key_fingerprint。
    # 因此这里也把 key_fingerprint 作为敏感标记，避免历史 WiFi/蓝牙动态设备项继续展示。
    sensitive = bool(ctrl.get("sensitive_key")) or bool(ctrl.get("key_fingerprint")) or is_sensitive_key(key)
    if sensitive:
        if key and not ctrl.get("key_fingerprint"):
            ctrl["key_fingerprint"] = key_fingerprint(key)
        ctrl["sensitive_key"] = True
        ctrl["key"] = ""
        ctrl["value"] = ""
        if ctrl.get("bounds_center"):
            ctrl["locator"] = "bounds_center"
    else:
        ctrl["value"] = sanitize_value(ctrl.get("value", ""), key)
    return ctrl


def scrub_index_privacy(index: Dict[str, Any]) -> None:
    """保存前统一清理 settings_nodes_index.json 里的历史敏感数据。"""
    for page in index.get("pages", {}).values():
        for ctrl in page.get("controls", {}).values():
            scrub_control_privacy(ctrl)
        for comp in page.get("components", {}).values():
            if comp.get("sensitive_key") or comp.get("sensitive_context"):
                raw_key = str(comp.get("key") or "")
                if raw_key and not comp.get("key_fingerprint"):
                    comp["key_fingerprint"] = key_fingerprint(raw_key)
                comp["key"] = ""
                comp["text"] = ""
                comp["name"] = comp.get("type") or "敏感上下文组件"
                comp["value"] = ""


def is_sensitive_entry_control(ctrl: Dict[str, Any]) -> bool:
    """用户自己的 WLAN/蓝牙等动态设备项：作为入口候选时直接丢弃。

    这类控件的 key 中通常包含 *。新采集时可直接判断 key；旧数据在脱敏后
    key 已为空，但会保留 sensitive_key/key_fingerprint，因此也要一并识别。
    """
    if str(ctrl.get("record_group") or "") != "entry_controls":
        return False
    return bool(ctrl.get("sensitive_key")) or bool(ctrl.get("key_fingerprint")) or is_sensitive_key(ctrl.get("key"))


def drop_sensitive_entry_controls(index: Dict[str, Any]) -> None:
    """从历史索引中删除已经写入的敏感入口项，避免旧 WiFi 信息继续出现在树和候选列表里。"""
    for page in index.get("pages", {}).values():
        controls = page.get("controls", {})
        if not isinstance(controls, dict):
            continue
        for uid in list(controls.keys()):
            ctrl = controls.get(uid, {})
            if isinstance(ctrl, dict) and is_sensitive_entry_control(ctrl):
                del controls[uid]


def has_sensitive_key_in_subtree(node: Node) -> bool:
    """只要当前节点或子节点出现含 * 的 key，就认为它是动态隐私设备项。"""
    return any(is_sensitive_key(get_key(n)) for n, _, _ in walk(node))


# ============================================================
# 基础 I/O
# ============================================================

def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def run_id() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def load_json(path: Path) -> Any:
    with open(path, "r", encoding="utf-8-sig") as f:
        return json.load(f)


def save_json(data: Any, path: Path, desc: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"✓ {desc}: {path}")


def save_text(text: str, path: Path, desc: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text.rstrip() + "\n")
    print(f"✓ {desc}: {path}")


def run_cmd(cmd: List[str], cwd: Optional[str] = None, timeout: int = 30) -> bool:
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd)
        if result.returncode != 0:
            print(f"✗ 命令失败: {' '.join(cmd)}")
            if result.stdout.strip():
                print(f"  stdout: {result.stdout.strip()}")
            if result.stderr.strip():
                print(f"  stderr: {result.stderr.strip()}")
            return False
        return True
    except Exception as e:
        print(f"✗ 执行异常: {e}")
        return False


def capture_artifacts(device_id: str, output_dir: Path) -> bool:
    output_dir.mkdir(parents=True, exist_ok=True)
    if not run_cmd(["hdc", "version"]):
        print("✗ hdc 不可用，请确认 hdc 已加入 PATH")
        return False
    print("✓ hdc 可用")

    base = ["hdc", "-t", device_id]
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
        print(f"✓ {name} 成功")
        if name == "拉取 JSON":
            try:
                raw_json_path = output_dir / "current_ui_tree.json"
                pretty_json_path = output_dir / "current_ui_tree_pretty.json"
                save_json(load_json(raw_json_path), pretty_json_path, "格式化 UI 树 JSON")
            except Exception as e:
                print(f"✗ 生成格式化 UI 树 JSON 失败: {e}")
                return False
    return True


# ============================================================
# 轻量导航状态图 / 路径录制
# ============================================================

def navigation_dir(work_dir: Path) -> Path:
    return work_dir / "outputs" / "navigation"


def navigation_graph_path(work_dir: Path) -> Path:
    return navigation_dir(work_dir) / "settings_navigation_graph.json"


def pending_transition_path(work_dir: Path) -> Path:
    return navigation_dir(work_dir) / "current_pending_transition.json"


def path_cases_path(work_dir: Path) -> Path:
    return navigation_dir(work_dir) / "settings_path_cases.json"


def empty_navigation_graph() -> Dict[str, Any]:
    return {
        "package_name": PACKAGE_NAME,
        "main_page_name": MAIN_PAGE_NAME,
        "updated_at": now_iso(),
        "states": {},
        "transitions": [],
    }


def load_navigation_graph(work_dir: Path) -> Dict[str, Any]:
    path = navigation_graph_path(work_dir)
    if path.exists():
        graph = load_json(path)
        graph.setdefault("package_name", PACKAGE_NAME)
        graph.setdefault("main_page_name", MAIN_PAGE_NAME)
        graph.setdefault("states", {})
        graph.setdefault("transitions", [])
        return graph
    return empty_navigation_graph()


def save_navigation_graph(graph: Dict[str, Any], work_dir: Path) -> None:
    graph["updated_at"] = now_iso()
    save_json(graph, navigation_graph_path(work_dir), "轻量导航状态图")


def is_stable_key(key: Any) -> bool:
    text = str(key or "").strip()
    if not text or "*" in text or "AvailableDeviceGroup" in text:
        return False
    if re.search(r"\d{8,}", text):
        return False
    if re.fullmatch(r"[0-9a-fA-F\-]{16,}", text):
        return False
    return True


def is_stable_text(text: Any) -> bool:
    value = clean_label(str(text or ""))
    if not value:
        return False
    if value in {"WLAN", "蓝牙", "移动网络", "隐私和安全", "流量管理"}:
        return True
    if re.fullmatch(r"\d+(\.\d+)?", value):
        return False
    if re.fullmatch(r"\d{1,2}:\d{2}(:\d{2})?", value):
        return False
    if re.fullmatch(r"[\d.]+\s*(KB|MB|GB|TB|B|K|M|G)(/s)?", value, flags=re.I):
        return False
    if len(value) >= 24 and re.fullmatch(r"[0-9A-Za-z_\-]+", value):
        return False
    return True


def detect_dialog_root(root: Node) -> Optional[Node]:
    candidates = find_all(
        root,
        lambda n: is_visible(n) and any(word in get_type(n).lower() for word in ["dialog", "popup"])
    )
    if not candidates:
        candidates = find_all(root, lambda n: is_visible(n) and any(word in get_key(n).lower() for word in ["dialog", "popup"]))
    if not candidates:
        return None
    return max(candidates, key=lambda n: parse_rect(get_attr(n, "bounds"))["area"])


def state_name_from_title(title: str, overlay: bool = False) -> str:
    title = clean_label(title)
    prefix = "Overlay" if overlay else "Pages"
    if title == "设置":
        return "Pages_root"
    segment = tree_segment(title)
    if not segment:
        digest = hashlib.sha1(title.encode("utf-8", errors="ignore")).hexdigest()[:8]
        segment = f"unknown_{digest}"
    return f"{prefix}_{segment}"


def build_navigation_state(root: Node) -> Dict[str, Any]:
    page = page_identity(root)
    dialog_root = detect_dialog_root(root)
    scope_root = dialog_root or root
    title = page.get("title") or (nearest_label(scope_root) if dialog_root else "")
    page_name = state_name_from_title(title or page.get("page_id", "page"), overlay=bool(dialog_root))
    texts = [t for t in meaningful_texts(scope_root) if is_stable_text(t)][:8]
    return {
        "page_name": page_name,
        "page_description": ("弹窗：" if dialog_root else "") + (title or page_name),
        "last_title": title,
        "signature": {
            "title": title,
            "texts_any": texts,
        },
    }


def target_from_node(node: Node, dialog: bool = False) -> Dict[str, Any]:
    text = next((t for t in meaningful_texts(node) if is_stable_text(t)), "")
    key = get_key(node)
    label = text or nearest_label(node)
    rect = parse_rect(get_attr(node, "bounds"))
    if text:
        target = {"type": "text", "value": text, "key_description": text, "step_prompt": text}
    elif is_stable_key(key):
        desc = label or key
        target = {"type": "key", "value": key, "key_description": desc, "step_prompt": desc}
    else:
        desc = label or "未命名控件"
        target = {"type": "bounds", "value": rect["center"], "key_description": desc, "step_prompt": "点击指定位置" if not label else desc}
    if dialog:
        target["scope"] = "dialog"
    return target


def extract_navigation_candidates(root_json: Node) -> List[Dict[str, Any]]:
    dialog_root = detect_dialog_root(root_json)
    scope_root = dialog_root or find_content_root(root_json)
    candidates: List[Dict[str, Any]] = []
    preferred = {"Row", "ListItem", "Button", "Column"}

    def valid(n: Node) -> bool:
        if not (to_bool(attrs(n).get("clickable", False)) and is_visible(n) and is_enabled(n)):
            return False
        if get_type(n) not in preferred:
            return False
        if has_sensitive_key_in_subtree(n) or not is_stable_key(get_key(n)) and not any(is_stable_text(t) for t in meaningful_texts(n)):
            # 没有稳定 text/key 时仍允许 bounds 兜底，但动态敏感项必须排除。
            if has_sensitive_key_in_subtree(n):
                return False
        label = nearest_label(n) or get_text(n)
        if label in {"返回", "返回按钮"}:
            return False
        rect = parse_rect(get_attr(n, "bounds"))
        return rect["valid"] and rect["area"] > 0

    seen = set()
    nodes = find_all(scope_root, valid)
    nodes.sort(key=lambda n: (parse_rect(get_attr(n, "bounds"))["top"], parse_rect(get_attr(n, "bounds"))["left"]))
    for n in nodes:
        rect = parse_rect(get_attr(n, "bounds"))
        target = target_from_node(n, dialog=bool(dialog_root))
        sig = json.dumps([target.get("type"), target.get("value"), rect["center"]], ensure_ascii=False)
        if sig in seen:
            continue
        seen.add(sig)
        candidates.append({
            "index": len(candidates) + 1,
            "text": next((t for t in meaningful_texts(n) if is_stable_text(t)), ""),
            "key": get_key(n) if is_stable_key(get_key(n)) else "",
            "type": get_type(n),
            "bounds": get_attr(n, "bounds"),
            "bounds_center": rect["center"],
            "suggested_target": target,
        })
    return candidates


def transition_id(from_page: str, operate: str, to_page: str) -> str:
    return f"{from_page}__{operate}__{to_page}"


def add_transition(graph: Dict[str, Any], transition: Dict[str, Any]) -> None:
    tid = transition.get("transition_id")
    graph["transitions"] = [t for t in graph.get("transitions", []) if t.get("transition_id") != tid]
    graph.setdefault("transitions", []).append(transition)


def light_step_from_transition(t: Dict[str, Any]) -> Dict[str, Any]:
    target = dict(t.get("target") or {})
    target["operate"] = t.get("operate", target.get("operate", "tap"))
    return {k: target[k] for k in LIGHT_PATH_KEYS if k in target}


def horizontal_target(direction: str) -> Dict[str, Any]:
    left = direction == "left"
    return {
        "type": "special",
        "value": "横向列表向左滑动一次" if left else "横向列表向右滑动一次",
        "key_description": "横向列表",
        "step_prompt": "在横向列表中向左滑动一次" if left else "在横向列表中向右滑动一次",
        "axis": "horizontal",
        "scope": "local_container",
    }


# ============================================================
# UI 树基础工具
# ============================================================

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
    for c in children(node):
        yield from walk(c, depth + 1, node)


def find_all(root: Node, pred: Callable[[Node], bool]) -> List[Node]:
    return [n for n, _, _ in walk(root) if pred(n)]


def any_desc(root: Node, pred: Callable[[Node], bool]) -> bool:
    return any(pred(n) for n, _, _ in walk(root))


def parse_rect(bounds: Any) -> Dict[str, Any]:
    empty = {
        "left": 0, "top": 0, "right": 0, "bottom": 0,
        "width": 0, "height": 0, "center": None, "area": 0, "valid": False,
    }
    if not bounds:
        return empty
    try:
        nums = re.findall(r"-?\d+", str(bounds))
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
    except Exception:
        return empty


def screen_metrics_from_root(root: Node) -> Dict[str, Any]:
    """从 UI 根节点 bounds 推断当前 dump 的屏幕坐标系。"""
    root_bounds = get_attr(root, "bounds")
    rect = parse_rect(root_bounds)
    if not rect["valid"]:
        max_right = 0
        max_bottom = 0
        for node, _, _ in walk(root):
            node_rect = parse_rect(get_attr(node, "bounds"))
            if node_rect["valid"]:
                max_right = max(max_right, int(node_rect["right"]))
                max_bottom = max(max_bottom, int(node_rect["bottom"]))
        if max_right > 0 and max_bottom > 0:
            rect = {
                "left": 0, "top": 0, "right": max_right, "bottom": max_bottom,
                "width": max_right, "height": max_bottom,
                "center": [max_right // 2, max_bottom // 2],
                "area": max_right * max_bottom,
                "valid": True,
            }
            root_bounds = f"[0,0][{max_right},{max_bottom}]"

    screen_size = [int(rect["width"]), int(rect["height"])] if rect["valid"] else None
    return {
        "coordinate_space": "screen_absolute_px",
        "screen_size": screen_size,
        "root_bounds": root_bounds if rect["valid"] else "",
    }


def normalized_center(bounds_center: Any, screen_size: Any) -> Optional[List[float]]:
    if not isinstance(bounds_center, list) or len(bounds_center) != 2:
        return None
    if not isinstance(screen_size, list) or len(screen_size) != 2:
        return None
    try:
        width = float(screen_size[0])
        height = float(screen_size[1])
        if width <= 0 or height <= 0:
            return None
        return [round(float(bounds_center[0]) / width, 6), round(float(bounds_center[1]) / height, 6)]
    except Exception:
        return None


def clean_label(text: str) -> str:
    text = str(text or "").strip()
    if not text:
        return ""
    parts = [p.strip() for p in re.split(r"[,，]", text) if p.strip()]
    parts = [p for p in parts if p not in NOISE_TEXTS]
    return parts[0] if parts else text


def meaningful_texts(root: Node, include_numeric: bool = False) -> List[str]:
    out: List[str] = []

    def rec(n: Node) -> None:
        text = clean_label(get_text(n))
        if text and text not in NOISE_TEXTS:
            if include_numeric or not re.fullmatch(r"\d+(\.\d+)?", text):
                if text not in out:
                    out.append(text)
        for c in children(n):
            rec(c)

    rec(root)
    return out


def annotate(root: Node) -> None:
    """给每个节点加 parent/type_path，方便后续人工调试。"""
    def rec(node: Node, parent: Optional[Node], type_path: str, index_path: str) -> None:
        node["__parent"] = parent
        node["__type_path"] = type_path
        node["__index_path"] = index_path
        counts: Dict[str, int] = defaultdict(int)
        for i, c in enumerate(children(node)):
            t = get_type(c) or "Unknown"
            idx = counts[t]
            counts[t] += 1
            rec(c, node, f"{type_path}/{t}[{idx}]", f"{index_path}.{i}")
    rec(root, None, "/root", "0")


def type_path(node: Node) -> str:
    return str(node.get("__type_path", ""))


def index_path(node: Node) -> str:
    return str(node.get("__index_path", ""))


def parent_chain(node: Node, limit: int = 6) -> List[Node]:
    chain = []
    cur = node.get("__parent")
    while isinstance(cur, dict) and len(chain) < limit:
        chain.append(cur)
        cur = cur.get("__parent")
    return chain


# ============================================================
# 当前页身份与控件提取：只保留通用规则
# ============================================================

def find_page_title(root: Node) -> str:
    title_nodes = find_all(
        root,
        lambda n: get_type(n) == "Text" and get_key(n).endswith("title_id") and get_text(n),
    )
    if title_nodes:
        return clean_label(get_text(title_nodes[0]))

    for title_bar in find_all(root, lambda n: "TitleBar" in get_type(n)):
        texts = meaningful_texts(title_bar)
        if texts:
            return texts[0]
    return ""


def find_nav_destination_key(root: Node) -> str:
    candidates: List[Tuple[int, str]] = []
    for n, depth, _ in walk(root):
        if get_type(n) == "NavDestination" and is_visible(n):
            key = get_key(n)
            if key:
                candidates.append((depth, key))
    if not candidates:
        return ""
    candidates.sort(key=lambda x: -x[0])
    return candidates[0][1]


def page_identity(root: Node) -> Dict[str, str]:
    title = find_page_title(root)
    nav_key = find_nav_destination_key(root)
    page_id = nav_key or (f"title::{title}" if title else "unknown::page")
    return {"page_id": page_id, "title": title or page_id, "nav_key": nav_key}


def find_content_root(root: Node) -> Node:
    candidates = find_all(root, lambda n: get_type(n) == "NavDestinationContent" and is_visible(n))
    if not candidates:
        return root
    return max(candidates, key=lambda n: parse_rect(get_attr(n, "bounds"))["area"])


def app_content_roots(root: Node) -> List[Node]:
    """返回属于当前 App 页面本身的根节点，排除状态栏等系统 WindowScene。"""
    top_children = children(root)
    app_roots = [
        child for child in top_children
        if get_type(child) != "WindowScene" and not str(get_key(child)).startswith("session")
    ]
    if app_roots:
        return app_roots
    return [root]


def nearest_label(node: Node) -> str:
    texts = meaningful_texts(node)
    if texts:
        return texts[0]
    for anc in parent_chain(node):
        if get_type(anc) in {"Row", "Column", "ListItem", "Button"}:
            texts = meaningful_texts(anc)
            if texts:
                return texts[0]
    return ""


def make_control(name: str, node: Node, kind: str, record_group: str, value: str = "") -> Dict[str, Any]:
    rect = parse_rect(get_attr(node, "bounds"))
    raw_key = get_key(node)
    sensitive = is_sensitive_key(raw_key)
    stored_key = storage_key(raw_key)
    return {
        "name": name,
        "text": name,
        "kind": kind,
        "record_group": record_group,
        "value": sanitize_value(value, raw_key),
        "key": stored_key,
        "key_fingerprint": key_fingerprint(raw_key) if sensitive else "",
        "sensitive_key": sensitive,
        "type": get_type(node),
        "type_path": type_path(node),
        "index_path": index_path(node),
        "bounds": get_attr(node, "bounds"),
        "bounds_center": rect["center"],
        "locator": "bounds_center" if sensitive else ("key" if stored_key else "bounds_center"),
        "navigates": False if record_group in {"operation_controls", "nav_controls"} else "unknown",
    }


def target_uid(page_id: str, c: Dict[str, Any]) -> str:
    if c.get("key"):
        return f"{page_id}::key::{c['key']}"
    if c.get("key_fingerprint"):
        return f"{page_id}::keyhash::{c['key_fingerprint']}"
    if c.get("name"):
        return f"{page_id}::name::{c.get('kind')}::{c['name']}"
    return f"{page_id}::bounds::{c.get('kind')}::{c.get('bounds')}"


def append_unique(controls: List[Dict[str, Any]], c: Dict[str, Any]) -> None:
    sig = (c.get("key") or "", c.get("kind"), c.get("name"), c.get("bounds"))
    for old in controls:
        old_sig = (old.get("key") or "", old.get("kind"), old.get("name"), old.get("bounds"))
        if old_sig == sig:
            return
    controls.append(c)


def label_for_toggle(toggle: Node, scope: Node) -> str:
    tb = parse_rect(get_attr(toggle, "bounds"))
    best = ""
    best_dist = 999999
    if tb["valid"]:
        ty = (tb["top"] + tb["bottom"]) // 2
        tleft = tb["left"]
        search_scopes = parent_chain(toggle, limit=4) + [scope]
        for s in search_scopes:
            for text_node in find_all(s, lambda n: get_type(n) == "Text" and get_text(n)):
                rb = parse_rect(get_attr(text_node, "bounds"))
                if not rb["valid"]:
                    continue
                if rb["left"] > tleft:
                    continue
                dy = abs(((rb["top"] + rb["bottom"]) // 2) - ty)
                if dy < best_dist:
                    best = clean_label(get_text(text_node))
                    best_dist = dy
            if best and best_dist < 120:
                break
    return best or "开关"


def is_toggle_like(node: Node) -> bool:
    """识别通用开关。保守扩展，补充部分自定义 Switch/Toggle。"""
    if not is_visible(node) or not is_enabled(node):
        return False
    t = get_type(node)
    if t in {"Toggle", "Switch", "CheckBox"}:
        return True
    if to_bool(attrs(node).get("checkable", False)) and t != "Radio":
        return True
    key = get_key(node).lower()
    if not ("switch" in key or "toggle" in key):
        return False
    rect = parse_rect(get_attr(node, "bounds"))
    return rect["valid"] and 40 <= rect["width"] <= 180 and 30 <= rect["height"] <= 120


def extract_controls(root: Node) -> List[Dict[str, Any]]:
    """最小通用版控件提取：不做 WLAN/蓝牙专用适配，不做父子自动推断。"""
    content = find_content_root(root)
    controls: List[Dict[str, Any]] = []

    # 标题栏按钮：返回、菜单等。
    for btn in find_all(root, lambda n: get_type(n) == "Button" and to_bool(attrs(n).get("clickable", False)) and is_visible(n)):
        p = type_path(btn)
        if "/TitleBar" not in p:
            continue
        rect = parse_rect(get_attr(btn, "bounds"))
        if not rect["valid"]:
            continue
        if rect["left"] < 220:
            name = "返回按钮"
            kind = "nav_back"
        else:
            name = nearest_label(btn) or "标题栏按钮"
            kind = "titlebar_button"
        append_unique(controls, make_control(name, btn, kind, "nav_controls"))

    # Slider。
    for slider in find_all(content, lambda n: get_type(n) == "Slider" and is_visible(n)):
        key = get_key(slider).lower()
        name = "亮度滑条" if "brightness" in key else "滑条"
        append_unique(controls, make_control(name, slider, "slider", "operation_controls", value=get_text(slider)))

    # Toggle / Switch / checkable 非 Radio。
    # 包含 WLAN/蓝牙页面顶部总开关这类自定义开关。
    for toggle in find_all(content, is_toggle_like):
        state = "开启" if to_bool(attrs(toggle).get("checked", False)) else "关闭"
        append_unique(controls, make_control(label_for_toggle(toggle, content), toggle, "toggle", "operation_controls", value=state))

    # 页面内容里的普通按钮，例如“更多”。标题栏按钮已在 nav_controls 中单独处理。
    for btn in find_all(content, lambda n: get_type(n) == "Button" and to_bool(attrs(n).get("clickable", False)) and is_visible(n) and is_enabled(n)):
        if "/TitleBar" in type_path(btn):
            continue
        name = nearest_label(btn) or clean_label(get_text(btn)) or "按钮"
        append_unique(controls, make_control(name, btn, "button", "operation_controls", value=get_text(btn)))

    # 通用页面入口：Row/ListItem/Column，且不包含操作控件。
    def is_entry(n: Node) -> bool:
        if get_type(n) not in {"Row", "ListItem", "Column"}:
            return False
        if not (to_bool(attrs(n).get("clickable", False)) and is_visible(n) and is_enabled(n)):
            return False
        # WLAN/蓝牙扫描出的用户设备项通常带有动态敏感 key（包含 *）。
        # 这类项不是稳定的设置入口，不进入 entry_controls，也不进入后续树选择。
        if has_sensitive_key_in_subtree(n):
            return False
        if any_desc(n, lambda x: get_type(x) in {"Slider", "Toggle", "Switch", "Radio"}):
            return False
        rect = parse_rect(get_attr(n, "bounds"))
        if not rect["valid"]:
            return False
        if rect["width"] < 300 or not (50 <= rect["height"] <= 420):
            return False
        texts = meaningful_texts(n)
        return bool(texts or get_key(n))

    for row in find_all(content, is_entry):
        texts = meaningful_texts(row)
        name = texts[0] if texts else (get_key(row) or "未命名入口")
        value = texts[1] if len(texts) > 1 else ""
        append_unique(controls, make_control(name, row, "row", "entry_controls", value=value))

    # 去掉嵌套重复：如果同名 image/子 row 在父 row 内，当前最小版不单独保留 Image。
    # 兜底：即使上游规则漏掉了含 * key 的动态 WiFi/蓝牙入口，也在这里直接丢弃。
    controls = [c for c in controls if c.get("name") and not is_sensitive_entry_control(c)]
    return controls


def is_sensitive_node_context(node: Node) -> bool:
    """判断原始 UI 节点是否处于敏感动态设备上下文。"""
    if is_sensitive_key(get_key(node)) or has_sensitive_key_in_subtree(node):
        return True
    return any(is_sensitive_key(get_key(parent)) for parent in parent_chain(node, limit=8))


def component_identity(page_id: str, source: str, item: Dict[str, Any]) -> Tuple[str, str, str]:
    """给组件生成稳定 ID，并返回 identity 策略和值。"""
    key = str(item.get("key") or "")
    key_hash = str(item.get("key_fingerprint") or "")
    text = clean_label(str(item.get("text") or item.get("name") or ""))
    kind = str(item.get("kind") or "")
    node_type = str(item.get("type") or "")
    type_path_value = str(item.get("type_path") or "")
    index_path_value = str(item.get("index_path") or "")
    bounds = str(item.get("bounds") or "")

    if key and is_stable_key(key):
        strategy = "stable_key"
        value = key
    elif key_hash:
        strategy = "key_fingerprint"
        value = key_hash
    elif text and is_stable_text(text):
        strategy = "stable_text"
        value = f"{kind}|{node_type}|{text}"
    elif type_path_value:
        strategy = "type_path"
        value = type_path_value
    elif index_path_value:
        strategy = "index_path"
        value = index_path_value
    else:
        strategy = "bounds"
        value = bounds

    seed = f"{page_id}|{source}|{strategy}|{value}"
    component_id = "cmp_" + hashlib.sha256(seed.encode("utf-8", errors="ignore")).hexdigest()[:16]
    return component_id, strategy, value


def component_record_from_control(page_id: str, ctrl: Dict[str, Any], order: int, screen_metrics: Dict[str, Any]) -> Dict[str, Any]:
    item = scrub_control_privacy(dict(ctrl))
    item["source"] = "recognized_control"
    item["observed_order"] = order
    component_id, strategy, value = component_identity(page_id, item["source"], item)
    screen_size = screen_metrics.get("screen_size")
    center = item.get("bounds_center")
    return {
        "component_id": component_id,
        "page_id": page_id,
        "source": item["source"],
        "identity_strategy": strategy,
        "identity_value": value,
        "observed_order": order,
        "name": item.get("name", ""),
        "text": item.get("text", ""),
        "kind": item.get("kind", ""),
        "record_group": item.get("record_group", ""),
        "value": sanitize_value(item.get("value", ""), item.get("key", "")),
        "key": item.get("key", ""),
        "key_fingerprint": item.get("key_fingerprint", ""),
        "sensitive_key": item.get("sensitive_key", False),
        "type": item.get("type", ""),
        "type_path": item.get("type_path", ""),
        "index_path": item.get("index_path", ""),
        "bounds": item.get("bounds", ""),
        "bounds_center": center,
        "coordinate_space": screen_metrics.get("coordinate_space", "screen_absolute_px"),
        "screen_size": screen_size,
        "normalized_center": normalized_center(center, screen_size),
        "locator": item.get("locator", ""),
        "clickable": item.get("navigates") == "unknown" or item.get("record_group") in {"operation_controls", "nav_controls"},
        "enabled": True,
        "visible": True,
        "navigates": item.get("navigates", ""),
    }


def component_kind_from_node(node: Node) -> str:
    node_type = get_type(node)
    if node_type in {"Toggle", "Switch", "CheckBox"} or is_toggle_like(node):
        return "toggle"
    if node_type == "Slider":
        return "slider"
    if node_type == "Button":
        return "button"
    if node_type == "Text":
        return "text"
    if to_bool(attrs(node).get("clickable", False)):
        return "clickable_container"
    return "ui_node"


def should_record_component_node(node: Node) -> bool:
    if not is_visible(node):
        return False
    node_type = get_type(node)
    if not node_type:
        return False
    rect = parse_rect(get_attr(node, "bounds"))
    if not rect["valid"]:
        return False
    if rect["width"] <= 1 or rect["height"] <= 1:
        return False
    if get_text(node) or get_key(node):
        return True
    if to_bool(attrs(node).get("clickable", False)) or to_bool(attrs(node).get("checkable", False)):
        return True
    return node_type in {"Button", "Text", "Image", "Toggle", "Switch", "Slider", "ListItem", "Row", "Column"}


def component_record_from_node(page_id: str, node: Node, order: int, depth: int, screen_metrics: Dict[str, Any]) -> Dict[str, Any]:
    rect = parse_rect(get_attr(node, "bounds"))
    screen_size = screen_metrics.get("screen_size")
    raw_key = get_key(node)
    sensitive_context = is_sensitive_node_context(node)
    sensitive_key = is_sensitive_key(raw_key)
    key = "" if sensitive_context else storage_key(raw_key)
    text = "" if sensitive_context else clean_label(get_text(node))
    name = text or (key if key else get_type(node))
    checked_value = attrs(node).get("checked")
    checked_is_meaningful = get_type(node) in {"Toggle", "Switch", "CheckBox"} or to_bool(attrs(node).get("checkable", False))
    value = ""
    if checked_is_meaningful and checked_value not in (None, "") and not sensitive_context:
        value = "开启" if to_bool(checked_value) else "关闭"

    item = {
        "source": "ui_node",
        "name": name,
        "text": text,
        "kind": component_kind_from_node(node),
        "record_group": "ui_tree_components",
        "value": value,
        "key": key,
        "key_fingerprint": key_fingerprint(raw_key) if (sensitive_key or sensitive_context) and raw_key else "",
        "sensitive_key": sensitive_key,
        "sensitive_context": sensitive_context,
        "type": get_type(node),
        "type_path": type_path(node),
        "index_path": index_path(node),
        "bounds": get_attr(node, "bounds"),
        "bounds_center": rect["center"],
        "coordinate_space": screen_metrics.get("coordinate_space", "screen_absolute_px"),
        "screen_size": screen_size,
        "normalized_center": normalized_center(rect["center"], screen_size),
        "locator": "key" if key else "bounds_center",
    }
    component_id, strategy, value_for_identity = component_identity(page_id, item["source"], item)
    return {
        "component_id": component_id,
        "page_id": page_id,
        "source": item["source"],
        "identity_strategy": strategy,
        "identity_value": value_for_identity,
        "observed_order": order,
        "depth": depth,
        "name": item["name"],
        "text": item["text"],
        "kind": item["kind"],
        "record_group": item["record_group"],
        "value": item["value"],
        "key": item["key"],
        "key_fingerprint": item["key_fingerprint"],
        "sensitive_key": item["sensitive_key"],
        "sensitive_context": item["sensitive_context"],
        "type": item["type"],
        "type_path": item["type_path"],
        "index_path": item["index_path"],
        "bounds": item["bounds"],
        "bounds_center": item["bounds_center"],
        "coordinate_space": item["coordinate_space"],
        "screen_size": item["screen_size"],
        "normalized_center": item["normalized_center"],
        "locator": item["locator"],
        "clickable": to_bool(attrs(node).get("clickable", False)),
        "enabled": is_enabled(node),
        "visible": is_visible(node),
        "checked": to_bool(checked_value) if checked_is_meaningful and checked_value not in (None, "") else None,
    }


LAYOUT_ONLY_TYPES = {
    "Column", "Row", "Stack", "Flex", "List", "ListItemGroup", "NavBarContent",
    "Navigation", "NavigationContent", "NavDestination", "NavDestinationContent",
    "__Common__", "JsView", "HdsTitleBar", "TitleBar", "Mask", "MaskBlur",
}


def component_label(comp: Dict[str, Any]) -> str:
    return clean_label(str(comp.get("text") or comp.get("name") or ""))


def component_rect(comp: Dict[str, Any]) -> Dict[str, Any]:
    return parse_rect(comp.get("bounds"))


def center_inside_rect(center: Any, rect: Dict[str, Any]) -> bool:
    if not rect.get("valid") or not isinstance(center, list) or len(center) != 2:
        return False
    x, y = center
    return int(rect["left"]) <= x <= int(rect["right"]) and int(rect["top"]) <= y <= int(rect["bottom"])


def rect_contains(parent: Dict[str, Any], child: Dict[str, Any]) -> bool:
    if not parent.get("valid") or not child.get("valid"):
        return False
    return (
        int(parent["left"]) <= int(child["left"])
        and int(parent["top"]) <= int(child["top"])
        and int(parent["right"]) >= int(child["right"])
        and int(parent["bottom"]) >= int(child["bottom"])
    )


def same_or_nested_bounds(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    ar = component_rect(a)
    br = component_rect(b)
    if not ar.get("valid") or not br.get("valid"):
        return False
    if str(a.get("bounds") or "") == str(b.get("bounds") or ""):
        return True
    return rect_contains(ar, br) or rect_contains(br, ar)


def is_layout_only_component(comp: Dict[str, Any]) -> bool:
    node_type = str(comp.get("type") or "")
    label = component_label(comp)
    return (
        comp.get("source") == "ui_node"
        and node_type in LAYOUT_ONLY_TYPES
        and not comp.get("clickable")
        and (not label or label == node_type)
    )


def child_component_summary(comp: Dict[str, Any], reason: str) -> Dict[str, Any]:
    return {
        "component_id": comp.get("component_id", ""),
        "source": comp.get("source", ""),
        "merge_reason": reason,
        "kind": comp.get("kind", ""),
        "type": comp.get("type", ""),
        "name": comp.get("name", ""),
        "text": comp.get("text", ""),
        "key": comp.get("key", ""),
        "bounds": comp.get("bounds", ""),
        "bounds_center": comp.get("bounds_center"),
    }


def merge_child_component(parent: Dict[str, Any], child: Dict[str, Any], reason: str) -> None:
    parent.setdefault("merged_children", [])
    child_id = child.get("component_id")
    if child_id and any(old.get("component_id") == child_id for old in parent["merged_children"]):
        return
    parent["merged_children"].append(child_component_summary(child, reason))
    if not parent.get("text") and child.get("text"):
        parent["text"] = child.get("text")
    if not parent.get("name") and child.get("name"):
        parent["name"] = child.get("name")
    if not parent.get("key") and child.get("key"):
        parent["key"] = child.get("key")


def merge_target_for_component(comp: Dict[str, Any], semantic: List[Dict[str, Any]]) -> Tuple[Optional[Dict[str, Any]], str]:
    label = component_label(comp)
    comp_key = str(comp.get("key") or "")
    comp_rect_value = component_rect(comp)

    for target in semantic:
        target_label = component_label(target)
        target_key = str(target.get("key") or "")
        target_rect = component_rect(target)

        if comp_key and target_key and comp_key == target_key:
            return target, "same_key"
        if label and target_label and label == target_label and same_or_nested_bounds(target, comp):
            return target, "same_label_nested_bounds"
        if target.get("source") == "recognized_control":
            if center_inside_rect(comp.get("bounds_center"), target_rect):
                return target, "inside_recognized_control"
            if comp_rect_value.get("valid") and rect_contains(target_rect, comp_rect_value):
                return target, "child_of_recognized_control"
        if comp.get("source") == "ui_node" and target.get("source") == "ui_node":
            if label and target_label and label == target_label:
                return target, "same_label"

    return None, ""


def semantic_component_sort_key(comp: Dict[str, Any]) -> Tuple[int, int, str]:
    if comp.get("source") == "recognized_control":
        return (0, int(comp.get("observed_order", 0)), component_label(comp))
    kind = str(comp.get("kind") or "")
    node_type = str(comp.get("type") or "")
    if kind == "text" or node_type == "Text":
        priority = 1
    elif comp.get("clickable"):
        priority = 2
    elif comp.get("key"):
        priority = 3
    else:
        priority = 4
    return (priority, int(comp.get("observed_order", 0)), component_label(comp))


def merge_component_inventory(raw_components: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    semantic: List[Dict[str, Any]] = []
    for comp in sorted(raw_components, key=semantic_component_sort_key):
        target, reason = merge_target_for_component(comp, semantic)
        if target:
            merge_child_component(target, comp, reason)
            continue

        if is_layout_only_component(comp):
            continue

        item = dict(comp)
        item["semantic_component_id"] = item.get("component_id")
        item["merged_children"] = []
        semantic.append(item)

    for order, comp in enumerate(semantic):
        comp["semantic_order"] = order
        comp["merged_child_count"] = len(comp.get("merged_children", []))
    return semantic


def extract_component_inventory(root: Node, page_id: str, controls: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """提取当前页面语义组件；底层 raw 节点会合并进 merged_children。"""
    raw_components: List[Dict[str, Any]] = []
    seen_ids = set()
    screen_metrics = screen_metrics_from_root(root)

    for order, ctrl in enumerate(controls):
        comp = component_record_from_control(page_id, ctrl, order, screen_metrics)
        seen_ids.add(comp["component_id"])
        raw_components.append(comp)

    raw_order = 0
    for app_root in app_content_roots(root):
        for node, depth, _ in walk(app_root):
            if not should_record_component_node(node):
                continue
            comp = component_record_from_node(page_id, node, raw_order, depth, screen_metrics)
            raw_order += 1
            if comp["component_id"] in seen_ids:
                continue
            seen_ids.add(comp["component_id"])
            raw_components.append(comp)

    return merge_component_inventory(raw_components)


# ============================================================
# 持久化：settings_nodes_index.json 作为唯一数据库，tree 每次重建
# ============================================================

def tree_segment(name: str) -> str:
    name = str(name or "").strip() or "unnamed"
    name = re.sub(r"[\\/\s]+", "_", name)
    name = re.sub(r"[^0-9A-Za-z_\-\u4e00-\u9fff]+", "_", name)
    name = re.sub(r"_+", "_", name).strip("_")
    return name or "unnamed"


def make_node(node_id: str, parent_id: Optional[str], name: str, depth: int, node_type: str, **extra: Any) -> Dict[str, Any]:
    node = {
        "node_id": node_id,
        "parent_id": parent_id,
        "name": name,
        "text": extra.pop("text", name),
        "depth": depth,
        "type": node_type,
        "children": [],
    }
    for k, v in extra.items():
        if v is not None:
            node[k] = v
    return node


def append_child(parent: Dict[str, Any], child: Dict[str, Any]) -> Dict[str, Any]:
    for old in parent.setdefault("children", []):
        if old.get("node_id") == child.get("node_id"):
            old_children = old.get("children", [])
            old.update({k: v for k, v in child.items() if k != "children"})
            old["children"] = old_children
            return old
    parent.setdefault("children", []).append(child)
    return child


def empty_index() -> Dict[str, Any]:
    return {
        "schema_version": "0.1-minimal",
        "app": "Settings",
        "updated_at": now_iso(),
        "total_capture_runs": 0,
        "active_node_id": "root",
        "pages": {},
        "nodes": {},
        "state_machine": {
            "schema_version": "0.1",
            "states": {},
            "transitions": {},
            "signatures": {},
            "execution_verify": {}
        }
    }


def load_index(index_path: Path) -> Dict[str, Any]:
    if index_path.exists():
        try:
            return load_json(index_path)
        except Exception:
            pass
    return empty_index()



def page_storage_id(page: Dict[str, str], parent_node_id: str = "") -> str:
    """
    内部 pages 字典的 key。

    不能只用 NavDestination 的 page_id。比如多个 WiFi 详情页可能都是
    Setting.wifi_config_menu，但它们分别属于不同 WiFi 条目。
    所以只要明确了 parent_node_id，就用 parent_node_id + raw_page_id 生成页面记录键。
    这样：
      root/WLAN/Huawei-Guest::__page__::Setting.wifi_config_menu
      root/WLAN/Jambolaya::__page__::Setting.wifi_config_menu
    会成为两个不同页面记录。
    """
    raw_page_id = str(page.get("page_id") or "unknown::page")
    title = str(page.get("title") or "")
    if parent_node_id and parent_node_id != "root" and title != "设置":
        return f"{parent_node_id}::__page__::{raw_page_id}"
    return raw_page_id

def merge_current_page(index: Dict[str, Any], page: Dict[str, str], controls: List[Dict[str, Any]], parent_node_id: str = "") -> str:
    rid = run_id()
    ts = now_iso()
    index["updated_at"] = ts
    index["total_capture_runs"] = int(index.get("total_capture_runs", 0)) + 1

    pages = index.setdefault("pages", {})
    raw_page_id = page["page_id"]
    page_id = page_storage_id(page, parent_node_id)
    if page_id not in pages:
        pages[page_id] = {
            "page_id": page_id,
            "title": page["title"],
            "nav_key": page.get("nav_key", ""),
            "parent_node_id": parent_node_id or "",
            "first_seen_at": ts,
            "last_seen_at": ts,
            "capture_count": 0,
            "controls": {},
        }
    p = pages[page_id]
    p["title"] = page["title"]
    p["raw_page_id"] = raw_page_id
    p["nav_key"] = page.get("nav_key", "") or p.get("nav_key", "")
    if parent_node_id:
        p["parent_node_id"] = parent_node_id
    p["last_seen_at"] = ts
    p["capture_count"] = int(p.get("capture_count", 0)) + 1

    for order, c in enumerate(controls):
        uid = target_uid(page_id, c)
        if uid not in p["controls"]:
            p["controls"][uid] = {
                "control_uid": uid,
                "page_id": page_id,
                "first_seen_at": ts,
                "first_seen_run_id": rid,
                "first_seen_order": order,
                "seen_count": 0,
            }
        old = p["controls"][uid]
        old.update(c)
        old["last_seen_at"] = ts
        old["last_seen_run_id"] = rid
        old["last_seen_order"] = order
        old["seen_count"] = int(old.get("seen_count", 0)) + 1

    return page_id


def merge_page_components(index: Dict[str, Any], page_id: str, components: List[Dict[str, Any]]) -> None:
    ts = now_iso()
    rid = run_id()
    page_record = index.setdefault("pages", {}).setdefault(page_id, {"page_id": page_id, "controls": {}})
    stored = page_record.setdefault("components", {})
    current_ids = []

    for order, comp in enumerate(components):
        component_id = str(comp.get("component_id") or "")
        if not component_id:
            continue
        current_ids.append(component_id)
        if component_id not in stored:
            stored[component_id] = {
                "component_id": component_id,
                "page_id": page_id,
                "first_seen_at": ts,
                "first_seen_run_id": rid,
                "first_seen_order": order,
                "seen_count": 0,
            }
        old = stored[component_id]
        old.update(comp)
        old["last_seen_at"] = ts
        old["last_seen_run_id"] = rid
        old["last_seen_order"] = order
        old["seen_count"] = int(old.get("seen_count", 0)) + 1

    page_record["last_component_scan_at"] = ts
    page_record["last_component_count"] = len(current_ids)
    page_record["last_component_ids"] = current_ids


def components_sorted(page_record: Dict[str, Any]) -> List[Dict[str, Any]]:
    return sorted(
        page_record.get("components", {}).values(),
        key=lambda c: (
            c.get("source", ""),
            c.get("last_seen_order", c.get("observed_order", 0)),
            c.get("record_group", ""),
            c.get("name", ""),
        ),
    )


def component_region_id(comp: Dict[str, Any]) -> str:
    """把页面里的组件放进稳定的页面区域，便于程序理解页面内部结构。"""
    record_group = str(comp.get("record_group") or "")
    type_path_value = str(comp.get("type_path") or "")
    kind = str(comp.get("kind") or "")

    if record_group == "nav_controls" or "/TitleBar" in type_path_value:
        return "title_bar"
    if record_group == "entry_controls":
        return "content_entries"
    if record_group == "operation_controls":
        return "content_operations"
    if kind == "text" or str(comp.get("type") or "") == "Text":
        return "content_texts"
    return "content_misc"


def component_region_name(region_id: str) -> str:
    return {
        "title_bar": "标题栏",
        "content_entries": "页面入口",
        "content_operations": "页面操作控件",
        "content_texts": "页面文本",
        "content_misc": "其它组件",
    }.get(region_id, region_id)


def compact_component_child(comp: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "node_type": "merged_child",
        "component_id": comp.get("component_id", ""),
        "source": comp.get("source", ""),
        "merge_reason": comp.get("merge_reason", ""),
        "name": comp.get("name", ""),
        "text": comp.get("text", ""),
        "kind": comp.get("kind", ""),
        "type": comp.get("type", ""),
        "key": comp.get("key", ""),
        "bounds": comp.get("bounds", ""),
        "bounds_center": comp.get("bounds_center"),
    }


def transition_matches_component(transition: Dict[str, Any], comp: Dict[str, Any]) -> bool:
    trigger = transition.get("trigger_node") or {}
    tap_target = transition.get("tap_target") or {}
    candidates = [trigger, tap_target]

    comp_key = str(comp.get("key") or "")
    comp_label = component_label(comp)
    comp_bounds = str(comp.get("bounds") or "")
    comp_rect_value = component_rect(comp)

    for target in candidates:
        if not isinstance(target, dict):
            continue
        target_key = str(target.get("key") or "")
        target_text = clean_label(str(target.get("text") or target.get("name") or ""))
        target_bounds = str(target.get("bounds") or "")

        if comp_key and target_key and comp_key == target_key:
            return True
        if comp_bounds and target_bounds and comp_bounds == target_bounds:
            return True
        if comp_label and target_text and comp_label == target_text:
            return True
        if center_inside_rect(target.get("bounds_center"), comp_rect_value):
            return True
    return False


def compact_transition_ref(transition: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "transition_id": transition.get("transition_id", ""),
        "event_type": transition.get("event_type", ""),
        "from_state_id": transition.get("from_state_id", ""),
        "to_state_id": transition.get("to_state_id", ""),
        "to_page_id": transition.get("to_page_id", ""),
        "to_page_title": transition.get("to_page_title", ""),
        "tap_target": transition.get("tap_target", {}),
        "verify_required": transition.get("verify_required", False),
    }


def outgoing_transitions_for_page(index: Dict[str, Any], page_id: str, page: Dict[str, Any]) -> List[Dict[str, Any]]:
    sm = index.get("state_machine", {})
    state_id = str(page.get("state_id") or "")
    transitions = []
    for transition in sm.get("transitions", {}).values():
        from_state = transition.get("from_state") if isinstance(transition.get("from_state"), dict) else {}
        if state_id and transition.get("from_state_id") == state_id:
            transitions.append(transition)
            continue
        if page_id and from_state.get("page_id") == page_id:
            transitions.append(transition)
    return sorted(transitions, key=lambda t: str(t.get("transition_id") or ""))


def compact_component_node(comp: Dict[str, Any], outgoing_transitions: List[Dict[str, Any]]) -> Dict[str, Any]:
    matched_transitions = [
        compact_transition_ref(transition)
        for transition in outgoing_transitions
        if transition_matches_component(transition, comp)
    ]
    return {
        "node_type": "semantic_component",
        "component_id": comp.get("component_id", ""),
        "semantic_component_id": comp.get("semantic_component_id", comp.get("component_id", "")),
        "semantic_order": comp.get("semantic_order", comp.get("last_seen_order", comp.get("observed_order", 0))),
        "name": comp.get("name", ""),
        "text": comp.get("text", ""),
        "kind": comp.get("kind", ""),
        "type": comp.get("type", ""),
        "record_group": comp.get("record_group", ""),
        "source": comp.get("source", ""),
        "identity_strategy": comp.get("identity_strategy", ""),
        "identity_value": comp.get("identity_value", ""),
        "key": comp.get("key", ""),
        "key_fingerprint": comp.get("key_fingerprint", ""),
        "value": comp.get("value", ""),
        "locator": comp.get("locator", ""),
        "bounds": comp.get("bounds", ""),
        "bounds_center": comp.get("bounds_center"),
        "normalized_center": comp.get("normalized_center"),
        "coordinate_space": comp.get("coordinate_space", ""),
        "screen_size": comp.get("screen_size"),
        "clickable": comp.get("clickable", False),
        "enabled": comp.get("enabled", True),
        "visible": comp.get("visible", True),
        "checked": comp.get("checked"),
        "outgoing_transitions": matched_transitions,
        "children": [compact_component_child(child) for child in comp.get("merged_children", [])],
    }


def build_page_component_tree(index: Dict[str, Any]) -> Dict[str, Any]:
    pages = index.get("pages", {})
    result: Dict[str, Any] = {
        "schema_version": "0.1",
        "generated_at": now_iso(),
        "model": {
            "page_tree": "settings_tree.json 记录页面在哪里",
            "state_machine": "settings_state_machine.json 记录页面之间怎么跳转",
            "component_tree": "本文件记录每个页面里面有什么组件",
        },
        "pages": {},
    }

    region_order = ["title_bar", "content_entries", "content_operations", "content_texts", "content_misc"]
    for page_id in sorted(pages.keys()):
        page = pages.get(page_id, {})
        outgoing_transitions = outgoing_transitions_for_page(index, page_id, page)
        regions: Dict[str, Dict[str, Any]] = {
            region_id: {
                "node_type": "component_region",
                "region_id": region_id,
                "name": component_region_name(region_id),
                "children": [],
            }
            for region_id in region_order
        }

        for comp in components_sorted(page):
            region_id = component_region_id(comp)
            if region_id not in regions:
                regions[region_id] = {
                    "node_type": "component_region",
                    "region_id": region_id,
                    "name": component_region_name(region_id),
                    "children": [],
                }
            regions[region_id]["children"].append(compact_component_node(comp, outgoing_transitions))

        non_empty_regions = [regions[r] for r in region_order if regions.get(r, {}).get("children")]
        for region_id, region in regions.items():
            if region_id not in region_order and region.get("children"):
                non_empty_regions.append(region)

        result["pages"][page_id] = {
            "node_type": "page",
            "page_id": page_id,
            "title": page.get("title", ""),
            "nav_key": page.get("nav_key", ""),
            "state_id": page.get("state_id", ""),
            "signature_id": page.get("signature_id", ""),
            "parent_node_id": page.get("parent_node_id", ""),
            "incoming_transition_id": page.get("incoming_transition_id", ""),
            "last_component_scan_at": page.get("last_component_scan_at", ""),
            "component_count": len(page.get("components", {})),
            "outgoing_transition_count": len(outgoing_transitions),
            "children": non_empty_regions,
        }
    return result


def controls_sorted(page_record: Dict[str, Any]) -> List[Dict[str, Any]]:
    return sorted(
        page_record.get("controls", {}).values(),
        key=lambda c: (c.get("first_seen_run_id", ""), c.get("first_seen_order", 0), c.get("name", "")),
    )


def control_node(ctrl: Dict[str, Any], parent_id: str, depth: int) -> Dict[str, Any]:
    ctrl = scrub_control_privacy(dict(ctrl))
    name = ctrl.get("name", "未命名")
    node_id = f"{parent_id}/{tree_segment(name)}"
    node_type = {
        "entry_controls": "page_entry",
        "operation_controls": ctrl.get("kind") or "operation_control",
        "nav_controls": ctrl.get("kind") or "nav_control",
    }.get(ctrl.get("record_group"), ctrl.get("kind") or "control")
    return make_node(
        node_id=node_id,
        parent_id=parent_id,
        name=name,
        depth=depth,
        node_type=node_type,
        kind=ctrl.get("kind", ""),
        record_group=ctrl.get("record_group", ""),
        value=sanitize_value(ctrl.get("value", ""), ctrl.get("key", "")),
        key=ctrl.get("key", ""),
        key_fingerprint=ctrl.get("key_fingerprint", ""),
        sensitive_key=ctrl.get("sensitive_key", False),
        type_path=ctrl.get("type_path", ""),
        index_path=ctrl.get("index_path", ""),
        locator=ctrl.get("locator", ""),
        bounds=ctrl.get("bounds", ""),
        bounds_center=ctrl.get("bounds_center"),
        navigates=ctrl.get("navigates", ""),
        last_seen_at=ctrl.get("last_seen_at", ""),
    )


def attach_page_controls(page_record: Dict[str, Any], parent_node: Dict[str, Any], start_depth: int) -> None:
    groups = [
        ("entry_controls", "页面入口候选", "group"),
        ("operation_controls", "页面内操作控件", "operation_group"),
        ("nav_controls", "导航控件", "nav_group"),
    ]
    controls = controls_sorted(page_record)
    for group_key, group_name, group_type in groups:
        group_controls = [c for c in controls if c.get("record_group") == group_key]
        if not group_controls:
            continue
        # 入口控件直接挂；操作/导航控件建组。
        if group_key == "entry_controls":
            for c in group_controls:
                append_child(parent_node, control_node(c, parent_node["node_id"], start_depth))
        else:
            group_id = f"{parent_node['node_id']}/{tree_segment(group_name)}"
            group_node = append_child(parent_node, make_node(group_id, parent_node["node_id"], group_name, start_depth, group_type))
            for c in group_controls:
                append_child(group_node, control_node(c, group_node["node_id"], start_depth + 1))


def find_home_page(pages: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    for p in pages.values():
        if p.get("title") == "设置":
            return p
    for p in pages.values():
        if str(p.get("page_id", "")).startswith("title::设置"):
            return p
    return None


def find_node_by_id(root: Dict[str, Any], node_id: str) -> Optional[Dict[str, Any]]:
    if root.get("node_id") == node_id:
        return root
    for c in root.get("children", []):
        found = find_node_by_id(c, node_id)
        if found:
            return found
    return None


def node_parent_id(node_id: str) -> str:
    node_id = str(node_id or "").strip().rstrip("/")
    if not node_id or node_id == "root" or "/" not in node_id:
        return "root"
    return node_id.rsplit("/", 1)[0] or "root"


def is_same_or_descendant_node_id(node_id: Any, branch_node_id: str) -> bool:
    node_id = str(node_id or "").strip().rstrip("/")
    branch_node_id = str(branch_node_id or "").strip().rstrip("/")
    if not node_id or not branch_node_id:
        return False
    return node_id == branch_node_id or node_id.startswith(branch_node_id + "/")


def page_record_belongs_to_branch(page_id: str, page: Dict[str, Any], branch_node_id: str) -> bool:
    """判断某个页面记录是否挂在待删除分支下面。"""
    pid = str(page_id or page.get("page_id") or "").strip()
    parent_id = str(page.get("parent_node_id") or "").strip()
    if is_same_or_descendant_node_id(parent_id, branch_node_id):
        return True
    # page_storage_id 的形式通常是：<parent_node_id>::__page__::<raw_page_id>
    return pid.startswith(branch_node_id + "::__page__::") or pid.startswith(branch_node_id + "/")


def derived_control_node_id(page_record: Dict[str, Any], ctrl: Dict[str, Any], home_page_id: str = "") -> str:
    """按 build_tree/attach_page_controls 的规则，推导一个 control 最终会生成的 node_id。"""
    record_group = str(ctrl.get("record_group") or "")
    name = tree_segment(ctrl.get("name") or "未命名")

    if page_record.get("page_id") == home_page_id and record_group == "entry_controls":
        return f"root/{name}"

    parent_id = str(page_record.get("parent_node_id") or "").strip()
    if not parent_id:
        return ""

    if record_group == "entry_controls":
        return f"{parent_id}/{name}"
    if record_group == "operation_controls":
        return f"{parent_id}/{tree_segment('页面内操作控件')}/{name}"
    if record_group == "nav_controls":
        return f"{parent_id}/{tree_segment('导航控件')}/{name}"
    return f"{parent_id}/{name}"


def delete_branch_from_index(index: Dict[str, Any], branch_node_id: str) -> Dict[str, Any]:
    """从持久化 index 中删除某个节点及其子分支对应的数据。

    注意：settings_tree.json 是派生文件，真正要删的是 settings_nodes_index.json
    里的 pages/controls。删完后再 rebuild tree，即可得到新的 txt/tree/nodes。
    """
    branch_node_id = str(branch_node_id or "").strip().rstrip("/")
    result = {"deleted_pages": 0, "deleted_controls": 0, "branch_node_id": branch_node_id}
    if not branch_node_id or branch_node_id == "root":
        result["error"] = "不能用分支删除删除 root；需要全量清空请直接使用 --reset。"
        return result

    pages = index.setdefault("pages", {})
    home = find_home_page(pages)
    home_page_id = str(home.get("page_id") or "") if home else ""

    # 1) 删除会生成该节点或其子节点的 control。
    for page in pages.values():
        controls = page.get("controls", {})
        if not isinstance(controls, dict):
            continue
        for uid in list(controls.keys()):
            ctrl = controls.get(uid, {})
            if not isinstance(ctrl, dict):
                continue
            ctrl_node_id = derived_control_node_id(page, ctrl, home_page_id)
            if ctrl_node_id and is_same_or_descendant_node_id(ctrl_node_id, branch_node_id):
                del controls[uid]
                result["deleted_controls"] += 1

    # 2) 删除挂在该分支及其子分支下面的页面记录。
    for page_id in list(pages.keys()):
        page = pages.get(page_id, {})
        if isinstance(page, dict) and page_record_belongs_to_branch(page_id, page, branch_node_id):
            del pages[page_id]
            result["deleted_pages"] += 1

    # 3) active_node_id 如果落在已删分支内，就回退到删除节点的父节点。
    active_id = str(index.get("active_node_id") or "root")
    if is_same_or_descendant_node_id(active_id, branch_node_id):
        index["active_node_id"] = node_parent_id(branch_node_id)
    last_parent = str(index.get("last_parent_node_id") or "")
    if is_same_or_descendant_node_id(last_parent, branch_node_id):
        index["last_parent_node_id"] = node_parent_id(branch_node_id)

    index["updated_at"] = now_iso()
    return result


GROUP_NODE_TYPES = {"group", "dynamic_group", "operation_group", "nav_group", "unassigned_group", "unassigned_page"}

def selectable_display_children(node: Dict[str, Any]) -> List[Dict[str, Any]]:
    out = []
    for child in node.get("children", []):
        t = str(child.get("type") or "")
        if t in GROUP_NODE_TYPES:
            for gc in child.get("children", []):
                if is_selectable_option_node(gc):
                    out.append(gc)
            continue
        if is_selectable_option_node(child):
            out.append(child)
    return out

def build_numbered_rows(root: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows = []
    def rec(parent: Dict[str, Any], prefix: str, menu_depth: int) -> None:
        display_children = selectable_display_children(parent)
        for i, child in enumerate(display_children, start=1):
            label = f"{prefix}.{i}" if prefix else str(i)
            child["__select_idx"] = label
            rows.append({"label": label, "node": child, "menu_depth": menu_depth})
            rec(child, label, menu_depth + 1)
    rec(root, "", 0)
    return rows

def build_numbered_node_map(root: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {row["label"]: row["node"] for row in build_numbered_rows(root)}

def build_node_id_to_label(root: Dict[str, Any]) -> Dict[str, str]:
    out = {}
    for row in build_numbered_rows(root):
        node_id = str(row["node"].get("node_id") or "")
        if node_id:
            out[node_id] = row["label"]
    return out

def is_selectable_option_node(node: Dict[str, Any]) -> bool:
    """人工确认列表中允许选择的节点。返回按钮不允许作为扩展分支。"""
    t = str(node.get("type") or "")
    name = str(node.get("name") or "")
    kind = str(node.get("kind") or "")
    if t in {"root", "nav_back"} or kind == "nav_back" or name == "返回按钮":
        return False
    if t in GROUP_NODE_TYPES:
        return False
    # 防御性过滤：历史树里如果仍残留敏感动态设备入口，不允许作为扩展分支显示。
    if is_sensitive_entry_control(node):
        return False
    return True


def selectable_children(node: Dict[str, Any]) -> List[Dict[str, Any]]:
    """返回当前节点下可被数字选择的候选。

    入口控件直接显示；操作控件和标题栏按钮如果在分组下，展开一层显示。
    这样 WLAN 总开关、WLAN 安全检测、标题栏按钮也能出现在选择列表中。
    """
    out: List[Dict[str, Any]] = []
    for child in node.get("children", []):
        t = str(child.get("type") or "")
        if t in GROUP_NODE_TYPES:
            for gc in child.get("children", []):
                if is_selectable_option_node(gc):
                    out.append(gc)
            continue
        if is_selectable_option_node(child):
            out.append(child)
    return out


def node_path_text(node_id: str) -> str:
    if node_id == "root":
        return "root"
    return node_id.replace("root/", "root > ").replace("/", " > ")


def print_choice_menu(current_page: Dict[str, str], active_id: str, options: List[Dict[str, Any]]) -> None:
    print("\n" + "=" * 60)
    print("本次采集结果归属确认")
    print("=" * 60)
    print(f"当前页面: {current_page.get('title')}  page_id={current_page.get('page_id')}")
    print(f"当前 active_node_id: {active_id}")
    print(f"当前路径: {node_path_text(active_id)}")
    print(f"\n请输入 -1 或 0-{len(options)}：")
    print("-1. 返回父节点，只调整 active_node_id 后重新选择")
    print(f"0. 继续记录当前节点：{node_path_text(active_id)}")
    for i, child in enumerate(options, start=1):
        value = sanitize_value(child.get("value"), child.get("key", ""))
        value_part = f"，value={value}" if value else ""
        key_part = "" if child.get("sensitive_key") else (f"，key={child.get('key')}" if child.get("key") else "")
        print(f"{i}. 扩展分支：{child.get('name')}  ({child.get('type')}){value_part}{key_part}")


def choose_parent_by_number(index: Dict[str, Any], current_page: Dict[str, str]) -> Tuple[str, str, Optional[Dict[str, Any]]]:
    """
    人工确认本次采集页面的父节点。

    允许输入：
       0  继续记录当前 active_node_id
       1.x  把本次页面挂到编号 1.x 对应的节点下面
       -1  兼容旧逻辑，返回父节点（完整树模式下不需要）
    """
    tree = build_tree(index)
    root = tree["root"]
    active_id = str(index.get("active_node_id") or "root")
    rows = build_numbered_rows(root)
    numbered_map = {row["label"]: row["node"] for row in rows}

    print("\n" + "=" * 60)
    print("本次采集结果归属确认")
    print("=" * 60)
    print(f"当前页面: {current_page.get('title')}  page_id={current_page.get('page_id')}")
    print(f"当前 active_node_id: {active_id}")
    print(f"当前路径: {node_path_text(active_id)}")
    print(f"\n请输入 0 或层级编号（如 1, 1.2, 1.2.1）：")
    print(f"0. 继续记录当前 active_node_id：{node_path_text(active_id)}")

    for row in rows:
        idx = row["label"]
        node = row["node"]
        indent = "  " * row["menu_depth"]
        value = sanitize_value(node.get("value"), node.get("key", ""))
        value_part = f"，value={value}" if value else ""
        key_part = "" if node.get("sensitive_key") else (f"，key={node.get('key')}" if node.get("key") else "")
        print(f"{indent}{idx}. {node.get('name')}  ({node.get('type')}){value_part}{key_part}")

    while True:
        raw = input("\n请选择: ").strip()

        if raw == "-1":
            print("✗ 完整树模式下不需要使用 -1，请直接输入编号。")
            continue

        if raw == "0":
            parent_node_id = active_id
            new_active_id = active_id
            selected_node = None
            break

        selected_node = find_node_by_number(numbered_map, raw)
        if selected_node:
            parent_node_id = selected_node["node_id"]
            new_active_id = selected_node["node_id"]
            break
        selected_node = None

        print(f"输入无效。只允许输入 0 或有效编号（如 1, 1.2, 1.2.1）。")

    print(f"✓ 本次页面将挂载到: {parent_node_id}")
    return parent_node_id, new_active_id, selected_node

def build_tree(index: Dict[str, Any]) -> Dict[str, Any]:
    root = make_node("root", None, "root", 0, "root")
    pages = index.get("pages", {})
    home = find_home_page(pages)
    attached_pages = set()

    # 1. 设置首页入口直接成为 root 的 depth=1 子节点。
    if home:
        for c in [x for x in controls_sorted(home) if x.get("record_group") == "entry_controls"]:
            append_child(root, control_node(c, "root", 1))
        attached_pages.add(home.get("page_id"))

    # 2. 只处理显式 parent_node_id 的页面。
    #    这里刻意不做“标题匹配”“WLAN/蓝牙识别”“同名入口自动挂载”。
    for page_id, page in pages.items():
        if page_id in attached_pages:
            continue
        parent_node_id = str(page.get("parent_node_id") or "")
        if parent_node_id:
            parent = find_node_by_id(root, parent_node_id)
            if parent:
                attach_page_controls(page, parent, int(parent.get("depth", 0)) + 1)
                attached_pages.add(page_id)

    # 3. 没有 parent_node_id 的非首页页面放入“未归类页面”，不再错误挂到 root 一级。
    unassigned = [p for pid, p in pages.items() if pid not in attached_pages]
    if unassigned:
        group = append_child(root, make_node("root/未归类页面", "root", "未归类页面", 1, "unassigned_group"))
        for p in unassigned:
            page_node = append_child(
                group,
                make_node(
                    f"root/未归类页面/{tree_segment(p.get('title') or p.get('page_id'))}",
                    group["node_id"],
                    p.get("title") or p.get("page_id"),
                    2,
                    "unassigned_page",
                    page_id=p.get("page_id"),
                    nav_key=p.get("nav_key", ""),
                ),
            )
            attach_page_controls(p, page_node, 3)

    return {
        "schema_version": "0.1-minimal",
        "app": "Settings",
        "updated_at": index.get("updated_at", ""),
        "total_capture_runs": index.get("total_capture_runs", 0),
        "active_node_id": index.get("active_node_id", "root"),
        "root": root,
    }


def rebuild_nodes_index(tree: Dict[str, Any], index: Dict[str, Any]) -> Dict[str, Any]:
    nodes: Dict[str, Any] = {}

    def rec(n: Dict[str, Any]) -> None:
        item = {k: v for k, v in n.items() if k != "children"}
        item["child_ids"] = [c.get("node_id") for c in n.get("children", [])]
        nodes[n["node_id"]] = item
        for c in n.get("children", []):
            rec(c)

    rec(tree["root"])
    index["nodes"] = nodes
    return index


STATE_MACHINE_EMPTY = {
    "schema_version": "0.1",
    "states": {},
    "transitions": {},
    "signatures": {},
    "execution_verify": {},
}


def empty_state_machine() -> Dict[str, Any]:
    return {
        "schema_version": "0.1",
        "states": {},
        "transitions": {},
        "signatures": {},
        "execution_verify": {},
    }


def clear_page_state_machine_refs(index: Dict[str, Any], state_ids: Optional[set] = None, signature_ids: Optional[set] = None, transition_ids: Optional[set] = None) -> int:
    cleared = 0
    for page in index.get("pages", {}).values():
        if not isinstance(page, dict):
            continue
        remove_state = state_ids is None or page.get("state_id") in state_ids
        remove_sig = signature_ids is None or page.get("signature_id") in signature_ids
        remove_transition = transition_ids is None or page.get("incoming_transition_id") in transition_ids
        if remove_state and "state_id" in page:
            page.pop("state_id", None)
            cleared += 1
        if remove_sig and "signature_id" in page:
            page.pop("signature_id", None)
            cleared += 1
        if remove_transition and "incoming_transition_id" in page:
            page.pop("incoming_transition_id", None)
            cleared += 1
    return cleared


def transition_references_state(transition: Dict[str, Any], state_ids: set) -> bool:
    fields = [
        transition.get("from_state_id"),
        transition.get("to_state_id"),
        transition.get("from_state", {}).get("state_id") if isinstance(transition.get("from_state"), dict) else "",
        transition.get("to_state", {}).get("state_id") if isinstance(transition.get("to_state"), dict) else "",
        transition.get("trigger_node", {}).get("node_id") if isinstance(transition.get("trigger_node"), dict) else "",
    ]
    return any(str(value or "") in state_ids for value in fields)


def state_id_matches_any_node(state_id: Any, node_ids: List[str]) -> bool:
    sid = str(state_id or "").strip().rstrip("/")
    return any(is_same_or_descendant_node_id(sid, node_id) for node_id in node_ids)


def transition_matches_any_node(transition: Dict[str, Any], node_ids: List[str]) -> bool:
    values = [
        transition.get("from_state_id"),
        transition.get("to_state_id"),
        transition.get("from_state", {}).get("state_id") if isinstance(transition.get("from_state"), dict) else "",
        transition.get("to_state", {}).get("state_id") if isinstance(transition.get("to_state"), dict) else "",
        transition.get("trigger_node", {}).get("node_id") if isinstance(transition.get("trigger_node"), dict) else "",
        transition.get("tap_target", {}).get("node_id") if isinstance(transition.get("tap_target"), dict) else "",
    ]
    return any(state_id_matches_any_node(value, node_ids) for value in values)


def used_signature_ids(index: Dict[str, Any]) -> set:
    ensure_state_machine(index)
    sm = index["state_machine"]
    used = set()
    for state in sm.get("states", {}).values():
        sig = str(state.get("signature_id") or "")
        if sig:
            used.add(sig)
    for transition in sm.get("transitions", {}).values():
        sig = str(transition.get("signature_id") or "")
        if sig:
            used.add(sig)
        to_sig = transition.get("to_state", {}).get("signature_id") if isinstance(transition.get("to_state"), dict) else ""
        if to_sig:
            used.add(str(to_sig))
        from_sig = transition.get("from_state", {}).get("signature_id") if isinstance(transition.get("from_state"), dict) else ""
        if from_sig:
            used.add(str(from_sig))
    for verify in sm.get("execution_verify", {}).values():
        sig = str(verify.get("signature_id") or "")
        if sig:
            used.add(sig)
    for page in index.get("pages", {}).values():
        sig = str(page.get("signature_id") or "")
        if sig:
            used.add(sig)
    return used


def prune_state_machine(index: Dict[str, Any]) -> Dict[str, int]:
    ensure_state_machine(index)
    sm = index["state_machine"]
    result = {"deleted_execution_verify": 0, "deleted_signatures": 0, "cleared_page_refs": 0}

    transitions = sm.setdefault("transitions", {})
    verifies = sm.setdefault("execution_verify", {})
    for transition_id in list(verifies.keys()):
        if transition_id not in transitions:
            del verifies[transition_id]
            result["deleted_execution_verify"] += 1

    state_ids = set(sm.setdefault("states", {}).keys())
    transition_ids = set(transitions.keys())
    signature_ids = set(sm.setdefault("signatures", {}).keys())
    for page in index.get("pages", {}).values():
        if not isinstance(page, dict):
            continue
        changed = False
        if page.get("state_id") and page.get("state_id") not in state_ids:
            page.pop("state_id", None)
            changed = True
        if page.get("incoming_transition_id") and page.get("incoming_transition_id") not in transition_ids:
            page.pop("incoming_transition_id", None)
            changed = True
        if page.get("signature_id") and page.get("signature_id") not in signature_ids:
            page.pop("signature_id", None)
            changed = True
        if changed:
            result["cleared_page_refs"] += 1

    used_sigs = used_signature_ids(index)
    for signature_id in list(sm.get("signatures", {}).keys()):
        if signature_id not in used_sigs:
            del sm["signatures"][signature_id]
            result["deleted_signatures"] += 1

    index["updated_at"] = now_iso()
    return result


def delete_state_machine_transitions(index: Dict[str, Any], transition_ids: List[str], prune: bool = True) -> Dict[str, int]:
    ensure_state_machine(index)
    sm = index["state_machine"]
    result = {"deleted_transitions": 0, "deleted_execution_verify": 0, "cleared_page_refs": 0, "deleted_signatures": 0}
    for transition_id in [str(t or "").strip() for t in transition_ids if str(t or "").strip()]:
        if transition_id in sm.get("transitions", {}):
            del sm["transitions"][transition_id]
            result["deleted_transitions"] += 1
        if transition_id in sm.get("execution_verify", {}):
            del sm["execution_verify"][transition_id]
            result["deleted_execution_verify"] += 1
    result["cleared_page_refs"] += clear_page_state_machine_refs(index, state_ids=set(), signature_ids=set(), transition_ids=set(transition_ids))
    if prune:
        pruned = prune_state_machine(index)
        result["deleted_execution_verify"] += pruned.get("deleted_execution_verify", 0)
        result["deleted_signatures"] += pruned.get("deleted_signatures", 0)
        result["cleared_page_refs"] += pruned.get("cleared_page_refs", 0)
    index["updated_at"] = now_iso()
    return result


def delete_state_machine_states(index: Dict[str, Any], state_ids: List[str], prune: bool = True) -> Dict[str, int]:
    ensure_state_machine(index)
    sm = index["state_machine"]
    target_state_ids = {str(s or "").strip().rstrip("/") for s in state_ids if str(s or "").strip()}
    result = {"deleted_states": 0, "deleted_transitions": 0, "deleted_execution_verify": 0, "cleared_page_refs": 0, "deleted_signatures": 0}
    deleted_signature_ids = set()

    for state_id in list(target_state_ids):
        state = sm.get("states", {}).get(state_id)
        if state:
            sig = str(state.get("signature_id") or "")
            if sig:
                deleted_signature_ids.add(sig)
            del sm["states"][state_id]
            result["deleted_states"] += 1

    transition_ids = [
        transition_id
        for transition_id, transition in sm.get("transitions", {}).items()
        if isinstance(transition, dict) and transition_references_state(transition, target_state_ids)
    ]
    transition_result = delete_state_machine_transitions(index, transition_ids, prune=False)
    result["deleted_transitions"] += transition_result.get("deleted_transitions", 0)
    result["deleted_execution_verify"] += transition_result.get("deleted_execution_verify", 0)
    result["cleared_page_refs"] += transition_result.get("cleared_page_refs", 0)
    result["cleared_page_refs"] += clear_page_state_machine_refs(index, state_ids=target_state_ids, signature_ids=deleted_signature_ids, transition_ids=set(transition_ids))

    if prune:
        pruned = prune_state_machine(index)
        result["deleted_execution_verify"] += pruned.get("deleted_execution_verify", 0)
        result["deleted_signatures"] += pruned.get("deleted_signatures", 0)
        result["cleared_page_refs"] += pruned.get("cleared_page_refs", 0)
    index["updated_at"] = now_iso()
    return result


def cleanup_state_machine_for_node_ids(index: Dict[str, Any], node_ids: List[str]) -> Dict[str, int]:
    ensure_state_machine(index)
    sm = index["state_machine"]
    clean_node_ids = [str(node_id or "").strip().rstrip("/") for node_id in node_ids if str(node_id or "").strip()]
    state_ids = [
        state_id
        for state_id in sm.get("states", {}).keys()
        if state_id_matches_any_node(state_id, clean_node_ids)
    ]
    result = delete_state_machine_states(index, state_ids, prune=False)

    transition_ids = [
        transition_id
        for transition_id, transition in sm.get("transitions", {}).items()
        if isinstance(transition, dict) and transition_matches_any_node(transition, clean_node_ids)
    ]
    transition_result = delete_state_machine_transitions(index, transition_ids, prune=False)
    result["deleted_transitions"] += transition_result.get("deleted_transitions", 0)
    result["deleted_execution_verify"] += transition_result.get("deleted_execution_verify", 0)
    result["cleared_page_refs"] = result.get("cleared_page_refs", 0) + transition_result.get("cleared_page_refs", 0)

    pruned = prune_state_machine(index)
    result["deleted_execution_verify"] += pruned.get("deleted_execution_verify", 0)
    result["deleted_signatures"] += pruned.get("deleted_signatures", 0)
    result["cleared_page_refs"] = result.get("cleared_page_refs", 0) + pruned.get("cleared_page_refs", 0)
    return result


def reset_state_machine(index: Dict[str, Any]) -> Dict[str, int]:
    ensure_state_machine(index)
    old = index.get("state_machine", {})
    result = {
        "deleted_states": len(old.get("states", {})),
        "deleted_transitions": len(old.get("transitions", {})),
        "deleted_execution_verify": len(old.get("execution_verify", {})),
        "deleted_signatures": len(old.get("signatures", {})),
        "cleared_page_refs": clear_page_state_machine_refs(index),
    }
    index["state_machine"] = empty_state_machine()
    index["updated_at"] = now_iso()
    return result


def parse_reset_number(number_str: str) -> Tuple[str, bool]:
    """解析删除编号，返回 (idx, keep_self)"""
    number_str = number_str.strip()

    if number_str.startswith("/"):
        number_str = number_str[1:]

    if number_str.endswith("/"):
        number_str = number_str[:-1]
        return number_str, True

    return number_str, False


def find_node_by_number(numbered_map: Dict[str, Dict[str, Any]], idx: str) -> Optional[Dict[str, Any]]:
    """根据编号在映射中查找节点"""
    idx = str(idx).strip()
    if not idx:
        return None
    return numbered_map.get(idx)


def collect_descendant_node_ids(node: Dict[str, Any]) -> List[str]:
    """收集节点及其所有子孙节点的 node_id"""
    node_ids = [str(node.get("node_id") or "")]
    for child in node.get("children", []):
        node_ids.extend(collect_descendant_node_ids(child))
    return node_ids


def collect_children_node_ids(node: Dict[str, Any]) -> List[str]:
    """只收集子节点及其所有子孙节点的 node_id，不包含自己"""
    node_ids = []
    for child in node.get("children", []):
        node_ids.extend(collect_descendant_node_ids(child))
    return node_ids


def delete_node_and_descendants(index: Dict[str, Any], target_node_id: str) -> Dict[str, Any]:
    """删除目标节点本体及其所有子节点"""
    pages = index.setdefault("pages", {})
    home = find_home_page(pages)
    home_page_id = str(home.get("page_id") or "") if home else ""

    result = {"deleted_pages": 0, "deleted_controls": 0, "target_node_id": target_node_id}

    tree = build_tree(index)
    root = tree["root"]
    target_node = find_node_by_id(root, target_node_id)

    if not target_node:
        result["error"] = f"未找到节点 {target_node_id}"
        return result

    node_ids_to_delete = collect_descendant_node_ids(target_node)
    sm_result = cleanup_state_machine_for_node_ids(index, node_ids_to_delete)
    result.update({
        "deleted_states": sm_result.get("deleted_states", 0),
        "deleted_transitions": sm_result.get("deleted_transitions", 0),
        "deleted_execution_verify": sm_result.get("deleted_execution_verify", 0),
        "deleted_signatures": sm_result.get("deleted_signatures", 0),
    })

    for page in pages.values():
        controls = page.get("controls", {})
        if not isinstance(controls, dict):
            continue
        for uid in list(controls.keys()):
            ctrl = controls.get(uid, {})
            if not isinstance(ctrl, dict):
                continue
            ctrl_node_id = derived_control_node_id(page, ctrl, home_page_id)
            if ctrl_node_id and ctrl_node_id in node_ids_to_delete:
                del controls[uid]
                result["deleted_controls"] += 1

    for page_id in list(pages.keys()):
        page = pages.get(page_id, {})
        if isinstance(page, dict) and page_record_belongs_to_branch(page_id, page, target_node_id):
            del pages[page_id]
            result["deleted_pages"] += 1

    active_id = str(index.get("active_node_id") or "root")
    if active_id in node_ids_to_delete:
        parent_id = str(target_node.get("parent_id") or "root") if target_node else "root"
        index["active_node_id"] = parent_id

    last_parent = str(index.get("last_parent_node_id") or "")
    if last_parent in node_ids_to_delete:
        parent_id = str(target_node.get("parent_id") or "root") if target_node else "root"
        index["last_parent_node_id"] = parent_id

    index["updated_at"] = now_iso()
    return result


def clear_node_children(index: Dict[str, Any], target_node_id: str) -> Dict[str, Any]:
    """保留目标节点本体，只删除其子节点"""
    pages = index.setdefault("pages", {})
    home = find_home_page(pages)
    home_page_id = str(home.get("page_id") or "") if home else ""

    result = {"deleted_pages": 0, "deleted_controls": 0, "target_node_id": target_node_id}

    tree = build_tree(index)
    root = tree["root"]
    target_node = find_node_by_id(root, target_node_id)

    if not target_node:
        result["error"] = f"未找到节点 {target_node_id}"
        return result

    children_node_ids = collect_children_node_ids(target_node)

    if not children_node_ids:
        return result

    sm_result = cleanup_state_machine_for_node_ids(index, children_node_ids)
    result.update({
        "deleted_states": sm_result.get("deleted_states", 0),
        "deleted_transitions": sm_result.get("deleted_transitions", 0),
        "deleted_execution_verify": sm_result.get("deleted_execution_verify", 0),
        "deleted_signatures": sm_result.get("deleted_signatures", 0),
    })

    for page in pages.values():
        controls = page.get("controls", {})
        if not isinstance(controls, dict):
            continue
        for uid in list(controls.keys()):
            ctrl = controls.get(uid, {})
            if not isinstance(ctrl, dict):
                continue
            ctrl_node_id = derived_control_node_id(page, ctrl, home_page_id)
            if ctrl_node_id and ctrl_node_id in children_node_ids:
                del controls[uid]
                result["deleted_controls"] += 1

    for page_id in list(pages.keys()):
        page = pages.get(page_id, {})
        if isinstance(page, dict):
            parent_id = str(page.get("parent_node_id") or "")
            if parent_id in children_node_ids or page_id.startswith(target_node_id + "/"):
                del pages[page_id]
                result["deleted_pages"] += 1

    active_id = str(index.get("active_node_id") or "root")
    if active_id in children_node_ids:
        index["active_node_id"] = target_node_id

    last_parent = str(index.get("last_parent_node_id") or "")
    if last_parent in children_node_ids:
        index["last_parent_node_id"] = target_node_id

    index["updated_at"] = now_iso()
    return result


def reset_by_number(index: Dict[str, Any], numbers_str: str) -> List[Dict[str, Any]]:
    """根据编号列表执行删除/清空操作"""
    numbered_map = {}
    tree = build_tree(index)
    numbered_map = build_numbered_node_map(tree["root"])

    results = []
    numbers = [n.strip() for n in numbers_str.split(",") if n.strip()]

    for num_str in numbers:
        idx, keep_self = parse_reset_number(num_str)

        if idx == "0":
            results.append({"error": "不能删除编号 0（继续记录当前节点）", "number": num_str})
            continue

        target_node = find_node_by_number(numbered_map, idx)
        if not target_node:
            results.append({"error": f"未找到编号 {idx} 对应的节点", "number": num_str})
            continue

        target_node_id = str(target_node.get("node_id") or "")
        node_path = node_path_text(target_node_id)

        if keep_self:
            result = clear_node_children(index, target_node_id)
            result["action"] = "clear"
            result["node_path"] = node_path
            result["number"] = num_str
        else:
            result = delete_node_and_descendants(index, target_node_id)
            result["action"] = "delete"
            result["node_path"] = node_path
            result["number"] = num_str

        results.append(result)

    return results


def parse_multi_indices(indices_str: str) -> List[Tuple[str, bool]]:
    """解析多个编号，返回 [(idx, keep_self), ...] 列表"""
    results = []
    indices = [i.strip() for i in indices_str.split(",") if i.strip()]

    for idx_str in indices:
        idx, keep_self = parse_reset_number(idx_str)
        results.append((idx, keep_self))

    return results


def split_ids(ids_str: str) -> List[str]:
    return [item.strip() for item in str(ids_str or "").split(",") if item.strip()]


def print_state_machine_result(prefix: str, result: Dict[str, int]) -> None:
    print(
        f"✓ {prefix}: "
        f"删除 state {result.get('deleted_states', 0)} 个，"
        f"删除 transition {result.get('deleted_transitions', 0)} 条，"
        f"删除 verify {result.get('deleted_execution_verify', 0)} 条，"
        f"删除 signature {result.get('deleted_signatures', 0)} 个，"
        f"清理页面引用 {result.get('cleared_page_refs', 0)} 处"
    )


def state_machine_result_suffix(result: Dict[str, Any]) -> str:
    deleted_states = int(result.get("deleted_states", 0) or 0)
    deleted_transitions = int(result.get("deleted_transitions", 0) or 0)
    deleted_verify = int(result.get("deleted_execution_verify", 0) or 0)
    deleted_signatures = int(result.get("deleted_signatures", 0) or 0)
    if not any([deleted_states, deleted_transitions, deleted_verify, deleted_signatures]):
        return ""
    return (
        f"，同步清理状态机 state {deleted_states} 个，"
        f"transition {deleted_transitions} 条，"
        f"verify {deleted_verify} 条，"
        f"signature {deleted_signatures} 个"
    )


def handle_state_machine_maintenance_commands(index: Dict[str, Any], graph_dir: Path, args: argparse.Namespace) -> bool:
    has_command = bool(args.sm_reset or args.sm_delete_state or args.sm_delete_transition or args.sm_prune)
    if not has_command:
        return False

    if args.sm_reset:
        result = reset_state_machine(index)
        print_state_machine_result("已清空状态机", result)

    if args.sm_delete_state:
        state_ids = split_ids(args.sm_delete_state)
        before = set(index.get("state_machine", {}).get("states", {}).keys())
        result = delete_state_machine_states(index, state_ids)
        missing = [state_id for state_id in state_ids if state_id not in before]
        print_state_machine_result(f"已删除状态 {', '.join(state_ids)}", result)
        if missing:
            print(f"  提示：未找到这些 state_id: {', '.join(missing)}")

    if args.sm_delete_transition:
        transition_ids = split_ids(args.sm_delete_transition)
        before = set(index.get("state_machine", {}).get("transitions", {}).keys())
        result = delete_state_machine_transitions(index, transition_ids)
        missing = [transition_id for transition_id in transition_ids if transition_id not in before]
        print_state_machine_result(f"已删除跳转 {', '.join(transition_ids)}", result)
        if missing:
            print(f"  提示：未找到这些 transition_id: {', '.join(missing)}")

    if args.sm_prune:
        result = prune_state_machine(index)
        print_state_machine_result("已清理状态机孤儿记录", result)

    save_outputs(index, graph_dir)
    print("执行完成。")
    return True


def handle_graph_maintenance_commands(index: Dict[str, Any], graph_dir: Path, args: argparse.Namespace) -> bool:
    """处理图形维护命令（--reset, --delete, --clear）

    返回 True 表示已处理命令并应该退出，False 表示继续正常流程
    """
    has_maintenance_command = bool(args.reset == "__ALL__" or args.reset or args.delete or args.clear)

    if not has_maintenance_command:
        return False

    if args.reset == "__ALL__":
        if graph_dir.exists():
            shutil.rmtree(graph_dir)
            graph_dir.mkdir(parents=True, exist_ok=True)
            print(f"✓ 已清空旧图谱目录: {graph_dir}")
        return True

    tree = build_tree(index)
    numbered_map = build_numbered_node_map(tree["root"])

    if args.reset:
        reset_str = str(args.reset).strip()
        # 兼容旧格式：--reset 7 等价于 --delete 7，--reset 7/ 等价于 --clear 7
        results = reset_by_number(index, reset_str)
        for result in results:
            if result.get("error"):
                print(f"✗ {result.get('error')} (编号: {result.get('number', '')})")
            else:
                action = "清空子节点" if result.get("action") == "clear" else "删除分支"
                print(
                    f"✓ 已{action}: {result.get('number')} -> {result.get('node_path')}，"
                    f"删除页面 {result.get('deleted_pages', 0)} 个，"
                    f"删除控件 {result.get('deleted_controls', 0)} 个"
                    f"{state_machine_result_suffix(result)}"
                )
    elif args.delete:
        indices = parse_multi_indices(args.delete)
        for idx, _ in indices:
            if idx == "0":
                print(f"✗ 不能删除编号 0（继续记录当前节点）")
                continue

            target_node = find_node_by_number(numbered_map, idx)
            if not target_node:
                print(f"✗ 未找到编号 {idx} 对应的节点")
                continue

            target_node_id = str(target_node.get("node_id") or "")
            node_path = node_path_text(target_node_id)
            result = delete_node_and_descendants(index, target_node_id)

            if result.get("error"):
                print(f"✗ {result.get('error')} (编号: {idx})")
            else:
                print(
                    f"✓ 已删除分支: {idx} -> {node_path}，"
                    f"删除页面 {result.get('deleted_pages', 0)} 个，"
                    f"删除控件 {result.get('deleted_controls', 0)} 个"
                    f"{state_machine_result_suffix(result)}"
                )
    elif args.clear:
        indices = parse_multi_indices(args.clear)
        for idx, _ in indices:
            if idx == "0":
                print(f"✗ 不能清空编号 0（继续记录当前节点）")
                continue

            target_node = find_node_by_number(numbered_map, idx)
            if not target_node:
                print(f"✗ 未找到编号 {idx} 对应的节点")
                continue

            target_node_id = str(target_node.get("node_id") or "")
            node_path = node_path_text(target_node_id)
            result = clear_node_children(index, target_node_id)

            if result.get("error"):
                print(f"✗ {result.get('error')} (编号: {idx})")
            else:
                print(
                    f"✓ 已清空分支子节点: {idx} -> {node_path}，"
                    f"删除页面 {result.get('deleted_pages', 0)} 个，"
                    f"删除控件 {result.get('deleted_controls', 0)} 个"
                    f"{state_machine_result_suffix(result)}"
                )

    save_outputs(index, graph_dir)
    print("执行完成。")
    return True


def readable_tree(tree: Dict[str, Any], node_id_to_label: Optional[Dict[str, str]] = None) -> str:
    if node_id_to_label is None:
        node_id_to_label = build_node_id_to_label(tree["root"])

    lines = [
        "设置 App 手动采集树（最小版）",
        f"更新时间：{tree.get('updated_at', '')}",
        f"累计采集次数：{tree.get('total_capture_runs', 0)}",
        f"当前 active_node_id：{tree.get('active_node_id', 'root')}",
        "",
    ]

    def rec(n: Dict[str, Any]) -> None:
        indent = "  " * int(n.get("depth", 0))
        extra = []
        node_id = str(n.get("node_id") or "")
        idx = node_id_to_label.get(node_id, "")
        if idx and is_selectable_option_node(n):
            extra.append(f"idx={idx}")
        if n.get("key") and not n.get("sensitive_key"):
            extra.append(f"key={n.get('key')}")
        if n.get("locator"):
            extra.append(f"locator={n.get('locator')}")
        if n.get("locator") == "bounds_center" and n.get("bounds_center"):
            extra.append(f"bounds_center={n.get('bounds_center')}")
        value = sanitize_value(n.get("value"), n.get("key", ""))
        if value:
            extra.append(f"value={value}")
        extra_text = "，" + "，".join(extra) if extra else ""

        lines.append(f"{indent}- depth={n.get('depth')} [{n.get('type')}] {n.get('name')}{extra_text}")
        for c in n.get("children", []):
            rec(c)

    rec(tree["root"])
    return "\n".join(lines)


def write_page_components_jsonl(index: Dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pages = index.get("pages", {})
    with open(path, "w", encoding="utf-8") as f:
        for page_id in sorted(pages.keys()):
            page = pages.get(page_id, {})
            for comp in components_sorted(page):
                record = dict(comp)
                record["page_id"] = page_id
                record["page_title"] = page.get("title", "")
                record["page_nav_key"] = page.get("nav_key", "")
                record["state_id"] = page.get("state_id", "")
                f.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    print(f"✓ 页面组件事实库 JSONL: {path}")


def component_summary_by_group(components: List[Dict[str, Any]]) -> Dict[str, int]:
    summary: Dict[str, int] = {}
    for comp in components:
        key = str(comp.get("record_group") or comp.get("source") or "unknown")
        summary[key] = summary.get(key, 0) + 1
    return summary


def build_components_report(index: Dict[str, Any]) -> str:
    pages = index.get("pages", {})
    page_items = sorted(
        pages.items(),
        key=lambda item: (str(item[1].get("title") or ""), str(item[0])),
    )
    total_components = sum(len(p.get("components", {})) for p in pages.values())
    total_controls = sum(
        len([c for c in p.get("components", {}).values() if c.get("source") == "recognized_control"])
        for p in pages.values()
    )
    bounds_only = sum(
        len([c for c in p.get("components", {}).values() if c.get("identity_strategy") == "bounds"])
        for p in pages.values()
    )
    key_components = sum(
        len([c for c in p.get("components", {}).values() if c.get("identity_strategy") == "stable_key"])
        for p in pages.values()
    )

    lines = [
        "# 设置页面组件清单",
        "",
        "这份报告由脚本自动生成；完整逐组件数据请以 `settings_page_components.jsonl` 为准。",
        "",
        "## 总览",
        "",
        f"- 页面数：{len(pages)}",
        f"- 组件观测数：{total_components}",
        f"- 已识别控件数：{total_controls}",
        f"- 使用稳定 key 的组件数：{key_components}",
        f"- 仅能用 bounds 兜底识别的组件数：{bounds_only}",
        "",
        "## 页面摘要",
        "",
        "| 页面 | page_id | 组件数 | 已识别控件 | 仅 bounds | 最近扫描 |",
        "| --- | --- | ---: | ---: | ---: | --- |",
    ]

    for page_id, page in page_items:
        components = list(page.get("components", {}).values())
        recognized = [c for c in components if c.get("source") == "recognized_control"]
        page_bounds_only = [c for c in components if c.get("identity_strategy") == "bounds"]
        title = str(page.get("title") or page_id).replace("|", "\\|")
        safe_page_id = str(page_id).replace("|", "\\|")
        lines.append(
            f"| {title} | `{safe_page_id}` | {len(components)} | {len(recognized)} | "
            f"{len(page_bounds_only)} | {page.get('last_component_scan_at', '')} |"
        )

    lines.extend(["", "## 页面明细", ""])

    for page_id, page in page_items:
        components = components_sorted(page)
        recognized = [c for c in components if c.get("source") == "recognized_control"]
        summary = component_summary_by_group(components)
        lines.extend([
            f"### {page.get('title') or page_id}",
            "",
            f"- page_id：`{page_id}`",
            f"- state_id：`{page.get('state_id', '')}`",
            f"- 组件数：{len(components)}",
            f"- 分组：{', '.join(f'{k}={v}' for k, v in sorted(summary.items())) or '无'}",
            "",
        ])

        if not recognized:
            lines.extend(["未识别到可操作控件。", ""])
            continue

        lines.extend([
            "| 控件 | 分组 | 类型 | 定位 | bounds_center | value |",
            "| --- | --- | --- | --- | --- | --- |",
        ])
        for comp in recognized:
            name = str(comp.get("name") or comp.get("text") or comp.get("type") or "").replace("|", "\\|")
            group = str(comp.get("record_group") or "").replace("|", "\\|")
            kind = str(comp.get("kind") or comp.get("type") or "").replace("|", "\\|")
            if comp.get("key"):
                locator = f"key:{comp.get('key')}"
            elif comp.get("key_fingerprint"):
                locator = f"keyhash:{comp.get('key_fingerprint')}"
            else:
                locator = str(comp.get("locator") or comp.get("identity_strategy") or "")
            locator = locator.replace("|", "\\|")
            center = json.dumps(comp.get("bounds_center"), ensure_ascii=False)
            value = str(comp.get("value") or "").replace("|", "\\|")
            lines.append(f"| {name} | {group} | {kind} | `{locator}` | `{center}` | {value} |")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def save_outputs(index: Dict[str, Any], graph_dir: Path) -> None:
    scrub_index_privacy(index)
    drop_sensitive_entry_controls(index)
    tree = build_tree(index)
    index = rebuild_nodes_index(tree, index)
    scrub_index_privacy(index)
    drop_sensitive_entry_controls(index)
    node_id_to_label = build_node_id_to_label(tree["root"])
    save_text(readable_tree(tree, node_id_to_label), graph_dir / "settings_ui_graph_readable.txt", "可读树摘要")
    save_json(tree, graph_dir / "settings_tree.json", "设置层级树")
    save_json(index, graph_dir / "settings_nodes_index.json", "设置节点索引/数据库")
    save_json(index.get("state_machine", {}), graph_dir / "settings_state_machine.json", "设置状态机")
    save_json(build_page_component_tree(index), graph_dir / "settings_page_component_tree.json", "页面组件结构树")
    write_page_components_jsonl(index, graph_dir / "settings_page_components.jsonl")
    save_text(build_components_report(index), graph_dir / "settings_components_report.md", "页面组件清单报告")


# ============================================================
# 主流程
# ============================================================

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="最小交互版设置 UI 树采集与层级树记录。")
    p.add_argument("--device-id", default=DEFAULT_DEVICE_ID)
    p.add_argument("--work-dir", default=str(DEFAULT_WORK_DIR))
    p.add_argument("--output-dir", default="")
    p.add_argument("--graph-dir", default="")
    p.add_argument("--json", default="")
    p.add_argument("--skip-capture", action="store_true")
    p.add_argument(
        "--reset",
        nargs="?",
        const="__ALL__",
        default="",
        help="清空 graph 目录；也可写 --reset 7 删除节点，--reset 7/ 清空子节点"
    )
    p.add_argument(
        "--delete",
        default="",
        help="删除节点本体及其所有子分支，如 --delete 7 或 --delete 7.3,7.4"
    )
    p.add_argument(
        "--clear",
        default="",
        help="保留节点本体，只清空子分支，如 --clear 7 或 --clear 7.3,7.4"
    )
    p.add_argument("--sm-reset", action="store_true", help="只清空状态机，保留设置树、页面记录和组件事实库")
    p.add_argument("--sm-delete-state", default="", help="删除状态机 state 及相关 transition，如 --sm-delete-state root/WLAN")
    p.add_argument("--sm-delete-transition", default="", help="删除状态机 transition 及对应 verify，如 --sm-delete-transition abc123")
    p.add_argument("--sm-prune", action="store_true", help="清理状态机孤儿 verify/signature 和页面中的失效引用")
    p.add_argument("--parent-node-id", default="", help="非交互模式：直接指定当前页面挂载到哪个节点下面")
    p.add_argument("--no-prompt", action="store_true", help="不弹出数字选择，直接继续记录当前 active_node_id")
    p.add_argument("--nav-record", action="store_true", help="轻量导航状态图录制模式")
    p.add_argument("--nav-pending-clear", action="store_true", help="清除未完成的导航 transition")
    p.add_argument("--nav-graph-show", action="store_true", help="显示轻量导航状态图摘要")
    p.add_argument("--nav-path-to", default="", help="从导航图 BFS 生成到目标页面的轻量 path_snapshot")
    p.add_argument("--description", default="", help="nav-path 输出的页面描述")
    return p.parse_args()


def print_nav_graph(work_dir: Path) -> None:
    graph = load_navigation_graph(work_dir)
    print(json.dumps(graph, ensure_ascii=False, indent=2))


def clear_pending_transition(work_dir: Path) -> None:
    path = pending_transition_path(work_dir)
    if path.exists():
        path.unlink()
        print(f"✓ 已删除未完成转移: {path}")
    else:
        print(f"✓ 当前没有未完成转移: {path}")


def prompt_special_target() -> Tuple[str, Dict[str, Any], Optional[str]]:
    operate = input("operate（默认 tap）: ").strip() or "tap"
    value = input("value: ").strip()
    desc = input("key_description: ").strip() or value or "特殊控件"
    prompt = input("step_prompt: ").strip() or desc
    scope = input("scope（可空/page/dialog/local_container）: ").strip()
    expect = input("expect（可空/dialog/same_page/new_page/list_changed）: ").strip()
    effect = input("effect（可空/dialog）: ").strip()
    target = {"type": "special", "value": value, "key_description": desc, "step_prompt": prompt}
    if scope:
        target["scope"] = scope
    if expect:
        target["expect"] = expect
    return operate, target, effect or None


def complete_pending_if_needed(work_dir: Path, graph: Dict[str, Any], state: Dict[str, Any]) -> bool:
    path = pending_transition_path(work_dir)
    if not path.exists():
        return False
    pending = load_json(path)
    print("\n当前存在未完成转移：")
    print(f"from_page={pending.get('from_page')}")
    print(f"target={(pending.get('target') or {}).get('step_prompt') or (pending.get('target') or {}).get('value')}")
    print(f"当前 dump 页面识别为 {state.get('page_name')}")
    choice = input("是否将其补全为 transition？[y/n]: ").strip().lower()
    if choice == "y":
        transition = {
            "transition_id": transition_id(pending["from_page"], pending.get("operate", "tap"), state["page_name"]),
            "from_page": pending["from_page"],
            "to_page": state["page_name"],
            "operate": pending.get("operate", "tap"),
            "target": pending.get("target", {}),
        }
        if pending.get("effect"):
            transition["effect"] = pending["effect"]
        add_transition(graph, transition)
        save_navigation_graph(graph, work_dir)
        path.unlink()
        print("✓ 已补全 transition 并清空 pending")
        return True
    delete = input("是否删除当前 pending？[y/n]: ").strip().lower()
    if delete == "y":
        path.unlink()
        print("✓ 已删除 pending")
    else:
        print("✓ 已保留 pending")
    return False


def run_nav_record(args: argparse.Namespace, work_dir: Path, output_dir: Path) -> None:
    if not args.skip_capture:
        if not capture_artifacts(args.device_id, output_dir):
            return
    else:
        print("已跳过 hdc 采集，仅解析已有 JSON")
    json_path = Path(args.json) if args.json else output_dir / "current_ui_tree.json"
    if not json_path.exists():
        print(f"✗ JSON 文件不存在: {json_path}")
        return
    raw_root = load_json(json_path)
    root_json = load_json(json_path)
    annotate(root_json)
    state = build_navigation_state(root_json)
    graph = load_navigation_graph(work_dir)
    graph.setdefault("states", {})[state["page_name"]] = state
    save_navigation_graph(graph, work_dir)
    complete_pending_if_needed(work_dir, graph, state)

    candidates = extract_navigation_candidates(root_json)
    print("\n当前页面状态")
    print(f"page_name={state['page_name']}  title={state.get('last_title')}")
    print("\n候选入口（普通上下滚动不会记录；hl/hr 会记录横向局部滑动）")
    for c in candidates:
        print(f"{c['index']}. [{c.get('type')}] text=\"{c.get('text')}\" key={c.get('key') or '(无 key)'} center={c.get('bounds_center')}")
    print("hl. 局部横向向左滑动")
    print("hr. 局部横向向右滑动")
    print("s. 手动添加 special")
    choice = input("请选择候选编号/hl/hr/s（回车退出）: ").strip()
    if not choice:
        return
    effect = None
    if choice in {"hl", "hr"}:
        operate = "swipe_left" if choice == "hl" else "swipe_right"
        target = horizontal_target("left" if choice == "hl" else "right")
        trans = {
            "transition_id": transition_id(state["page_name"], operate, state["page_name"]),
            "from_page": state["page_name"],
            "to_page": state["page_name"],
            "operate": operate,
            "target": target,
            "effect": "local_horizontal_view_changed",
        }
        add_transition(graph, trans)
        save_navigation_graph(graph, work_dir)
        print("✓ 已记录横向滑动 transition（from_page == to_page）")
        return
    if choice == "s":
        operate, target, effect = prompt_special_target()
    else:
        try:
            selected = candidates[int(choice) - 1]
        except Exception:
            print("✗ 无效选择")
            return
        operate = "tap"
        target = selected["suggested_target"]
        effect = input("effect（可空/dialog）: ").strip() or None
        expect = input("expect（可空/dialog/same_page/new_page/list_changed）: ").strip()
        if expect:
            target["expect"] = expect
    pending = {"from_page": state["page_name"], "operate": operate, "target": target, "created_at": now_iso()}
    if effect:
        pending["effect"] = effect
    save_json(pending, pending_transition_path(work_dir), "未完成导航转移")
    _ = raw_root  # 保持 raw dump 与 pretty JSON 只由 capture_artifacts 负责生成，不使用 annotate 后内容覆盖。
    print("请在设备上手动执行该动作进入目标页面后，再运行 nav-record 补全 transition。")


def build_nav_path(work_dir: Path, to_page: str, description: str = "") -> None:
    graph = load_navigation_graph(work_dir)
    queue: List[Tuple[str, List[Dict[str, Any]]]] = [("Pages_root", [])]
    seen = {"Pages_root"}
    while queue:
        page, path = queue.pop(0)
        if page == to_page:
            result = {
                "package_name": graph.get("package_name", PACKAGE_NAME),
                "main_page_name": graph.get("main_page_name", MAIN_PAGE_NAME),
                "page_description": description or graph.get("states", {}).get(to_page, {}).get("page_description", to_page),
                "path_snapshot": path,
            }
            save_json(result, path_cases_path(work_dir), "轻量路径用例")
            return
        for t in graph.get("transitions", []):
            if t.get("from_page") != page:
                continue
            nxt = t.get("to_page")
            step = light_step_from_transition(t)
            # 普通纵向 scroll 永不写入 path_snapshot；目前录制器也不会生成该动作。
            if step.get("operate") == "scroll" and step.get("axis") != "horizontal":
                continue
            if nxt not in seen:
                seen.add(nxt)
                queue.append((nxt, path + [step]))
    print(f"✗ 未找到从 Pages_root 到 {to_page} 的路径")


def main() -> None:
    args = parse_args()
    work_dir = Path(args.work_dir)
    output_dir = Path(args.output_dir) if args.output_dir else work_dir / "outputs" / "latest"
    graph_dir = Path(args.graph_dir) if args.graph_dir else work_dir / "outputs" / "graph"
    output_dir.mkdir(parents=True, exist_ok=True)
    graph_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 60)
    print("设置 UI 采集记录工具：最小交互版 v15-full-tree-reset")
    print("=" * 60)
    print(f"工作目录: {work_dir}")
    print(f"输出目录: {output_dir}")
    print(f"图谱目录: {graph_dir}")

    if args.nav_pending_clear:
        clear_pending_transition(work_dir)
        return
    if args.nav_graph_show:
        print_nav_graph(work_dir)
        return
    if args.nav_path_to:
        build_nav_path(work_dir, args.nav_path_to, args.description)
        return
    if args.nav_record:
        run_nav_record(args, work_dir, output_dir)
        return

    if args.reset == "__ALL__" and graph_dir.exists():
        shutil.rmtree(graph_dir)
        graph_dir.mkdir(parents=True, exist_ok=True)
        print(f"✓ 已清空旧图谱目录: {graph_dir}")
        return

    index_path = graph_dir / "settings_nodes_index.json"
    index = load_index(index_path)
    scrub_index_privacy(index)
    drop_sensitive_entry_controls(index)

    if handle_state_machine_maintenance_commands(index, graph_dir, args):
        return

    if handle_graph_maintenance_commands(index, graph_dir, args):
        return

    if not args.skip_capture:
        if not capture_artifacts(args.device_id, output_dir):
            return
    else:
        print("已跳过 hdc 采集，仅解析已有 JSON")

    json_path = Path(args.json) if args.json else output_dir / "current_ui_tree.json"
    if not json_path.exists():
        print(f"✗ JSON 文件不存在: {json_path}")
        return

    root_json = load_json(json_path)
    annotate(root_json)
    page = page_identity(root_json)
    controls = extract_controls(root_json)

    if args.parent_node_id:
        parent_node_id = args.parent_node_id
        new_active_node_id = args.parent_node_id
        selected_node = None
        print(f"✓ 已通过参数指定 parent_node_id: {parent_node_id}")
    elif args.no_prompt:
        parent_node_id = str(index.get("active_node_id") or "root")
        new_active_node_id = parent_node_id
        selected_node = None
        print(f"✓ --no-prompt：继续记录当前 active_node_id: {parent_node_id}")
    else:
        parent_node_id, new_active_node_id, selected_node = choose_parent_by_number(index, page)

    page_id = merge_current_page(index, page, controls, parent_node_id=parent_node_id)
    screen_metrics = screen_metrics_from_root(root_json)
    component_inventory = extract_component_inventory(root_json, page_id, controls)
    merge_page_components(index, page_id, component_inventory)
    signature = build_page_signature(root_json, page, controls, component_inventory=component_inventory)
    if selected_node:
        tree = build_tree(index)
        transition = build_transition_event(
            index,
            selected_node,
            page,
            signature,
            tree["root"],
            page_id=page_id,
            screen_metrics=screen_metrics,
        )
        verify = build_execution_verify(transition["transition_id"], signature)
    else:
        transition = None
        verify = None
    persist_state_machine_record(
        index=index,
        page_id=page_id,
        selected_node=selected_node,
        page=page,
        signature=signature,
        transition=transition,
        verify=verify,
        state_id=new_active_node_id,
        state_type="page",
    )
    scrub_index_privacy(index)
    drop_sensitive_entry_controls(index)
    index["active_node_id"] = new_active_node_id or "root"
    index["last_parent_node_id"] = parent_node_id
    save_outputs(index, graph_dir)

    print("\n" + "=" * 60)
    print("当前页面")
    print("=" * 60)
    print(f"title={page['title']}")
    print(f"page_id={page['page_id']}")
    print(f"nav_key={page.get('nav_key', '')}")
    print(f"识别控件数={len(controls)}")
    print(f"组件事实数={len(component_inventory)}")
    print(f"本次挂载 parent_node_id={parent_node_id}")
    print(f"新的 active_node_id={index.get('active_node_id', 'root')}")

    print("\n" + "=" * 60)
    print("当前页控件")
    print("=" * 60)
    for i, c in enumerate(controls):
        center_part = f", bounds_center={c.get('bounds_center')}" if c.get("locator") == "bounds_center" else ""
        safe_value = sanitize_value(c.get("value"))
        value_part = f", value={safe_value}" if safe_value else ""
        print(
            f"{i}. [{c.get('record_group')}/{c.get('kind')}] "
            f"text=\"{c.get('text')}\", key={c.get('key') or '(无 key)'}, "
            f"locator={c.get('locator')}{center_part}, bounds={c.get('bounds')}{value_part}"
        )

    print("\n" + "=" * 60)
    print("输出文件")
    print("=" * 60)
    print(f"可读树摘要: {graph_dir / 'settings_ui_graph_readable.txt'}")
    print(f"设置层级树: {graph_dir / 'settings_tree.json'}")
    print(f"设置节点索引/数据库: {graph_dir / 'settings_nodes_index.json'}")
    print(f"页面组件结构树: {graph_dir / 'settings_page_component_tree.json'}")
    print(f"页面组件事实库: {graph_dir / 'settings_page_components.jsonl'}")
    print(f"页面组件报告: {graph_dir / 'settings_components_report.md'}")
    print("执行完成。")

# ============================================================
# 状态机核心函数
# ============================================================

def ensure_state_machine(index):
    """确保 index 中存在 state_machine 字段结构"""
    if "state_machine" not in index:
        index["state_machine"] = {
            "schema_version": "0.1",
            "states": {},
            "transitions": {},
            "signatures": {},
            "execution_verify": {}
        }
    sm = index["state_machine"]
    for key in ["states", "transitions", "signatures", "execution_verify"]:
        if key not in sm:
            sm[key] = {}


def is_stable_key(key):
    """判断 key 是否适合作为页面签名的一部分。"""
    text = str(key or "").strip()
    if not text:
        return False
    if "*" in text or "AvailableDeviceGroup" in text:
        return False
    stable_keywords = (
        "searchButton",
        "wifi_entry",
        "mobile_network_entry",
        "Setting.MobileNetwork.",
    )
    if any(keyword in text for keyword in stable_keywords):
        return True
    if re.search(r"\d{6,}", text):
        return False
    if re.search(r"[0-9a-fA-F]{8,}", text):
        return False
    if re.search(r"(?:[0-9A-Fa-f]{2}[:-]){3,}[0-9A-Fa-f]{2}", text):
        return False
    if len(text) > 48 and re.fullmatch(r"[A-Za-z0-9_.:-]+", text):
        return False
    return bool(re.search(r"[A-Za-z_\u4e00-\u9fff]", text))


def is_stable_text(text):
    """判断文本是否适合作为页面签名的一部分。"""
    value = clean_label(str(text or "").strip())
    if not value or value in NOISE_TEXTS:
        return False
    if re.fullmatch(r"\d+(\.\d+)?", value):
        return False
    if re.fullmatch(r"\d{1,2}:\d{2}(:\d{2})?", value):
        return False
    if re.fullmatch(r"\d{4}[-/.]\d{1,2}[-/.]\d{1,2}.*", value):
        return False
    if "*" in value:
        return False
    if len(value) > 48 and re.fullmatch(r"[A-Za-z0-9_.:-]+", value):
        return False
    if re.search(r"[0-9a-fA-F]{12,}", value):
        return False
    if re.search(r"\d{8,}", value):
        return False
    if re.search(r"(?:[0-9A-Fa-f]{2}[:-]){3,}[0-9A-Fa-f]{2}", value):
        return False
    return len(value) < 50


def append_unique_limited(items, value, limit):
    if value and value not in items and len(items) < limit:
        items.append(value)


def find_nearest_non_group_parent(node_id, tree_root):
    """找到节点最近的非 group 父节点，用于确定 from_state_id"""
    if node_id == "root" or not node_id:
        return "root"

    current_node = find_node_by_id(tree_root, node_id)
    if not current_node:
        return "root"

    parent_id = str(current_node.get("parent_id") or "")
    while parent_id and parent_id != "root":
        parent_node = find_node_by_id(tree_root, parent_id)
        if not parent_node:
            break

        parent_type = str(parent_node.get("type") or "")
        if parent_type not in GROUP_NODE_TYPES:
            return parent_id

        parent_id = str(parent_node.get("parent_id") or "")

    return "root"


def state_reference_from_index(index, state_id):
    ensure_state_machine(index)
    sm = index.get("state_machine", {})
    state = sm.get("states", {}).get(state_id, {})
    page_id = str(state.get("page_id") or "")
    page = index.get("pages", {}).get(page_id, {}) if page_id else {}
    signature_id = str(state.get("signature_id") or page.get("signature_id") or "")
    signature = sm.get("signatures", {}).get(signature_id, {}) if signature_id else {}
    return {
        "state_id": state_id,
        "page_id": page_id,
        "title": state.get("title") or page.get("title") or signature.get("page_title", ""),
        "nav_key": state.get("nav_key") or page.get("nav_key") or signature.get("nav_key", ""),
        "signature_id": signature_id,
    }


def state_reference_from_current(page_id, page, signature, state_id):
    return {
        "state_id": state_id,
        "page_id": page_id,
        "title": page.get("title", ""),
        "nav_key": page.get("nav_key", ""),
        "signature_id": signature.get("signature_id", ""),
    }


def build_tap_target(selected_node, screen_metrics):
    text = str(selected_node.get("text") or selected_node.get("name") or "")
    key = str(selected_node.get("key") or "")
    node_type = str(selected_node.get("type") or "")
    bounds_center = selected_node.get("bounds_center")
    screen_size = screen_metrics.get("screen_size") if isinstance(screen_metrics, dict) else None
    fallback_order = []

    if key and is_stable_key(key):
        preferred = "key"
        fallback_order.append("key")
    elif text and is_stable_text(text):
        preferred = "text_type"
    else:
        preferred = "bounds_center"

    if text and is_stable_text(text):
        fallback_order.append("text_type")
    if selected_node.get("type_path"):
        fallback_order.append("type_path")
    if bounds_center:
        fallback_order.extend(["bounds_center", "normalized_center"])

    deduped_fallback = []
    for item in fallback_order:
        if item not in deduped_fallback:
            deduped_fallback.append(item)

    return {
        "preferred": preferred,
        "fallback_order": deduped_fallback,
        "coordinate_space": screen_metrics.get("coordinate_space", "screen_absolute_px") if isinstance(screen_metrics, dict) else "screen_absolute_px",
        "screen_size": screen_size,
        "key": key,
        "key_fingerprint": selected_node.get("key_fingerprint", ""),
        "text": text,
        "type": node_type,
        "kind": str(selected_node.get("kind") or ""),
        "type_path": str(selected_node.get("type_path") or ""),
        "index_path": str(selected_node.get("index_path") or ""),
        "bounds": str(selected_node.get("bounds") or ""),
        "bounds_center": bounds_center,
        "normalized_center": normalized_center(bounds_center, screen_size),
        "locator": str(selected_node.get("locator") or ""),
    }


def build_page_signature(root_json, page, controls, component_inventory=None, is_float=False):
    """生成页面签名，用于自动化测试时识别当前 UI 是否处于目标页面"""
    title = str(page.get("title") or "")
    nav_key = str(page.get("nav_key") or "")

    required_keys_any = []
    required_texts_any = []

    for ctrl in controls:
        ctrl_key = str(ctrl.get("key") or "")
        ctrl_text = str(ctrl.get("text") or "").strip()

        if is_sensitive_key(ctrl_key) or is_sensitive_entry_control(ctrl):
            continue

        if is_stable_key(ctrl_key):
            append_unique_limited(required_keys_any, ctrl_key, 5)

        if is_stable_text(ctrl_text):
            append_unique_limited(required_texts_any, clean_label(ctrl_text), 8)

    signature_content = f"{title}|{nav_key}|{'|'.join(sorted(required_keys_any))}|{'|'.join(sorted(required_texts_any))}"
    signature_id = hashlib.sha256(signature_content.encode('utf-8', errors='ignore')).hexdigest()[:16]

    return {
        "signature_id": signature_id,
        "page_title": title,
        "nav_key": nav_key,
        "required_title": title,
        "required_keys_any": required_keys_any,
        "required_texts_any": required_texts_any,
        "match_policy": {
            "min_score": 0.65,
            "title_weight": 0.35,
            "key_weight": 0.35,
            "text_weight": 0.30
        },
        "is_float": is_float,
        "created_at": now_iso()
    }


def build_transition_event(index, selected_node, current_page, signature, tree_root, page_id="", screen_metrics=None):
    """构建状态机转移事件"""
    screen_metrics = screen_metrics or {}
    selected_node_id = str(selected_node.get("node_id") or "")
    from_state_id = find_nearest_non_group_parent(selected_node_id, tree_root)
    to_state_id = selected_node_id
    bounds_center = selected_node.get("bounds_center")
    screen_size = screen_metrics.get("screen_size")

    trigger_node = {
        "node_id": selected_node_id,
        "name": str(selected_node.get("name") or ""),
        "text": str(selected_node.get("text") or selected_node.get("name") or ""),
        "type": str(selected_node.get("type") or ""),
        "kind": str(selected_node.get("kind") or ""),
        "key": str(selected_node.get("key") or ""),
        "key_fingerprint": str(selected_node.get("key_fingerprint") or ""),
        "locator": str(selected_node.get("locator") or ""),
        "type_path": str(selected_node.get("type_path") or ""),
        "index_path": str(selected_node.get("index_path") or ""),
        "bounds": str(selected_node.get("bounds") or ""),
        "bounds_center": bounds_center,
        "coordinate_space": screen_metrics.get("coordinate_space", "screen_absolute_px"),
        "screen_size": screen_size,
        "normalized_center": normalized_center(bounds_center, screen_size),
    }
    from_state = state_reference_from_index(index, from_state_id)
    to_state = state_reference_from_current(page_id, current_page, signature, to_state_id)
    tap_target = build_tap_target(selected_node, screen_metrics)

    transition_content = f"{from_state_id}|{to_state_id}|tap|{page_id}|{signature.get('signature_id', '')}"
    transition_id = hashlib.sha256(transition_content.encode('utf-8', errors='ignore')).hexdigest()[:16]

    return {
        "transition_id": transition_id,
        "from_state_id": from_state_id,
        "from_state": from_state,
        "event_type": "tap",
        "trigger_node": trigger_node,
        "tap_target": tap_target,
        "to_state_id": to_state_id,
        "to_state": to_state,
        "to_page_id": page_id,
        "to_page_title": current_page.get("title", ""),
        "to_page_nav_key": current_page.get("nav_key", ""),
        "signature_id": signature.get("signature_id", ""),
        "created_at": now_iso(),
        "verify_required": True
    }


def build_execution_verify(transition_id, signature):
    """构建自动化执行验证规则"""
    return {
        "transition_id": transition_id,
        "required": True,
        "after_action": "dump_ui_tree",
        "match_target": "page_signature",
        "signature_id": signature.get("signature_id", ""),
        "on_success": "update_active_state",
        "on_failure": "do_not_update_state_and_report_mismatch"
    }


def persist_state_machine_record(index, page_id, selected_node, page, signature, transition, verify, state_id, state_type="page"):
    """持久化状态机记录到 index"""
    ensure_state_machine(index)
    sm = index["state_machine"]

    state_record = {
        "state_id": state_id,
        "tree_node_id": state_id,
        "page_id": page_id,
        "title": page.get("title", ""),
        "nav_key": page.get("nav_key", ""),
        "signature_id": signature["signature_id"],
        "state_type": state_type,
        "last_seen_at": now_iso()
    }
    sm["states"][state_id] = state_record
    sm["signatures"][signature["signature_id"]] = signature

    if transition and verify:
        sm["transitions"][transition["transition_id"]] = transition
        sm["execution_verify"][transition["transition_id"]] = verify

    if page_id in index["pages"]:
        index["pages"][page_id]["state_id"] = state_id
        index["pages"][page_id]["signature_id"] = signature["signature_id"]
        if transition:
            index["pages"][page_id]["incoming_transition_id"] = transition["transition_id"]


if __name__ == "__main__":
    main()
