import json
import unittest

from special_opearte_contract import (
    build_special_opearte,
    install_dfs_contract,
    normalize_special_opearte,
)


def _target(*, key="", text="", value="", raw_type="", description=""):
    target = {}
    if key:
        target["key"] = key
    if text:
        target["text"] = text
    if value:
        target["value"] = value
    if raw_type:
        target["type"] = raw_type
    if description:
        target["key_description"] = description
        target["step_prompt"] = description
    return target


class SpecialOperateArrayContractTest(unittest.TestCase):
    def test_multi_action_session_is_one_ordered_array(self):
        state = {
            "special_operations": [
                {
                    "created_at": "2026-08-07T01:00:00Z",
                    "operation_kind": "special_operate",
                    "effect": "special_capture::session-a",
                    "target": _target(key="first_key", description="第一项"),
                },
                {
                    "created_at": "2026-08-07T01:00:01Z",
                    "operation_kind": "special_operate",
                    "effect": "special_capture::session-a",
                    "target": _target(text="第二个入口", description="第二项"),
                },
            ],
        }
        self.assertEqual(
            build_special_opearte(state),
            {
                "operation1": [
                    {
                        "type": "key",
                        "value": "first_key",
                        "key_description": "第一项",
                        "step_prompt": "第一项",
                    },
                    {
                        "type": "text",
                        "value": "第二个入口",
                        "key_description": "第二项",
                        "step_prompt": "第二项",
                    },
                ],
            },
        )

    def test_legacy_step_object_migrates_to_array_without_wrapper_fields(self):
        legacy = {
            "operate1": {
                "kind": "special_operate",
                "step1": {
                    "operate": "tap",
                    "type": "key",
                    "value": "a",
                },
                "step2": {
                    "operate": "tap",
                    "type": "text",
                    "value": "B",
                },
            },
        }
        normalized = normalize_special_opearte(legacy)
        self.assertEqual(
            normalized,
            {
                "operation1": [
                    {"type": "key", "value": "a"},
                    {"type": "text", "value": "B"},
                ],
            },
        )
        serialized = json.dumps(normalized, ensure_ascii=False)
        self.assertNotIn('"step1"', serialized)
        self.assertNotIn('"step2"', serialized)
        self.assertNotIn('"kind"', serialized)
        self.assertNotIn('"operate"', serialized)

    def test_popup_metadata_never_becomes_locator_type(self):
        state = {
            "page_operations": [
                {
                    "created_at": "2026-08-07T01:00:00Z",
                    "effect": "open_popup",
                    "popup_type": "Dialog",
                    "target": {
                        "type": "Dialog",
                        "value": "确认",
                        "key_description": "打开确认弹窗",
                    },
                },
            ],
        }
        item = build_special_opearte(state)["operation1"][0]
        self.assertEqual(item["type"], "text")
        self.assertEqual(item["value"], "确认")
        self.assertNotEqual(item["type"], "Dialog")

    def test_dfs_formatter_exports_only_special_opearte_contract(self):
        install_dfs_contract()
        import DFS

        formatted = DFS.format_dfs_record({
            "package_name": "pkg",
            "main_page_name": "Main",
            "page_description": "设置_测试页",
            "path_snapshot": [],
            "special": {
                "operation1": [
                    {"type": "key", "value": "x"},
                    {"type": "text", "value": "Y"},
                ],
            },
        })
        self.assertEqual(
            formatted["special_opearte"],
            {
                "operation1": [
                    {"type": "key", "value": "x"},
                    {"type": "text", "value": "Y"},
                ],
            },
        )
        self.assertNotIn("special", formatted)
        serialized = json.dumps(formatted, ensure_ascii=False)
        self.assertNotIn('"step1"', serialized)
        self.assertNotIn('"kind"', serialized)
        self.assertNotIn('"operate"', serialized)


if __name__ == "__main__":
    unittest.main()
