#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Canonical special_opearte contract.

The public/persisted DFS shape is always::

    special_opearte.operationN = [locator, locator, ...]

Array order is execution order.  There are deliberately no ``step1`` /
``step2`` keys and no ``kind`` / ``operate`` wrapper fields in exported DFS.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple

SPECIAL_MANUAL_FIELD = "special_manual"
SPECIAL_EFFECT_PREFIX = "special_capture::"
SPECIAL_ITEM_FIELDS = ("type", "value", "key_description", "step_prompt")


def _normalize_item(value: Any, *, location: str) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{location} 必须是 JSON 对象")
    locator_type = str(value.get("type") or "").strip()
    locator_value = value.get("value")
    if locator_type not in {"key", "text"}:
        raise ValueError(f"{location}.type 只能是 key 或 text")
    if locator_value in (None, "", []):
        raise ValueError(f"{location}.value 不能为空")
    item: Dict[str, Any] = {
        "type": locator_type,
        "value": locator_value,
    }
    for field in ("key_description", "step_prompt"):
        text = str(value.get(field) or "").strip()
        if text:
            item[field] = text
    return item


def _legacy_step_items(raw_group: Dict[str, Any], *, operation_index: int) -> List[Dict[str, Any]]:
    """Read the temporary PR#19 stepN representation for one-time migration."""
    indexed: List[Tuple[int, Dict[str, Any]]] = []
    for key, raw_item in raw_group.items():
        match = re.fullmatch(r"step(\d+)", str(key))
        if not match:
            continue
        # PR#19 added ``operate`` inside a step.  It is execution metadata, not
        # part of the locator contract, so normalization intentionally drops it.
        indexed.append((
            int(match.group(1)),
            _normalize_item(
                raw_item,
                location=f"special_opearte.operation{operation_index}.{key}",
            ),
        ))
    return [item for _, item in sorted(indexed, key=lambda pair: pair[0])]


def normalize_special_opearte(value: Any) -> Dict[str, List[Dict[str, Any]]]:
    """Normalize manual data to ``operationN -> [item, ...]``.

    The desired array form is authoritative.  The short-lived PR#19
    ``operateN -> {step1: ..., step2: ...}`` form is accepted only so existing
    data can be migrated without loss; it is never emitted again.
    """
    if value in (None, ""):
        return {}
    if not isinstance(value, dict):
        raise ValueError("special_opearte 必须是 JSON 对象")

    normalized: Dict[str, List[Dict[str, Any]]] = {}
    for operation_index, (_raw_name, raw_group) in enumerate(value.items(), start=1):
        if isinstance(raw_group, list):
            items = [
                _normalize_item(
                    raw_item,
                    location=f"special_opearte.operation{operation_index}[{item_index}]",
                )
                for item_index, raw_item in enumerate(raw_group)
            ]
        elif isinstance(raw_group, dict):
            items = _legacy_step_items(raw_group, operation_index=operation_index)
        else:
            raise ValueError(
                f"special_opearte.operation{operation_index} 必须是 JSON 数组"
            )
        if not items:
            raise ValueError(
                f"special_opearte.operation{operation_index} 至少需要一个数组项"
            )
        normalized[f"operation{operation_index}"] = items
    return normalized


def format_special_item(operation: Any) -> Dict[str, Any]:
    """Convert one recorded operation to the executable locator item."""
    if not isinstance(operation, dict):
        return {}
    target = operation.get("target")
    if not isinstance(target, dict):
        return {}

    key = str(target.get("key") or "").strip()
    text = str(target.get("text") or "").strip()
    raw_type = str(target.get("type") or "").strip()
    raw_value = target.get("value")

    if key:
        locator_type, locator_value = "key", key
    elif text:
        locator_type, locator_value = "text", text
    elif raw_type in {"key", "text"} and raw_value not in (None, "", []):
        locator_type, locator_value = raw_type, raw_value
    else:
        # Popup recording may keep Dialog/SheetWrapper/MenuWrapper in
        # target.type.  Popup type is metadata, never a locator type.
        if raw_value in (None, "", []):
            return {}
        locator_type, locator_value = "text", raw_value

    description = str(
        target.get("key_description")
        or target.get("step_prompt")
        or text
        or locator_value
        or ""
    ).strip()
    prompt = str(
        target.get("step_prompt")
        or target.get("key_description")
        or text
        or locator_value
        or ""
    ).strip()
    result: Dict[str, Any] = {
        "type": locator_type,
        "value": locator_value,
    }
    if description:
        result["key_description"] = description
    if prompt:
        result["step_prompt"] = prompt
    return result


def _session_id(effect: Any) -> str:
    """Return the special capture group id without exposing step numbering."""
    value = str(effect or "").strip()
    if not value.startswith(SPECIAL_EFFECT_PREFIX):
        return ""
    remainder = value[len(SPECIAL_EFFECT_PREFIX):]
    # New recordings use special_capture::<session>.  Old recordings may still
    # contain special_capture::<session>::stepN; both become the same array.
    return remainder.split("::", 1)[0].strip()


def build_special_opearte(state: Any) -> Dict[str, List[Dict[str, Any]]]:
    """Build operation arrays in persisted recording order."""
    if not isinstance(state, dict):
        return {}

    manual = state.get(SPECIAL_MANUAL_FIELD)
    if isinstance(manual, dict):
        return normalize_special_opearte(manual)

    recorded: List[Tuple[str, int, Dict[str, Any]]] = []
    sequence = 0
    for field in ("page_operations", "special_operations"):
        source = state.get(field)
        if not isinstance(source, list):
            continue
        for operation in source:
            if not isinstance(operation, dict):
                continue
            recorded.append((str(operation.get("created_at") or ""), sequence, operation))
            sequence += 1
    recorded.sort(key=lambda row: (row[0], row[1]))

    groups: List[List[Dict[str, Any]]] = []
    session_groups: Dict[str, List[Dict[str, Any]]] = {}
    for _created_at, _sequence, operation in recorded:
        effect = str(operation.get("effect") or "").strip()
        session_id = _session_id(effect)
        popup_type = str(operation.get("popup_type") or "").strip()
        is_popup = bool(popup_type or effect == "open_popup")
        is_special = str(operation.get("operation_kind") or "").strip() == "special_operate"
        if not session_id and not is_popup and not is_special:
            continue

        item = format_special_item(operation)
        if not item:
            continue
        if session_id:
            group = session_groups.get(session_id)
            if group is None:
                group = []
                session_groups[session_id] = group
                groups.append(group)
            group.append(item)
        else:
            groups.append([item])

    return {
        f"operation{index}": items
        for index, items in enumerate(groups, start=1)
        if items
    }


def install_dfs_contract() -> None:
    """Install the canonical contract into the existing DFS module.

    This keeps the request-scoped profile integration intact while correcting
    the accidental PR#19 schema rewrite without duplicating the large DFS file.
    """
    import DFS as dfs

    dfs.DFS_RECORD_FIELDS = (
        "package_name",
        "main_page_name",
        "page_description",
        "path_snapshot",
        "special_opearte",
    )
    dfs.SPECIAL_MANUAL_FIELD = SPECIAL_MANUAL_FIELD
    dfs.normalize_special = normalize_special_opearte
    dfs.normalize_special_opearte = normalize_special_opearte
    dfs.format_special_step = format_special_item
    dfs.build_special_operations = build_special_opearte
    dfs.build_special_opearte = build_special_opearte

    def format_dfs_record(record: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "package_name": str(record.get("package_name") or ""),
            "main_page_name": str(record.get("main_page_name") or ""),
            "page_description": str(record.get("page_description") or ""),
            "path_snapshot": [
                formatted
                for target in record.get("path_snapshot") or []
                if (formatted := dfs.format_path_target(target))
            ],
            "special_opearte": normalize_special_opearte(
                record.get("special_opearte")
                or record.get("special")
                or {}
            ),
        }

    dfs.format_dfs_record = format_dfs_record
