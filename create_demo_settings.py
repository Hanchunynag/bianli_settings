#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 Web 导航录制器的模拟设置页面数据。"""

import json
import struct
import zlib
from pathlib import Path

from settings_ui_manual_recorder import save_navigation_graph


ROOT = Path(__file__).resolve().parent
DEMO_DIR = ROOT / "demo_settings"
LATEST_DIR = DEMO_DIR / "outputs" / "latest"
NAV_DIR = DEMO_DIR / "outputs" / "navigation"


def node(node_type, key="", text="", bounds="[0,0][0,0]", clickable=False, children=None, **extra):
    attrs = {
        "type": node_type,
        "key": key,
        "text": text,
        "bounds": bounds,
        "visible": "true",
        "enabled": "true",
    }
    if clickable:
        attrs["clickable"] = "true"
    attrs.update({k: v for k, v in extra.items() if v not in (None, "")})
    return {"attributes": attrs, "children": children or []}


def text(value, bounds):
    return node("Text", text=value, bounds=bounds)


def png(path, width=1080, height=2400):
    def chunk(kind, data):
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    rows = []
    for y in range(height):
        row = bytearray([0])
        for x in range(width):
            if y < 180:
                color = (247, 248, 252)
            elif 205 <= y <= 330 and x > 820:
                color = (45, 108, 223)
            elif 390 <= y <= 520:
                color = (255, 255, 255)
            elif 560 <= y <= 690:
                color = (255, 255, 255)
            elif 730 <= y <= 860:
                color = (255, 255, 255)
            else:
                color = (238, 242, 247)
            row.extend(color)
        rows.append(bytes(row))
    raw = b"".join(rows)
    data = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 6))
        + chunk(b"IEND", b"")
    )
    path.write_bytes(data)


def wlan_tree():
    return node("Root", bounds="[0,0][1080,2400]", children=[
        node("NavDestination", key="settings.wlan", bounds="[0,0][1080,2400]", children=[
            node("TitleBar", bounds="[0,0][1080,180]", children=[
                node("Button", key="nav.back", text="返回", bounds="[24,64][112,152]", clickable=True),
                node("Text", key="page.title_id", text="WLAN", bounds="[128,72][360,144]"),
                node("Button", key="wifi.more.button", text="更多", bounds="[936,64][1024,152]", clickable=True),
            ]),
            node("NavDestinationContent", bounds="[0,180][1080,2400]", children=[
                node("Row", key="wifi.master.row", text="WLAN 开关", bounds="[36,204][1044,340]", children=[
                    text("WLAN", "[64,232][260,292]"),
                    node("Button", key="wifi.master.switch", text="开", bounds="[842,224][1016,320]", clickable=True),
                ]),
                node("ListItem", key="wifi.connected.network", text="HanHome-5G", bounds="[36,392][1044,528]", clickable=True, children=[
                    text("HanHome-5G", "[64,418][420,468]"),
                    text("已连接", "[64,472][260,512]"),
                ]),
                node("ListItem", key="wifi.saved.networks", text="已保存的网络", bounds="[36,560][1044,696]", clickable=True, children=[
                    text("已保存的网络", "[64,596][420,656]"),
                ]),
                node("ListItem", key="wifi.add.network", text="添加网络", bounds="[36,728][1044,864]", clickable=True, children=[
                    text("添加网络", "[64,764][360,824]"),
                ]),
            ]),
        ]),
    ])


def themes_tree():
    return node("Root", bounds="[0,0][1080,2400]", children=[
        node("NavDestination", key="settings.themes", bounds="[0,0][1080,2400]", children=[
            node("TitleBar", bounds="[0,0][1080,180]", children=[
                node("Button", key="nav.back", text="返回", bounds="[24,64][112,152]", clickable=True),
                node("Text", key="page.title_id", text="主题", bounds="[128,72][360,144]"),
            ]),
            node("NavDestinationContent", bounds="[0,180][1080,2400]", children=[
                node("Column", key="theme.current.card", text="晨雾主题", bounds="[96,260][984,1240]", clickable=True, children=[
                    text("晨雾主题", "[156,320][520,388]"),
                    text("左滑切换下一个主题", "[156,1070][620,1120]"),
                    text("上滑删除当前主题", "[156,1130][580,1180]"),
                ]),
                node("ListItem", key="theme.store.entry", text="主题商店", bounds="[36,1340][1044,1476]", clickable=True, children=[
                    text("主题商店", "[64,1376][360,1436]"),
                ]),
            ]),
        ]),
    ])


def state(page_name, title, candidates=0, outgoing=0, incoming=0):
    return {
        "page_name": page_name,
        "page_description": title,
        "last_title": title,
        "signature": {"title": title, "texts_any": [title]},
        "candidate_count": candidates,
        "incoming_count": incoming,
        "outgoing_count": outgoing,
        "merged_candidates": [],
    }


def step(name, key, component_type="ListItem"):
    return {
        "operate": "tap",
        "target": {
            "type": "key",
            "value": key,
            "key": key,
            "component_type": component_type,
            "key_description": name,
            "step_prompt": name,
        },
    }


def transition(tid, from_page, to_page, steps):
    return {
        "transition_id": tid,
        "from_page": from_page,
        "to_page": to_page,
        "operate": "tap",
        "target": steps[0]["target"],
        "steps": steps,
    }


def operation(oid, operate, name, key, effect, component_type="Card"):
    return {
        "operation_id": oid,
        "created_at": "2026-07-01T00:00:00",
        "operate": operate,
        "target": {
            "type": "key",
            "value": key,
            "key": key,
            "component_type": component_type,
            "key_description": name,
            "step_prompt": name,
        },
        "effect": effect,
    }


def main():
    LATEST_DIR.mkdir(parents=True, exist_ok=True)
    NAV_DIR.mkdir(parents=True, exist_ok=True)
    (LATEST_DIR / "current_ui_tree.json").write_text(json.dumps(themes_tree(), ensure_ascii=False, indent=2), encoding="utf-8")
    png(LATEST_DIR / "current_screen.png")

    graph = {
        "package_name": "com.huawei.hmos.settings",
        "main_page_name": "com.huawei.hmos.settings.MainAbility",
        "updated_at": "2026-07-01T00:00:00",
        "states": {
            "Pages_root": state("Pages_root", "设置", candidates=4, outgoing=4),
            "Pages_WLAN": state("Pages_WLAN", "WLAN", candidates=5, incoming=1, outgoing=3),
            "Pages_WLAN_更多信息": state("Pages_WLAN_更多信息", "WLAN 更多信息", candidates=3, incoming=1),
            "Pages_已保存的网络": state("Pages_已保存的网络", "已保存的网络", candidates=2, incoming=1),
            "Pages_蓝牙": state("Pages_蓝牙", "蓝牙", candidates=4, incoming=1),
            "Pages_应用": state("Pages_应用", "应用", candidates=5, incoming=1),
            "Pages_主题": state("Pages_主题", "主题", candidates=2, incoming=1),
        },
        "transitions": [
            transition("root_to_wlan", "Pages_root", "Pages_WLAN", [step("WLAN", "settings.entry.wlan")]),
            transition("root_to_bluetooth", "Pages_root", "Pages_蓝牙", [step("蓝牙", "settings.entry.bluetooth")]),
            transition("root_to_apps", "Pages_root", "Pages_应用", [step("应用", "settings.entry.apps")]),
            transition("root_to_themes", "Pages_root", "Pages_主题", [step("主题", "settings.entry.themes")]),
            transition("wlan_to_more_info", "Pages_WLAN", "Pages_WLAN_更多信息", [
                step("右上角更多按钮", "wifi.more.button", "Button"),
                step("弹出菜单：更多信息", "wifi.more.info.menu_item", "MenuItem"),
            ]),
            transition("wlan_to_saved_networks", "Pages_WLAN", "Pages_已保存的网络", [step("已保存的网络", "wifi.saved.networks")]),
            transition("wlan_to_network_detail", "Pages_WLAN", "Pages_WLAN_更多信息", [step("当前连接网络 HanHome-5G", "wifi.connected.network")]),
        ],
    }
    graph["states"]["Pages_WLAN"]["merged_candidates"] = [
        {
            "candidate_id": "key::wifi.master.switch",
            "type": "key",
            "value": "wifi.master.switch",
            "component_type": "Button",
            "text": "开",
            "key": "wifi.master.switch",
            "key_description": "WLAN 开关",
            "step_prompt": "点击 WLAN 开关",
            "source": "auto_detected",
            "transition_ids": [],
            "operation_ids": [],
        },
        {
            "candidate_id": "key::wifi.more.button",
            "type": "key",
            "value": "wifi.more.button",
            "component_type": "Button",
            "text": "更多",
            "key": "wifi.more.button",
            "key_description": "右上角更多按钮",
            "step_prompt": "右上角更多按钮",
            "source": "auto_detected",
            "transition_ids": ["wlan_to_more_info"],
            "operation_ids": [],
        },
        {
            "candidate_id": "key::wifi.saved.networks",
            "type": "key",
            "value": "wifi.saved.networks",
            "component_type": "ListItem",
            "text": "已保存的网络",
            "key": "wifi.saved.networks",
            "key_description": "已保存的网络",
            "step_prompt": "已保存的网络",
            "source": "auto_detected",
            "transition_ids": ["wlan_to_saved_networks"],
            "operation_ids": [],
        },
    ]
    graph["states"]["Pages_主题"]["page_operations"] = [
        operation("themes_card_swipe_left", "swipe_left", "当前主题卡片", "theme.current.card", "select_next_theme"),
        operation("themes_card_swipe_up", "swipe_up", "当前主题卡片", "theme.current.card", "delete_theme"),
    ]
    graph["states"]["Pages_主题"]["merged_candidates"] = [
        {
            "candidate_id": "key::theme.current.card",
            "type": "key",
            "value": "theme.current.card",
            "component_type": "Card",
            "text": "晨雾主题",
            "key": "theme.current.card",
            "key_description": "当前主题卡片",
            "step_prompt": "当前主题卡片",
            "source": "auto_detected",
            "transition_ids": [],
            "operation_ids": ["themes_card_swipe_left", "themes_card_swipe_up"],
        }
    ]
    save_navigation_graph(graph, DEMO_DIR)
    (NAV_DIR / "current_path_session.json").write_text(json.dumps({"active_page": "Pages_主题"}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"demo data written to {DEMO_DIR}")


if __name__ == "__main__":
    main()
