#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
组件视觉检测的 LLM/VLM 协作接口层。

本脚本不绑定任何具体模型 API。它负责：
1. 对组件 crop 做基础规则检测；
2. 为 VLM 生成结构化视觉观察请求；
3. 读取 VLM 结果后，为 LLM 生成最终判定请求；
4. 读取 LLM 结果后，生成统一报告。

推荐角色划分：
- VLM: 只看图片并描述视觉事实，不做业务裁决；
- LLM: 结合 UI tree 元数据、规则检测、VLM 视觉事实做最终裁决。
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


DEFAULT_WORK_DIR = Path(r"D:\hanchunyang_6_3\AItest")

ABNORMAL_TYPES = [
    "blank_or_empty",
    "not_visible",
    "text_mismatch",
    "text_clipped",
    "layout_clipped",
    "overlap_or_occlusion",
    "icon_missing",
    "low_contrast",
    "unexpected_state",
    "bounds_or_crop_error",
    "unknown",
]


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def import_image():
    try:
        from PIL import Image, ImageStat
        return Image, ImageStat
    except Exception as exc:
        raise SystemExit(
            "缺少 Pillow，无法执行规则检测。\n"
            "可以用 Codex 内置 Python 运行：\n"
            "  /Users/hanchunyang/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 "
            "settings_visual_review_interface.py ...\n"
            f"原始错误: {exc}"
        )


def load_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8-sig") as f:
        return json.load(f)


def load_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def write_jsonl(rows: Iterable[Dict[str, Any]], path: Path, desc: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    print(f"✓ {desc}: {path}")


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


def component_label(item: Dict[str, Any]) -> str:
    return str(item.get("name") or item.get("text") or item.get("type") or item.get("component_id") or "")


def result_key(item: Dict[str, Any]) -> str:
    return str(item.get("component_id") or item.get("crop_path") or item.get("request_id") or "")


def index_by_component(rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    out = {}
    for row in rows:
        key = result_key(row)
        if key:
            out[key] = row
    return out


def image_rule_check(crop: Dict[str, Any], blank_threshold: float, low_contrast_threshold: float) -> Dict[str, Any]:
    Image, ImageStat = import_image()
    crop_path = Path(str(crop.get("crop_path") or ""))
    result = {
        "component_id": crop.get("component_id", ""),
        "crop_path": str(crop_path),
        "rule_status": "ok",
        "rule_abnormal_types": [],
        "needs_vlm": False,
        "image_size": None,
        "mean_luma": None,
        "luma_stddev": None,
        "blank_score": None,
        "notes": [],
    }

    if not crop_path.exists():
        result["rule_status"] = "abnormal"
        result["rule_abnormal_types"].append("bounds_or_crop_error")
        result["needs_vlm"] = True
        result["notes"].append("crop image does not exist")
        return result

    image = Image.open(crop_path).convert("L")
    width, height = image.size
    result["image_size"] = [width, height]
    if width < 4 or height < 4:
        result["rule_status"] = "abnormal"
        result["rule_abnormal_types"].append("bounds_or_crop_error")
        result["needs_vlm"] = True
        result["notes"].append("crop image is too small")

    stat = ImageStat.Stat(image)
    mean_luma = float(stat.mean[0])
    luma_stddev = float(stat.stddev[0])
    result["mean_luma"] = round(mean_luma, 4)
    result["luma_stddev"] = round(luma_stddev, 4)
    result["blank_score"] = round(1.0 - min(luma_stddev / 64.0, 1.0), 6)

    if result["blank_score"] >= blank_threshold:
        result["rule_status"] = "suspicious"
        result["rule_abnormal_types"].append("blank_or_empty")
        result["needs_vlm"] = True
        result["notes"].append("crop has very low visual variation")

    if luma_stddev <= low_contrast_threshold:
        if "low_contrast" not in result["rule_abnormal_types"]:
            result["rule_abnormal_types"].append("low_contrast")
        result["rule_status"] = "suspicious" if result["rule_status"] == "ok" else result["rule_status"]
        result["needs_vlm"] = True
        result["notes"].append("crop has low contrast")

    if crop.get("clickable") or crop.get("record_group") in {"entry_controls", "operation_controls", "nav_controls"}:
        result["needs_vlm"] = True

    return result


def vlm_response_schema() -> Dict[str, Any]:
    return {
        "is_visible": "boolean",
        "is_blank": "boolean",
        "observed_text": "string",
        "text_matches_expected": "boolean|null",
        "is_text_clipped": "boolean",
        "is_layout_clipped": "boolean",
        "is_overlapped_or_occluded": "boolean",
        "icon_or_control_present": "boolean|null",
        "visual_abnormal_types": f"array of {ABNORMAL_TYPES}",
        "confidence": "number between 0 and 1",
        "visual_evidence": "short Chinese explanation of what is visible in the image",
    }


def llm_response_schema() -> Dict[str, Any]:
    return {
        "label": "normal|abnormal|uncertain",
        "severity": "none|low|medium|high",
        "abnormal_types": f"array of {ABNORMAL_TYPES}",
        "confidence": "number between 0 and 1",
        "should_block_traversal": "boolean",
        "should_recapture_page": "boolean",
        "final_reason": "short Chinese explanation",
        "suggested_action": "short Chinese action suggestion",
    }


def build_vlm_prompt(crop: Dict[str, Any], rule: Dict[str, Any]) -> str:
    expected_text = crop.get("text") or crop.get("name") or ""
    return (
        "你是设置 App UI 组件的视觉观察模型。只根据图片和给定元数据描述视觉事实，"
        "不要推测状态机，不要编造图片中看不到的内容。必须输出严格 JSON。\n\n"
        f"页面: {crop.get('page_title') or crop.get('page_id')}\n"
        f"组件: {component_label(crop)}\n"
        f"类型: {crop.get('record_group')}/{crop.get('kind')}/{crop.get('type')}\n"
        f"期望文字: {expected_text or '-'}\n"
        f"bounds: {crop.get('bounds')}\n"
        f"规则检测: {json.dumps(rule, ensure_ascii=False)}\n\n"
        "请检查：组件是否可见、是否空白、文字是否匹配、是否裁切、是否遮挡、图标/控件是否存在。"
    )


def build_llm_prompt(crop: Dict[str, Any], rule: Dict[str, Any], vlm_result: Dict[str, Any]) -> str:
    return (
        "你是设置 App UI 组件检测的最终裁决模型。你不能看图，只能根据 UI tree 元数据、"
        "规则检测和 VLM 视觉观察做综合判断。必须输出严格 JSON。\n\n"
        f"组件元数据: {json.dumps(crop, ensure_ascii=False)}\n"
        f"规则检测: {json.dumps(rule, ensure_ascii=False)}\n"
        f"VLM 观察: {json.dumps(vlm_result, ensure_ascii=False)}\n\n"
        "裁决原则：规则异常和 VLM 异常一致时判 abnormal；信息冲突时判 uncertain；"
        "不要因为没有 key 就判异常；普通视觉正常但 bounds/裁剪错误时应建议重新采集。"
    )


def build_vlm_request(crop: Dict[str, Any], rule: Dict[str, Any]) -> Dict[str, Any]:
    request_id = f"vlm::{crop.get('component_id') or crop.get('crop_index')}"
    return {
        "request_id": request_id,
        "role": "vision_observer",
        "model_family": "vlm",
        "component_id": crop.get("component_id", ""),
        "crop_path": crop.get("crop_path", ""),
        "page_id": crop.get("page_id", ""),
        "component_metadata": crop,
        "rule_check": rule,
        "prompt": build_vlm_prompt(crop, rule),
        "response_schema": vlm_response_schema(),
    }


def build_llm_request(crop: Dict[str, Any], rule: Dict[str, Any], vlm_result: Dict[str, Any]) -> Dict[str, Any]:
    request_id = f"llm::{crop.get('component_id') or crop.get('crop_index')}"
    return {
        "request_id": request_id,
        "role": "final_judge",
        "model_family": "llm",
        "component_id": crop.get("component_id", ""),
        "crop_path": crop.get("crop_path", ""),
        "page_id": crop.get("page_id", ""),
        "component_metadata": crop,
        "rule_check": rule,
        "vlm_result": vlm_result,
        "prompt": build_llm_prompt(crop, rule, vlm_result),
        "response_schema": llm_response_schema(),
    }


def build_review_plan(
    crops: List[Dict[str, Any]],
    vlm_results: Dict[str, Dict[str, Any]],
    blank_threshold: float,
    low_contrast_threshold: float,
    vlm_all: bool,
) -> Dict[str, Any]:
    rule_checks = []
    vlm_requests = []
    llm_requests = []

    for crop in crops:
        rule = image_rule_check(crop, blank_threshold, low_contrast_threshold)
        rule_checks.append(rule)
        should_send_vlm = vlm_all or bool(rule.get("needs_vlm"))
        if should_send_vlm:
            vlm_requests.append(build_vlm_request(crop, rule))
        vlm = vlm_results.get(str(crop.get("component_id") or "")) or vlm_results.get(str(crop.get("crop_path") or ""))
        if vlm:
            llm_requests.append(build_llm_request(crop, rule, vlm))

    return {
        "generated_at": now_iso(),
        "summary": {
            "crop_count": len(crops),
            "rule_check_count": len(rule_checks),
            "vlm_request_count": len(vlm_requests),
            "llm_request_count": len(llm_requests),
        },
        "rule_checks": rule_checks,
        "vlm_requests": vlm_requests,
        "llm_requests": llm_requests,
    }


def build_label_rows(crops: List[Dict[str, Any]], llm_results: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows = []
    for crop in crops:
        key = str(crop.get("component_id") or "")
        result = llm_results.get(key) or llm_results.get(str(crop.get("crop_path") or ""))
        if not result:
            continue
        rows.append({
            "component_id": crop.get("component_id", ""),
            "crop_path": crop.get("crop_path", ""),
            "page_id": crop.get("page_id", ""),
            "component_name": component_label(crop),
            "model_label": result.get("label", "uncertain"),
            "severity": result.get("severity", "none"),
            "abnormal_types": result.get("abnormal_types", []),
            "confidence": result.get("confidence", 0.0),
            "should_block_traversal": result.get("should_block_traversal", False),
            "should_recapture_page": result.get("should_recapture_page", False),
            "final_reason": result.get("final_reason", ""),
            "suggested_action": result.get("suggested_action", ""),
            "human_label": "",
            "human_note": "",
        })
    return rows


def build_report(plan: Dict[str, Any], label_rows: List[Dict[str, Any]]) -> str:
    rule_status = Counter(row.get("rule_status", "") for row in plan.get("rule_checks", []))
    labels = Counter(row.get("model_label", "") for row in label_rows)
    lines = [
        "# 组件视觉检测接口报告",
        "",
        f"- 生成时间：{plan.get('generated_at', '')}",
        f"- crop 数：{plan.get('summary', {}).get('crop_count', 0)}",
        f"- VLM 请求数：{plan.get('summary', {}).get('vlm_request_count', 0)}",
        f"- LLM 请求数：{plan.get('summary', {}).get('llm_request_count', 0)}",
        f"- 规则状态：{dict(rule_status)}",
        f"- 模型标签：{dict(labels)}",
        "",
        "## 说明",
        "",
        "- VLM 请求只要求模型观察图片事实。",
        "- LLM 请求只要求模型做最终裁决，不直接看图。",
        "- `component_visual_labels_seed.jsonl` 可作为人工复核标注起点。",
    ]
    return "\n".join(lines).rstrip() + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="生成组件视觉检测的 VLM/LLM 接口请求")
    parser.add_argument("--work-dir", default=str(DEFAULT_WORK_DIR))
    parser.add_argument("--vision-dir", default="")
    parser.add_argument("--manifest", default="")
    parser.add_argument("--vlm-results-jsonl", default="")
    parser.add_argument("--llm-results-jsonl", default="")
    parser.add_argument("--output-dir", default="")
    parser.add_argument("--blank-threshold", type=float, default=0.96)
    parser.add_argument("--low-contrast-threshold", type=float, default=6.0)
    parser.add_argument("--vlm-all", action="store_true", help="为所有 crop 生成 VLM 请求")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    work_dir = Path(args.work_dir)
    vision_dir = Path(args.vision_dir) if args.vision_dir else work_dir / "outputs" / "vision"
    manifest_path = Path(args.manifest) if args.manifest else vision_dir / "component_crops_manifest.jsonl"
    output_dir = Path(args.output_dir) if args.output_dir else vision_dir

    crops = load_jsonl(manifest_path)
    if not crops:
        raise SystemExit(f"没有找到组件 crop manifest: {manifest_path}")

    vlm_results = index_by_component(load_jsonl(Path(args.vlm_results_jsonl))) if args.vlm_results_jsonl else {}
    llm_results = index_by_component(load_jsonl(Path(args.llm_results_jsonl))) if args.llm_results_jsonl else {}

    plan = build_review_plan(crops, vlm_results, args.blank_threshold, args.low_contrast_threshold, args.vlm_all)
    label_rows = build_label_rows(crops, llm_results)

    write_jsonl(plan["rule_checks"], output_dir / "component_visual_rule_checks.jsonl", "组件视觉规则检测 JSONL")
    write_jsonl(plan["vlm_requests"], output_dir / "component_vlm_requests.jsonl", "VLM 请求 JSONL")
    write_jsonl(plan["llm_requests"], output_dir / "component_llm_judge_requests.jsonl", "LLM 裁决请求 JSONL")
    if label_rows:
        write_jsonl(label_rows, output_dir / "component_visual_labels_seed.jsonl", "人工复核标注种子 JSONL")
    save_json(plan["summary"], output_dir / "component_visual_review_summary.json", "组件视觉检测接口摘要")
    save_text(build_report(plan, label_rows), output_dir / "component_visual_review_report.md", "组件视觉检测接口报告")

    print(
        f"执行完成：规则检测 {len(plan['rule_checks'])} 条，"
        f"VLM 请求 {len(plan['vlm_requests'])} 条，"
        f"LLM 请求 {len(plan['llm_requests'])} 条。"
    )


if __name__ == "__main__":
    main()
