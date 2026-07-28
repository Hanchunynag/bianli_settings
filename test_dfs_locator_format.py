import unittest

from DFS import format_dfs_records, format_path_target


class DfsLocatorFormatTest(unittest.TestCase):
    def test_key_locator_has_type_value_and_keeps_readable_text(self):
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
                "key": "wifi_entry",
                "component_type": "Row",
                "text": "WLAN",
                "key_description": "WLAN",
                "step_prompt": "WLAN",
                "expect": "new_page",
            },
        )

    def test_text_locator_has_type_value_without_empty_key(self):
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
                "component_type": "MenuItem",
                "text": "安装证书",
                "key_description": "安装证书",
                "step_prompt": "安装证书",
                "expect": "new_page",
            },
        )

    def test_component_type_can_be_recovered_from_recorded_candidates(self):
        graph = {
            "states": {
                "Pages_root": {
                    "merged_candidates": [{
                        "key": "wifi_entry",
                        "text": "WLAN",
                        "component_type": "Row",
                    }],
                },
            },
        }
        records = [{
            "page_description": "WLAN",
            "path_snapshot": [{"key": "wifi_entry", "text": "WLAN"}],
            "special_operate": [],
        }]

        formatted = format_dfs_records(records, graph)

        self.assertEqual(formatted[0]["path_snapshot"][0], {
            "type": "key",
            "value": "wifi_entry",
            "key": "wifi_entry",
            "component_type": "Row",
            "text": "WLAN",
        })


if __name__ == "__main__":
    unittest.main()
