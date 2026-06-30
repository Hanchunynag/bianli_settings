# bianli_settings

HarmonyOS 设置页面遍历、控件提取和视觉检测准备工具。

## 日常入口

优先使用统一入口：

```powershell
python settings_tool.py record
python settings_tool.py nav-record
python settings_tool.py nav-path --to Pages_data_usage --description "流量管理"
python settings_tool.py traverse
python settings_tool.py crop
python settings_tool.py visual-prepare
python settings_tool.py yolo-match
```

日常维护只需要记住 `settings_tool.py`；底层脚本用于单独调试某一层能力。

## 核心结构

新录制主流程以“轻量导航状态图 + 路径动作录制器”为核心；旧的页面树、状态机、组件树和组件清单仍保留用于兼容已有遍历/视觉检测流程。

### 新主产物：轻量导航状态图

| 输出 | 作用 |
| --- | --- |
| `outputs/navigation/settings_navigation_graph.json` | 主数据库：记录 `from_page -> operate(target) -> to_page` 的所有可达分支 |
| `outputs/navigation/current_pending_transition.json` | 记录用户已选择入口、但尚未确认目标页面的未完成转移 |
| `outputs/navigation/current_path_session.json` | 预留给后续路径会话状态 |
| `outputs/navigation/settings_path_cases.json` | 从 navigation graph 通过 BFS 派生出的轻量 `path_snapshot` |

路径 step 只保留 `type`、`operate`、`value`、`key_description`、`step_prompt` 以及可选的 `scope`、`expect`、`axis`。普通纵向滚动是执行器查找策略，不写入图或 `path_snapshot`；局部横向滑动（`hl`/`hr`）会写入自环 transition，并标记 `effect=local_horizontal_view_changed`。

### 旧兼容产物

旧流程仍分四层，分别回答四个问题：

| 层 | 输出 | 作用 |
| --- | --- | --- |
| 页面树 | `outputs/graph/settings_tree.json` | 记录设置页面在哪里、父子层级是什么 |
| 状态机 | `outputs/graph/settings_state_machine.json` | 记录从哪个页面通过什么点击跳到哪个页面 |
| 组件树 | `outputs/graph/settings_page_component_tree.json` | 记录每个页面里面有哪些区域和语义组件 |
| 组件清单 | `outputs/graph/settings_page_components.jsonl` | 给遍历、裁剪、视觉检测使用的逐组件事实库 |

一句话理解：

```text
页面树记录页面在哪里；
状态机记录怎么跳过去；
组件树记录页面里面有什么；
视觉层记录这些东西显示得对不对。
```

## 主要命令

采集或解析当前页面，并更新页面树、状态机、组件树：

```powershell
python settings_tool.py record
python settings_tool.py record --skip-capture
python settings_tool.py record --skip-capture --json current_ui_tree.json --no-prompt
```

录制轻量导航状态图：

```powershell
python settings_tool.py nav-record
python settings_tool.py nav-pending-clear
python settings_tool.py nav-graph-show
python settings_tool.py nav-path --to Pages_data_usage --description "流量管理"
```

`nav-record` 每次都会 dump 当前 UI，识别当前 state，并展示可点击候选项。选择候选编号会生成 `current_pending_transition.json`；用户手动进入下一页后再次运行 `nav-record`，确认即可把 pending 补全为 `settings_navigation_graph.json` 中的一条 transition。选择 `hl`/`hr` 会直接记录局部横向滑动自环 transition；不会记录普通上下滚动。

生成检测遍历任务：

```powershell
python settings_tool.py traverse
python settings_tool.py traverse --run
```

按组件 bounds 裁剪当前截图中的组件小图：

```powershell
python settings_tool.py crop --page-id "title::流量管理"
```

生成规则检测、VLM 请求和 LLM 裁决请求：

```powershell
python settings_tool.py visual-prepare --vlm-all
```

接入 YOLO 备份检测结果并和 UI tree 组件匹配：

```powershell
python settings_tool.py yolo-match --detections-json detections.json
```

清理状态机：

```powershell
python settings_tool.py sm-prune
python settings_tool.py sm-reset
python settings_tool.py sm-delete-state root/WLAN
python settings_tool.py sm-delete-transition <transition_id>
```

清理页面树，同时同步清理状态机引用：

```powershell
python settings_tool.py tree-delete 7.3
python settings_tool.py tree-clear 7.3
python settings_tool.py tree-reset
```

## 文件分工

| 文件 | 分工 |
| --- | --- |
| `settings_tool.py` | 统一命令入口，只做路由 |
| `settings_ui_manual_recorder.py` | 页面采集、控件提取、页面树、组件树、状态机持久化 |
| `settings_detection_traverser.py` | 根据页面/状态机/组件生成检测遍历任务 |
| `settings_component_cropper.py` | 根据组件 bounds 裁剪截图 |
| `settings_visual_review_interface.py` | 生成规则检测、VLM、LLM 请求数据 |
| `settings_yolo_backup_detector.py` | YOLO 备份检测与 UI 组件匹配 |

## 组件树

`settings_page_component_tree.json` 是给程序读的结构化页面控件树。每个页面下按区域组织：

```text
page
  title_bar
    semantic_component
      merged_child
  content_entries
    semantic_component
  content_operations
    semantic_component
  content_texts
    semantic_component
```

语义组件会保留 `bounds`、`bounds_center`、`normalized_center`、`key`、`text`、`kind`、`type`、`children`。如果某个组件对应状态机跳转，还会带 `outgoing_transitions`。
