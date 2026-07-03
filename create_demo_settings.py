#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 Web 导航录制器的模拟设置页面数据。"""

import json
import re
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


def page_name_for(title, used):
    if title == "设置":
        return "Pages_root"
    safe = re.sub(r"\s+", "_", title)
    safe = re.sub(r"[^\w\u4e00-\u9fff]+", "_", safe).strip("_") or "page"
    base = f"Pages_{safe}"
    page = base
    index = 2
    while page in used:
        page = f"{base}_{index}"
        index += 1
    used.add(page)
    return page


def key_for(*parts):
    text = ".".join(str(part) for part in parts if str(part))
    text = text.lower()
    text = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", ".", text).strip(".")
    return f"settings.demo.{text}"


def candidate(name, key, transition_ids=None, component_type="ListItem"):
    transition_ids = transition_ids or []
    return {
        "candidate_id": f"key::{key}",
        "type": "key",
        "value": key,
        "component_type": component_type,
        "text": name,
        "key": key,
        "key_description": name,
        "step_prompt": name,
        "source": "demo_generated",
        "transition_ids": transition_ids,
        "operation_ids": [],
    }


def add_state(states, page_name, title):
    states[page_name] = state(page_name, title)


def add_transition_record(graph, from_page, to_page, name, key, component_type="ListItem"):
    tid = f"{from_page}__to__{to_page}"
    graph["transitions"].append(transition(tid, from_page, to_page, [step(name, key, component_type)]))
    graph["states"][from_page].setdefault("merged_candidates", []).append(candidate(name, key, [tid], component_type))


def settings_catalog():
    return [
        ("WLAN", ["当前连接网络 HanHome-5G", "已保存的网络", "添加网络", "WLAN 助手", "更多设置", "WLAN 更多信息"]),
        ("蓝牙", ["已配对设备", "可用设备", "设备名称", "蓝牙共享", "高级设置", "耳机音频编码"]),
        ("移动网络", ["SIM 卡管理", "移动数据", "个人热点", "流量管理", "网络模式", "VoLTE 高清通话"]),
        ("连接与共享", ["飞行模式", "NFC", "投屏", "打印", "VPN", "私人 DNS"]),
        ("桌面和个性化", ["桌面设置", "图标风格", "壁纸", "主题", "杂志锁屏", "息屏显示"]),
        ("显示和亮度", ["亮度", "护眼模式", "深色模式", "字体大小", "屏幕刷新率", "应用全屏显示"]),
        ("声音和振动", ["来电铃声", "通知铃声", "振动强度", "免打扰", "系统反馈音", "更多声音设置"]),
        ("通知和状态栏", ["应用通知管理", "锁屏通知", "横幅通知", "状态栏图标", "通知勿扰", "角标管理"]),
        ("应用", ["应用管理", "默认应用", "权限管理", "应用启动管理", "应用分身", "特殊访问权限"]),
        ("电池", ["电量使用情况", "省电模式", "超级省电", "电池健康", "更多电池设置", "无线反向充电"]),
        ("存储", ["清理加速", "应用占用", "图片和视频", "文件管理", "云空间", "存储设置"]),
        ("安全", ["锁屏密码", "指纹", "人脸识别", "支付保护中心", "查找设备", "更多安全设置"]),
        ("隐私", ["权限使用记录", "隐私空间", "广告与隐私", "剪贴板提醒", "定位权限", "隐私保护中心"]),
        ("位置服务", ["访问我的位置信息", "应用位置权限", "系统服务", "提高精确度", "最近位置请求", "位置信息共享"]),
        ("健康使用手机", ["屏幕时间", "应用限额", "停用时间", "内容访问限制", "睡眠时间", "家庭守护"]),
        ("辅助功能", ["无障碍", "单手模式", "快捷启动及手势", "智慧多窗", "防误触模式", "悬浮导航"]),
        ("用户和账户", ["华为帐号", "云空间", "付款与账单", "自动同步数据", "添加账户", "家庭共享"]),
        ("系统和更新", ["软件更新", "系统导航方式", "语言和输入法", "日期和时间", "备份和恢复", "重置"]),
        ("关于手机", ["设备名称", "型号", "HarmonyOS 版本", "状态信息", "法律信息", "认证标志"]),
        ("智慧助手", ["智慧语音", "智慧视觉", "智慧识屏", "今天", "场景建议", "实验室功能"]),
    ]


def build_large_settings_graph():
    graph = {
        "package_name": "com.huawei.hmos.settings",
        "main_page_name": "com.huawei.hmos.settings.MainAbility",
        "updated_at": "2026-07-01T00:00:00",
        "traversal_config": {
            "strategy": "dfs",
            "root_page": "Pages_root",
            "default_return_policy": {"type": "system_back"},
        },
        "states": {},
        "transitions": [],
    }
    used = {"Pages_root"}
    add_state(graph["states"], "Pages_root", "设置")
    catalog = settings_catalog()
    title_to_page = {"设置": "Pages_root"}

    for category_index, (category, children) in enumerate(catalog, start=1):
        category_page = page_name_for(category, used)
        title_to_page[category] = category_page
        add_state(graph["states"], category_page, category)
        add_transition_record(graph, "Pages_root", category_page, category, key_for("root", category_index, category))

        for child_index, child in enumerate(children, start=1):
            child_page = page_name_for(child, used)
            title_to_page[f"{category}/{child}"] = child_page
            add_state(graph["states"], child_page, child)
            add_transition_record(graph, category_page, child_page, child, key_for(category_index, child_index, child))

            if child_index in {1, 2}:
                detail_title = f"{child}详情"
                detail_page = page_name_for(detail_title, used)
                add_state(graph["states"], detail_page, detail_title)
                add_transition_record(graph, child_page, detail_page, detail_title, key_for(category_index, child_index, "detail", child))

    wlan_page = title_to_page["WLAN"]
    saved_page = title_to_page["WLAN/已保存的网络"]
    wlan_more_page = title_to_page["WLAN/WLAN 更多信息"]
    graph["states"][wlan_page]["merged_candidates"] = [
        candidate("WLAN 开关", "wifi.master.switch", [], "Button"),
        candidate("右上角更多按钮", "wifi.more.button", ["wlan_to_more_info"], "Button"),
        candidate("已保存的网络", "wifi.saved.networks", ["wlan_to_saved_networks"]),
    ] + graph["states"][wlan_page].get("merged_candidates", [])
    graph["transitions"].extend([
        transition("wlan_to_more_info", wlan_page, wlan_more_page, [
            step("右上角更多按钮", "wifi.more.button", "Button"),
            step("弹出菜单：更多信息", "wifi.more.info.menu_item", "MenuItem"),
        ]),
        transition("wlan_to_saved_networks", wlan_page, saved_page, [step("已保存的网络", "wifi.saved.networks")]),
    ])

    themes_page = title_to_page["桌面和个性化/主题"]
    graph["states"][themes_page]["page_operations"] = [
        operation("themes_card_swipe_left", "swipe_left", "当前主题卡片", "theme.current.card", "select_next_theme"),
        operation("themes_card_swipe_up", "swipe_up", "当前主题卡片", "theme.current.card", "delete_theme"),
    ]
    graph["states"][themes_page].setdefault("merged_candidates", []).append({
        "candidate_id": "key::theme.current.card",
        "type": "key",
        "value": "theme.current.card",
        "component_type": "Card",
        "text": "晨雾主题",
        "key": "theme.current.card",
        "key_description": "当前主题卡片",
        "step_prompt": "当前主题卡片",
        "source": "demo_generated",
        "transition_ids": [],
        "operation_ids": ["themes_card_swipe_left", "themes_card_swipe_up"],
    })

    for page_name, st in graph["states"].items():
        incoming = sum(1 for t in graph["transitions"] if t.get("to_page") == page_name)
        outgoing = sum(1 for t in graph["transitions"] if t.get("from_page") == page_name)
        st["incoming_count"] = incoming
        st["outgoing_count"] = outgoing
        st["candidate_count"] = len(st.get("merged_candidates", []) or [])
    return graph, themes_page


def main():
    LATEST_DIR.mkdir(parents=True, exist_ok=True)
    NAV_DIR.mkdir(parents=True, exist_ok=True)
    (LATEST_DIR / "current_ui_tree.json").write_text(json.dumps(themes_tree(), ensure_ascii=False, indent=2), encoding="utf-8")
    png(LATEST_DIR / "current_screen.png")

    graph, active_page = build_large_settings_graph()
    save_navigation_graph(graph, DEMO_DIR)
    (NAV_DIR / "current_path_session.json").write_text(json.dumps({"active_page": active_page}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"demo data written to {DEMO_DIR}")
    print(f"states={len(graph['states'])}, transitions={len(graph['transitions'])}")


if __name__ == "__main__":
    main()
