#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
YOLO 备份检测器。

用途：
1. 用 YOLO 模型从当前页面截图中检测 UI 组件候选框；
2. 将 YOLO 检测框和 UI tree 语义组件 bounds 做 IoU 匹配；
3. 输出检测结果、匹配结果和标注图，作为 UI tree bounds 的备份/校验。

注意：通用 COCO YOLO 模型不适合直接识别设置 UI 控件。实际使用时应传入
自定义 UI 组件检测模型，例如按钮、开关、输入框、列表项等类别。
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


DEFAULT_WORK_DIR = Path(r"D:\hanchunyang_6_3\AItest")


def import_pil():
    try:
        from PIL import Image, ImageDraw, ImageFont
        return Image, ImageDraw, ImageFont
    except Exception as exc:
        raise SystemExit(f"缺少 Pillow，无法读取/标注截图: {exc}")


def import_yolo():
    try:
        from ultralytics import YOLO
        return YOLO
    except Exception as exc:
        raise SystemExit(
            "缺少 ultralytics，无法直接运行 YOLO。\n"
            "安装示例：\n"
            "  python -m pip install ultralytics\n"
            "或者先用其它环境生成 detections JSON，再通过 --detections-json 传入。\n"
            f"原始错误: {exc}"
        )


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


def write_jsonl(rows: Iterable[Dict[str, Any]], path: Path, desc: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    print(f"✓ {desc}: {path}")


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


def parse_rect(bounds: Any) -> Optional[Tuple[float, float, float, float]]:
    nums = re.findall(r"-?\d+(?:\.\d+)?", str(bounds or ""))
    if len(nums) < 4:
        return None
    left, top, right, bottom = map(float, nums[:4])
    if right <= left or bottom <= top:
        return None
    return left, top, right, bottom


def rect_area(rect: Tuple[float, float, float, float]) -> float:
    left, top, right, bottom = rect
    return max(0.0, right - left) * max(0.0, bottom - top)


def rect_iou(a: Tuple[float, float, float, float], b: Tuple[float, float, float, float]) -> float:
    left = max(a[0], b[0])
    top = max(a[1], b[1])
    right = min(a[2], b[2])
    bottom = min(a[3], b[3])
    if right <= left or bottom <= top:
        return 0.0
    inter = (right - left) * (bottom - top)
    union = rect_area(a) + rect_area(b) - inter
    return round(inter / union, 6) if union > 0 else 0.0


def safe_label(text: Any) -> str:
    value = str(text or "").strip()
    return value if value else "-"


def component_label(component: Dict[str, Any]) -> str:
    return str(component.get("name") or component.get("text") or component.get("type") or component.get("component_id") or "")


def filter_components(components: List[Dict[str, Any]], page_id: str, include_non_visible: bool) -> List[Dict[str, Any]]:
    out = []
    for comp in components:
        if page_id and str(comp.get("page_id") or "") != page_id:
            continue
        if not include_non_visible and comp.get("visible") is False:
            continue
        if parse_rect(comp.get("bounds")):
            out.append(comp)
    return out


def run_yolo_model(
    model_path: str,
    screenshot_path: Path,
    conf: float,
    iou: float,
    imgsz: int,
    device: str,
) -> List[Dict[str, Any]]:
    YOLO = import_yolo()
    model = YOLO(model_path)
    kwargs: Dict[str, Any] = {"conf": conf, "iou": iou, "imgsz": imgsz, "verbose": False}
    if device:
        kwargs["device"] = device
    results = model.predict(str(screenshot_path), **kwargs)

    detections: List[Dict[str, Any]] = []
    for result in results:
        names = result.names or {}
        boxes = getattr(result, "boxes", None)
        if boxes is None:
            continue
        for idx, box in enumerate(boxes):
            xyxy = box.xyxy[0].tolist()
            cls_id = int(box.cls[0].item()) if box.cls is not None else -1
            score = float(box.conf[0].item()) if box.conf is not None else 0.0
            detections.append({
                "detection_id": f"yolo_{len(detections):04d}",
                "source": "yolo",
                "class_id": cls_id,
                "class_name": str(names.get(cls_id, cls_id)),
                "confidence": round(score, 6),
                "bbox": [round(float(v), 3) for v in xyxy],
            })
    return detections


def load_external_detections(path: Path) -> List[Dict[str, Any]]:
    data = load_json(path, [])
    if isinstance(data, dict):
        data = data.get("detections", [])
    detections = []
    for i, item in enumerate(data or []):
        bbox = item.get("bbox") or item.get("xyxy") or item.get("bounds")
        rect = parse_rect(bbox)
        if not rect:
            continue
        detections.append({
            "detection_id": str(item.get("detection_id") or f"external_{i:04d}"),
            "source": str(item.get("source") or "external_yolo"),
            "class_id": item.get("class_id", -1),
            "class_name": str(item.get("class_name") or item.get("label") or "unknown"),
            "confidence": float(item.get("confidence", item.get("score", 0.0)) or 0.0),
            "bbox": [round(float(v), 3) for v in rect],
        })
    return detections


def best_component_match(detection: Dict[str, Any], components: List[Dict[str, Any]]) -> Tuple[Optional[Dict[str, Any]], float]:
    det_rect = parse_rect(detection.get("bbox"))
    if not det_rect:
        return None, 0.0
    best: Optional[Dict[str, Any]] = None
    best_iou = 0.0
    for comp in components:
        comp_rect = parse_rect(comp.get("bounds"))
        if not comp_rect:
            continue
        iou = rect_iou(det_rect, comp_rect)
        if iou > best_iou:
            best = comp
            best_iou = iou
    return best, best_iou


def match_detections_to_components(detections: List[Dict[str, Any]], components: List[Dict[str, Any]], threshold: float) -> List[Dict[str, Any]]:
    rows = []
    for det in detections:
        comp, iou = best_component_match(det, components)
        matched = comp is not None and iou >= threshold
        rows.append({
            "detection_id": det.get("detection_id", ""),
            "class_name": det.get("class_name", ""),
            "confidence": det.get("confidence", 0.0),
            "bbox": det.get("bbox"),
            "matched": matched,
            "iou": iou,
            "component_id": comp.get("component_id", "") if comp else "",
            "component_name": component_label(comp) if comp else "",
            "component_group": comp.get("record_group", "") if comp else "",
            "component_kind": comp.get("kind", "") if comp else "",
            "component_bounds": comp.get("bounds", "") if comp else "",
            "page_id": comp.get("page_id", "") if comp else "",
        })
    return rows


def unmatched_components(components: List[Dict[str, Any]], matches: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    matched_ids = {m.get("component_id") for m in matches if m.get("matched") and m.get("component_id")}
    rows = []
    for comp in components:
        component_id = str(comp.get("component_id") or "")
        if component_id and component_id not in matched_ids:
            rows.append({
                "component_id": component_id,
                "component_name": component_label(comp),
                "component_group": comp.get("record_group", ""),
                "component_kind": comp.get("kind", ""),
                "component_bounds": comp.get("bounds", ""),
                "page_id": comp.get("page_id", ""),
            })
    return rows


def draw_rect(draw: Any, rect: Tuple[float, float, float, float], color: str, label: str) -> None:
    left, top, right, bottom = rect
    draw.rectangle([left, top, right, bottom], outline=color, width=3)
    if label:
        y = max(0, top - 18)
        draw.rectangle([left, y, min(right, left + max(80, len(label) * 8)), top], fill=color)
        draw.text([left + 3, y + 2], label, fill="white")


def save_annotated_image(screenshot_path: Path, detections: List[Dict[str, Any]], components: List[Dict[str, Any]], output_path: Path) -> None:
    Image, ImageDraw, _ = import_pil()
    image = Image.open(screenshot_path).convert("RGB")
    draw = ImageDraw.Draw(image)
    for comp in components:
        rect = parse_rect(comp.get("bounds"))
        if rect:
            draw_rect(draw, rect, "#2E86DE", f"UI:{component_label(comp)[:18]}")
    for det in detections:
        rect = parse_rect(det.get("bbox"))
        if rect:
            label = f"Y:{det.get('class_name')} {det.get('confidence', 0):.2f}"
            draw_rect(draw, rect, "#E74C3C", label[:28])
    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path)
    print(f"✓ YOLO/UI 标注图: {output_path}")


def build_report(detections: List[Dict[str, Any]], matches: List[Dict[str, Any]], missing_components: List[Dict[str, Any]]) -> str:
    matched_count = sum(1 for m in matches if m.get("matched"))
    lines = [
        "# YOLO 备份检测报告",
        "",
        f"- YOLO 检测数：{len(detections)}",
        f"- 匹配到 UI tree 组件：{matched_count}",
        f"- 未匹配 YOLO 检测：{len(matches) - matched_count}",
        f"- 未被 YOLO 覆盖的 UI tree 组件：{len(missing_components)}",
        "",
        "## YOLO 与 UI tree 匹配",
        "",
        "| detection | class | conf | matched | IoU | component | group/kind |",
        "| --- | --- | ---: | --- | ---: | --- | --- |",
    ]
    for m in matches:
        lines.append(
            f"| `{m.get('detection_id')}` | {m.get('class_name')} | {m.get('confidence', 0):.3f} | "
            f"{m.get('matched')} | {m.get('iou', 0):.3f} | {safe_label(m.get('component_name'))} | "
            f"{m.get('component_group')}/{m.get('component_kind')} |"
        )
    lines.extend(["", "## UI tree 中未被 YOLO 覆盖的组件", ""])
    for comp in missing_components:
        lines.append(
            f"- `{comp.get('component_id')}` {safe_label(comp.get('component_name'))} "
            f"{comp.get('component_group')}/{comp.get('component_kind')} bounds={comp.get('component_bounds')}"
        )
    return "\n".join(lines).rstrip() + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="YOLO 备份检测并和 UI tree 组件 bounds 匹配")
    parser.add_argument("--work-dir", default=str(DEFAULT_WORK_DIR))
    parser.add_argument("--graph-dir", default="")
    parser.add_argument("--screenshot", default="")
    parser.add_argument("--components-jsonl", default="")
    parser.add_argument("--index-json", default="")
    parser.add_argument("--output-dir", default="")
    parser.add_argument("--page-id", default="")
    parser.add_argument("--model", default="", help="YOLO 模型路径或名称，如 custom_ui.pt")
    parser.add_argument("--detections-json", default="", help="外部 YOLO 检测 JSON；传入后不运行 ultralytics")
    parser.add_argument("--conf", type=float, default=0.25)
    parser.add_argument("--iou", type=float, default=0.7)
    parser.add_argument("--imgsz", type=int, default=1280)
    parser.add_argument("--device", default="")
    parser.add_argument("--match-iou-threshold", type=float, default=0.30)
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
    components = filter_components(components, args.page_id, args.include_non_visible)

    if args.detections_json:
        detections = load_external_detections(Path(args.detections_json))
    else:
        if not args.model:
            raise SystemExit("请提供 --model custom_ui.pt，或用 --detections-json 传入外部检测结果。")
        detections = run_yolo_model(args.model, screenshot_path, args.conf, args.iou, args.imgsz, args.device)

    matches = match_detections_to_components(detections, components, args.match_iou_threshold)
    missing = unmatched_components(components, matches)

    output_dir.mkdir(parents=True, exist_ok=True)
    save_json({"detections": detections}, output_dir / "yolo_backup_detections.json", "YOLO 检测结果 JSON")
    write_jsonl(detections, output_dir / "yolo_backup_detections.jsonl", "YOLO 检测结果 JSONL")
    save_json({"matches": matches, "unmatched_components": missing}, output_dir / "yolo_component_matches.json", "YOLO/UI 匹配结果 JSON")
    write_jsonl(matches, output_dir / "yolo_component_matches.jsonl", "YOLO/UI 匹配结果 JSONL")
    save_annotated_image(screenshot_path, detections, components, output_dir / "yolo_backup_annotated.png")
    report = build_report(detections, matches, missing)
    report_path = output_dir / "yolo_backup_report.md"
    report_path.write_text(report, encoding="utf-8")
    print(f"✓ YOLO 备份检测报告: {report_path}")
    print(f"执行完成：YOLO 检测 {len(detections)} 个，匹配 UI 组件 {sum(1 for m in matches if m.get('matched'))} 个。")


if __name__ == "__main__":
    main()
