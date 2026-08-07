from DFS import build_special_operations, export_dfs_paths, format_dfs_records


def _target(key: str, description: str):
    return {
        "key": key,
        "key_description": description,
        "step_prompt": description,
    }


def test_special_operate_groups_multiple_steps_and_keeps_popup_order():
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

    assert list(special) == ["operate1", "operate2"]
    assert special["operate1"]["kind"] == "special_operate"
    assert special["operate1"]["step1"]["value"] == "first_key"
    assert special["operate1"]["step2"]["value"] == "second_key"
    assert special["operate2"]["kind"] == "popup"
    assert special["operate2"]["popup_type"] == "Dialog"
    assert special["operate2"]["step1"]["value"] == "dialog_key"


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
    assert output[1]["special"] == {}
