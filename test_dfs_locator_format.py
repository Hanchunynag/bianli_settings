import unittest

from DFS import format_dfs_records, format_path_target


class DfsLocatorFormatTest(unittest.TestCase):
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
        }])


if __name__ == "__main__":
    unittest.main()
