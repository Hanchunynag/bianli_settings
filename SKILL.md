# 设置 UI 控件树采集项目 Skill

## 项目定位

本仓库用于 HarmonyOS 设置 App 的手动遍历采集、UI JSON 解析和控件树构建。

目标是把多次手动采集的页面合并成稳定树结构：

```text
root
  WLAN
    Huawei-Guest
      断开连接
      自动重新连接
  蓝牙
  移动网络
```

## 文件职责

必须保持两脚本结构：

1. `settings_json_tree_analyzer.py`
   - 脚本 1。
   - AI / 离线分析使用。
   - 输入 `current_ui_tree.json`。
   - 不连接手机，不执行设备采集。
   - 负责解析 JSON、提取控件、人工确认挂载分支、生成树结构文件。

2. `settings_hdc_capture_runner.py`
   - 脚本 2。
   - 用户在 PC 端连接手机使用。
   - 负责获取 `current_ui_tree.json` 和 `current_screen.png`。
   - 采集完成后调用脚本 1 的 `analyze_json_file()`。

3. `settings_ui_manual_recorder.py`
   - 兼容入口。
   - 只作为旧命令包装器。
   - 不放核心解析逻辑。

## 修改规则

修改代码时必须同步维护脚本 1 和脚本 2：

- JSON 解析、控件识别、树结构、隐私脱敏、人工确认逻辑，只能放在 `settings_json_tree_analyzer.py`。
- PC 端采集逻辑，只能放在 `settings_hdc_capture_runner.py`。
- 不要在脚本 2 中复制脚本 1 的解析逻辑。
- 不要在脚本 1 中加入手机采集逻辑。
- `settings_ui_manual_recorder.py` 只能保持兼容转发。

## 运行命令规范

PowerShell 命令不要带 `$`。

PC 端连接手机采集：

```powershell
python D:\hanchunyang_6_3\AItest\settings_hdc_capture_runner.py
```

兼容旧入口：

```powershell
python D:\hanchunyang_6_3\AItest\settings_ui_manual_recorder.py
```

AI / 离线 JSON 分析：

```powershell
python D:\hanchunyang_6_3\AItest\settings_json_tree_analyzer.py --json D:\hanchunyang_6_3\AItest\outputs\latest\current_ui_tree.json
```

清空旧树重新记录：

```powershell
python D:\hanchunyang_6_3\AItest\settings_hdc_capture_runner.py --reset
```

## 交互规则

每次采集后只允许用户输入数字：

- `-1`：返回父节点。若当前节点是 `root`，必须提示错误，不能继续返回。
- `0`：继续记录当前 `active_node_id`。
- `1-x`：把本次采集结果挂载到对应候选分支下，并更新 `active_node_id`。

不要恢复复杂自动父节点推断逻辑。父子关系应由人工数字选择确认。

## 隐私规则

只要控件 `key` 中包含 `*`，即视为敏感 key：

- 不在 txt/json/终端中展示 key。
- 不在 txt/json/终端中展示 value。
- `locator` 改为 `bounds_center`。
- 保留 `text/name`，否则用户无法在候选列表里选择分支。

不能仅凭 `value=加密` 判断隐私，因为 WLAN 未连接状态、已保存状态、开放状态都可能对应敏感网络条目。

## 输出文件

每次运行后应更新：

```text
outputs/graph/settings_ui_graph_readable.txt
outputs/graph/settings_tree.json
outputs/graph/settings_nodes_index.json
```

脚本 1 还应更新当前页摘要：

```text
outputs/latest/ui_semantic_summary.txt
```

## 设计原则

- 保持最小可用骨架，不逐个页面写死适配规则。
- 不恢复 WLAN、蓝牙、WiFi 详情页的自动挂载 if/else。
- 动态页面父子关系靠人工选择，而不是程序猜测。
- 解析逻辑可增强，但必须先保证树结构可控、可解释、可人工修正。
