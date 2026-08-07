import unittest
from pathlib import Path

from DFS import build_special_operations, format_dfs_records


class SpecialOperateExportTest(unittest.TestCase):
    def test_multistep_special_and_popup_keep_page_order(self):
        state = {
            "special_operations": [
                {
                    "operation_id": "operation1",
                    "created_at": "2026-08-07T10:00:01",
                    "operate": "tap",
                    "effect": "special_capture::abc::step1",
                    "operation_kind": "special_operate",
                    "target": {"key": "first", "key_description": "第一步", "step_prompt": "第一步"},
                },
                {
                    "operation_id": "operation2",
                    "created_at": "2026-08-07T10:00:02",
                    "operate": "tap",
                    "effect": "special_capture::abc::step2",
                    "operation_kind": "special_operate",
                    "target": {"text": "第二步", "key_description": "第二步", "step_prompt": "第二步"},
                },
            ],
            "page_operations": [
                {
                    "operation_id": "operation3",
                    "created_at": "2026-08-07T10:00:03",
                    "operate": "tap",
                    "effect": "open_popup",
                    "popup_type": "Dialog",
                    "target": {"key": "dialog_entry", "key_description": "弹窗入口", "step_prompt": "弹窗入口"},
                },
            ],
        }
        special = build_special_operations(state)
        self.assertEqual(list(special), ["operate1", "operate2"])
        self.assertEqual(special["operate1"]["kind"], "special_operate")
        self.assertEqual(special["operate1"]["step1"]["value"], "first")
        self.assertEqual(special["operate1"]["step2"]["value"], "第二步")
        self.assertEqual(special["operate2"]["kind"], "popup")
        self.assertEqual(special["operate2"]["popup_type"], "Dialog")

    def test_manual_special_overrides_recorded_special(self):
        state = {
            "special_operations": [{
                "created_at": "2026-08-07T10:00:01",
                "operate": "tap",
                "effect": "special_capture::abc::step1",
                "operation_kind": "special_operate",
                "target": {"key": "auto"},
            }],
            "special_manual": {
                "operate1": {
                    "kind": "special_operate",
                    "step1": {
                        "operate": "tap",
                        "type": "text",
                        "value": "人工维护",
                        "key_description": "人工维护",
                        "step_prompt": "人工维护",
                    },
                },
            },
        }
        self.assertEqual(build_special_operations(state)["operate1"]["step1"]["value"], "人工维护")

    def test_manual_page_without_transition_is_exported_after_dfs_maintenance(self):
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
        self.assertEqual(output[1]["special"], {})

    def test_frontend_has_persistent_special_capture_controls_and_matching_editor(self):
        nav = Path("static/nav.js").read_text(encoding="utf-8")
        render = Path("static/nav/render.js").read_text(encoding="utf-8")
        template = Path("templates/nav.html").read_text(encoding="utf-8")
        self.assertIn("specialCapture", nav)
        self.assertIn("cancel_special_capture", nav)
        self.assertIn("special_tap", nav)
        self.assertIn('id="finishSpecialOperateBtn"', template)
        self.assertIn('id="cancelSpecialOperateBtn"', template)
        self.assertIn('id="specialManualForm"', render)
        self.assertIn("maintain_special_dfs", render)


if __name__ == "__main__":
    unittest.main()
