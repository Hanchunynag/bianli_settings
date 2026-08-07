import tempfile
import unittest
from pathlib import Path

from settings_ui_manual_recorder import (
    NavigationGraphRepository,
    save_current_path_session,
    save_json,
)
from settings_profiles import (
    DEFAULT_SETTINGS_PROFILE_ID,
    SettingsProfileManager,
)
from web_nav_server import REQUEST_SETTINGS_PROFILE_ID, ServerConfig


class SettingsProfileManagerTest(unittest.TestCase):
    def _seed_project(self, work_dir: Path) -> None:
        graph = {
            "package_name": "com.huawei.hmos.settings",
            "main_page_name": "com.huawei.settings.MainAbility",
            "traversal_config": {"root_page": "Pages_root"},
            "states": {
                "Pages_root": {
                    "page_name": "Pages_root",
                    "page_description": "设置",
                },
                "Pages_WLAN": {
                    "page_name": "Pages_WLAN",
                    "page_description": "WLAN",
                },
            },
            "transitions": [
                {
                    "transition_id": "root_to_wlan",
                    "from_page": "Pages_root",
                    "to_page": "Pages_WLAN",
                    "operate": "tap",
                    "target": {"type": "text", "value": "WLAN"},
                }
            ],
        }
        NavigationGraphRepository(work_dir).save(graph)
        save_json(
            [{"page_description": "设置", "path_snapshot": []}],
            work_dir / "outputs" / "navigation" / "settings_navigation_paths.json",
            "seed DFS",
        )
        save_current_path_session(work_dir, "Pages_root")

    def test_derived_profile_inherits_then_writes_independently(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            self._seed_project(base)
            manager = SettingsProfileManager(base)
            child = manager.create(
                name="Mate X5 · HarmonyOS 5.1",
                settings_version="HarmonyOS 5.1",
                device_model="Mate X5",
                parent_profile_id=DEFAULT_SETTINGS_PROFILE_ID,
            )

            child_dir = manager.profile_work_dir(child["profile_id"])
            child_graph = NavigationGraphRepository(child_dir).load()
            self.assertEqual(child_graph["settings_profile"]["profile_id"], child["profile_id"])
            self.assertTrue(
                (child_dir / "outputs" / "navigation" / "settings_navigation_paths.json").exists()
            )

            child_graph["states"]["Pages_WLAN"]["page_description"] = "WLAN（Mate X5）"
            NavigationGraphRepository(child_dir).save(child_graph)
            base_graph = NavigationGraphRepository(base).load()
            self.assertEqual(base_graph["states"]["Pages_WLAN"]["page_description"], "WLAN")

    def test_server_config_resolves_work_dir_from_request_context(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            self._seed_project(base)
            server_config = ServerConfig(base)
            child = server_config.settings_profiles.create(
                name="Pura 80 · HarmonyOS 6",
                settings_version="HarmonyOS 6",
                device_model="Pura 80",
                parent_profile_id=DEFAULT_SETTINGS_PROFILE_ID,
            )

            self.assertEqual(server_config.work_dir, base)
            token = REQUEST_SETTINGS_PROFILE_ID.set(child["profile_id"])
            try:
                self.assertEqual(
                    server_config.work_dir,
                    server_config.settings_profiles.profile_work_dir(child["profile_id"]),
                )
                self.assertEqual(
                    server_config.graphs.load()["settings_profile"]["profile_id"],
                    child["profile_id"],
                )
            finally:
                REQUEST_SETTINGS_PROFILE_ID.reset(token)
            self.assertEqual(server_config.work_dir, base)

    def test_duplicate_version_and_device_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            self._seed_project(base)
            manager = SettingsProfileManager(base)
            kwargs = dict(
                name="Mate X5",
                settings_version="HarmonyOS 5.1",
                device_model="Mate X5",
                parent_profile_id=DEFAULT_SETTINGS_PROFILE_ID,
            )
            manager.create(**kwargs)
            with self.assertRaisesRegex(ValueError, "已经存在"):
                manager.create(**kwargs)


class FrontendProfileScopeTest(unittest.TestCase):
    def test_frontend_sends_profile_id_and_exposes_profile_manager(self):
        project = Path(__file__).resolve().parent
        api_js = (project / "static" / "nav" / "api.js").read_text(encoding="utf-8")
        nav_js = (project / "static" / "nav.js").read_text(encoding="utf-8")
        template = (project / "templates" / "nav.html").read_text(encoding="utf-8")
        self.assertIn("url.searchParams.set", api_js)
        self.assertIn("profile_id", api_js)
        self.assertIn("settingsProfileId", nav_js)
        self.assertIn('id="settingsProfileSelect"', template)
        self.assertIn('id="settingsProfileDialog"', template)


if __name__ == "__main__":
    unittest.main()
