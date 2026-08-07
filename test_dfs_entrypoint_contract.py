import json
import unittest

import DFS


class DfsEntrypointContractTest(unittest.TestCase):
    def test_public_dfs_module_uses_special_opearte_arrays_without_bootstrap(self):
        state = {
            "special_operations": [
                {
                    "created_at": "2026-08-07T10:00:01",
                    "effect": "special_capture::session::step1",
                    "operation_kind": "special_operate",
                    "target": {"key": "first"},
                },
                {
                    "created_at": "2026-08-07T10:00:02",
                    "effect": "special_capture::session::step2",
                    "operation_kind": "special_operate",
                    "target": {"text": "第二项"},
                },
            ],
        }
        special = DFS.build_special_operations(state)
        self.assertEqual(
            special,
            {
                "operation1": [
                    {
                        "type": "key",
                        "value": "first",
                        "key_description": "first",
                        "step_prompt": "first",
                    },
                    {
                        "type": "text",
                        "value": "第二项",
                        "key_description": "第二项",
                        "step_prompt": "第二项",
                    },
                ],
            },
        )
        formatted = DFS.format_dfs_record({
            "package_name": "pkg",
            "main_page_name": "Main",
            "page_description": "测试",
            "path_snapshot": [],
            "special": special,
        })
        self.assertEqual(formatted["special_opearte"], special)
        self.assertNotIn("special", formatted)
        serialized = json.dumps(formatted, ensure_ascii=False)
        self.assertNotIn('"step1"', serialized)
        self.assertNotIn('"step2"', serialized)
        self.assertNotIn('"kind"', serialized)
        self.assertNotIn('"operate"', serialized)

    def test_profile_work_dir_resolver_remains_public(self):
        self.assertTrue(callable(DFS.resolve_settings_profile_work_dir))


if __name__ == "__main__":
    unittest.main()
