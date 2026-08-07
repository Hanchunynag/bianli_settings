from DFS import build_special_operations, export_dfs_paths, format_dfs_records


def _target(key: str, description: str):
    return {
        "key": key,
        "key_description": description,
        "step_prompt": description,
    }


def test_special_opearte_uses_operation_arrays_and_keeps_popup_order():
    state = {
        "page_operations": [
            {
                "operation_id": "operation1",
                "operate": "tap",
                "effect": "special_capture::sessionA::step1",
                "operation_kind": "special_operate",
                "target": _target("first_key", "第一步"),
            },
            {
                "operation_id": "operation2",
                "operate": "tap",
                "effect": "special_capture::sessionA::step2",
                "operation_kind": "special_operate",
                "target": _target("second_key", "第二步"),
            },
            {
                "operation_id": "ordinary_same_page",
                "operate": "tap",
                "effect": "content_changed",
                "target": _target("ordinary_key", "普通同页操作"),
            },
            {
                "operation_id": "operation3",
                "operate": "tap",
                "effect": "open_popup",
                "popup_type": "Dialog",
                "target": {
                    **_target("dialog_key", "打开弹窗"),
                    "type": "Dialog",
                },
            },
        ]
    }

    special = build_special_operations(state)

    assert list(special) == ["operation1", "operation2"]
    assert len(special["operation1"]) == 2
    assert special["operation1"][0]["type"] == "key"
    assert special["operation1"][0]["value"] == "first_key"
    assert special["operation1"][1]["value"] == "second_key"
    assert special["operation2"] == [
        {
            "type": "key",
            "value": "dialog_key",
            "key_description": "打开弹窗",
            "step_prompt": "打开弹窗",
        }
    ]


def test_popup_metadata_never_replaces_locator_type():
    state = {
        "page_operations": [
            {
                "operation_id": "operation_popup",
                "effect": "open_popup",
                "popup_type": "SheetWrapper",
                "target": {
                    "text": "更多选项",
                    "type": "SheetWrapper",
                    "key_description": "打开更多选项弹窗",
                    "step_prompt": "打开更多选项弹窗",
                },
            }
        ]
    }

    special = build_special_operations(state)
    assert special["operation1"][0]["type"] == "text"
    assert special["operation1"][0]["value"] == "更多选项"


def test_special_opearte_order_follows_recorded_group_order():
    state = {
        "page_operations": [
            {
                "operation_id": "operation9",
                "effect": "special_capture::later::step1",
                "operation_kind": "special_operate",
                "target": _target("later_key", "后执行"),
            },
            {
                "operation_id": "operation10",
                "effect": "special_capture::later::step2",
                "operation_kind": "special_operate",
                "target": _target("later_key_2", "后执行第二步"),
            },
            {
                "operation_id": "operation11",
                "effect": "special_capture::next::step1",
                "operation_kind": "special_operate",
                "target": _target("next_key", "下一组"),
            },
        ]
    }

    special = build_special_operations(state)

    assert list(special) == ["operation1", "operation2"]
    assert [step["value"] for step in special["operation1"]] == ["later_key", "later_key_2"]
    assert [step["value"] for step in special["operation2"]] == ["next_key"]


def test_manual_page_without_transition_is_exported_after_manual_dfs_is_saved():
    graph = {
        "package_name": "com.example.settings",
        "main_page_name": "MainAbility",
        "traversal_config": {"root_page": "Pages_root"},
        "states": {
            "Pages_root": {
                "page_name": "Pages_root",
                "page_description": "设置",
            },
            "Pages_manual": {
                "page_name": "Pages_manual",
                "page_description": "人工页面",
                "manual_page": True,
                "dfs_manual": {
                    "package_name": "com.example.settings",
                    "main_page_name": "MainAbility",
                    "page_description": "设置_人工页面",
                    "path_snapshot": [
                        {
                            "type": "text",
                            "value": "人工入口",
                            "key_description": "人工入口",
                            "step_prompt": "人工入口",
                        }
                    ],
                },
                "page_operations": [],
            },
        },
        "transitions": [],
    }

    records, unreachable = export_dfs_paths(graph, "Pages_root")
    output = format_dfs_records(records, graph)

    assert "Pages_manual" not in unreachable
    assert len(output) == 2
    assert output[1]["page_description"] == "设置_人工页面"
    assert output[1]["path_snapshot"][0]["value"] == "人工入口"
    assert output[1]["special_opearte"] == {}
