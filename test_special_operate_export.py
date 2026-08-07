import json
import unittest
from pathlib import Path

from special_opearte_contract import install_dfs_contract

install_dfs_contract()

from DFS import build_special_operations, format_dfs_records


class SpecialOperateExportTest(unittest.TestCase):
    def test_multiaction_special_and_popup_keep_page_order(self):
        state = {
            "special_operations": [
                {
                    "operation_id": "operation1",
                    "created_at": "2026-08-07T10:00:01",
                    "operate": "tap",
                    "effect": "special_capture::abc",
                    "operation_kind": "special_operate",
                    "target": {
                        "key": "first",
                        "key_description": "第一项",
                        "step_prompt": "第一项",
                    },
                },
                {
                    "operation_id": "operation2",
                    "created_at": "2026-08-07T10:00:02",
                    "operate": "tap",
                    "effect": "special_capture::abc",
                    "operation_kind": "special_operate",
                    "target": {
                        "text": "第二个入口",
                        "key_description": "第二项",
                        "step_prompt": "第二项",
                    },
                },
            ],
            "page_operations": [
                {
                    "operation_id": "operation3",
                    "created_at": "2026-08-07T10:00:03",
                    "operate": "tap",
                    "effect": "open_popup",
                    "popup_type": "Dialog",
                    "target": {
                        "key": "dialog_entry",
                        "key_description": "弹窗入口",
                        "step_prompt": "弹窗入口",
                    },
                },
            ],
        }
        special = build_special_operations(state)
        self.assertEqual(list(special), ["operation1", "operation2"])
        self.assertEqual(
            [item["value"] for item in special["operation1"]],
            ["first", "第二个入口"],
        )
        self.assertEqual(special["operation1"][0]["type"], "key")
        self.assertEqual(special["operation1"][1]["type"], "text")
        self.assertEqual(
            special["operation2"],
            [{
                "type": "key",
                "value": "dialog_entry",
                "key_description": "弹窗入口",
                "step_prompt": "弹窗入口",
            }],
        )

    def test_manual_special_uses_operation_arrays(self):
        state = {
            "special_operations": [{
                "created_at": "2026-08-07T10:00:01",
                "effect": "special_capture::abc",
                "operation_kind": "special_operate",
                "target": {"key": "auto"},
            }],
            "special_manual": {
                "operation1": [
                    {
                        "type": "text",
                        "value": "人工维护一",
                        "key_description": "人工维护一",
                        "step_prompt": "人工维护一",
                    },
                    {
                        "type": "key",
                        "value": "manual_second",
                    },
                ],
            },
        }
        special = build_special_operations(state)
        self.assertEqual(
            [item["value"] for item in special["operation1"]],
            ["人工维护一", "manual_second"],
        )
        serialized = json.dumps(special, ensure_ascii=False)
        self.assertNotIn('"step1"', serialized)
        self.assertNotIn('"step2"', serialized)
        self.assertNotIn('"kind"', serialized)
        self.assertNotIn('"operate"', serialized)

    def test_manual_page_export_uses_special_opearte_field(self):
        graph = {
            "package_name": "pkg",
            "main_page_name": "Main",
            "traversal_config": {"root_page": "Pages_root"},
            "states": {
                "Pages_root": {"page_name": "Pages_root", "last_title": "设置"},
                "Pages_manual": {
                    "page_name": "Pages_manual",
                    "manual_page": True,
                    "dfs_manual": {
                        "package_name": "pkg",
                        "main_page_name": "Main",
                        "page_description": "人工页面",
                        "path_snapshot": [{
                            "type": "text",
                            "value": "人工入口",
                            "key_description": "人工入口",
                            "step_prompt": "人工入口",
                        }],
                    },
                },
            },
            "transitions": [],
        }
        output = format_dfs_records([], graph)
        self.assertEqual(len(output), 2)
        self.assertEqual(output[1]["page_description"], "人工页面")
        self.assertEqual(output[1]["special_opearte"], {})
        self.assertNotIn("special", output[1])

    def test_frontend_maintains_operation_arrays_instead_of_numbered_step_keys(self):
        nav = Path("static/nav.js").read_text(encoding="utf-8")
        api_js = Path("static/nav/api.js").read_text(encoding="utf-8")
        editor = Path("static/nav/special-array-maintenance.js").read_text(encoding="utf-8")
        template = Path("templates/nav.html").read_text(encoding="utf-8")

        self.assertIn("specialCapture", nav)
        self.assertIn("special_tap", nav)
        self.assertIn("withoutSpecialStepMarker", api_js)
        self.assertIn("special_opearte.operation", editor)
        self.assertIn("数组第", editor)
        self.assertIn("追加一个数组项", editor)
        self.assertNotIn("data-special-array-field=\"step1\"", editor)
        self.assertNotIn("data-special-array-field=\"step2\"", editor)
        self.assertIn("special-array-maintenance.js", template)


if __name__ == "__main__":
    unittest.main()
