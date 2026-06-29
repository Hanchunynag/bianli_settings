# 设置 UI 遍历检测项目 Skill

## 项目定位

本仓库用于 HarmonyOS 设置 App 的页面遍历、控件提取、组件截图裁剪和视觉检测准备。

程序要维护四类核心数据：

```text
页面树：页面在哪里
状态机：页面之间怎么跳转
组件树：每个页面里面有什么
视觉检测数据：组件截图和模型请求怎么生成
```

## 当前架构

日常只从统一入口运行：

```powershell
python D:\hanchunyang_6_3\AItest\settings_tool.py <command>
```

文件职责：

1. `settings_tool.py`
   - 统一命令入口。
   - 只做命令路由，不放业务逻辑。

2. `settings_ui_manual_recorder.py`
   - 页面采集、当前 UI tree 解析、控件提取。
   - 维护页面树、状态机、页面组件树、组件 JSONL 清单。
   - 负责人工数字选择挂载关系和删除/清理命令。

3. `settings_detection_traverser.py`
   - 根据页面树、状态机和组件清单生成检测遍历任务。

4. `settings_component_cropper.py`
   - 根据组件 bounds 从当前截图裁剪组件小图。

5. `settings_visual_review_interface.py`
   - 生成规则检测、VLM 请求、LLM 裁决请求和初始标注数据。

6. `settings_yolo_backup_detector.py`
   - 可选 YOLO 备份检测。
   - 支持外部检测结果 JSON，并与 UI tree 语义组件按 IoU 匹配。

不要恢复旧的 `settings_json_tree_analyzer.py` / `settings_hdc_capture_runner.py` 两脚本结构。

## 常用命令

采集或解析当前页面：

```powershell
python D:\hanchunyang_6_3\AItest\settings_tool.py record
python D:\hanchunyang_6_3\AItest\settings_tool.py record --skip-capture
```

生成检测遍历任务：

```powershell
python D:\hanchunyang_6_3\AItest\settings_tool.py traverse
python D:\hanchunyang_6_3\AItest\settings_tool.py traverse --run
```

组件截图裁剪：

```powershell
python D:\hanchunyang_6_3\AItest\settings_tool.py crop --page-id "title::流量管理"
```

视觉检测请求准备：

```powershell
python D:\hanchunyang_6_3\AItest\settings_tool.py visual-prepare --vlm-all
```

状态机维护：

```powershell
python D:\hanchunyang_6_3\AItest\settings_tool.py sm-prune
python D:\hanchunyang_6_3\AItest\settings_tool.py sm-reset
python D:\hanchunyang_6_3\AItest\settings_tool.py sm-delete-state root/WLAN
python D:\hanchunyang_6_3\AItest\settings_tool.py sm-delete-transition <transition_id>
```

页面树维护：

```powershell
python D:\hanchunyang_6_3\AItest\settings_tool.py tree-delete 7.3
python D:\hanchunyang_6_3\AItest\settings_tool.py tree-clear 7.3
python D:\hanchunyang_6_3\AItest\settings_tool.py tree-reset
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
- 可保留非敏感的 text/name，否则用户无法选择候选分支。

不能仅凭 `value=加密` 判断隐私，因为 WLAN 未连接状态、已保存状态、开放状态都可能对应敏感网络条目。

## 输出文件

每次 `record` 后应更新：

```text
outputs/graph/settings_ui_graph_readable.txt
outputs/graph/settings_tree.json
outputs/graph/settings_nodes_index.json
outputs/graph/settings_state_machine.json
outputs/graph/settings_page_component_tree.json
outputs/graph/settings_page_components.jsonl
outputs/graph/settings_components_report.md
```

其中：

- `settings_tree.json`：页面树。
- `settings_state_machine.json`：跳转状态机。
- `settings_page_component_tree.json`：页面内部组件结构。
- `settings_page_components.jsonl`：逐组件事实库，供遍历、裁剪、视觉检测使用。

## 设计原则

- 优先维护统一入口，避免让用户记多个脚本。
- 不逐个页面写死适配规则。
- 动态页面父子关系靠人工选择，而不是程序猜测。
- 解析逻辑可增强，但必须保证树结构、状态机、组件树可解释、可删除、可重建。
