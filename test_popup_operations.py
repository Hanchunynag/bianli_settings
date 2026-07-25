import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import web_nav_server
from create_demo_settings import node
from settings_ui_manual_recorder import (
    annotate,
    build_navigation_state,
    navigation_graph_path,
    normalize_semantic_target_types,
)


def popup_tree(popup_type: str = ""):
    children = [
        node("NavDestination", bounds="[0,0][1080,2400]", children=[
            node("Text", text="设置", bounds="[80,40][400,160]"),
            node(
                "Button",
                key="open_popup",
                text="打开弹窗",
                clickable=True,
                bounds="[80,260][1000,420]",
            ),
        ]),
    ]
    if popup_type:
        children.append(node(popup_type, bounds="[100,600][980,1500]", children=[
            node(
                "Button",
                key="confirm",
                text="确定",
                clickable=True,
                bounds="[600,1300][900,1450]",
            ),
        ]))
    root = node("Root", bounds="[0,0][1080,2400]", children=children)
    annotate(root)
    return root


class PopupOperationTest(unittest.TestCase):
    def test_popup_type_is_required_and_limited_to_the_collection(self):
        self.assertEqual(web_nav_server.POPUP_TYPES, ("SheetWrapper", "Dialog", "MenuWrapper"))
        for popup_type in web_nav_server.POPUP_TYPES:
            self.assertEqual(web_nav_server.normalize_popup_type(popup_type), popup_type)
        with self.assertRaisesRegex(ValueError, "可选类型"):
            web_nav_server.normalize_popup_type("")
        with self.assertRaisesRegex(ValueError, "可选类型"):
            web_nav_server.normalize_popup_type("Unknown")

    def test_legacy_locator_type_is_migrated_to_key_without_navigation_type(self):
        legacy = {
            "target": {
                "type": "key",
                "value": "open_sheet",
                "component_type": "Button",
            },
        }
        normalize_semantic_target_types(legacy["target"])
        self.assertEqual(legacy["target"]["key"], "open_sheet")
        self.assertNotIn("type", legacy["target"])
        self.assertNotIn("component_type", legacy["target"])
        self.assertNotIn("value", legacy["target"])

    def test_selected_popup_type_is_saved_as_operation(self):
        before_root = popup_tree()
        after_root = popup_tree("SheetWrapper")
        before = {"root": before_root, "state": build_navigation_state(before_root)}
        after = {"root": after_root, "state": build_navigation_state(after_root)}
        page_name = before["state"]["page_name"]
        graph = {"states": {page_name: before["state"]}, "transitions": []}
        original_config = web_nav_server.config
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                work_dir = Path(temp_dir)
                graph_path = navigation_graph_path(work_dir)
                graph_path.parent.mkdir(parents=True)
                graph_path.write_text(json.dumps(graph, ensure_ascii=False), encoding="utf-8")
                web_nav_server.config = web_nav_server.ServerConfig(work_dir=work_dir)

                with (
                    patch.object(
                        web_nav_server,
                        "capture_state_without_graph_write",
                        side_effect=[before, after],
                    ),
                    patch.object(web_nav_server, "execute_device_input"),
                    patch.object(web_nav_server.time, "sleep"),
                ):
                    web_nav_server.record_page_operation(
                        300,
                        330,
                        mode="popup",
                        popup_type="SheetWrapper",
                    )

                saved = json.loads(graph_path.read_text(encoding="utf-8"))
                operation = saved["states"][page_name]["page_operations"][0]
                self.assertEqual(operation["operation_id"], "operation1")
                self.assertEqual(operation["operate"], "tap")
                self.assertEqual(operation["popup_type"], "SheetWrapper")
                self.assertEqual(operation["target"]["type"], "SheetWrapper")
                self.assertEqual(operation["target"]["key"], "open_popup")
                self.assertNotIn("component_type", operation["target"])
                self.assertNotIn("value", operation["target"])
                self.assertEqual(operation["effect"], "open_popup")
        finally:
            web_nav_server.config = original_config


if __name__ == "__main__":
    unittest.main()
