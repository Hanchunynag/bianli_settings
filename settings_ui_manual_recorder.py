#!/usr/bin/env python3
# -*- coding: utf-8 -*-

r"""
HarmonyOS 设置 UI 手动采集记录工具。

功能：
1. 可通过 hdc 采集当前页面 current_ui_tree.json 与 current_screen.png；
2. 解析当前页面的入口控件、操作控件、导航控件；
3. 通过人工数字选择确认本次采集结果应挂载到哪个树节点；
4. 每次运行更新：
   - outputs/graph/settings_ui_graph_readable.txt
   - outputs/graph/settings_tree.json
   - outputs/graph/settings_nodes_index.json

交互规则：
-1：返回父节点并重新选择；当前节点为 root 时会报错并继续选择。
 0：继续记录当前 active_node_id。
 1-x：把本次采集结果挂载到对应候选分支下，并将 active_node_id 更新为该分支。

隐私规则：
只要控件 key 中包含 *，即视为敏感 key：
- 不在 txt/json/终端中展示 key；
- 不在 txt/json/终端中展示 value；
- locator 改为 bounds_center，使用坐标作为兜底点击方式。

运行：
  python .\settings_ui_manual_recorder.py

只解析已有 JSON：
  python .\settings_ui_manual_recorder.py --skip-capture

清空旧树重新记录：
  python .\settings_ui_manual_recorder.py --reset

非交互指定父节点：
  python .\settings_ui_manual_recorder.py --parent-node-id "root/WLAN"
"""

import argparse
import json
import re
import shutil
import subprocess
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

Node = Dict[str, Any]

DEFAULT_DEVICE_ID = "68Q0223918000004"
DEFAULT_WORK_DIR = Path(r"D:\hanchunyang_6_3\AItest")
DEFAULT_OUTPUT_DIR = DEFAULT_WORK_DIR / "outputs" / "latest"
DEFAULT_GRAPH_DIR = DEFAULT_WORK_DIR / "outputs" / "graph"

SCHEMA_VERSION = "0.3-manual-choice"
NOISE_TEXTS = {"tab_unlock"}


# =============================================================================
# 基础 IO / 命令
# =============================================================================

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
    except Exception as exc:
        print(f"✗ 执行异常: {exc}")
        return False


def check_hdc() -> bool:
    if run_cmd(["hdc", "version"]):
        print("✓ hdc 可用")
        return True
    print("✗ hdc 不可用，请确认 hdc 已加入 PATH")
    return False


def capture_artifacts(device_id: str, output_dir: Path) -> bool:
    output_dir.mkdir(parents=True, exist_ok=True)
    base_cmd = ["hdc", "-t", device_id]
    commands = [
        (base_cmd + ["shell", "uitest", "dumpLayout", "-p", "/data/local/tmp/current_ui_tree.json"], "dumpLayout"),
        (base_cmd + ["file", "recv", "/data/local/tmp/current_ui_tree.json", "current_ui_tree.json"], "拉取 JSON"),
        (base_cmd + ["shell", "uitest", "screenCap", "-p", "/data/local/tmp/current_screen.png"], "screenCap"),
        (base_cmd + ["file", "recv", "/data/local/tmp/current_screen.png", "current_screen.png"], "拉取截图"),
    ]
    for cmd, desc in commands:
        if not run_cmd(cmd, cwd=str(output_dir)):
            print(f"✗ {desc} 失败")
            return False
        print(f"✓ {desc} 成功")
    return True


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8-sig") as f:
        return json.load(f)


def save_json(data: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def save_text(text: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        f.write(text.rstrip() + "\n")


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


# =============================================================================
# UI 树通用工具
# =============================================================================

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


def get_attr(node: Node, name: str, default: str = "") -> str:
    return str(attrs(node).get(name, default) or "")


def get_type(node: Node) -> str:
    a = attrs(node)
    return str(a.get("type") or a.get("className") or a.get("componentType") or "")


def get_key(node: Node) -> str:
    a = attrs(node)
    return str(a.get("key") or a.get("id") or "")


def get_text(node: Node) -> str:
    a = attrs(node)
    return str(a.get("text") or a.get("originalText") or "").strip()


def is_visible(node: Node) -> bool:
    return get_attr(node, "visible", "true").lower() != "false"


def is_enabled(node: Node) -> bool:
    return get_attr(node, "enabled", "true").lower() != "false"


def walk(node: Node, depth: int = 0, parent: Optional[Node] = None) -> Iterable[Tuple[Node, int, Optional[Node]]]:
    yield node, depth, parent
    for child in children(node):
        yield from walk(child, depth + 1, node)


def find_all(root: Node, pred) -> List[Node]:
    return [node for node, _, _ in walk(root) if pred(node)]


def any_desc(root: Node, pred) -> bool:
    return any(pred(node) for node, _, _ in walk(root))


def parse_rect(bounds: Any) -> Dict[str, Any]:
    result = {
        "left": 0,
        "top": 0,
        "right": 0,
        "bottom": 0,
        "width": 0,
        "height": 0,
        "center": None,
        "area": 0,
        "valid": False,
    }
    if not bounds:
        return result
    try:
        if isinstance(bounds, dict):
            left = int(bounds.get("left", 0))
            top = int(bounds.get("top", 0))
            right = int(bounds.get("right", 0))
            bottom = int(bounds.get("bottom", 0))
        else:
            nums = re.findall(r"-?\d+", str(bounds))
            if len(nums) < 4:
                return result
            left, top, right, bottom = map(int, nums[:4])
        if right > left and bottom > top:
            result.update(
                left=left,
                top=top,
                right=right,
                bottom=bottom,
                width=right - left,
                height=bottom - top,
                center=[(left + right) // 2, (top + bottom) // 2],
                area=(right - left) * (bottom - top),
                valid=True,
            )
    except Exception:
        pass
    return result


def clean_label(text: str) -> str:
    text = str(text or "").strip()
    if not text:
        return ""
    parts = [p.strip() for p in re.split(r"[,，]", text) if p.strip()]
    parts = [p for p in parts if p not in NOISE_TEXTS]
    return parts[0] if parts else text


def meaningful_texts(root: Node, include_numeric: bool = False) -> List[str]:
    result: List[str] = []
    for node, _, _ in walk(root):
        text = clean_label(get_text(node))
        if not text or text in NOISE_TEXTS:
            continue
        if not include_numeric and re.fullmatch(r"\d+(\.\d+)?", text):
            continue
        if text not in result:
            result.append(text)
    return result


def annotate_runtime_metadata(root: Node) -> None:
    def rec(node: Node, parent: Optional[Node], type_path: str, index_path: str) -> None:
        node["__parent"] = parent
        node["__type_path"] = type_path
        node["__index_path"] = index_path
        type_count: Dict[str, int] = defaultdict(int)
        for idx, child in enumerate(children(node)):
            t = get_type(child) or "Unknown"
            type_idx = type_count[t]
            type_count[t] += 1
            rec(child, node, f"{type_path}/{t}[{type_idx}]", f"{index_path}.{idx}")
    rec(root, None, "/root", "0")


def ancestor_chain(node: Node, max_depth: int = 8) -> List[Node]:
    result: List[Node] = []
    cur = node.get("__parent")
    while isinstance(cur, dict) and len(result) < max_depth:
        result.append(cur)
        cur = cur.get("__parent")
    return result


def path_has_segment(path: str, segment: str) -> bool:
    return re.search(rf"/(?:{re.escape(segment)})(?:\[\d+\])?(?:/|$)", path or "") is not None


def nearest_semantic_label(node: Node) -> str:
    texts = meaningful_texts(node)
    if texts:
        return texts[0]
    for anc in ancestor_chain(node, 6):
        if get_type(anc) not in {"Row", "Column", "ListItem", "ListItemGroup", "Button"}:
            continue
        texts = meaningful_texts(anc)
        if texts:
            return texts[0]
    return ""


def find_page_title(root: Node) -> Tuple[str, str]:
    candidates = find_all(
        root,
        lambda n: get_type(n) == "Text" and get_key(n).endswith("title_id") and get_text(n),
    )
    if candidates:
        n = candidates[0]
        return clean_label(get_text(n)), get_key(n)
    for title_bar in find_all(root, lambda n: "TitleBar" in get_type(n)):
        texts = meaningful_texts(title_bar)
        if texts:
            return texts[0], ""
    return "", ""


def find_nav_destination_key(root: Node) -> str:
    candidates: List[Tuple[int, str]] = []
    for node, depth, _ in walk(root):
        if get_type(node) == "NavDestination" and is_visible(node):
            key = get_key(node)
            if key:
                candidates.append((depth, key))
    if not candidates:
        return ""
    candidates.sort(key=lambda x: -x[0])
    return candidates[0][1]


def infer_page_identity(root: Node) -> Dict[str, str]:
    title, title_key = find_page_title(root)
    nav_key = find_nav_destination_key(root)
    if nav_key:
        page_id = nav_key
    elif title:
        page_id = f"title::{title}"
    else:
        texts = meaningful_texts(root)[:8]
        page_id = "unknown::" + str(abs(hash("|".join(texts))))
    return {
        "page_id": page_id,
        "title": title or page_id,
        "title_key": title_key,
        "nav_key": nav_key,
    }


# =============================================================================
# 敏感信息处理
# =============================================================================

def is_sensitive_key(key: str) -> bool:
    return "*" in str(key or "")


def sanitize_value(value: Any, sensitive: bool = False) -> str:
    if value in (None, ""):
        return ""
    if sensitive:
        return ""
    return str(value)


def sanitize_key(key: str) -> str:
    return "" if is_sensitive_key(key) else str(key or "")


# =============================================================================
# 当前页控件提取
# =============================================================================

def make_target(name: str, node: Node, kind: str, record_group: str, value: str = "", navigates: Any = "unknown") -> Dict[str, Any]:
    raw_key = get_key(node)
    sensitive = is_sensitive_key(raw_key)
    rect = parse_rect(get_attr(node, "bounds"))
    locator = "bounds_center" if sensitive or not raw_key else "key"
    return {
        "name": name,
        "text": name,
        "kind": kind,
        "record_group": record_group,
        "value": sanitize_value(value, sensitive),
        "raw_key_sensitive": sensitive,
        "sensitive_key": sensitive,
        "key": sanitize_key(raw_key),
        "type": get_type(node),
        "bounds": get_attr(node, "bounds"),
        "bounds_center": rect["center"],
        "locator": locator,
        "navigates": navigates,
    }


def target_signature(t: Dict[str, Any]) -> str:
    if t.get("key"):
        return f"key::{t['key']}"
    name = t.get("name", "")
    kind = t.get("kind", "")
    return f"name::{kind}::{name}::{t.get('bounds', '')}"


def append_unique(targets: List[Dict[str, Any]], target: Dict[str, Any]) -> None:
    sig = target_signature(target)
    if sig not in {target_signature(t) for t in targets}:
        targets.append(target)


def find_main_list(root: Node) -> Optional[Node]:
    lists = find_all(root, lambda n: get_type(n) == "List" and to_bool(attrs(n).get("scrollable", False)) and is_visible(n))
    if not lists:
        return None
    return max(lists, key=lambda n: parse_rect(get_attr(n, "bounds"))["area"])


def find_nav_content_root(root: Node) -> Node:
    contents = find_all(root, lambda n: get_type(n) == "NavDestinationContent" and is_visible(n))
    if contents:
        return max(contents, key=lambda n: parse_rect(get_attr(n, "bounds"))["area"])
    return root


def label_for_toggle(toggle: Node, root: Node) -> str:
    tb = parse_rect(get_attr(toggle, "bounds"))
    best_text: Optional[Node] = None
    best_dist = 10**9
    if tb["valid"]:
        toggle_y = (tb["top"] + tb["bottom"]) // 2
        toggle_left = tb["left"]
        scopes = ancestor_chain(toggle, 4) + [find_nav_content_root(root)]
        for scope in scopes:
            for text_node in find_all(scope, lambda n: get_type(n) == "Text" and get_text(n)):
                b = parse_rect(get_attr(text_node, "bounds"))
                if not b["valid"]:
                    continue
                if b["left"] > toggle_left:
                    continue
                text_y = (b["top"] + b["bottom"]) // 2
                dist = abs(text_y - toggle_y)
                if dist < best_dist:
                    best_dist = dist
                    best_text = text_node
            if best_text is not None and best_dist < 120:
                break
    if best_text is not None and best_dist < 120:
        return clean_label(get_text(best_text))
    key = get_key(toggle).lower()
    if "wlan" in key:
        return "WLAN"
    if "bluetooth" in key:
        return "蓝牙"
    return "开关"


def extract_titlebar_targets(root: Node) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    for btn in find_all(
        root,
        lambda n: get_type(n) == "Button"
        and to_bool(attrs(n).get("clickable", False))
        and is_visible(n)
        and is_enabled(n)
        and path_has_segment(str(n.get("__type_path", "")), "TitleBar"),
    ):
        rect = parse_rect(get_attr(btn, "bounds"))
        if not rect["valid"]:
            continue
        if rect["left"] < 220:
            target = make_target("返回按钮", btn, "nav_back", "nav_controls", navigates=False)
        else:
            label = nearest_semantic_label(btn) or "标题栏按钮"
            target = make_target(label, btn, "titlebar_button", "nav_controls", navigates=False)
        append_unique(result, target)
    return result


def extract_toggle_and_slider_targets(root: Node) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    content = find_nav_content_root(root)
    for toggle in find_all(
        content,
        lambda n: (
            get_type(n) in {"Toggle", "Switch"}
            or (to_bool(attrs(n).get("checkable", False)) and get_type(n) != "Radio")
        )
        and is_visible(n)
        and is_enabled(n),
    ):
        state = "开启" if to_bool(attrs(toggle).get("checked", False)) else "关闭"
        append_unique(result, make_target(label_for_toggle(toggle, root), toggle, "toggle", "operation_controls", value=state, navigates=False))

    for slider in find_all(content, lambda n: get_type(n) == "Slider" and is_visible(n) and is_enabled(n)):
        key = get_key(slider).lower()
        title = "亮度滑条" if "brightness" in key else "滑条"
        append_unique(result, make_target(title, slider, "slider", "operation_controls", value=get_text(slider), navigates=False))
    return result


def is_entry_container(node: Node) -> bool:
    if get_type(node) not in {"Row", "ListItem", "Column"}:
        return False
    if not to_bool(attrs(node).get("clickable", False)):
        return False
    if not is_visible(node) or not is_enabled(node):
        return False
    if any_desc(node, lambda x: get_type(x) in {"Toggle", "Switch", "Slider", "Radio"}):
        return False
    rect = parse_rect(get_attr(node, "bounds"))
    if not rect["valid"]:
        return False
    if rect["width"] < 300 or not (45 <= rect["height"] <= 380):
        return False
    return True


def extract_entry_targets(root: Node) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    main_list = find_main_list(root) or find_nav_content_root(root)
    for row in find_all(main_list, is_entry_container):
        texts = meaningful_texts(row)
        if not texts:
            continue
        title = texts[0]
        value = texts[1] if len(texts) > 1 else ""
        combined = f"{title} {value} {get_attr(row, 'description')}'.lower()"
        if any(word in combined for word in ["最近任务", "返回主屏幕", "单指双击即可", "打开最近任务"]):
            continue
        append_unique(result, make_target(title, row, "row", "entry_controls", value=value, navigates="unknown"))
    return result


def extract_current_page_targets(root: Node) -> List[Dict[str, Any]]:
    annotate_runtime_metadata(root)
    targets: List[Dict[str, Any]] = []
    for t in extract_titlebar_targets(root):
        append_unique(targets, t)
    for t in extract_toggle_and_slider_targets(root):
        append_unique(targets, t)
    for t in extract_entry_targets(root):
        append_unique(targets, t)
    return targets


# =============================================================================
# 树数据库
# =============================================================================

def safe_segment(name: str) -> str:
    name = str(name or "").strip()
    if not name:
        return "unnamed"
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
    for key, value in extra.items():
        if value not in (None, ""):
            node[key] = value
    return node


def init_db() -> Dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "app": "Settings",
        "updated_at": now_iso(),
        "total_capture_runs": 0,
        "active_node_id": "root",
        "root": make_node("root", None, "root", 0, "root"),
    }


def normalize_loaded_db(data: Dict[str, Any]) -> Dict[str, Any]:
    if not data or "root" not in data:
        return init_db()
    data.setdefault("schema_version", SCHEMA_VERSION)
    data.setdefault("app", "Settings")
    data.setdefault("updated_at", now_iso())
    data.setdefault("total_capture_runs", 0)
    data.setdefault("active_node_id", "root")
    return data


def load_db(graph_dir: Path) -> Dict[str, Any]:
    path = graph_dir / "settings_tree.json"
    if not path.exists():
        return init_db()
    try:
        return normalize_loaded_db(load_json(path))
    except Exception:
        return init_db()


def find_node(root: Dict[str, Any], node_id: str) -> Optional[Dict[str, Any]]:
    if root.get("node_id") == node_id:
        return root
    for child in root.get("children", []):
        found = find_node(child, node_id)
        if found:
            return found
    return None


def path_to_node(root: Dict[str, Any], node_id: str) -> List[Dict[str, Any]]:
    path: List[Dict[str, Any]] = []

    def rec(node: Dict[str, Any]) -> bool:
        path.append(node)
        if node.get("node_id") == node_id:
            return True
        for child in node.get("children", []):
            if rec(child):
                return True
        path.pop()
        return False

    rec(root)
    return path


def parent_node_id(node_id: str) -> Optional[str]:
    if node_id == "root":
        return None
    if "/" not in node_id:
        return "root"
    return node_id.rsplit("/", 1)[0] or "root"


def append_or_update_child(parent: Dict[str, Any], child: Dict[str, Any]) -> Dict[str, Any]:
    for old in parent.setdefault("children", []):
        if old.get("node_id") == child.get("node_id"):
            old_children = old.get("children", [])
            old.update({k: v for k, v in child.items() if k != "children"})
            old["children"] = old_children
            return old
    parent.setdefault("children", []).append(child)
    return child


def node_type_for_target(t: Dict[str, Any]) -> str:
    group = t.get("record_group")
    kind = t.get("kind")
    if group == "nav_controls":
        if kind == "nav_back":
            return "nav_back"
        return "titlebar_button" if kind == "titlebar_button" else "nav_control"
    if group == "operation_controls":
        return kind or "operation_control"
    return "page_entry"


def group_for_record_group(parent: Dict[str, Any], record_group: str) -> Dict[str, Any]:
    if record_group == "operation_controls":
        name, node_type = "页面内操作控件", "operation_group"
    elif record_group == "nav_controls":
        name, node_type = "导航控件", "nav_group"
    else:
        return parent
    node_id = f"{parent['node_id']}/{safe_segment(name)}"
    group = make_node(node_id, parent["node_id"], name, parent["depth"] + 1, node_type)
    return append_or_update_child(parent, group)


def target_to_tree_node(t: Dict[str, Any], parent: Dict[str, Any]) -> Dict[str, Any]:
    node_id = f"{parent['node_id']}/{safe_segment(t.get('text') or t.get('name'))}"
    existing = [c for c in parent.get("children", []) if c.get("node_id") == node_id]
    if existing:
        old = existing[0]
        if old.get("key", "") != t.get("key", "") or old.get("bounds", "") != t.get("bounds", ""):
            if t.get("key"):
                node_id = f"{node_id}_{safe_segment(t['key'])[-8:]}"
            else:
                c = t.get("bounds_center") or []
                node_id = f"{node_id}_{c[0]}_{c[1]}" if len(c) == 2 else f"{node_id}_dup"
    return make_node(
        node_id=node_id,
        parent_id=parent["node_id"],
        name=t.get("name", ""),
        text=t.get("text", t.get("name", "")),
        depth=parent["depth"] + 1,
        node_type=node_type_for_target(t),
        kind=t.get("kind", ""),
        record_group=t.get("record_group", ""),
        value=t.get("value", ""),
        key=t.get("key", ""),
        sensitive_key=t.get("sensitive_key", False),
        locator=t.get("locator", ""),
        bounds=t.get("bounds", ""),
        bounds_center=t.get("bounds_center"),
        navigates=t.get("navigates", ""),
        last_seen_at=now_iso(),
    )


def merge_targets_under_parent(db: Dict[str, Any], parent_id: str, targets: List[Dict[str, Any]]) -> None:
    root = db["root"]
    parent = find_node(root, parent_id)
    if parent is None:
        print(f"✗ parent_node_id 不存在: {parent_id}，本次改为 root")
        parent = root
    for t in targets:
        record_group = t.get("record_group")
        if record_group in {"operation_controls", "nav_controls"}:
            attach_parent = group_for_record_group(parent, record_group)
        else:
            attach_parent = parent
        child = target_to_tree_node(t, attach_parent)
        append_or_update_child(attach_parent, child)


def candidate_nodes_for_active(root: Dict[str, Any], active_id: str) -> List[Dict[str, Any]]:
    active = find_node(root, active_id) or root
    result: List[Dict[str, Any]] = []
    for child in active.get("children", []):
        node_type = child.get("type")
        if node_type == "nav_back":
            continue
        if node_type in {"operation_group", "nav_group"}:
            for sub in child.get("children", []):
                if sub.get("type") == "nav_back":
                    continue
                result.append(sub)
            continue
        result.append(child)
    return result


def format_path(root: Dict[str, Any], node_id: str) -> str:
    path = path_to_node(root, node_id)
    return " > ".join([str(n.get("name") or n.get("node_id")) for n in path]) or node_id


def choose_parent_node(db: Dict[str, Any], page_identity: Dict[str, str], parent_node_id_arg: str = "") -> Tuple[str, str]:
    root = db["root"]
    if parent_node_id_arg:
        return parent_node_id_arg, parent_node_id_arg

    active_id = db.get("active_node_id") or "root"
    while True:
        if find_node(root, active_id) is None:
            active_id = "root"
            db["active_node_id"] = "root"
        candidates = candidate_nodes_for_active(root, active_id)

        print("\n" + "=" * 60)
        print("本次采集结果归属确认")
        print("=" * 60)
        print(f"当前页面: {page_identity.get('title')}  page_id={page_identity.get('page_id')}")
        print(f"当前 active_node_id: {active_id}")
        print(f"当前路径: {format_path(root, active_id)}")
        print(f"\n请输入 -1 或 0-{len(candidates)}：")
        print("-1. 返回父节点，只调整 active_node_id 后重新选择")
        print(f"0. 继续记录当前节点：{format_path(root, active_id)}")
        for idx, cand in enumerate(candidates, start=1):
            value = f"，value={cand.get('value')}" if cand.get("value") else ""
            key = f"，key={cand.get('key')}" if cand.get("key") else ""
            print(f"{idx}. 扩展分支：{cand.get('text') or cand.get('name')}  ({cand.get('type')}){value}{key}")

        raw = input("请选择: ").strip()
        try:
            choice = int(raw)
        except ValueError:
            print(f"✗ 输入无效：{raw}，请输入 -1 到 {len(candidates)} 的整数。")
            continue

        if choice == -1:
            p = parent_node_id(active_id)
            if p is None:
                print("✗ 当前节点已经是 root，不能继续返回。")
                continue
            active_id = p
            db["active_node_id"] = active_id
            print(f"✓ 已返回父节点: {format_path(root, active_id)}")
            continue

        if choice == 0:
            return active_id, active_id

        if 1 <= choice <= len(candidates):
            selected = candidates[choice - 1]
            selected_id = selected["node_id"]
            return selected_id, selected_id

        print(f"✗ 输入超出范围：{choice}，请输入 -1 到 {len(candidates)}。")


# =============================================================================
# 输出
# =============================================================================

def build_nodes_index(db: Dict[str, Any]) -> Dict[str, Any]:
    nodes: Dict[str, Any] = {}

    def rec(node: Dict[str, Any]) -> None:
        children = node.get("children", [])
        item = {k: v for k, v in node.items() if k != "children"}
        item["child_ids"] = [c.get("node_id") for c in children]
        nodes[node["node_id"]] = item
        for child in children:
            rec(child)

    rec(db["root"])
    return {
        "schema_version": db.get("schema_version", SCHEMA_VERSION),
        "app": "Settings",
        "updated_at": db.get("updated_at", ""),
        "total_capture_runs": db.get("total_capture_runs", 0),
        "active_node_id": db.get("active_node_id", "root"),
        "nodes": nodes,
    }


def build_readable_tree(db: Dict[str, Any]) -> str:
    lines = [
        "设置 App 手动采集树（最小交互版）",
        f"更新时间：{db.get('updated_at', '')}",
        f"累计采集次数：{db.get('total_capture_runs', 0)}",
        f"当前 active_node_id：{db.get('active_node_id', 'root')}",
        "",
    ]

    def rec(node: Dict[str, Any], indent: int = 0) -> None:
        prefix = "  " * indent
        parts = [f"{prefix}- depth={node.get('depth')} [{node.get('type')}] {node.get('text') or node.get('name')}"]
        if node.get("key"):
            parts.append(f"key={node.get('key')}")
        if node.get("sensitive_key"):
            parts.append("sensitive_key=true")
        if node.get("locator"):
            parts.append(f"locator={node.get('locator')}")
        if node.get("locator") == "bounds_center" and node.get("bounds_center"):
            parts.append(f"bounds_center={node.get('bounds_center')}")
        if node.get("value"):
            parts.append(f"value={node.get('value')}")
        lines.append("，".join(parts))
        for child in node.get("children", []):
            rec(child, indent + 1)

    rec(db["root"], 0)
    return "\n".join(lines)


def save_outputs(db: Dict[str, Any], graph_dir: Path) -> None:
    graph_dir.mkdir(parents=True, exist_ok=True)
    save_json(db, graph_dir / "settings_tree.json")
    save_json(build_nodes_index(db), graph_dir / "settings_nodes_index.json")
    save_text(build_readable_tree(db), graph_dir / "settings_ui_graph_readable.txt")
    print(f"✓ 可读树摘要: {graph_dir / 'settings_ui_graph_readable.txt'}")
    print(f"✓ 设置层级树: {graph_dir / 'settings_tree.json'}")
    print(f"✓ 设置节点索引/数据库: {graph_dir / 'settings_nodes_index.json'}")


def print_current_targets(targets: List[Dict[str, Any]]) -> None:
    print("\n" + "=" * 60)
    print("当前页控件")
    print("=" * 60)
    for idx, t in enumerate(targets):
        parts = [f"{idx}. [{t.get('record_group')}/{t.get('kind')}] text=\"{t.get('text')}\""]
        if t.get("key"):
            parts.append(f"key={t.get('key')}")
        elif t.get("sensitive_key"):
            parts.append("sensitive_key=true")
        else:
            parts.append("key=(无 key)")
        parts.append(f"locator={t.get('locator')}")
        if t.get("locator") == "bounds_center":
            parts.append(f"bounds_center={t.get('bounds_center')}")
        if t.get("bounds"):
            parts.append(f"bounds={t.get('bounds')}")
        if t.get("value"):
            parts.append(f"value={t.get('value')}")
        print(", ".join(parts))


# =============================================================================
# 主流程
# =============================================================================

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="HarmonyOS 设置 UI 手动采集记录工具")
    parser.add_argument("--device-id", default=DEFAULT_DEVICE_ID)
    parser.add_argument("--work-dir", default=str(DEFAULT_WORK_DIR))
    parser.add_argument("--output-dir", default="")
    parser.add_argument("--graph-dir", default="")
    parser.add_argument("--json", default="")
    parser.add_argument("--skip-capture", action="store_true")
    parser.add_argument("--reset", action="store_true", help="清空 outputs/graph 后重新记录")
    parser.add_argument("--parent-node-id", default="", help="非交互模式：直接指定本次采集挂载父节点")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    work_dir = Path(args.work_dir)
    output_dir = Path(args.output_dir) if args.output_dir else work_dir / "outputs" / "latest"
    graph_dir = Path(args.graph_dir) if args.graph_dir else work_dir / "outputs" / "graph"
    output_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 60)
    print("设置 UI 采集记录工具：最小交互版")
    print("=" * 60)
    print(f"工作目录: {work_dir}")
    print(f"输出目录: {output_dir}")
    print(f"图谱目录: {graph_dir}")

    if args.reset and graph_dir.exists():
        shutil.rmtree(graph_dir)
        print(f"✓ 已清空旧图谱目录: {graph_dir}")

    if not args.skip_capture:
        if not check_hdc():
            return
        if not capture_artifacts(args.device_id, output_dir):
            return
    else:
        print("已跳过 hdc 采集，仅解析已有 JSON")

    local_json = Path(args.json) if args.json else output_dir / "current_ui_tree.json"
    if not local_json.exists():
        print(f"✗ JSON 文件不存在: {local_json}")
        return

    root_json = load_json(local_json)
    page_identity = infer_page_identity(root_json)
    targets = extract_current_page_targets(root_json)

    db = load_db(graph_dir)
    parent_id, next_active_id = choose_parent_node(db, page_identity, args.parent_node_id)
    merge_targets_under_parent(db, parent_id, targets)
    db["active_node_id"] = next_active_id
    db["updated_at"] = now_iso()
    db["total_capture_runs"] = int(db.get("total_capture_runs", 0)) + 1
    db["schema_version"] = SCHEMA_VERSION

    save_outputs(db, graph_dir)

    print("\n" + "=" * 60)
    print("当前页面")
    print("=" * 60)
    print(f"title={page_identity.get('title')}")
    print(f"page_id={page_identity.get('page_id')}")
    print(f"nav_key={page_identity.get('nav_key')}")
    print(f"识别控件数={len(targets)}")
    print(f"本次挂载 parent_node_id={parent_id}")
    print(f"新的 active_node_id={next_active_id}")

    print_current_targets(targets)

    print("\n" + "=" * 60)
    print("输出文件")
    print("=" * 60)
    print(f"可读树摘要: {graph_dir / 'settings_ui_graph_readable.txt'}")
    print(f"设置层级树: {graph_dir / 'settings_tree.json'}")
    print(f"设置节点索引/数据库: {graph_dir / 'settings_nodes_index.json'}")
    print("执行完成。")


if __name__ == "__main__":
    main()
