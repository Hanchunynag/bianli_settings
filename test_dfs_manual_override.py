import unittest

from DFS import (
    build_page_dfs_preview,
    export_dfs_paths,
    format_dfs_records,
    normalize_dfs_override,
)


class DfsManualOverrideTest(unittest.TestCase):
    def graph(self):
        return {
            "package_name": "com.huawei.hmos.settings",
            "main_page_name": "com.huawei.hmos.settings.MainAbility",
            "traversal_config": {"root_page": "Pages_root"},
            "states": {
                "Pages_root": {"page_name": "Pages_root", "last_title": "设置"},
                "Pages_update": {"page_name": "Pages_update", "last_title": "更新选项"},
            },
            "transitions": [
                {
                    "transition_id": "root_to_update",
                    "from_page": "Pages_root",
                    "to_page": "Pages_update",
                    "steps": [
                        {
                            "operate": "tap",
                            "target": {
                                "key": "about_device",
                                "key_description": "Mate X5",
                                "step_prompt": "Mate X5",
                            },
                        },
                        {
                            "operate": "tap",
                            "target": {
                                "key": "menu_grid",
                                "key_description": "menu_grid",
                                "step_prompt": "menu_grid",
                            },
                        },
                        {
                            "operate": "tap",
                            "target": {
                                "key": "SettingMenu_MenuItem_0",
                                "key_description": "更新选项",
                                "step_prompt": "更新选项",
                            },
                        },
                    ],
                }
            ],
        }

    def test_page_description_is_independent_from_required_path_steps(self):
        graph = self.graph()
        graph["states"]["Pages_update"]["dfs_override"] = {
            "package_name": "com.huawei.hmos.settings",
            "main_page_name": "com.huawei.hmos.settings.MainAbility",
            "page_description": "Mate X5_检查更新_更新选项",
            "path_snapshot": [
                {
                    "type": "key",
                    "value": "about_device",
                    "key_description": "Mate X5",
                    "step_prompt": "Mate X5",
                },
                {
                    "type": "key",
                    "value": "menu_grid",
                    "key_description": "menu_grid",
                    "step_prompt": "menu_grid",
                },
                {
                    "type": "key",
                    "value": "SettingMenu_MenuItem_0",
                    "key_description": "更新选项",
                    "step_prompt": "更新选项",
                },
            ],
        }

        records, _ = export_dfs_paths(graph, "Pages_root")
        output = format_dfs_records(records, graph)
        page = output[1]

        self.assertEqual(page["page_description"], "Mate X5_检查更新_更新选项")
        self.assertEqual(
            [step["value"] for step in page["path_snapshot"]],
            ["about_device", "menu_grid", "SettingMenu_MenuItem_0"],
        )
        self.assertNotIn("menu_grid", page["page_description"])

    def test_preview_returns_automatic_and_resolved_records(self):
        graph = self.graph()
        automatic = build_page_dfs_preview(graph, "Pages_update")
        self.assertFalse(automatic["manual_override"])
        self.assertEqual(
            automatic["record"]["page_description"],
            "Mate X5_menu_grid_更新选项",
        )

        graph["states"]["Pages_update"]["dfs_override"] = {
            **automatic["record"],
            "page_description": "人工页面名称",
        }
        resolved = build_page_dfs_preview(graph, "Pages_update")
        self.assertTrue(resolved["manual_override"])
        self.assertEqual(resolved["record"]["page_description"], "人工页面名称")
        self.assertEqual(
            resolved["automatic_record"]["page_description"],
            "Mate X5_menu_grid_更新选项",
        )

    def test_override_validation_rejects_invalid_locator(self):
        with self.assertRaisesRegex(ValueError, "第 1 步"):
            normalize_dfs_override({
                "package_name": "pkg",
                "main_page_name": "MainAbility",
                "page_description": "页面",
                "path_snapshot": [{"type": "key", "value": ""}],
            })


if __name__ == "__main__":
    unittest.main()
