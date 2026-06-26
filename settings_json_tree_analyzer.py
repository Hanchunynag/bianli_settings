#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
脚本1：离线分析 current_ui_tree.json 并更新设置控件树。

AI 使用这个脚本。它不连接手机、不执行 hdc，只接收 JSON 文件。
输出：
  outputs/graph/settings_ui_graph_readable.txt
  outputs/graph/settings_tree.json
  outputs/graph/settings_nodes_index.json

交互：
  -1 返回父节点，root 下返回会报错
   0 继续记录当前 active_node_id
  1-x 将本次采集结果挂到对应候选分支

隐私：只要 key 中包含 *，就不展示 key/value，locator 改为 bounds_center。
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

Node = Dict[str, Any]
DEFAULT_WORK_DIR = Path(r"D:\hanchunyang_6_3\AItest")
SCHEMA_VERSION = "0.4-split-analyzer"
NOISE_TEXTS = {"tab_unlock"}


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


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


def attrs(n: Node) -> Dict[str, Any]:
    return n.get("attributes", n)


def children(n: Node) -> List[Node]:
    return n.get("children", []) or []


def to_bool(v: Any) -> bool:
    if v is True:
        return True
    if v in (False, None):
        return False
    if isinstance(v, (int, float)):
        return v != 0
    return str(v).strip().lower() in {"true", "1", "yes"}


def get_type(n: Node) -> str:
    a = attrs(n)
    return str(a.get("type") or a.get("className") or a.get("componentType") or "")


def get_key(n: Node) -> str:
    a = attrs(n)
    return str(a.get("key") or a.get("id") or "")


def get_text(n: Node) -> str:
    a = attrs(n)
    return str(a.get("text") or a.get("originalText") or "").strip()


def get_attr(n: Node, name: str, default: str = "") -> str:
    return str(attrs(n).get(name, default) or "")


def is_visible(n: Node) -> bool:
    return get_attr(n, "visible", "true").lower() != "false"


def is_enabled(n: Node) -> bool:
    return get_attr(n, "enabled", "true").lower() != "false"


def walk(n: Node, depth: int = 0, parent: Optional[Node] = None) -> Iterable[Tuple[Node, int, Optional[Node]]]:
    yield n, depth, parent
    for c in children(n):
        yield from walk(c, depth + 1, n)


def find_all(root: Node, pred: Callable[[Node], bool]) -> List[Node]:
    return [n for n, _, _ in walk(root) if pred(n)]


def any_desc(root: Node, pred: Callable[[Node], bool]) -> bool:
    return any(pred(n) for n, _, _ in walk(root))


def parse_rect(bounds: Any) -> Dict[str, Any]:
    out = {"left": 0, "top": 0, "right": 0, "bottom": 0, "width": 0, "height": 0, "center": None, "area": 0, "valid": False}
    if not bounds:
        return out
    try:
        nums = re.findall(r"-?\d+", str(bounds))
        if len(nums) < 4:
            return out
        l, t, r, b = map(int, nums[:4])
        if r > l and b > t:
            out.update(left=l, top=t, right=r, bottom=b, width=r-l, height=b-t, center=[(l+r)//2, (t+b)//2], area=(r-l)*(b-t), valid=True)
    except Exception:
        pass
    return out


def clean_label(s: str) -> str:
    parts = [p.strip() for p in re.split(r"[,，]", str(s or "")) if p.strip()]
    parts = [p for p in parts if p not in NOISE_TEXTS]
    return parts[0] if parts else ""


def meaningful_texts(root: Node) -> List[str]:
    out: List[str] = []
    for n, _, _ in walk(root):
        t = clean_label(get_text(n))
        if t and t not in out and not re.fullmatch(r"\d+(\.\d+)?", t):
            out.append(t)
    return out


def annotate(root: Node) -> None:
    def rec(n: Node, parent: Optional[Node], path: str) -> None:
        n["__parent"] = parent
        n["__type_path"] = path
        cnt: Dict[str, int] = defaultdict(int)
        for c in children(n):
            typ = get_type(c) or "Unknown"
            i = cnt[typ]
            cnt[typ] += 1
            rec(c, n, f"{path}/{typ}[{i}]")
    rec(root, None, "/root")


def ancestors(n: Node, max_depth: int = 8) -> List[Node]:
    out: List[Node] = []
    cur = n.get("__parent")
    while isinstance(cur, dict) and len(out) < max_depth:
        out.append(cur)
        cur = cur.get("__parent")
    return out


def path_has(path: str, seg: str) -> bool:
    return re.search(rf"/(?:{re.escape(seg)})(?:\[\d+\])?(?:/|$)", path or "") is not None


def nearest_label(n: Node) -> str:
    texts = meaningful_texts(n)
    if texts:
        return texts[0]
    for a in ancestors(n, 6):
        if get_type(a) in {"Row", "Column", "ListItem", "ListItemGroup", "Button"}:
            texts = meaningful_texts(a)
            if texts:
                return texts[0]
    return ""


def page_identity(root: Node) -> Dict[str, str]:
    title, title_key = "", ""
    titles = find_all(root, lambda n: get_type(n) == "Text" and get_key(n).endswith("title_id") and get_text(n))
    if titles:
        title, title_key = clean_label(get_text(titles[0])), get_key(titles[0])
    else:
        for tb in find_all(root, lambda n: "TitleBar" in get_type(n)):
            ts = meaningful_texts(tb)
            if ts:
                title = ts[0]
                break
    navs = [(d, get_key(n)) for n, d, _ in walk(root) if get_type(n) == "NavDestination" and get_key(n)]
    nav_key = sorted(navs, key=lambda x: -x[0])[0][1] if navs else ""
    pid = nav_key or (f"title::{title}" if title else "unknown::" + str(abs(hash("|".join(meaningful_texts(root)[:8])))))
    return {"page_id": pid, "title": title or pid, "title_key": title_key, "nav_key": nav_key}


def sensitive_key(key: str) -> bool:
    return "*" in str(key or "")


def make_target(name: str, node: Node, kind: str, group: str, value: str = "", navigates: Any = "unknown") -> Dict[str, Any]:
    raw_key = get_key(node)
    sensitive = sensitive_key(raw_key)
    rect = parse_rect(get_attr(node, "bounds"))
    return {
        "name": name,
        "text": name,
        "kind": kind,
        "record_group": group,
        "value": "" if sensitive else str(value or ""),
        "key": "" if sensitive else raw_key,
        "sensitive_key": sensitive,
        "locator": "bounds_center" if sensitive or not raw_key else "key",
        "bounds": get_attr(node, "bounds"),
        "bounds_center": rect["center"],
        "navigates": navigates,
    }


def add_unique(out: List[Dict[str, Any]], t: Dict[str, Any]) -> None:
    sig = (t.get("key") or "", t.get("kind"), t.get("name"), t.get("bounds"))
    if sig not in [(x.get("key") or "", x.get("kind"), x.get("name"), x.get("bounds")) for x in out]:
        out.append(t)


def nav_content(root: Node) -> Node:
    cs = find_all(root, lambda n: get_type(n) == "NavDestinationContent" and is_visible(n))
    return max(cs, key=lambda n: parse_rect(get_attr(n, "bounds"))["area"]) if cs else root


def main_list(root: Node) -> Node:
    ls = find_all(root, lambda n: get_type(n) == "List" and to_bool(attrs(n).get("scrollable", False)) and is_visible(n))
    return max(ls, key=lambda n: parse_rect(get_attr(n, "bounds"))["area"]) if ls else nav_content(root)


def label_for_toggle(toggle: Node, root: Node) -> str:
    tb = parse_rect(get_attr(toggle, "bounds"))
    best, best_dist = None, 10**9
    if tb["valid"]:
        y, left = (tb["top"] + tb["bottom"]) // 2, tb["left"]
        for scope in ancestors(toggle, 4) + [nav_content(root)]:
            for text_node in find_all(scope, lambda n: get_type(n) == "Text" and get_text(n)):
                b = parse_rect(get_attr(text_node, "bounds"))
                if not b["valid"] or b["left"] > left:
                    continue
                dist = abs(((b["top"] + b["bottom"]) // 2) - y)
                if dist < best_dist:
                    best, best_dist = text_node, dist
            if best is not None and best_dist < 120:
                break
    if best is not None and best_dist < 120:
        return clean_label(get_text(best))
    k = get_key(toggle).lower()
    if "wlan" in k:
        return "WLAN"
    if "bluetooth" in k:
        return "蓝牙"
    return "开关"


def extract_targets(root: Node) -> List[Dict[str, Any]]:
    annotate(root)
    out: List[Dict[str, Any]] = []
    for btn in find_all(root, lambda n: get_type(n) == "Button" and to_bool(attrs(n).get("clickable", False)) and is_visible(n) and is_enabled(n) and path_has(str(n.get("__type_path", "")), "TitleBar")):
        r = parse_rect(get_attr(btn, "bounds"))
        if not r["valid"]:
            continue
        name, kind = ("返回按钮", "nav_back") if r["left"] < 220 else (nearest_label(btn) or "标题栏按钮", "titlebar_button")
        add_unique(out, make_target(name, btn, kind, "nav_controls", navigates=False))
    content = nav_content(root)
    for tgl in find_all(content, lambda n: (get_type(n) in {"Toggle", "Switch"} or (to_bool(attrs(n).get("checkable", False)) and get_type(n) != "Radio")) and is_visible(n) and is_enabled(n)):
        state = "开启" if to_bool(attrs(tgl).get("checked", False)) else "关闭"
        add_unique(out, make_target(label_for_toggle(tgl, root), tgl, "toggle", "operation_controls", value=state, navigates=False))
    for sl in find_all(content, lambda n: get_type(n) == "Slider" and is_visible(n) and is_enabled(n)):
        add_unique(out, make_target("亮度滑条" if "brightness" in get_key(sl).lower() else "滑条", sl, "slider", "operation_controls", value=get_text(sl), navigates=False))
    for row in find_all(main_list(root), is_entry_row):
        texts = meaningful_texts(row)
        if not texts:
            continue
        title, value = texts[0], texts[1] if len(texts) > 1 else ""
        combined = f"{title} {value} {get_attr(row, 'description')}".lower()
        if any(x in combined for x in ["最近任务", "返回主屏幕", "单指双击即可", "打开最近任务"]):
            continue
        add_unique(out, make_target(title, row, "row", "entry_controls", value=value, navigates="unknown"))
    return out


def is_entry_row(n: Node) -> bool:
    if get_type(n) not in {"Row", "ListItem", "Column"}:
        return False
    if not to_bool(attrs(n).get("clickable", False)) or not is_visible(n) or not is_enabled(n):
        return False
    if any_desc(n, lambda x: get_type(x) in {"Toggle", "Switch", "Slider", "Radio"}):
        return False
    r = parse_rect(get_attr(n, "bounds"))
    return bool(r["valid"] and r["width"] >= 300 and 45 <= r["height"] <= 380)


def safe_segment(s: str) -> str:
    s = str(s or "").strip() or "unnamed"
    s = re.sub(r"[\\/\s]+", "_", s)
    s = re.sub(r"[^0-9A-Za-z_\-\u4e00-\u9fff]+", "_", s)
    return re.sub(r"_+", "_", s).strip("_") or "unnamed"


def make_node(node_id: str, parent_id: Optional[str], name: str, depth: int, typ: str, **kw: Any) -> Dict[str, Any]:
    node = {"node_id": node_id, "parent_id": parent_id, "name": name, "text": kw.pop("text", name), "depth": depth, "type": typ, "children": []}
    for k, v in kw.items():
        if v not in (None, ""):
            node[k] = v
    return node


def init_db() -> Dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "app": "Settings", "updated_at": now_iso(), "total_capture_runs": 0, "active_node_id": "root", "root": make_node("root", None, "root", 0, "root")}


def sanitize_old(node: Dict[str, Any]) -> None:
    if sensitive_key(node.get("key", "")):
        node["key"] = ""
        node["value"] = ""
        node["sensitive_key"] = True
        if node.get("bounds_center"):
            node["locator"] = "bounds_center"
    for c in node.get("children", []) or []:
        sanitize_old(c)


def load_db(graph_dir: Path) -> Dict[str, Any]:
    p = graph_dir / "settings_tree.json"
    if not p.exists():
        return init_db()
    try:
        db = load_json(p)
        db.setdefault("active_node_id", "root")
        db["schema_version"] = SCHEMA_VERSION
        sanitize_old(db.get("root", {}))
        return db
    except Exception:
        return init_db()


def find_node(root: Dict[str, Any], node_id: str) -> Optional[Dict[str, Any]]:
    if root.get("node_id") == node_id:
        return root
    for c in root.get("children", []) or []:
        f = find_node(c, node_id)
        if f:
            return f
    return None


def path_to(root: Dict[str, Any], node_id: str) -> List[Dict[str, Any]]:
    path: List[Dict[str, Any]] = []
    def rec(n: Dict[str, Any]) -> bool:
        path.append(n)
        if n.get("node_id") == node_id:
            return True
        for c in n.get("children", []) or []:
            if rec(c):
                return True
        path.pop()
        return False
    rec(root)
    return path


def fmt_path(root: Dict[str, Any], node_id: str) -> str:
    return " > ".join(str(n.get("text") or n.get("name")) for n in path_to(root, node_id)) or node_id


def parent_id(node_id: str) -> Optional[str]:
    if node_id == "root":
        return None
    return node_id.rsplit("/", 1)[0] if "/" in node_id else "root"


def append_child(parent: Dict[str, Any], child: Dict[str, Any]) -> Dict[str, Any]:
    for old in parent.setdefault("children", []):
        if old.get("node_id") == child.get("node_id"):
            keep = old.get("children", [])
            old.update({k: v for k, v in child.items() if k != "children"})
            old["children"] = keep
            return old
    parent.setdefault("children", []).append(child)
    return child


def node_type(t: Dict[str, Any]) -> str:
    if t.get("record_group") == "nav_controls":
        return "nav_back" if t.get("kind") == "nav_back" else "titlebar_button"
    if t.get("record_group") == "operation_controls":
        return t.get("kind") or "operation_control"
    return "page_entry"


def group_parent(parent: Dict[str, Any], group: str) -> Dict[str, Any]:
    if group == "operation_controls":
        name, typ = "页面内操作控件", "operation_group"
    elif group == "nav_controls":
        name, typ = "导航控件", "nav_group"
    else:
        return parent
    gid = f"{parent['node_id']}/{safe_segment(name)}"
    return append_child(parent, make_node(gid, parent["node_id"], name, parent["depth"] + 1, typ))


def target_node(t: Dict[str, Any], parent: Dict[str, Any]) -> Dict[str, Any]:
    nid = f"{parent['node_id']}/{safe_segment(t.get('text') or t.get('name'))}"
    return make_node(nid, parent["node_id"], t.get("name", ""), parent["depth"] + 1, node_type(t), text=t.get("text", t.get("name", "")), kind=t.get("kind", ""), record_group=t.get("record_group", ""), value=t.get("value", ""), key=t.get("key", ""), sensitive_key=t.get("sensitive_key", False), locator=t.get("locator", ""), bounds=t.get("bounds", ""), bounds_center=t.get("bounds_center"), navigates=t.get("navigates", ""), last_seen_at=now_iso())


def merge(db: Dict[str, Any], pid: str, targets: List[Dict[str, Any]]) -> None:
    parent = find_node(db["root"], pid) or db["root"]
    for t in targets:
        attach = group_parent(parent, t.get("record_group", ""))
        append_child(attach, target_node(t, attach))


def candidates(root: Dict[str, Any], active_id: str) -> List[Dict[str, Any]]:
    active = find_node(root, active_id) or root
    out: List[Dict[str, Any]] = []
    for c in active.get("children", []) or []:
        if c.get("type") == "nav_back":
            continue
        if c.get("type") in {"operation_group", "nav_group"}:
            out.extend([x for x in c.get("children", []) or [] if x.get("type") != "nav_back"])
        else:
            out.append(c)
    return out


def choose_parent(db: Dict[str, Any], page: Dict[str, str], preset: str = "") -> Tuple[str, str]:
    if preset:
        return preset, preset
    root = db["root"]
    active = db.get("active_node_id") or "root"
    while True:
        if find_node(root, active) is None:
            active = "root"
            db["active_node_id"] = active
        opts = candidates(root, active)
        print("\n" + "=" * 60)
        print("本次采集结果归属确认")
        print("=" * 60)
        print(f"当前页面: {page.get('title')}  page_id={page.get('page_id')}")
        print(f"当前 active_node_id: {active}")
        print(f"当前路径: {fmt_path(root, active)}")
        print(f"\n请输入 -1 或 0-{len(opts)}：")
        print("-1. 返回父节点，只调整 active_node_id 后重新选择")
        print(f"0. 继续记录当前节点：{fmt_path(root, active)}")
        for i, o in enumerate(opts, 1):
            key = f"，key={o.get('key')}" if o.get("key") else ""
            value = f"，value={o.get('value')}" if o.get("value") else ""
            sensitive = "，sensitive_key=true" if o.get("sensitive_key") else ""
            print(f"{i}. 扩展分支：{o.get('text') or o.get('name')}  ({o.get('type')}){value}{key}{sensitive}")
        raw = input("请选择: ").strip()
        try:
            n = int(raw)
        except ValueError:
            print(f"✗ 输入无效：{raw}")
            continue
        if n == -1:
            p = parent_id(active)
            if p is None:
                print("✗ 当前节点已经是 root，不能继续返回。")
                continue
            active = p
            db["active_node_id"] = active
            print(f"✓ 已返回父节点: {fmt_path(root, active)}")
            continue
        if n == 0:
            return active, active
        if 1 <= n <= len(opts):
            return opts[n - 1]["node_id"], opts[n - 1]["node_id"]
        print(f"✗ 输入超出范围：{n}")


def build_index(db: Dict[str, Any]) -> Dict[str, Any]:
    nodes: Dict[str, Any] = {}
    def rec(n: Dict[str, Any]) -> None:
        kids = n.get("children", []) or []
        item = {k: v for k, v in n.items() if k != "children"}
        item["child_ids"] = [c.get("node_id") for c in kids]
        nodes[n["node_id"]] = item
        for c in kids:
            rec(c)
    rec(db["root"])
    return {"schema_version": db.get("schema_version"), "app": "Settings", "updated_at": db.get("updated_at"), "total_capture_runs": db.get("total_capture_runs", 0), "active_node_id": db.get("active_node_id", "root"), "nodes": nodes}


def readable(db: Dict[str, Any]) -> str:
    lines = ["设置 App 手动采集树（最小交互版）", f"更新时间：{db.get('updated_at', '')}", f"累计采集次数：{db.get('total_capture_runs', 0)}", f"当前 active_node_id：{db.get('active_node_id', 'root')}", ""]
    def rec(n: Dict[str, Any], ind: int = 0) -> None:
        parts = [f"{'  '*ind}- depth={n.get('depth')} [{n.get('type')}] {n.get('text') or n.get('name')}"]
        if n.get("key"):
            parts.append(f"key={n.get('key')}")
        if n.get("sensitive_key"):
            parts.append("sensitive_key=true")
        if n.get("locator"):
            parts.append(f"locator={n.get('locator')}")
        if n.get("locator") == "bounds_center" and n.get("bounds_center"):
            parts.append(f"bounds_center={n.get('bounds_center')}")
        if n.get("value"):
            parts.append(f"value={n.get('value')}")
        lines.append("，".join(parts))
        for c in n.get("children", []) or []:
            rec(c, ind + 1)
    rec(db["root"])
    return "\n".join(lines)


def current_summary(page: Dict[str, str], targets: List[Dict[str, Any]]) -> str:
    lines = [f"标题：{page.get('title')}", f"page_id：{page.get('page_id')}", f"nav_key：{page.get('nav_key')}", "", "当前页控件："]
    for i, t in enumerate(targets):
        parts = [f"{i}. [{t.get('record_group')}/{t.get('kind')}] {t.get('text')}"]
        if t.get("key"):
            parts.append(f"key={t.get('key')}")
        elif t.get("sensitive_key"):
            parts.append("sensitive_key=true")
        else:
            parts.append("key=(无 key)")
        parts.append(f"locator={t.get('locator')}")
        if t.get("locator") == "bounds_center":
            parts.append(f"bounds_center={t.get('bounds_center')}")
        if t.get("value"):
            parts.append(f"value={t.get('value')}")
        lines.append("，".join(parts))
    return "\n".join(lines)


def save_outputs(db: Dict[str, Any], graph_dir: Path) -> None:
    graph_dir.mkdir(parents=True, exist_ok=True)
    save_json(db, graph_dir / "settings_tree.json")
    save_json(build_index(db), graph_dir / "settings_nodes_index.json")
    save_text(readable(db), graph_dir / "settings_ui_graph_readable.txt")
    print(f"✓ 可读树摘要: {graph_dir / 'settings_ui_graph_readable.txt'}")
    print(f"✓ 设置层级树: {graph_dir / 'settings_tree.json'}")
    print(f"✓ 设置节点索引/数据库: {graph_dir / 'settings_nodes_index.json'}")


def analyze_json_file(json_path: Path, graph_dir: Path, parent_node_id: str = "", reset: bool = False, interactive: bool = True, current_summary_path: Optional[Path] = None) -> Dict[str, Any]:
    if reset and graph_dir.exists():
        shutil.rmtree(graph_dir)
        print(f"✓ 已清空旧图谱目录: {graph_dir}")
    root_json = load_json(json_path)
    page = page_identity(root_json)
    targets = extract_targets(root_json)
    if current_summary_path:
        save_text(current_summary(page, targets), current_summary_path)
    db = load_db(graph_dir)
    if interactive:
        pid, active = choose_parent(db, page, parent_node_id)
    else:
        pid = parent_node_id or db.get("active_node_id", "root")
        active = pid
    merge(db, pid, targets)
    db["active_node_id"] = active
    db["updated_at"] = now_iso()
    db["schema_version"] = SCHEMA_VERSION
    db["total_capture_runs"] = int(db.get("total_capture_runs", 0)) + 1
    save_outputs(db, graph_dir)
    print("\n" + "=" * 60)
    print("当前页面")
    print("=" * 60)
    print(f"title={page.get('title')}")
    print(f"page_id={page.get('page_id')}")
    print(f"nav_key={page.get('nav_key')}")
    print(f"识别控件数={len(targets)}")
    print(f"本次挂载 parent_node_id={pid}")
    print(f"新的 active_node_id={active}")
    return {"page_identity": page, "target_count": len(targets), "parent_node_id": pid, "active_node_id": active}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="脚本1：分析 current_ui_tree.json 并更新控件树")
    p.add_argument("--json", required=True)
    p.add_argument("--work-dir", default=str(DEFAULT_WORK_DIR))
    p.add_argument("--graph-dir", default="")
    p.add_argument("--output-dir", default="")
    p.add_argument("--reset", action="store_true")
    p.add_argument("--parent-node-id", default="")
    p.add_argument("--no-interactive", action="store_true")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    work_dir = Path(args.work_dir)
    graph_dir = Path(args.graph_dir) if args.graph_dir else work_dir / "outputs" / "graph"
    output_dir = Path(args.output_dir) if args.output_dir else work_dir / "outputs" / "latest"
    output_dir.mkdir(parents=True, exist_ok=True)
    print("=" * 60)
    print("脚本1：设置 UI JSON 分析与树更新")
    print("=" * 60)
    analyze_json_file(Path(args.json), graph_dir, args.parent_node_id, args.reset, not args.no_interactive, output_dir / "ui_semantic_summary.txt")
    print("执行完成。")


if __name__ == "__main__":
    main()
