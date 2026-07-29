import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from test_ui_fixtures import node, themes_tree
from DFS import DfsPathExporter
from settings_ui_manual_recorder import (
    DeleteActionRequest,
    GraphMaintenance,
    NavigationGraph,
    NavigationGraphRepository,
    annotate,
    build_navigation_state,
    build_semantic_target_from_node,
    build_page_directory,
    contextualize_child_state,
    extract_navigation_candidates,
    hit_test_full_ui_tree,
    is_stable_key_for_navigation,
    navigation_graph_path,
    normalize_semantic_target_types,
    reorder_child_transitions,
    resolve_detected_state,
)

try:
    import fastapi  # noqa: F401
    from fastapi.testclient import TestClient
except ImportError:
    web_nav_server = None
else:
    import web_nav_server


def state(title, candidates=None):
    return {
        "last_title": title,
        "merged_candidates": candidates or [],
        "page_operations": [],
        "continued_captures": [],
    }


def transition(tid, source, target):
    return {"transition_id": tid, "from_page": source, "to_page": target}


def hdc_page(title, entry_text, entry_key=""):
    return node("Root", bounds="[0,0][1080,2400]", children=[
        node("NavDestination", bounds="[0,0][1080,2400]", children=[
            node("HdcTitleBar", bounds="[0,0][1080,180]", children=[
                node("Text", text=title, bounds="[100,50][500,150]"),
            ]),
            node("ListItem", key=entry_key, text=entry_text, clickable=True, bounds="[40,240][1040,400]", children=[
                node("Text", text=entry_text, bounds="[80,260][600,380]"),
            ]),
        ]),
    ])


class GraphMaintenanceTest(unittest.TestCase):
    def test_navigation_graph_rename_updates_every_structural_reference(self):
        graph = {
            "main_page_name": "Pages_old",
            "traversal_config": {"root_page": "Pages_old"},
            "states": {
                "Pages_old": {
                    **state("旧页面"),
                    "page_name": "Pages_old",
                },
                "Pages_child": {
                    **state("子页面"),
                    "page_name": "Pages_child",
                    "parent_page": "Pages_old",
                    "context_key": "Pages_old::Pages_child::text::入口",
                },
                "Pages_view": {
                    **state("局部视图"),
                    "page_name": "Pages_view",
                    "base_page": "Pages_old",
                },
            },
            "transitions": [{
                **transition("old-child", "Pages_old", "Pages_child"),
                "base_page": "Pages_old",
            }],
        }

        renamed = NavigationGraph(graph).rename_page(
            "Pages_old", "Pages_new", new_title="新页面",
        )

        self.assertIs(renamed, graph["states"]["Pages_new"])
        self.assertNotIn("Pages_old", graph["states"])
        self.assertEqual(renamed["page_name"], "Pages_new")
        self.assertEqual(renamed["last_title"], "新页面")
        self.assertEqual(graph["states"]["Pages_child"]["parent_page"], "Pages_new")
        self.assertEqual(
            graph["states"]["Pages_child"]["context_key"],
            "Pages_new::Pages_child::text::入口",
        )
        self.assertEqual(graph["states"]["Pages_view"]["base_page"], "Pages_new")
        self.assertEqual(graph["transitions"][0]["from_page"], "Pages_new")
        self.assertEqual(graph["transitions"][0]["base_page"], "Pages_new")
        self.assertEqual(graph["traversal_config"]["root_page"], "Pages_new")
        self.assertEqual(graph["main_page_name"], "Pages_new")

    def test_dfs_ignores_dangling_edges_and_tolerates_bad_priority(self):
        graph = {
            "package_name": "pkg",
            "main_page_name": "main",
            "states": {
                "Pages_root": state("设置"),
                "Pages_valid": state("有效"),
            },
            "transitions": [
                {
                    **transition("dangling", "Pages_root", "Pages_missing"),
                    "priority": "not-a-number",
                    "target": {"text": "不存在"},
                },
                {
                    **transition("valid", "Pages_root", "Pages_valid"),
                    "priority": None,
                    "target": {"text": "有效"},
                },
            ],
        }

        exporter = DfsPathExporter(graph, "Pages_root")
        directory = build_page_directory(graph)

        self.assertEqual(
            [item["page_description"] for item in exporter.build()],
            ["有效"],
        )
        self.assertEqual(exporter.unreachable_pages(), [])
        self.assertEqual(
            [item["page_name"] for item in directory["items"][0]["children"]],
            ["Pages_valid"],
        )
        self.assertEqual(
            directory["items"][0]["children"][0]["via"]["priority"], 1000,
        )

    def test_graph_backups_are_unique_even_when_created_immediately(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = NavigationGraphRepository(Path(temp_dir))
            repository.save({
                "states": {"Pages_root": state("设置")},
                "transitions": [],
            })

            first = repository.backup()
            second = repository.backup()

            self.assertNotEqual(first, second)
            self.assertTrue(Path(first).exists())
            self.assertTrue(Path(second).exists())

    def test_frontend_child_order_persists_to_graph_and_controls_dfs(self):
        graph = {
            "package_name": "com.huawei.hmos.settings",
            "main_page_name": "com.huawei.hmos.settings.MainAbility",
            "states": {
                "Pages_root": state("设置"),
                "Pages_a": state("A"),
                "Pages_b": state("B"),
                "Pages_c": state("C"),
            },
            "transitions": [
                {
                    **transition("root-a", "Pages_root", "Pages_a"),
                    "target": {"text": "A"},
                },
                {
                    **transition("root-b", "Pages_root", "Pages_b"),
                    "target": {"text": "B"},
                },
                {
                    **transition("root-c", "Pages_root", "Pages_c"),
                    "target": {"text": "C"},
                },
                {
                    **transition("root-a-alternate", "Pages_root", "Pages_a"),
                    "target": {"text": "A alternate"},
                },
            ],
        }

        persisted = reorder_child_transitions(
            graph,
            "Pages_root",
            ["root-c", "root-a-alternate", "root-a", "root-b"],
        )
        directory = build_page_directory(graph)
        dfs = DfsPathExporter(graph, "Pages_root").build()

        self.assertEqual(persisted, ["root-c", "root-a-alternate", "root-a", "root-b"])
        self.assertEqual(
            {
                item["transition_id"]: item["priority"]
                for item in graph["transitions"]
            },
            {"root-a": 30, "root-b": 40, "root-c": 10, "root-a-alternate": 20},
        )
        self.assertEqual(
            [item["transition_id"] for item in graph["transitions"]],
            ["root-c", "root-a-alternate", "root-a", "root-b"],
        )
        self.assertEqual(
            [child["page_name"] for child in directory["items"][0]["children"]],
            ["Pages_c", "Pages_a", "Pages_a", "Pages_b"],
        )
        self.assertEqual(
            [item["page_description"] for item in dfs],
            ["C", "A alternate", "B"],
        )

    def test_reorder_rejects_partial_duplicate_or_foreign_transition_sets(self):
        graph = {
            "states": {
                "Pages_root": state("设置"),
                "Pages_a": state("A"),
                "Pages_b": state("B"),
            },
            "transitions": [
                transition("root-a", "Pages_root", "Pages_a"),
                transition("root-b", "Pages_root", "Pages_b"),
            ],
        }
        original = copy.deepcopy(graph)
        navigation = NavigationGraph(graph)

        invalid_orders = [
            ["root-a"],
            ["root-a", "root-a"],
            ["root-a", "foreign"],
        ]
        for ordered_ids in invalid_orders:
            with self.subTest(ordered_ids=ordered_ids):
                with self.assertRaisesRegex(ValueError, "集合不一致|重复"):
                    navigation.reorder_children("Pages_root", ordered_ids)
                self.assertEqual(graph, original)

    def test_navigation_target_only_uses_type_for_unidentified_button(self):
        with_key_and_text = build_semantic_target_from_node({
            "component_type": "Button",
            "key": "save_button",
            "text": "保存",
        })
        text_only = build_semantic_target_from_node({
            "component_type": "Button",
            "key": "",
            "text": "继续",
        })
        key_only = build_semantic_target_from_node({
            "component_type": "Button",
            "key": "next_button",
            "text": "",
        })
        unidentified_button = build_semantic_target_from_node({
            "component_type": "Button",
            "key": "",
            "text": "",
        })

        self.assertEqual(with_key_and_text["key"], "save_button")
        self.assertEqual(with_key_and_text["text"], "保存")
        self.assertNotIn("type", with_key_and_text)
        self.assertEqual(text_only["text"], "继续")
        self.assertNotIn("type", text_only)
        self.assertEqual(key_only["key"], "next_button")
        self.assertNotIn("type", key_only)
        self.assertEqual(unidentified_button["type"], "button")

    def test_dynamic_inner_keys_are_unstable_and_rechecked_before_target_write(self):
        for key in ("0_Inner", "3_inner", "12_iNnEr"):
            with self.subTest(key=key):
                self.assertFalse(is_stable_key_for_navigation(key))
        for key in (
            "item*dynamic",
            "AvailableDeviceGroup.entry",
            "entry_12345678",
            "0123456789abcdef",
        ):
            with self.subTest(key=key):
                self.assertFalse(is_stable_key_for_navigation(key))

        target = build_semantic_target_from_node({
            "component_type": "ListItem",
            "key": "3_Inner",
            "text": "显示和亮度",
        })
        self.assertEqual(target, {
            "text": "显示和亮度",
            "key_description": "显示和亮度",
            "step_prompt": "显示和亮度",
        })

    def test_hit_test_uses_unique_deep_key_and_keeps_searching_for_text(self):
        root = node("Root", bounds="[0,0][1080,2400]", children=[
            node(
                "ListItem",
                key="duplicate_entry",
                clickable=True,
                bounds="[40,200][1040,420]",
                children=[
                    node("Column", key="3_Inner", bounds="[40,200][1040,420]", children=[
                        node("Text", key="display_settings", bounds="[80,230][500,290]"),
                        node("Text", text="显示和亮度", bounds="[80,300][500,370]"),
                    ]),
                ],
            ),
            node("Column", key="duplicate_entry", bounds="[40,600][1040,800]"),
        ])

        hit = hit_test_full_ui_tree(root, 300, 300)
        target = build_semantic_target_from_node(hit)

        self.assertEqual(hit["key"], "display_settings")
        self.assertEqual(hit["text"], "显示和亮度")
        self.assertEqual(target, {
            "key": "display_settings",
            "text": "显示和亮度",
            "key_description": "显示和亮度",
            "step_prompt": "显示和亮度",
        })

    def test_hit_test_does_not_borrow_identity_from_nested_clickable(self):
        root = node("Root", bounds="[0,0][1080,2400]", children=[
            node(
                "ListItem",
                clickable=True,
                bounds="[40,200][1040,420]",
                children=[
                    node("Text", text="显示和亮度", bounds="[80,230][500,290]"),
                    node(
                        "Switch",
                        key="brightness_toggle",
                        text="自动亮度",
                        clickable=True,
                        bounds="[800,240][980,360]",
                    ),
                ],
            ),
        ])

        hit = hit_test_full_ui_tree(root, 300, 300)

        self.assertEqual(hit["text"], "显示和亮度")
        self.assertEqual(hit["key"], "")

    def test_legacy_unstable_key_is_removed_without_losing_text(self):
        target = {
            "type": "key",
            "value": "3_Inner",
            "text": "显示和亮度",
        }

        normalize_semantic_target_types(target)

        self.assertEqual(target, {"text": "显示和亮度"})

    def test_add_transition_replaces_page_pair_and_inherits_priority(self):
        graph = {
            "states": {
                "Pages_root": state("设置", [{
                    "candidate_id": "text::显示和亮度",
                    "text": "显示和亮度",
                    "transition_ids": ["old-first", "old-latest"],
                }]),
                "Pages_display": state("显示和亮度"),
            },
            "transitions": [
                {
                    **transition("old-first", "Pages_root", "Pages_display"),
                    "priority": 20,
                    "target": {"text": "旧入口"},
                },
                {
                    **transition("old-latest", "Pages_root", "Pages_display"),
                    "target": {"text": "较新入口"},
                },
            ],
        }
        latest = {
            "transition_id": "ignored-steps-hash",
            "from_page": "Pages_root",
            "to_page": "Pages_display",
            "target": {"key": "display_settings", "text": "显示和亮度"},
            "steps": [{"operate": "tap", "target": {"text": "显示和亮度"}}],
        }

        NavigationGraph(graph).add_transition(latest)

        self.assertEqual(len(graph["transitions"]), 1)
        self.assertIs(graph["transitions"][0], latest)
        self.assertEqual(latest["transition_id"], "Pages_root__to__Pages_display")
        self.assertEqual(latest["priority"], 20)
        self.assertEqual(latest["target"]["text"], "显示和亮度")
        self.assertEqual(
            graph["states"]["Pages_root"]["merged_candidates"][0]["transition_ids"],
            ["Pages_root__to__Pages_display"],
        )

    def test_graph_load_and_save_clean_targets_and_duplicate_page_pairs(self):
        raw_graph = {
            "states": {
                "Pages_root": state("设置", [{
                    "candidate_id": "text::显示和亮度",
                    "text": "显示和亮度",
                    "transition_ids": ["old-a", "old-b"],
                }]),
                "Pages_display": {
                    **state("显示和亮度"),
                    "page_operations": [{
                        "operation_id": "op",
                        "target": {"key": "0_Inner", "text": "自动亮度"},
                    }],
                },
            },
            "transitions": [
                {
                    **transition("old-a", "Pages_root", "Pages_display"),
                    "target": {"key": "3_Inner", "text": "旧入口"},
                    "steps": [{"target": {"key": "3_Inner", "text": "旧入口"}}],
                },
                {
                    **transition("old-b", "Pages_root", "Pages_display"),
                    "target": {"type": "key", "value": "3_Inner", "text": "显示和亮度"},
                    "steps": [{"target": {"key": "display_settings", "text": "显示和亮度"}}],
                },
            ],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = NavigationGraphRepository(Path(temp_dir))
            repository.path.parent.mkdir(parents=True)
            repository.path.write_text(
                json.dumps(raw_graph, ensure_ascii=False),
                encoding="utf-8",
            )

            loaded = repository.load()

            self.assertEqual(len(loaded["transitions"]), 1)
            kept = loaded["transitions"][0]
            self.assertEqual(kept["transition_id"], "Pages_root__to__Pages_display")
            self.assertEqual(kept["target"], {"text": "显示和亮度"})
            self.assertEqual(kept["steps"][0]["target"]["key"], "display_settings")
            self.assertNotIn(
                "key",
                loaded["states"]["Pages_display"]["page_operations"][0]["target"],
            )
            self.assertEqual(
                loaded["states"]["Pages_root"]["merged_candidates"][0]["transition_ids"],
                ["Pages_root__to__Pages_display"],
            )

            repository.save(loaded)
            saved = json.loads(repository.path.read_text(encoding="utf-8"))
            self.assertEqual(len(saved["transitions"]), 1)
            self.assertEqual(saved["transitions"][0]["transition_id"], "Pages_root__to__Pages_display")

    def test_theme_ui_candidate_extraction_stays_compatible(self):
        root = themes_tree()
        annotate(root)
        candidates = extract_navigation_candidates(root)

        self.assertEqual(
            [item["key"] for item in candidates],
            ["theme.current.card", "theme.store.entry"],
        )

    def test_title_bar_back_button_does_not_override_explicit_page_title(self):
        root = themes_tree()
        annotate(root)

        detected = build_navigation_state(root)

        self.assertEqual(detected["last_title"], "主题")
        self.assertEqual(detected["page_name"], "Pages_主题")

    def test_same_title_child_keeps_parent_context_on_entry_and_refresh(self):
        root, wlan, child = (
            hdc_page("设置", "WLAN", "WLAN_entry"),
            hdc_page("WLAN", "加入 WLAN"),
            hdc_page("设置", "输入 WLAN 信息", "ssid_input"),
        )
        for tree in (root, wlan, child):
            annotate(tree)

        root_state = build_navigation_state(root)
        graph = {"states": {"Pages_root": root_state}, "transitions": []}
        wlan_target = {**extract_navigation_candidates(root)[0]["suggested_target"], "expect": "new_page"}
        self.assertNotIn("type", wlan_target)
        self.assertEqual(wlan_target["key"], "WLAN_entry")
        wlan_state = contextualize_child_state(
            graph, "Pages_root", build_navigation_state(wlan), wlan_target,
        )
        graph["states"][wlan_state["page_name"]] = wlan_state

        join_target = {**extract_navigation_candidates(wlan)[0]["suggested_target"], "expect": "new_page"}
        self.assertNotIn("type", join_target)
        self.assertEqual((join_target.get("key"), join_target["text"]), (None, "加入 WLAN"))
        child_state = contextualize_child_state(
            graph, wlan_state["page_name"], build_navigation_state(child), join_target,
        )
        graph["states"][child_state["page_name"]] = child_state

        self.assertEqual(child_state["page_name"], "Pages_WLAN_to设置")
        self.assertNotEqual(child_state["page_name"], "Pages_root")
        self.assertEqual(child_state["parent_page"], wlan_state["page_name"])
        self.assertIn("text::加入 WLAN", child_state["context_key"])
        refreshed = resolve_detected_state(
            graph, build_navigation_state(child), child_state["page_name"],
        )
        repeated = contextualize_child_state(
            graph, wlan_state["page_name"], build_navigation_state(child), join_target,
        )
        root_refreshed = resolve_detected_state(
            graph, build_navigation_state(root), "Pages_root",
        )
        self.assertEqual(refreshed["page_name"], child_state["page_name"])
        self.assertEqual(repeated["page_name"], child_state["page_name"])
        self.assertEqual(root_refreshed["page_name"], "Pages_root")
        self.assertEqual(set(graph["states"]), {"Pages_root", wlan_state["page_name"], child_state["page_name"]})

    @unittest.skipIf(web_nav_server is None, "FastAPI dependency is not installed")
    def test_http_rename_migrates_graph_session_and_pending_references(self):
        graph = {
            "states": {
                "Pages_root": state("设置"),
                "Pages_old": {
                    **state("旧页面"),
                    "page_name": "Pages_old",
                    "parent_page": "Pages_root",
                },
                "Pages_child": {
                    **state("子页面"),
                    "page_name": "Pages_child",
                    "parent_page": "Pages_old",
                    "context_key": "Pages_old::Pages_child::text::入口",
                },
            },
            "transitions": [transition("old-child", "Pages_old", "Pages_child")],
        }
        original_config = web_nav_server.config
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                work_dir = Path(temp_dir)
                repository = NavigationGraphRepository(work_dir)
                repository.save(graph)
                navigation_dir = repository.path.parent
                runtime_documents = {
                    "current_path_session.json": {
                        "active_page": "Pages_old",
                        "base_page": "Pages_old",
                    },
                    "pending_transition.json": {
                        "from_page": "Pages_old",
                        "to_page": "Pages_child",
                    },
                    "pending_action_chain.json": {
                        "from_page": "Pages_old",
                        "steps": [{"target": {"text": "Pages_old"}}],
                    },
                }
                for filename, document in runtime_documents.items():
                    (navigation_dir / filename).write_text(
                        json.dumps(document, ensure_ascii=False),
                        encoding="utf-8",
                    )
                web_nav_server.config = web_nav_server.ServerConfig(work_dir=work_dir)

                response = web_nav_server.api_rename_page(
                    web_nav_server.RenamePageRequest(
                        old_page_name="Pages_old",
                        new_page_name="Pages_new",
                    )
                )
                self.assertTrue(json.loads(response.body)["ok"])

                saved = repository.load()
                self.assertEqual(
                    saved["states"]["Pages_child"]["parent_page"], "Pages_new",
                )
                self.assertEqual(
                    saved["states"]["Pages_child"]["context_key"],
                    "Pages_new::Pages_child::text::入口",
                )
                session = json.loads(
                    (navigation_dir / "current_path_session.json").read_text(encoding="utf-8")
                )
                pending = json.loads(
                    (navigation_dir / "pending_transition.json").read_text(encoding="utf-8")
                )
                chain = json.loads(
                    (navigation_dir / "pending_action_chain.json").read_text(encoding="utf-8")
                )
                self.assertEqual(session, {
                    "active_page": "Pages_new",
                    "base_page": "Pages_new",
                })
                self.assertEqual(pending["from_page"], "Pages_new")
                self.assertEqual(chain["from_page"], "Pages_new")
                self.assertEqual(chain["steps"][0]["target"]["text"], "Pages_old")
        finally:
            web_nav_server.config = original_config

    @unittest.skipIf(web_nav_server is None, "FastAPI dependency is not installed")
    def test_browser_refresh_uses_persisted_context_instead_of_root_title(self):
        root, wlan, child = hdc_page("设置", "WLAN", "WLAN_entry"), hdc_page("WLAN", "加入 WLAN"), hdc_page("设置", "输入 WLAN 信息", "ssid_input")
        for tree in (root, wlan, child):
            annotate(tree)
        root_state = build_navigation_state(root)
        graph = {"states": {"Pages_root": root_state}, "transitions": []}
        wlan_state = contextualize_child_state(
            graph, "Pages_root", build_navigation_state(wlan),
            {**extract_navigation_candidates(root)[0]["suggested_target"], "expect": "new_page"},
        )
        graph["states"][wlan_state["page_name"]] = wlan_state
        child_state = contextualize_child_state(
            graph, wlan_state["page_name"], build_navigation_state(child),
            {**extract_navigation_candidates(wlan)[0]["suggested_target"], "expect": "new_page"},
        )
        graph["states"][child_state["page_name"]] = child_state

        original_config = web_nav_server.config
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                work_dir = Path(temp_dir)
                output_dir = work_dir / "outputs" / "latest"
                output_dir.mkdir(parents=True)
                (output_dir / "current_ui_tree.json").write_text(
                    json.dumps(hdc_page("设置", "输入 WLAN 信息", "ssid_input"), ensure_ascii=False),
                    encoding="utf-8",
                )
                (output_dir / "current_screen.png").write_bytes(b"png")
                graph_path = navigation_graph_path(work_dir)
                graph_path.parent.mkdir(parents=True)
                graph_path.write_text(json.dumps(graph, ensure_ascii=False), encoding="utf-8")
                (graph_path.parent / "current_path_session.json").write_text(
                    json.dumps({"active_page": child_state["page_name"]}, ensure_ascii=False),
                    encoding="utf-8",
                )
                web_nav_server.config = web_nav_server.ServerConfig(work_dir, output_dir=output_dir)
                refreshed = web_nav_server.read_current_state()
                self.assertEqual(refreshed["state"]["page_name"], child_state["page_name"])
                self.assertEqual(refreshed["active_page"], child_state["page_name"])
                self.assertIn("Pages_root", json.loads(graph_path.read_text(encoding="utf-8"))["states"])
        finally:
            web_nav_server.config = original_config

    def test_orphans_are_everything_unreachable_from_root(self):
        graph = {
            "states": {
                "Pages_root": state("设置"),
                "Pages_reachable": state("可达"),
                "Pages_chain_parent": state("孤儿父"),
                "Pages_chain_child": state("孤儿子"),
                "Pages_cycle_a": state("循环 A"),
                "Pages_cycle_b": state("循环 B"),
            },
            "transitions": [
                transition("root-reachable", "Pages_root", "Pages_reachable"),
                transition("chain", "Pages_chain_parent", "Pages_chain_child"),
                transition("cycle-a-b", "Pages_cycle_a", "Pages_cycle_b"),
                transition("cycle-b-a", "Pages_cycle_b", "Pages_cycle_a"),
            ],
        }

        pages = GraphMaintenance(graph).orphan_pages("Pages_cycle_a")

        self.assertEqual(
            [item["page_name"] for item in pages],
            ["Pages_chain_child", "Pages_chain_parent", "Pages_cycle_a", "Pages_cycle_b"],
        )
        self.assertTrue(next(item for item in pages if item["page_name"] == "Pages_cycle_a")["is_active"])

    def test_branch_preview_contains_new_orphan_cycle_but_not_old_orphan(self):
        old_orphan_candidate = {
            "candidate_id": "key::stale",
            "key": "stale",
            "transition_ids": ["already-missing"],
            "requires_operation_id": "already-missing-operation",
        }
        graph = {
            "states": {
                "Pages_root": state("设置", [{
                    "candidate_id": "key::open-a",
                    "key": "open-a",
                    "source": "hit_test_click",
                    "transition_ids": ["root-a"],
                    "operation_ids": [],
                }]),
                "Pages_a": state("A"),
                "Pages_b": state("B"),
                "Pages_keep": state("保留"),
                "Pages_old_orphan": state("历史孤儿", [old_orphan_candidate]),
            },
            "transitions": [
                transition("root-a", "Pages_root", "Pages_a"),
                transition("a-b", "Pages_a", "Pages_b"),
                transition("b-a", "Pages_b", "Pages_a"),
                transition("root-keep", "Pages_root", "Pages_keep"),
            ],
        }
        maintenance = GraphMaintenance(graph)

        plan = maintenance.plan_delete("branch", {"transition_id": "root-a", "delete_descendants": True})

        self.assertEqual(plan["states"], ["Pages_a", "Pages_b"])
        self.assertEqual(
            {item["transition_id"] for item in plan["transitions"]},
            {"root-a", "a-b", "b-a"},
        )
        self.assertNotIn("Pages_old_orphan", plan["states"])
        self.assertIn(
            {"page_name": "Pages_root", "candidate_id": "key::open-a", "action": "delete_orphan_clicked_candidate"},
            plan["candidates"],
        )

        maintenance.apply_delete(plan)

        self.assertEqual(set(graph["states"]), {"Pages_root", "Pages_keep", "Pages_old_orphan"})
        self.assertEqual([item["transition_id"] for item in graph["transitions"]], ["root-keep"])
        self.assertEqual(graph["states"]["Pages_old_orphan"]["merged_candidates"], [old_orphan_candidate])

    def test_page_preview_always_lists_every_edge_removed_with_state(self):
        graph = {
            "states": {
                "Pages_root": state("设置"),
                "Pages_target": state("目标"),
                "Pages_child": state("子页"),
            },
            "transitions": [
                transition("incoming", "Pages_root", "Pages_target"),
                transition("outgoing", "Pages_target", "Pages_child"),
            ],
        }
        maintenance = GraphMaintenance(graph)

        plan = maintenance.plan_delete("page", {
            "page_name": "Pages_target",
            "delete_incoming": False,
            "delete_outgoing": False,
        })

        self.assertEqual(
            {item["transition_id"] for item in plan["transitions"]},
            {"incoming", "outgoing"},
        )
        expected_plan = copy.deepcopy(plan)
        maintenance.apply_delete(plan)
        self.assertEqual(plan, expected_plan)
        self.assertNotIn("Pages_target", graph["states"])
        self.assertEqual(graph["transitions"], [])

    def test_orphan_delete_is_explicit_and_can_delete_a_selected_subset(self):
        graph = {
            "states": {
                "Pages_root": state("设置"),
                "Pages_orphan_a": state("孤儿 A"),
                "Pages_orphan_b": state("孤儿 B"),
            },
            "transitions": [transition("orphan-link", "Pages_orphan_a", "Pages_orphan_b")],
        }
        maintenance = GraphMaintenance(graph)

        plan = maintenance.plan_delete("orphan_pages", {"page_names": ["Pages_orphan_a"]})
        maintenance.apply_delete(plan)

        self.assertEqual(plan["states"], ["Pages_orphan_a"])
        self.assertEqual([item["transition_id"] for item in plan["transitions"]], ["orphan-link"])
        self.assertEqual(set(graph["states"]), {"Pages_root", "Pages_orphan_b"})

    def test_operation_and_capture_cleanup_match_their_candidate_preview(self):
        graph = {
            "states": {
                "Pages_root": {
                    **state("设置", [
                        {
                            "candidate_id": "key::revealed",
                            "key": "revealed",
                            "operation_ids": ["op-1"],
                            "requires_operation_id": "op-1",
                            "source_operation_id": "op-1",
                            "transition_ids": [],
                        },
                        {
                            "candidate_id": "key::capture-only",
                            "key": "capture-only",
                            "source_capture_id": "capture-1",
                            "operation_ids": [],
                            "transition_ids": [],
                        },
                    ]),
                    "page_operations": [{"operation_id": "op-1"}],
                    "continued_captures": [{"capture_id": "capture-1"}],
                },
            },
            "transitions": [],
        }
        maintenance = GraphMaintenance(graph)

        operation_plan = maintenance.plan_delete("page_operation", {
            "page_name": "Pages_root",
            "operation_id": "op-1",
            "delete_revealed_candidates": False,
        })
        self.assertIn(
            {"page_name": "Pages_root", "candidate_id": "key::revealed", "action": "remove_operation_ref"},
            operation_plan["candidates"],
        )
        maintenance.apply_delete(operation_plan)
        revealed = graph["states"]["Pages_root"]["merged_candidates"][0]
        self.assertNotIn("requires_operation_id", revealed)
        self.assertNotIn("source_operation_id", revealed)
        self.assertEqual(revealed["operation_ids"], [])

        capture_plan = maintenance.plan_delete("continued_capture", {
            "page_name": "Pages_root",
            "capture_id": "capture-1",
            "delete_candidates_from_capture": True,
        })
        self.assertIn(
            {"page_name": "Pages_root", "candidate_id": "key::capture-only", "action": "delete_from_capture"},
            capture_plan["candidates"],
        )
        maintenance.apply_delete(capture_plan)
        self.assertEqual(
            [item["candidate_id"] for item in graph["states"]["Pages_root"]["merged_candidates"]],
            ["key::revealed"],
        )

    @unittest.skipIf(web_nav_server is None, "FastAPI dependency is not installed")
    def test_orphan_management_http_deletes_selected_and_all_without_recreating_active_page(self):
        graph = {
            "states": {
                "Pages_root": state("设置"),
                "Pages_orphan_a": state("孤儿 A"),
                "Pages_orphan_b": state("孤儿 B"),
            },
            "transitions": [transition("orphan-link", "Pages_orphan_a", "Pages_orphan_b")],
        }
        original_config = web_nav_server.config
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                work_dir = Path(temp_dir)
                output_dir = work_dir / "outputs" / "latest"
                output_dir.mkdir(parents=True)
                graph_path = navigation_graph_path(work_dir)
                graph_path.parent.mkdir(parents=True)
                graph_path.write_text(json.dumps(graph, ensure_ascii=False), encoding="utf-8")
                (graph_path.parent / "current_path_session.json").write_text(
                    json.dumps({"active_page": "Pages_orphan_a"}, ensure_ascii=False),
                    encoding="utf-8",
                )
                orphan_tree = hdc_page("孤儿 A", "占位控件")
                (output_dir / "current_ui_tree.json").write_text(
                    json.dumps(orphan_tree, ensure_ascii=False),
                    encoding="utf-8",
                )
                (output_dir / "current_screen.png").write_bytes(b"png")
                web_nav_server.config = web_nav_server.ServerConfig(work_dir, output_dir=output_dir)

                client = TestClient(web_nav_server.app)
                listed = client.get("/api/orphan_pages").json()
                self.assertEqual(
                    [item["page_name"] for item in listed["orphan_pages"]],
                    ["Pages_orphan_a", "Pages_orphan_b"],
                )

                preview = client.post("/api/delete_action", json={
                    "target_type": "orphan_pages",
                    "payload": {"page_names": ["Pages_orphan_a"]},
                    "dry_run": True,
                }).json()
                deleted = client.post("/api/delete_action", json={
                    "target_type": "orphan_pages",
                    "payload": {"page_names": ["Pages_orphan_a"]},
                    "dry_run": False,
                    "preview_token": preview["preview_token"],
                }).json()
                self.assertTrue(deleted["ok"])
                self.assertEqual(deleted["delete_plan"]["states"], ["Pages_orphan_a"])

                # 管理界面删除后会刷新 /api/state；该只读请求不能把仍显示在
                # 设备上的已删孤儿页重新写回图中。
                self.assertTrue(client.get("/api/state").json()["ok"])
                saved = json.loads(graph_path.read_text(encoding="utf-8"))
                self.assertNotIn("Pages_orphan_a", saved["states"])

                remaining = client.get("/api/orphan_pages").json()["orphan_pages"]
                remaining_names = [item["page_name"] for item in remaining]
                self.assertEqual(remaining_names, ["Pages_orphan_b"])
                preview = client.post("/api/delete_action", json={
                    "target_type": "orphan_pages",
                    "payload": {"page_names": remaining_names},
                    "dry_run": True,
                }).json()
                self.assertTrue(client.post("/api/delete_action", json={
                    "target_type": "orphan_pages",
                    "payload": {"page_names": remaining_names},
                    "dry_run": False,
                    "preview_token": preview["preview_token"],
                }).json()["ok"])
                self.assertEqual(
                    set(json.loads(graph_path.read_text(encoding="utf-8"))["states"]),
                    {"Pages_root"},
                )
        finally:
            web_nav_server.config = original_config

    @unittest.skipIf(web_nav_server is None, "FastAPI dependency is not installed")
    def test_http_delete_requires_the_confirmed_preview_token(self):
        graph = {
            "states": {
                "Pages_root": state("设置"),
                "Pages_target": state("目标"),
                "Pages_old_orphan": state("历史孤儿"),
            },
            "transitions": [transition("root-target", "Pages_root", "Pages_target")],
        }
        original_config = web_nav_server.config
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                work_dir = Path(temp_dir)
                graph_path = navigation_graph_path(work_dir)
                graph_path.parent.mkdir(parents=True)
                graph_path.write_text(json.dumps(graph, ensure_ascii=False), encoding="utf-8")
                web_nav_server.config = web_nav_server.ServerConfig(work_dir=work_dir)
                payload = {"page_name": "Pages_target"}

                orphan_response = web_nav_server.api_orphan_pages()
                orphan_data = json.loads(orphan_response.body)
                self.assertEqual(
                    [item["page_name"] for item in orphan_data["orphan_pages"]],
                    ["Pages_old_orphan"],
                )

                preview_response = web_nav_server.api_delete_action(DeleteActionRequest(
                    target_type="page",
                    payload=payload,
                    dry_run=True,
                ))
                preview = json.loads(preview_response.body)
                self.assertEqual(preview["delete_plan"]["states"], ["Pages_target"])
                self.assertNotIn("Pages_old_orphan", preview["delete_plan"]["states"])

                with self.assertRaisesRegex(ValueError, "重新预览"):
                    web_nav_server.api_delete_action(DeleteActionRequest(
                        target_type="page",
                        payload=payload,
                        dry_run=False,
                    ))
                self.assertIn("Pages_target", json.loads(graph_path.read_text(encoding="utf-8"))["states"])

                changed_graph = json.loads(graph_path.read_text(encoding="utf-8"))
                changed_graph["states"]["Pages_target"]["page_operations"] = [{"operation_id": "late-op"}]
                graph_path.write_text(json.dumps(changed_graph, ensure_ascii=False), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "计划已变化"):
                    web_nav_server.api_delete_action(DeleteActionRequest(
                        target_type="page",
                        payload=payload,
                        dry_run=False,
                        preview_token=preview["preview_token"],
                    ))

                preview_response = web_nav_server.api_delete_action(DeleteActionRequest(
                    target_type="page",
                    payload=payload,
                    dry_run=True,
                ))
                preview = json.loads(preview_response.body)
                result_response = web_nav_server.api_delete_action(DeleteActionRequest(
                    target_type="page",
                    payload=payload,
                    dry_run=False,
                    preview_token=preview["preview_token"],
                ))
                result = json.loads(result_response.body)
                saved = json.loads(graph_path.read_text(encoding="utf-8"))
                self.assertEqual(result["delete_plan"], preview["delete_plan"])
                self.assertNotIn("Pages_target", saved["states"])
                self.assertIn("Pages_old_orphan", saved["states"])
                self.assertTrue(Path(result["graph_backup"]).exists())
        finally:
            web_nav_server.config = original_config


if __name__ == "__main__":
    unittest.main()
