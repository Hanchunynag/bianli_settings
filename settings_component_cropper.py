#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
按组件 bounds 从当前页面截图中裁剪组件图片。

这个脚本不做视觉检测；它使用 UI tree 已经给出的 bounds 做确定性裁剪。
后续如果要接视觉模型，应优先把这里输出的 crop 图片交给模型分析。
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


DEFAULT_WORK_DIR = Path(r"D:\hanchunyang_6_3\AItest")


def import_image():
    try:
        from PIL import Image
        return Image
    except Exception as exc:
        raise SystemExit(
            "缺少 Pillow，无法裁剪图片。\n"
            "可以用 Codex 内置 Python 运行：\n"
            "  /Users/hanchunyang/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 "
            "settings_component_cropper.py ...\n"
            f"原始错误: {exc}"
        )


def load_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8-sig") as f:
        return json.load(f)


def load_components_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def save_json(data: Any, path: Path, desc: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"✓ {desc}: {path}")


def write_jsonl(rows: Iterable[Dict[str, Any]], path: Path, desc: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    print(f"✓ {desc}: {path}")


def parse_rect(bounds: Any) -> Optional[Tuple[int, int, int, int]]:
    nums = re.findall(r"-?\d+", str(bounds or ""))
    if len(nums) < 4:
        return None
    left, top, right, bottom = map(int, nums[:4])
    if right <= left or bottom <= top:
        return None
    return left, top, right, bottom


def clamp_rect(rect: Tuple[int, int, int, int], width: int, height: int, margin: int = 0) -> Optional[Tuple[int, int, int, int]]:
    left, top, right, bottom = rect
    left = max(0, left - margin)
    top = max(0, top - margin)
    right = min(width, right + margin)
    bottom = min(height, bottom + margin)
    if right <= left or bottom <= top:
        return None
    return left, top, right, bottom


def safe_segment(text: Any, max_len: int = 80) -> str:
    value = str(text or "").strip() or "unnamed"
    value = re.sub(r"[\\/\s]+", "_", value)
    value = re.sub(r"[^0-9A-Za-z_\-\u4e00-\u9fff]+", "_", value)
    value = re.sub(r"_+", "_", value).strip("_")
    if len(value) > max_len:
        value = value[:max_len].rstrip("_")
    return value or "unnamed"


def component_label(component: Dict[str, Any]) -> str:
    return str(component.get("name") or component.get("text") or component.get("type") or component.get("component_id") or "")


def components_from_index(index_path: Path) -> List[Dict[str, Any]]:
    index = load_json(index_path, {"pages": {}}) or {"pages": {}}
    rows: List[Dict[str, Any]] = []
    for page_id, page in (index.get("pages", {}) or {}).items():
        for comp in (page.get("components", {}) or {}).values():
            item = dict(comp)
            item["page_id"] = str(item.get("page_id") or page_id)
            item["page_title"] = page.get("title", "")
            rows.append(item)
    return rows


def filter_components(components: List[Dict[str, Any]], page_id: str, include_non_visible: bool) -> List[Dict[str, Any]]:
    out = []
    for comp in components:
        if page_id and str(comp.get("page_id") or "") != page_id:
            continue
        if not include_non_visible and comp.get("visible") is False:
            continue
        if not parse_rect(comp.get("bounds")):
            continue
        out.append(comp)
    return sorted(
        out,
        key=lambda c: (
            str(c.get("page_id") or ""),
            int(c.get("semantic_order", c.get("last_seen_order", c.get("observed_order", 0))) or 0),
            str(c.get("record_group") or ""),
            component_label(c),
        ),
    )


def crop_components(
    screenshot_path: Path,
    components: List[Dict[str, Any]],
    output_dir: Path,
    margin: int,
    image_format: str,
) -> List[Dict[str, Any]]:
    Image = import_image()
    image = Image.open(screenshot_path)
    width, height = image.size
    crops_dir = output_dir / "component_crops"
    crops_dir.mkdir(parents=True, exist_ok=True)
    manifest = []

    for i, comp in enumerate(components):
        rect = parse_rect(comp.get("bounds"))
        if not rect:
            continue
        clipped = clamp_rect(rect, width, height, margin=margin)
        if not clipped:
            continue

        page_id = str(comp.get("page_id") or "unknown_page")
        component_id = str(comp.get("component_id") or f"component_{i}")
        label = component_label(comp)
        filename = (
            f"{i:04d}__{safe_segment(page_id, 48)}__"
            f"{safe_segment(component_id, 48)}__{safe_segment(label, 48)}.{image_format.lower()}"
        )
        crop_path = crops_dir / filename
        image.crop(clipped).save(crop_path)

        manifest.append({
            "crop_index": i,
            "crop_path": str(crop_path),
            "page_id": page_id,
            "page_title": comp.get("page_title", ""),
            "component_id": component_id,
            "semantic_component_id": comp.get("semantic_component_id", component_id),
            "name": comp.get("name", ""),
            "text": comp.get("text", ""),
            "record_group": comp.get("record_group", ""),
            "kind": comp.get("kind", ""),
            "type": comp.get("type", ""),
            "key": comp.get("key", ""),
            "locator": comp.get("locator", ""),
            "bounds": comp.get("bounds", ""),
            "clipped_bounds": list(clipped),
            "bounds_center": comp.get("bounds_center"),
            "normalized_center": comp.get("normalized_center"),
            "screenshot_size": [width, height],
            "margin": margin,
            "clickable": comp.get("clickable", False),
            "merged_child_count": comp.get("merged_child_count", 0),
        })
    return manifest


def build_preview_html(manifest: List[Dict[str, Any]], output_dir: Path) -> str:
    lines = [
        "<!doctype html>",
        "<meta charset=\"utf-8\">",
        "<title>Settings Component Crops</title>",
        "<style>",
        "body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;margin:24px;background:#f7f7f8;color:#1f2328}",
        ".grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:16px}",
        ".card{background:white;border:1px solid #ddd;border-radius:8px;padding:10px}",
        "img{max-width:100%;height:auto;border:1px solid #eee;background:#fafafa}",
        ".meta{font-size:12px;line-height:1.45;color:#555;word-break:break-all}",
        ".title{font-weight:600;margin:8px 0 4px}",
        "</style>",
        "<h1>Settings Component Crops</h1>",
        f"<p>Total: {len(manifest)}</p>",
        "<div class=\"grid\">",
    ]
    for item in manifest:
        crop_path = Path(item["crop_path"])
        rel = crop_path.relative_to(output_dir)
        label = item.get("name") or item.get("text") or item.get("type") or item.get("component_id")
        lines.extend([
            "<div class=\"card\">",
            f"<img src=\"{rel.as_posix()}\" alt=\"{label}\">",
            f"<div class=\"title\">{label}</div>",
            "<div class=\"meta\">",
            f"page: {item.get('page_id')}<br>",
            f"group: {item.get('record_group')} / {item.get('kind')}<br>",
            f"type: {item.get('type')}<br>",
            f"key: {item.get('key') or '-'}<br>",
            f"bounds: {item.get('bounds')}<br>",
            f"center: {item.get('bounds_center')}<br>",
            "</div>",
            "</div>",
        ])
    lines.append("</div>")
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="按组件 bounds 从截图裁剪组件图片")
    parser.add_argument("--work-dir", default=str(DEFAULT_WORK_DIR))
    parser.add_argument("--graph-dir", default="")
    parser.add_argument("--screenshot", default="")
    parser.add_argument("--components-jsonl", default="")
    parser.add_argument("--index-json", default="")
    parser.add_argument("--output-dir", default="")
    parser.add_argument("--page-id", default="", help="只裁剪指定 page_id 的组件")
    parser.add_argument("--margin", type=int, default=4)
    parser.add_argument("--format", choices=["png", "jpg"], default="png")
    parser.add_argument("--include-non-visible", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    work_dir = Path(args.work_dir)
    graph_dir = Path(args.graph_dir) if args.graph_dir else work_dir / "outputs" / "graph"
    screenshot_path = Path(args.screenshot) if args.screenshot else work_dir / "outputs" / "latest" / "current_screen.png"
    components_jsonl = Path(args.components_jsonl) if args.components_jsonl else graph_dir / "settings_page_components.jsonl"
    index_json = Path(args.index_json) if args.index_json else graph_dir / "settings_nodes_index.json"
    output_dir = Path(args.output_dir) if args.output_dir else work_dir / "outputs" / "vision"

    if not screenshot_path.exists():
        raise SystemExit(f"截图不存在: {screenshot_path}")

    components = load_components_jsonl(components_jsonl)
    if not components:
        components = components_from_index(index_json)
    if not components:
        raise SystemExit(f"没有找到组件数据: {components_jsonl} 或 {index_json}")

    selected = filter_components(components, args.page_id, args.include_non_visible)
    manifest = crop_components(screenshot_path, selected, output_dir, args.margin, args.format)
    write_jsonl(manifest, output_dir / "component_crops_manifest.jsonl", "组件截图 manifest JSONL")
    save_json(manifest, output_dir / "component_crops_manifest.json", "组件截图 manifest JSON")
    preview = build_preview_html(manifest, output_dir)
    preview_path = output_dir / "component_crops_preview.html"
    preview_path.parent.mkdir(parents=True, exist_ok=True)
    preview_path.write_text(preview, encoding="utf-8")
    print(f"✓ 组件截图预览: {preview_path}")
    print(f"执行完成：裁剪组件 {len(manifest)} 个。")


if __name__ == "__main__":
    main()
