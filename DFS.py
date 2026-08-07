#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Export compact DFS navigation paths from the settings navigation graph.

This script only builds JSON data. It does not connect to a device, replay
operations, resolve coordinates, verify pages, or save runtime sessions.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

Graph = Dict[str, Any]
Transition = Dict[str, Any]
Target = Dict[str, Any]

TARGET_FIELDS = (
    "type",
    "value",
    "key",
    "component_type",
    "text",
    "key_description",
    "step_prompt",
    "expect",
)


DFS_RECORD_FIELDS = (
    "package_name",
    "main_page_name",
    "page_description",
    "path_snapshot",
    "special_opearte",
)

DFS_TARGET_FIELDS = (
    "type",
    "value",
    "key_description",
    "step_prompt",
)

DFS_MANUAL_FIELD = "dfs_manual"
SPECIAL_MANUAL_FIELD = "special_manual"
SPECIAL_EFFECT_PREFIX = "special_capture::"


def resolve_settings_profile_work_dir(
    base_work_dir: Path,
    profile_id: str = "",
) -> Path:
    """Resolve an explicit profile; the CLI never reads global active state."""
    selected_profile_id = str(profile_id or "default").strip() or "default"
    if selected_profile_id == "default":
        return base_work_dir
    if not re.fullmatch(r"profile_[0-9a-f]{12}", selected_profile_id):
        raise ValueError(f"配置 ID 非法：{selected_profile_id}")
    return base_work_dir / "config_profiles" / selected_profile_id


def safe_priority(value: Any, default: int = 1000) -> int:
    """Malformed legacy priority must not make the whole export fail."""
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


def compact_target(target: Any) -> Target:
    """Keep semantic target fields needed while traversing the graph."""
    if not isinstance(target, dict):
        return {}
    return {
        key: target[key]
        for key in TARGET_FIELDS
        if target.get(key) not in (None, "", [])
    }


def format_path_target(target: Any) -> Target:
    """Convert a path step to the strict compact DFS locator schema.

    A stable key is preferred. When no key exists, visible text is used. The
    exported step contains only ``type``, ``value``, ``key_description`` and
    ``step_prompt``.
    """
    compact = compact_target(target)
    if not compact:
        return {}

    key = str(compact.get("key") or "").strip()
    text = str(compact.get("text") or "").strip()
    legacy_type = str(compact.get("type") or "").strip()
    legacy_value = compact.get("value")

    locator_type = ""
    locator_value: Any = None
    if key:
        locator_type, locator_value = "key", key
    elif text:
        locator_type, locator_value = "text", text
    elif legacy_type in {"key", "text"} and legacy_value not in (None, "", []):
        locator_type, locator_value = legacy_type, legacy_value

    if not locator_type:
        return {}

    description = str(
        compact.get("key_description")
        or compact.get("step_prompt")
        or text
        or locator_value
        or ""
    ).strip()
    step_prompt = str(
        compact.get("step_prompt")
        or compact.get("key_description")
        or text
        or locator_value
        or ""
    ).strip()

    formatted: Target = {
        "type": locator_type,
        "value": locator_value,
    }
    if description:
        formatted["key_description"] = description
    if step_prompt:
        formatted["step_prompt"] = step_prompt
    return formatted



def _normalize_special_item(value: Any, *, location: str = "special_opearte") -> Dict[str, Any]:
    """Normalize one executable locator to the fixed four-field schema."""
    if not isinstance(value, dict):
        raise ValueError(f"{location} 必须是 JSON 对象")
    locator_type = str(value.get("type") or "").strip()
    locator_value = value.get("value")
    if locator_type not in {"key", "text"}:
        raise ValueError(f"{location}.type 只能是 key 或 text")
    if locator_value in (None, "", []):
        raise ValueError(f"{location}.value 不能为空")
    description = str(
        value.get("key_description")
        or value.get("step_prompt")
        or locator_value
        or ""
    ).strip()
    prompt = str(
        value.get("step_prompt")
        or value.get("key_description")
        or locator_value
        or ""
    ).strip()
    return {
        "type": locator_type,
        "value": locator_value,
        "key_description": description,
        "step_prompt": prompt,
    }


def normalize_special(value: Any) -> Dict[str, List[Dict[str, Any]]]:
    """Normalize persisted special_opearte to operationN -> ordered locator array."""
    if value in (None, ""):
        return {}
    if not isinstance(value, dict):
        raise ValueError("special_opearte 必须是 JSON 对象")
    result: Dict[str, List[Dict[str, Any]]] = {}
    for operation_index, (_name, raw_group) in enumerate(value.items(), start=1):
        items: List[Dict[str, Any]] = []
        if isinstance(raw_group, list):
            for item_index, raw_item in enumerate(raw_group, start=1):
                items.append(_normalize_special_item(
                    raw_item,
                    location=f"special_opearte.operation{operation_index}[{item_index}]",
                ))
        elif isinstance(raw_group, dict):
            # 仅用于兼容之前误生成的 operateN -> {step1, step2...} 数据；
            # 保存或导出时统一恢复成 operationN -> []。
            indexed = []
            for key, raw_item in raw_group.items():
                match = re.fullmatch(r"step(\d+)", str(key))
                if match:
                    indexed.append((int(match.group(1)), raw_item))
            for step_index, raw_item in sorted(indexed):
                items.append(_normalize_special_item(
                    raw_item,
                    location=f"special_opearte.operation{operation_index}.step{step_index}",
                ))
        else:
            raise ValueError(f"special_opearte.operation{operation_index} 必须是 JSON 数组")
        if not items:
            raise ValueError(f"special_opearte.operation{operation_index} 至少需要一个数组项")
        result[f"operation{operation_index}"] = items
    return result


def format_special_step(operation: Any) -> Dict[str, Any]:
    """Format one recorded special operation as a key/text four-field locator."""
    if not isinstance(operation, dict):
        return {}
    target = compact_target(operation.get("target"))
    if not target:
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
        # Dialog/SheetWrapper/MenuWrapper 只是弹窗元数据，不作为 locator type。
        if raw_value in (None, "", []):
            return {}
        locator_type, locator_value = "text", raw_value
    return _normalize_special_item({
        "type": locator_type,
        "value": locator_value,
        "key_description": target.get("key_description") or target.get("step_prompt") or text or locator_value,
        "step_prompt": target.get("step_prompt") or target.get("key_description") or text or locator_value,
    })


def _special_session_metadata(effect: Any) -> Optional[tuple[str, int]]:
    """Internal capture metadata; stepN never becomes an exported JSON level."""
    value = str(effect or "").strip()
    if not value.startswith(SPECIAL_EFFECT_PREFIX):
        return None
    parts = value.split("::")
    if len(parts) < 2 or not parts[1]:
        return None
    if len(parts) >= 3:
        match = re.fullmatch(r"step(\d+)", parts[2])
        if match:
            return parts[1], int(match.group(1))
    return parts[1], 10**9


def build_special_operations(state: Any) -> Dict[str, List[Dict[str, Any]]]:
    """Return special_opearte as operationN -> ordered locator arrays."""
    if not isinstance(state, dict):
        return {}
    manual = state.get(SPECIAL_MANUAL_FIELD)
    if isinstance(manual, dict):
        return normalize_special(manual)

    combined = []
    sequence = 0
    for field in ("page_operations", "special_operations"):
        source = state.get(field)
        if not isinstance(source, list):
            continue
        for operation in source:
            if isinstance(operation, dict):
                combined.append((str(operation.get("created_at") or ""), sequence, operation))
                sequence += 1
    combined.sort(key=lambda item: (item[0], item[1]))

    groups: List[Dict[str, Any]] = []
    sessions: Dict[str, Dict[str, Any]] = {}
    for order, (_created_at, _sequence, operation) in enumerate(combined):
        effect = str(operation.get("effect") or "").strip()
        session = _special_session_metadata(effect)
        is_popup = bool(operation.get("popup_type")) or effect == "open_popup"
        is_special = str(operation.get("operation_kind") or "").strip() == "special_operate"
        if not session and not is_popup and not is_special:
            continue
        step = format_special_step(operation)
        if not step:
            continue
        if session:
            session_id, step_index = session
            group = sessions.get(session_id)
            if group is None:
                group = {"first": order, "steps": []}
                sessions[session_id] = group
                groups.append(group)
            group["steps"].append((step_index, order, step))
        else:
            groups.append({"first": order, "steps": [(1, order, step)]})
    groups.sort(key=lambda item: int(item["first"]))
    return {
        f"operation{index}": [
            step for _, _, step in sorted(group["steps"], key=lambda item: (item[0], item[1]))
        ]
        for index, group in enumerate(groups, start=1)
        if group.get("steps")
    }

def replace_navigation_target_locator(
    target: Any,
    manual_target: Any,
) -> bool:
    """Replace a transition target's key/text locator without stale fallback."""
    if not isinstance(target, dict):
        return False
    formatted = format_path_target(manual_target)
    locator_type = str(formatted.get("type") or "")
    locator_value = formatted.get("value")
    if locator_type not in {"key", "text"}:
        return False

    target.pop("text" if locator_type == "key" else "key", None)
    if str(target.get("type") or "") in {"key", "text"}:
        target.pop("type", None)
        target.pop("value", None)
    target[locator_type] = locator_value
    for field in ("key_description", "step_prompt"):
        value = str(formatted.get(field) or "").strip()
        if value:
            target[field] = value
    return True


def is_human_description(value: Any) -> bool:
    """Return whether a label is suitable for a user-facing page description."""
    label = str(value or "").strip()
    if not label or not any(char.isalnum() or "\u4e00" <= char <= "\u9fff" for char in label):
        return False
    # 下划线、点号连接的纯英文通常是控件 key；连字符可能是合法页面名，
    # 例如 a-b，因此不能把 "-" 当作技术标识符特征。
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9-]*(?:[._][A-Za-z0-9-]+)+", label):
        return False
    return True


def transition_destination_label(transition: Transition) -> str:
    """Use only the destination action label, never intermediate menu actions."""
    steps = transition.get("steps")
    targets: List[Target] = []
    if isinstance(steps, list):
        targets = [
            step.get("target")
            for step in steps
            if isinstance(step, dict) and isinstance(step.get("target"), dict)
        ]
    if not targets and isinstance(transition.get("target"), dict):
        targets = [transition["target"]]
    for target in reversed(targets):
        for field in ("step_prompt", "key_description", "text"):
            label = str(target.get(field) or "").strip()
            if is_human_description(label):
                return label
    return ""


def page_description_leaf(value: Any) -> str:
    """Return the page-local label from a contextual description or page id."""
    label = str(value or "").strip()
    if not label:
        return ""
    if label.startswith("Pages_"):
        label = label.removeprefix("Pages_")
    for separator in ("_to", " to"):
        if separator in label:
            label = label.rsplit(separator, 1)[-1].strip()
    if "_" in label:
        label = label.rsplit("_", 1)[-1].strip()
    return label


def dfs_record_display_name(record: Any, page_name: str = "") -> str:
    """Choose the one short page name shown by the Web console."""
    if isinstance(record, dict):
        description = page_description_leaf(record.get("page_description"))
        if is_human_description(description):
            return description
        for target in reversed(record.get("path_snapshot") or []):
            if not isinstance(target, dict):
                continue
            for field in ("step_prompt", "key_description", "text", "value", "key"):
                label = str(target.get(field) or "").strip()
                if is_human_description(label):
                    return label
    fallback = page_description_leaf(page_name)
    return fallback or str(page_name or "")


def page_description_segment(
    page_name: str,
    state: Dict[str, Any],
    transition: Transition,
    previous_segments: List[str],
) -> str:
    """Choose one semantic page label for this reached state."""
    previous = previous_segments[-1] if previous_segments else ""
    manual = normalize_manual_dfs(state.get(DFS_MANUAL_FIELD))
    manual_label = dfs_record_display_name(manual) if manual else ""
    candidates = [
        manual_label,
        page_description_leaf(state.get("page_description")),
        page_description_leaf(state.get("last_title")),
        transition_destination_label(transition),
        page_description_leaf(page_name),
    ]
    for value in candidates:
        label = str(value or "").strip()
        if not is_human_description(label) or label == previous:
            continue
        return label
    return ""


def normalize_manual_dfs(value: Any) -> Optional[Dict[str, Any]]:
    """Normalize a persisted manual DFS record without leaking extra fields."""
    if not isinstance(value, dict):
        return None
    path = value.get("path_snapshot")
    if not isinstance(path, list):
        return None
    return {
        "package_name": str(value.get("package_name") or "").strip(),
        "main_page_name": str(value.get("main_page_name") or "").strip(),
        "page_description": str(value.get("page_description") or "").strip(),
        "path_snapshot": [
            formatted
            for target in path
            if (formatted := format_path_target(target))
        ],
    }


def sync_descendant_manual_dfs_prefixes(
    graph: Graph,
    page_name: str,
    old_path: List[Target],
    new_path: List[Target],
    old_description: str = "",
    new_description: str = "",
) -> List[str]:
    """Replace one page's old DFS prefix in every manual descendant record.

    Descendant matching follows the navigation graph.  Normally the complete
    old locator prefix must match.  When the current page's final locator was
    previously left stale (for example key -> text), the shared ancestor
    prefix is enough to repair that current-page step in structural
    descendants.
    """
    states = graph.get("states")
    if not isinstance(states, dict) or page_name not in states:
        return []

    old_prefix = [
        formatted
        for target in old_path
        if (formatted := format_path_target(target))
    ]
    new_prefix = [
        formatted
        for target in new_path
        if (formatted := format_path_target(target))
    ]
    if not old_prefix:
        return []

    outgoing: Dict[str, List[str]] = {}
    for transition in graph.get("transitions") or []:
        if not isinstance(transition, dict):
            continue
        source = str(transition.get("from_page") or "")
        target = str(transition.get("to_page") or "")
        if source and target and source != target:
            outgoing.setdefault(source, []).append(target)

    descendants: Set[str] = set()
    stack = list(outgoing.get(page_name, []))
    while stack:
        current = stack.pop()
        if current == page_name or current in descendants:
            continue
        descendants.add(current)
        stack.extend(outgoing.get(current, []))

    def same_locator(left: Target, right: Target) -> bool:
        return (
            str(left.get("type") or "") == str(right.get("type") or "")
            and str(left.get("value") or "") == str(right.get("value") or "")
        )

    updated_pages: List[str] = []
    for descendant in sorted(descendants):
        state = states.get(descendant)
        if not isinstance(state, dict):
            continue
        manual = state.get(DFS_MANUAL_FIELD)
        if not isinstance(manual, dict):
            continue
        manual_path = [
            formatted
            for target in manual.get("path_snapshot") or []
            if (formatted := format_path_target(target))
        ]
        if len(manual_path) < len(old_prefix):
            continue
        full_prefix_matches = all(
            same_locator(manual_path[index], old_prefix[index])
            for index in range(len(old_prefix))
        )
        ancestor_prefix_length = len(old_prefix) - 1
        ancestor_prefix_matches = all(
            same_locator(manual_path[index], old_prefix[index])
            for index in range(ancestor_prefix_length)
        )
        if not full_prefix_matches and not ancestor_prefix_matches:
            continue
        manual["path_snapshot"] = [
            *(dict(target) for target in new_prefix),
            *(dict(target) for target in manual_path[len(old_prefix):]),
        ]
        descendant_description = str(manual.get("page_description") or "")
        if (
            old_description
            and new_description
            and (
                descendant_description == old_description
                or descendant_description.startswith(f"{old_description}_")
            )
        ):
            replaced_description = (
                f"{new_description}{descendant_description[len(old_description):]}"
            )
            manual["page_description"] = replaced_description
            if state.get("page_description") == descendant_description:
                state["page_description"] = replaced_description
        updated_pages.append(descendant)
    return updated_pages


def apply_manual_dfs(record: Dict[str, Any], state: Any) -> Dict[str, Any]:
    """Apply the page's explicit DFS record when one has been maintained."""
    manual = normalize_manual_dfs(
        state.get(DFS_MANUAL_FIELD) if isinstance(state, dict) else None
    )
    if not manual:
        return record
    return {
        **record,
        **manual,
        "page_name": record.get("page_name"),
        "is_manual": True,
    }


def root_dfs_record(graph: Graph, records: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Build the root-page record whose navigation path is intentionally empty."""
    states = graph.get("states")
    if not isinstance(states, dict):
        return None

    traversal_config = graph.get("traversal_config")
    configured_root = (
        traversal_config.get("root_page")
        if isinstance(traversal_config, dict)
        else ""
    )
    root_page = str(configured_root or "Pages_root")
    root_state = states.get(root_page)
    if not isinstance(root_state, dict):
        return None

    first_record = next((item for item in records if isinstance(item, dict)), {})
    description = str(
        root_state.get("last_title")
        or root_state.get("page_description")
        or root_page
    ).strip()
    record = {
        "page_name": root_page,
        "package_name": str(
            graph.get("package_name")
            or first_record.get("package_name")
            or ""
        ),
        "main_page_name": str(
            graph.get("main_page_name")
            or first_record.get("main_page_name")
            or ""
        ),
        "page_description": description,
        "path_snapshot": [],
        "special_opearte": build_special_operations(root_state),
    }
    return apply_manual_dfs(record, root_state)


def format_dfs_record(record: Dict[str, Any]) -> Dict[str, Any]:
    """Keep the compact DFS contract, including page-local special_opearte."""
    special = record.get("special_opearte")
    return {
        "package_name": str(record.get("package_name") or ""),
        "main_page_name": str(record.get("main_page_name") or ""),
        "page_description": str(record.get("page_description") or ""),
        "path_snapshot": [
            formatted_target
            for target in record.get("path_snapshot") or []
            if (formatted_target := format_path_target(target))
        ],
        "special_opearte": normalize_special(special) if isinstance(special, dict) and special else {},
    }


def format_dfs_records(records: List[Dict[str, Any]], graph: Graph) -> List[Dict[str, Any]]:
    formatted_records: List[Dict[str, Any]] = []
    emitted: Set[str] = set()
    root_page = str(graph.get("traversal_config", {}).get("root_page") or "Pages_root")
    if root_record := root_dfs_record(graph, records):
        formatted_records.append(format_dfs_record(root_record))
        emitted.add(root_page)
    for record in records:
        if not isinstance(record, dict):
            continue
        formatted_records.append(format_dfs_record(record))
        emitted.add(str(record.get("page_name") or ""))
    for page_name, state in graph.get("states", {}).items():
        if page_name in emitted or not isinstance(state, dict):
            continue
        manual = normalize_manual_dfs(state.get(DFS_MANUAL_FIELD))
        if not manual:
            continue
        formatted_records.append(format_dfs_record({
            **manual,
            "page_name": page_name,
            "special_opearte": build_special_operations(state),
        }))
    return formatted_records


def transition_dfs_targets(transition: Any) -> Optional[List[Target]]:
    """Return executable DFS locators for one transition, or None when incomplete."""
    if not isinstance(transition, dict):
        return None
    steps = transition.get("steps")
    if not isinstance(steps, list) or not steps:
        target = transition.get("target")
        steps = [
            {"operate": transition.get("operate") or "tap", "target": target}
        ] if isinstance(target, dict) and target else []
    if not steps:
        return None

    targets: List[Target] = []
    for step in steps:
        if not isinstance(step, dict):
            return None
        formatted = format_path_target(step.get("target"))
        if not formatted:
            return None
        targets.append(formatted)
    return targets


def export_dfs_paths(graph: Graph, root_page: str) -> tuple[List[Dict[str, Any]], List[str]]:
    states = graph.get("states")
    if not isinstance(states, dict):
        raise ValueError("navigation graph states must be an object")
    if root_page not in states:
        raise ValueError(f"root page does not exist in graph: {root_page}")

    outgoing: Dict[str, List[Transition]] = {}
    for record_order, transition in enumerate(graph.get("transitions") or []):
        if not isinstance(transition, dict):
            continue
        from_page = str(transition.get("from_page") or "")
        to_page = str(transition.get("to_page") or "")
        if not from_page or not to_page or from_page == to_page:
            continue
        if from_page not in states or to_page not in states:
            continue
        outgoing.setdefault(from_page, []).append({**transition, "_record_order": record_order})
    for transitions in outgoing.values():
        transitions.sort(key=lambda item: (
            safe_priority(item.get("priority")),
            int(item.get("_record_order", 0)),
            str(item.get("transition_id") or ""),
        ))

    visited: Set[str] = set()
    records: List[Dict[str, Any]] = []

    def visit(
        page_name: str,
        path_snapshot: List[Target],
        description_segments: List[str],
    ) -> None:
        if page_name in visited:
            return
        visited.add(page_name)

        if page_name != root_page:
            state = graph.get("states", {}).get(page_name, {})
            fallback = str(state.get("last_title") or state.get("page_description") or page_name) if isinstance(state, dict) else page_name
            record = {
                "page_name": page_name,
                "package_name": str(graph.get("package_name") or ""),
                "main_page_name": str(graph.get("main_page_name") or ""),
                "page_description": "_".join(description_segments) or fallback,
                "path_snapshot": path_snapshot,
                "special_opearte": build_special_operations(state),
            }
            records.append(apply_manual_dfs(record, state))

        for transition in outgoing.get(page_name, []):
            to_page = str(transition.get("to_page") or "")
            if not to_page or to_page in visited:
                continue
            targets = transition_dfs_targets(transition)
            if targets is None:
                continue
            child_state = states.get(to_page, {})
            segment = page_description_segment(
                to_page,
                child_state if isinstance(child_state, dict) else {},
                transition,
                description_segments,
            )
            visit(
                to_page,
                [*path_snapshot, *targets],
                [*description_segments, *([segment] if segment else [])],
            )

    visit(root_page, [], [])

    # A manually maintained page is a valid DFS anchor.  Continue walking its
    # descendants from the manual path instead of forcing every child below an
    # unlocatable transition to be maintained by hand as well.
    for page_name, state in states.items():
        if page_name in visited or not isinstance(state, dict):
            continue
        manual = normalize_manual_dfs(state.get(DFS_MANUAL_FIELD))
        if not manual or not manual.get("path_snapshot"):
            continue
        manual_description = str(
            manual.get("page_description") or page_name
        ).strip()
        visit(
            str(page_name),
            [dict(target) for target in manual["path_snapshot"]],
            [manual_description] if manual_description else [],
        )

    return records, sorted(str(page) for page in states if page not in visited)


def dfs_records_with_pages(
    graph: Graph,
) -> tuple[List[Dict[str, Any]], List[str]]:
    """Return compact records with page_name retained for Web inspection."""
    root_page = str(
        graph.get("traversal_config", {}).get("root_page") or "Pages_root"
    )
    raw_records, unreachable = export_dfs_paths(graph, root_page)
    records: List[Dict[str, Any]] = []
    if root_record := root_dfs_record(graph, raw_records):
        records.append({
            "page_name": root_page,
            **format_dfs_record(root_record),
        })
    records.extend({
        "page_name": str(record.get("page_name") or ""),
        **format_dfs_record(record),
    } for record in raw_records if isinstance(record, dict))
    return records, unreachable


def dfs_record_for_page(graph: Graph, page_name: str) -> Optional[Dict[str, Any]]:
    """Return the final compact DFS record for one reachable page."""
    records, _ = dfs_records_with_pages(graph)
    return next((
        {key: value for key, value in record.items() if key != "page_name"}
        for record in records
        if record.get("page_name") == page_name
    ), None)


def dfs_branch_for_page(graph: Graph, page_name: str) -> Dict[str, Any]:
    """Return current DFS record and every reachable descendant record."""
    states = graph.get("states")
    if not isinstance(states, dict) or page_name not in states:
        raise ValueError(f"page does not exist in graph: {page_name}")

    outgoing: Dict[str, List[tuple[int, int, str]]] = {}
    for record_order, transition in enumerate(graph.get("transitions") or []):
        if not isinstance(transition, dict):
            continue
        source = str(transition.get("from_page") or "")
        target = str(transition.get("to_page") or "")
        if (
            not source
            or not target
            or source == target
            or source not in states
            or target not in states
        ):
            continue
        outgoing.setdefault(source, []).append((
            safe_priority(transition.get("priority")),
            record_order,
            target,
        ))
    descendants: Set[str] = set()
    stack = [page_name]
    while stack:
        current = stack.pop()
        if current in descendants:
            continue
        descendants.add(current)
        children = sorted(outgoing.get(current, []))
        stack.extend(target for _, _, target in reversed(children))

    records, unreachable = dfs_records_with_pages(graph)
    current_record = next((
        record for record in records if record.get("page_name") == page_name
    ), None)
    branch_records = [
        {
            **record,
            "display_name": dfs_record_display_name(
                record,
                str(record.get("page_name") or ""),
            ),
        }
        for record in records
        if record.get("page_name") in descendants
    ]
    if current_record:
        current_record = {
            **current_record,
            "display_name": dfs_record_display_name(current_record, page_name),
        }
    return {
        "page_name": page_name,
        "display_name": (
            current_record.get("display_name")
            if isinstance(current_record, dict)
            else page_description_leaf(page_name)
        ),
        "current_record": current_record,
        "branch_records": branch_records,
        "unreachable_pages": unreachable,
    }


class DfsPathExporter:
    """Compatibility wrapper around the simple export function."""

    def __init__(self, graph: Graph, root_page: str) -> None:
        self.graph = graph
        self.root_page = root_page
        self._records: Optional[List[Dict[str, Any]]] = None
        self._unreachable: Optional[List[str]] = None

    def build(self) -> List[Dict[str, Any]]:
        if self._records is None:
            self._records, self._unreachable = export_dfs_paths(self.graph, self.root_page)
        return self._records

    def unreachable_pages(self) -> List[str]:
        if self._unreachable is None:
            self._records, self._unreachable = export_dfs_paths(self.graph, self.root_page)
        return self._unreachable


def main() -> None:
    parser = argparse.ArgumentParser(description="Export compact DFS paths from settings_navigation_graph.json.")
    parser.add_argument("--work-dir", default="demo_settings", help="Project work dir containing outputs/navigation/settings_navigation_graph.json.")
    parser.add_argument("--profile-id", default="default", help="Settings version/device profile; defaults to the inherited baseline configuration.")
    parser.add_argument("--graph", default="", help="Explicit graph path; overrides --work-dir.")
    parser.add_argument("--root", default="", help="DFS root; defaults to traversal_config.root_page or Pages_root.")
    parser.add_argument("--output", default="", help="Output path; defaults beside the graph.")
    parser.add_argument("--format", choices=["text", "json"], default="text", help="Console output format.")
    args = parser.parse_args()
    base_work_dir = Path(args.work_dir)
    if args.graph:
        graph_path = Path(args.graph)
    else:
        work_dir = resolve_settings_profile_work_dir(
            base_work_dir,
            str(args.profile_id or ""),
        )
        graph_path = (
            work_dir
            / "outputs"
            / "navigation"
            / "settings_navigation_graph.json"
        )
    if not graph_path.exists():
        raise FileNotFoundError(f"navigation graph does not exist: {graph_path}")
    graph = json.loads(graph_path.read_text(encoding="utf-8"))
    if not isinstance(graph, dict):
        raise ValueError("navigation graph root must be a JSON object")
    root_page = str(args.root or graph.get("traversal_config", {}).get("root_page") or "Pages_root")
    exporter = DfsPathExporter(graph, root_page)
    output = format_dfs_records(exporter.build(), graph)
    output_path = Path(args.output) if args.output else graph_path.parent / "settings_navigation_paths.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.format == "json":
        print(json.dumps(output, ensure_ascii=False, indent=2))
    else:
        print(f"DFS 页面路径数量: {len(output)}")
        for index, record in enumerate(output, start=1):
            print(f"{index:03d}. {record['page_description']}")
        print(f"精简路径已保存: {output_path}")
        unreachable = exporter.unreachable_pages()
        if unreachable:
            print(f"警告: {len(unreachable)} 个页面从 {root_page} 不可达")
            for page in unreachable:
                print(f"  - {page}")


if __name__ == "__main__":
    main()
