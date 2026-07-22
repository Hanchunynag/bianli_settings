from __future__ import annotations

import re
from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    print(f"{label}: {count} exact match(es)")
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


def regex_once(text: str, pattern: str, replacement: str, label: str, flags: int = 0) -> str:
    updated, count = re.subn(pattern, replacement, text, count=1, flags=flags)
    print(f"{label}: {count} regex match(es)")
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {count}")
    return updated


def patch_recorder() -> None:
    path = Path("settings_ui_manual_recorder.py")
    text = path.read_text(encoding="utf-8")
    if '"raw_page_name": page_name' not in text:
        text = regex_once(
            text,
            r'(?m)^(\s*)"page_name": page_name,\n\1"page_description":',
            lambda m: (
                f'{m.group(1)}"page_name": page_name,\n'
                f'{m.group(1)}"raw_page_name": page_name,\n'
                f'{m.group(1)}"page_description":'
            ),
            "add raw_page_name",
        )
    path.write_text(text, encoding="utf-8")


HELPERS = '''def state_raw_page_name(state: Dict[str, Any], page_name: str = "") -> str:
    """返回不受父路径影响、仅由当前页面标题生成的页面身份。"""
    raw_name = str(state.get("raw_page_name") or "").strip()
    if raw_name:
        return raw_name
    title = str(state.get("last_title") or "").strip()
    if title:
        return state_name_from_title(title, overlay=bool(state.get("is_overlay")))
    return str(state.get("page_name") or page_name or "").strip()


def state_display_title(state: Dict[str, Any], page_name: str = "") -> str:
    title = str(state.get("last_title") or state.get("page_description") or "").strip()
    title = title.removeprefix("弹窗：")
    if title:
        return title
    if page_name == "Pages_root":
        return "设置"
    return page_name.removeprefix("Pages_").removeprefix("Overlay_") or "page"


def current_session_page() -> str:
    path = config.work_dir / "outputs" / "navigation" / "current_path_session.json"
    if not path.exists():
        return ""
    try:
        return str(load_json(path).get("active_page") or "")
    except Exception:
        return ""


def copy_stored_page_context(
    detected_state: Dict[str, Any],
    stored_state: Dict[str, Any],
    page_name: str,
) -> Dict[str, Any]:
    state = dict(detected_state)
    state["page_name"] = page_name
    state["raw_page_name"] = state_raw_page_name(detected_state)
    for key in (
        "parent_page",
        "parent_title",
        "page_description",
        "state_type",
        "is_overlay",
        "overlay_parent",
        "overlay_title",
    ):
        if key in stored_state:
            state[key] = stored_state[key]
    return state


def resolve_detected_state(
    graph: Dict[str, Any],
    detected_state: Dict[str, Any],
    preferred_page: str = "",
) -> Dict[str, Any]:
    """将 UI Tree 的标题级名称恢复成导航图中的上下文名称。"""
    state = dict(detected_state)
    raw_name = state_raw_page_name(state)
    state["raw_page_name"] = raw_name
    current_name = str(state.get("page_name") or "")
    if current_name and current_name != raw_name and state.get("parent_page"):
        return state
    if raw_name == "Pages_root":
        state["page_name"] = "Pages_root"
        return state

    states = graph.get("states", {})
    preferred_state = states.get(preferred_page, {}) if preferred_page else {}
    if (
        isinstance(preferred_state, dict)
        and state_raw_page_name(preferred_state, preferred_page) == raw_name
    ):
        return copy_stored_page_context(state, preferred_state, preferred_page)

    matches = []
    for page_name, stored_state in states.items():
        if not isinstance(stored_state, dict):
            continue
        if state_raw_page_name(stored_state, str(page_name)) == raw_name:
            matches.append((str(page_name), stored_state))
    if len(matches) == 1:
        return copy_stored_page_context(state, matches[0][1], matches[0][0])
    return state


def states_represent_same_page(left: Dict[str, Any], right: Dict[str, Any]) -> bool:
    left_raw = state_raw_page_name(left)
    return bool(left_raw and left_raw == state_raw_page_name(right))


def state_matches_graph_page(
    graph: Dict[str, Any],
    detected_state: Dict[str, Any],
    page_name: str,
) -> bool:
    expected_state = graph.get("states", {}).get(page_name, {"page_name": page_name})
    return states_represent_same_page(detected_state, expected_state)


def rename_graph_page(graph: Dict[str, Any], old_name: str, new_name: str) -> None:
    """把同一父页面下的旧标题级节点迁移成上下文节点。"""
    if not old_name or old_name == new_name:
        return
    states = graph.setdefault("states", {})
    if old_name not in states or new_name in states:
        return
    state = states.pop(old_name)
    state["page_name"] = new_name
    states[new_name] = state
    for transition in graph.get("transitions", []):
        if transition.get("from_page") == old_name:
            transition["from_page"] = new_name
        if transition.get("to_page") == old_name:
            transition["to_page"] = new_name


def contextualize_child_state(
    graph: Dict[str, Any],
    from_page: str,
    detected_state: Dict[str, Any],
) -> Dict[str, Any]:
    """根据父页面标题与子页面标题生成 Pages_<父>to<子>。"""
    state = dict(detected_state)
    raw_name = state_raw_page_name(state)
    if raw_name == "Pages_root":
        state["page_name"] = "Pages_root"
        state["raw_page_name"] = "Pages_root"
        return state

    parent_state = graph.get("states", {}).get(from_page, {"page_name": from_page})
    parent_title = state_display_title(parent_state, from_page)
    child_title = state_display_title(state, raw_name)
    contextual_name = state_name_from_title(
        f"{parent_title}to{child_title}",
        overlay=bool(state.get("is_overlay")),
    )

    matching_children = []
    for transition in graph.get("transitions", []):
        if transition.get("from_page") != from_page:
            continue
        child_name = str(transition.get("to_page") or "")
        child_state = graph.get("states", {}).get(child_name, {})
        if (
            isinstance(child_state, dict)
            and state_raw_page_name(child_state, child_name) == raw_name
        ):
            matching_children.append(child_name)
    if len(matching_children) == 1 and matching_children[0] != contextual_name:
        rename_graph_page(graph, matching_children[0], contextual_name)

    existing_state = graph.get("states", {}).get(contextual_name, {})
    if isinstance(existing_state, dict) and existing_state:
        state = copy_stored_page_context(state, existing_state, contextual_name)
    state.update({
        "page_name": contextual_name,
        "raw_page_name": raw_name,
        "parent_page": from_page,
        "parent_title": parent_title,
        "page_description": f"{parent_title} -> {child_title}",
    })
    return state


'''


def patch_server() -> None:
    path = Path("web_nav_server.py")
    text = path.read_text(encoding="utf-8")

    if "    state_name_from_title,\n" not in text:
        text = replace_once(
            text,
            "    screen_metrics_from_root,\n    walk,",
            "    screen_metrics_from_root,\n    state_name_from_title,\n    walk,",
            "import state_name_from_title",
        )

    if "def state_raw_page_name(" not in text:
        text = replace_once(
            text,
            "def read_current_state(capture: bool, persist_candidates: bool = True) -> Dict[str, Any]:",
            HELPERS + "def read_current_state(capture: bool, persist_candidates: bool = True) -> Dict[str, Any]:",
            "insert contextual helpers",
        )

    text = replace_once(
        text,
        "    state = build_navigation_state(root_json)\n    graph = load_navigation_graph(config.work_dir)\n    existing_state = graph.get(\"states\", {}).get(state[\"page_name\"], {})",
        "    state = build_navigation_state(root_json)\n    graph = load_navigation_graph(config.work_dir)\n    state = resolve_detected_state(graph, state, current_session_page())\n    existing_state = graph.get(\"states\", {}).get(state[\"page_name\"], {})",
        "resolve read_current_state",
    )

    text = replace_once(
        text,
        ") -> Dict[str, Any]:\n    candidates = extract_navigation_candidates(root_json)\n    return {\n        \"state\": state,",
        ") -> Dict[str, Any]:\n    state = resolve_detected_state(graph, state, active_page)\n    candidates = extract_navigation_candidates(root_json)\n    return {\n        \"state\": state,",
        "resolve state_response_from_capture",
    )

    old_capture = '    return {"root": root_json, "state": build_navigation_state(root_json)}'
    capture_count = text.count(old_capture)
    print(f"resolve capture helpers: {capture_count} exact match(es)")
    if capture_count != 2:
        raise SystemExit(
            f"resolve capture helpers: expected exactly two matches, found {capture_count}"
        )
    new_capture = (
        '    state = build_navigation_state(root_json)\n'
        '    graph = load_navigation_graph(config.work_dir)\n'
        '    state = resolve_detected_state(graph, state, current_session_page())\n'
        '    return {"root": root_json, "state": state}'
    )
    text = text.replace(old_capture, new_capture, 2)

    text = replace_once(
        text,
        '    after_page = after["state"].get("page_name")\n    if after_page != before_page:',
        '    after_page = after["state"].get("page_name")\n    if not states_represent_same_page(after["state"], before["state"]):',
        "same-page tap comparison",
    )
    text = replace_once(
        text,
        '    if after["state"].get("page_name") != before_page:',
        '    if not states_represent_same_page(after["state"], before["state"]):',
        "same-page gesture comparison",
    )

    old_tap = (
        '    after_capture = capture_state_without_graph_write()\n'
        '    after = state_response_from_capture(after_capture["root"], after_capture["state"], load_navigation_graph(config.work_dir), from_page)\n'
        '    to_page = after["state"]["page_name"]\n'
        '    graph = load_navigation_graph(config.work_dir)\n'
        '    if to_page == from_page:'
    )
    new_tap = (
        '    after_capture = capture_state_without_graph_write()\n'
        '    graph = load_navigation_graph(config.work_dir)\n'
        '    from_state = graph.get("states", {}).get(\n'
        '        from_page,\n'
        '        current.get("active_state") or current.get("state") or {"page_name": from_page},\n'
        '    )\n'
        '    same_page = states_represent_same_page(after_capture["state"], from_state)\n'
        '    if same_page:\n'
        '        after_capture["state"] = resolve_detected_state(\n'
        '            graph, after_capture["state"], from_page\n'
        '        )\n'
        '    else:\n'
        '        after_capture["state"] = contextualize_child_state(\n'
        '            graph, from_page, after_capture["state"]\n'
        '        )\n'
        '    after = state_response_from_capture(\n'
        '        after_capture["root"],\n'
        '        after_capture["state"],\n'
        '        graph,\n'
        '        from_page,\n'
        '    )\n'
        '    to_page = after["state"]["page_name"]\n'
        '    if same_page:'
    )
    text = replace_once(text, old_tap, new_tap, "contextualize recorded child")

    text = replace_once(
        text,
        '                before = capture_ui_tree_state_without_graph_write()\n                last_capture = before\n                detected_page = str(before["state"].get("page_name") or "")\n                if from_page and detected_page != from_page:',
        '                before = capture_ui_tree_state_without_graph_write()\n                before["state"] = resolve_detected_state(\n                    graph, before["state"], from_page\n                )\n                last_capture = before\n                detected_page = str(before["state"].get("page_name") or "")\n                if from_page and not state_matches_graph_page(\n                    graph, before["state"], from_page\n                ):',
        "quick navigation step validation",
    )
    text = replace_once(
        text,
        '        last_capture = capture_ui_tree_state_without_graph_write()\n        if last_capture["state"].get("page_name") != req.page_name:',
        '        last_capture = capture_ui_tree_state_without_graph_write()\n        last_capture["state"] = resolve_detected_state(\n            graph, last_capture["state"], req.page_name\n        )\n        if not state_matches_graph_page(\n            graph, last_capture["state"], req.page_name\n        ):',
        "quick navigation final validation",
    )

    path.write_text(text, encoding="utf-8")


def main() -> None:
    patch_recorder()
    patch_server()
    print("contextual page naming patch applied")


if __name__ == "__main__":
    main()
