import json
import tempfile
import unittest
from pathlib import Path

from settings_profiles import DEFAULT_SETTINGS_PROFILE_ID, SettingsProfileManager
from settings_ui_manual_recorder import save_navigation_graph


class SettingsProfileManagerTest(unittest.TestCase):
    def test_create_profile_inherits_graph_without_mutating_parent(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = Path(tmp)
            save_navigation_graph({
                "package_name": "pkg",
                "main_page_name": "Main",
                "traversal_config": {"root_page": "Pages_root"},
                "states": {"Pages_root": {"page_name": "Pages_root"}},
                "transitions": [],
            }, work_dir)
            manager = SettingsProfileManager(work_dir)
            profile = manager.create(
                name="V2 Phone",
                settings_version="2.0",
                device_model="Phone",
                parent_profile_id=DEFAULT_SETTINGS_PROFILE_ID,
            )
            child_graph = json.loads((manager.profile_work_dir(profile["profile_id"]) / "outputs/navigation/settings_navigation_graph.json").read_text(encoding="utf-8"))
            self.assertEqual(profile["parent_profile_id"], DEFAULT_SETTINGS_PROFILE_ID)
            self.assertEqual(child_graph["settings_profile"]["profile_id"], profile["profile_id"])
            parent_graph = json.loads((work_dir / "outputs/navigation/settings_navigation_graph.json").read_text(encoding="utf-8"))
            self.assertNotIn("settings_profile", parent_graph)

    def test_profile_work_dirs_are_request_scoped(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = SettingsProfileManager(Path(tmp))
            self.assertEqual(manager.profile_work_dir("default"), Path(tmp))
            with self.assertRaises(ValueError):
                manager.profile_work_dir("../bad")

    def test_server_config_scopes_runtime_output_to_profile(self):
        import web_nav_server
        from settings_ui_manual_recorder import NavigationGraphRepository

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            NavigationGraphRepository(base).save({
                "package_name": "pkg",
                "main_page_name": "Main",
                "traversal_config": {"root_page": "Pages_root"},
                "states": {"Pages_root": {"page_name": "Pages_root"}},
                "transitions": [],
            })
            manager = SettingsProfileManager(base)
            profile = manager.create(
                name="V2 Phone",
                settings_version="2.0",
                device_model="Phone",
                parent_profile_id=DEFAULT_SETTINGS_PROFILE_ID,
            )
            config = web_nav_server.ServerConfig(
                base,
                output_dir=base / "legacy-latest",
            )
            token = web_nav_server.REQUEST_SETTINGS_PROFILE_ID.set(
                profile["profile_id"]
            )
            try:
                self.assertEqual(
                    config.output_dir,
                    manager.profile_work_dir(profile["profile_id"])
                    / "outputs" / "latest",
                )
            finally:
                web_nav_server.REQUEST_SETTINGS_PROFILE_ID.reset(token)



if __name__ == "__main__":
    unittest.main()
