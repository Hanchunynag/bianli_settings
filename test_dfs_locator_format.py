import tempfile
import unittest
from pathlib import Path

from DFS import (
    format_dfs_records,
    format_path_target,
    resolve_settings_profile_work_dir,
)


class DfsLocatorFormatTest(unittest.TestCase):
    def test_explicit_settings_profile_selects_matching_dfs_work_dir(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_work_dir = Path(temp_dir)

            self.assertEqual(
                resolve_settings_profile_work_dir(
                    base_work_dir,
                    "profile_0123456789ab",
                ),
                (
                    base_work_dir
                    / "config_profiles"
                    / "profile_0123456789ab"
                ),
            )
            self.assertEqual(
                resolve_settings_profile_work_dir(base_work_dir),
                base_work_dir,
            )

    def test_key_locator_only_keeps_compact_fields(self):
        self.assertEqual(
            format_path_target({
                "key": "wifi_entry",
                "text": "WLAN",
                "component_type": "Row",
                "key_description": "WLAN",
                "step_prompt": "WLAN",
                "expect": "new_page",
            }),
            {
                "type": "key",
                "value": "wifi_entry",
                "key_description": "WLAN",
                "step_prompt": "WLAN",
            },
        )

    def test_text_locator_only_keeps_compact_fields(self):
        self.assertEqual(
            format_path_target({
                "text": "安装证书",
                "component_type": "MenuItem",
                "key_description": "安装证书",
                "step_prompt": "安装证书",
                "expect": "new_page",
            }),
            {
                "type": "text",
                "value": "安装证书",
                "key_description": "安装证书",
                "step_prompt": "安装证书",
            },
        )

    def test_record_output_removes_all_non_contract_fields(self):
        records = [{
            "package_name": "com.huawei.hmos.settings",
            "main_page_name": "com.huawei.hmos.settings.MainAbility",
            "page_description": "应用和元服务_系统应用",
            "path_snapshot": [{
                "key": "Setting.Application.ApplicationTab.ApplicationSystemGroup",
                "text": "系统应用",
                "component_type": "Row",
                "key_description": "系统应用",
                "step_prompt": "系统应用",
                "expect": "new_page",
            }],
            "special_operate": [{"operation_id": "ignored"}],
            "page_name": "Pages_ignored",
        }]

        self.assertEqual(format_dfs_records(records, {}), [{
            "package_name": "com.huawei.hmos.settings",
            "main_page_name": "com.huawei.hmos.settings.MainAbility",
            "page_description": "应用和元服务_系统应用",
            "path_snapshot": [{
                "type": "key",
                "value": "Setting.Application.ApplicationTab.ApplicationSystemGroup",
                "key_description": "系统应用",
                "step_prompt": "系统应用",
            }],
            "special": {},
        }])

    def test_root_page_is_emitted_first_with_empty_path(self):
        graph = {
            "package_name": "com.huawei.hmos.settings",
            "main_page_name": "com.huawei.hmos.settings.MainAbility",
            "traversal_config": {"root_page": "Pages_root"},
            "states": {
                "Pages_root": {
                    "last_title": "应用首页",
                    "page_description": "不应优先使用该值",
                },
            },
        }
        records = [{
            "package_name": "com.huawei.hmos.settings",
            "main_page_name": "com.huawei.hmos.settings.MainAbility",
            "page_description": "应用和元服务",
            "path_snapshot": [{
                "text": "应用和元服务",
                "key_description": "应用和元服务",
                "step_prompt": "应用和元服务",
            }],
        }]

        output = format_dfs_records(records, graph)

        self.assertEqual(output[0], {
            "package_name": "com.huawei.hmos.settings",
            "main_page_name": "com.huawei.hmos.settings.MainAbility",
            "page_description": "应用首页",
            "path_snapshot": [],
            "special": {},
        })
        self.assertEqual(output[1]["page_description"], "应用和元服务")


if __name__ == "__main__":
    unittest.main()
