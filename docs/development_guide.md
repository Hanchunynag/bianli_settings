# Settings Navigation Recorder 开发指南

本文描述当前代码。项目目标是录制设置页面之间的稳定语义路径，把导航图导出为 DFS 路径数据，并允许对异常页面的 DFS 记录进行人工维护。

## 1. 结构

| 文件 | 职责 |
| --- | --- |
| `settings_web_console.py` | 正式 Web 入口；在原服务上增加 DFS 人工维护接口 |
| `web_nav_server.py` | FastAPI 录制、查询、删除及图维护流程 |
| `settings_ui_manual_recorder.py` | 设备动作、UI tree 解析、请求模型和导航图领域规则 |
| `DFS.py` | 导出紧凑 DFS 路径，并解析页面级 `dfs_override` |
| `templates/nav.html` | 页面结构 |
| `static/nav.js` | 原有前端事件入口 |
| `static/nav/render.js` | 页面目录和详情渲染 |
| `static/dfs_manual.js` | 页面详情中的 DFS 人工维护板块 |
| `static/nav/api.js` | HTTP 请求 |
| `static/nav/dom.js` | DOM 小工具 |
| `static/nav/state.js` | 前端状态 |

项目不再包含模拟设置数据生成器，也不依赖 demo 工作目录。开发和运行应使用设备真实采集数据。

不要把 HTML、CSS 或 JavaScript 字符串嵌入 Python 业务函数。前端逻辑继续放在静态文件中；除非有明确独立职责，不增加只转发一层的脚本。

## 2. 数据流

```text
设备 UI
  -> capture_device()
  -> build_navigation_state() / extract_navigation_candidates()
  -> web_nav_server.py 的录制流程
  -> settings_navigation_graph.json
  -> 页面详情中的 DFS 人工维护（可选）
  -> DFS.py
  -> settings_navigation_paths.json
```

正式导航图只保存可跨设备复用的语义，例如 `key`、`text`、`step_prompt`。坐标、屏幕尺寸和 bounds 只用于当前点击，保存前由 `strip_coordinate_fields()` 清除。

## 3. Web 启动入口

使用：

```bash
python settings_web_console.py \
  --work-dir settings_workspace \
  --output-dir settings_workspace/outputs/latest \
  --host 127.0.0.1 \
  --port 8020
```

`settings_web_console.py` 复用 `web_nav_server.app` 和 `ServerConfig`，只增加：

```text
GET  /api/dfs_record
POST /api/dfs_override
```

原录制接口仍由 `web_nav_server.py` 提供：

```text
/api/console_action
/api/record_action
/api/delete_action
/api/page_directory
/api/page_detail
/api/orphan_pages
/api/graph
```

## 4. 录制领域层

`settings_ui_manual_recorder.py` 的主要入口：

- `capture_device(include_screen=...)`：统一拉取 UI tree，并按需拉取截图。
- `build_navigation_state()`：遍历 UI tree，提取标题、NavDestination 和页面签名。
- `extract_navigation_candidates()`：识别可录制控件。
- `hit_test_full_ui_tree()`：按临时坐标命中节点。
- `build_semantic_target_from_node()`：把命中节点转换成稳定 target。
- `NavigationGraph`：维护加边、同级排序、页面重命名及页面引用。
- `NavigationGraphRepository`：统一完成 graph 读写、备份和运行期引用迁移。
- `execute_device_input()`：执行点击、返回和滑动等设备动作。
- `build_page_directory()`：构建页面目录。
- `GraphMaintenance`：执行可达性分析、删除计划和删除。

页面识别异常时直接检查 `build_navigation_state()`；候选缺失时检查 `extract_navigation_candidates()` 与 `is_recordable_clickable_area()`。不要在 Web 层硬编码具体页面标题。

## 5. 页面跳转与中间步骤

跳转使用 `steps` 表示单步或多步路径：

```json
{
  "transition_id": "Pages_A__to__Pages_B",
  "from_page": "Pages_A",
  "to_page": "Pages_B",
  "steps": [
    {
      "operate": "tap",
      "target": {
        "key": "menu_grid",
        "key_description": "menu_grid",
        "step_prompt": "menu_grid"
      }
    },
    {
      "operate": "tap",
      "target": {
        "key": "SettingMenu_MenuItem_0",
        "key_description": "更新选项",
        "step_prompt": "更新选项"
      }
    }
  ]
}
```

`menu_grid` 可能是进入目标页必须执行的中间步骤，因此不能从 `path_snapshot` 中随意删除。但中间步骤不等于页面名称组成部分。页面描述和实际点击路径必须解耦。

## 6. DFS 人工维护

每个 state 可以保存完整的页面级覆盖：

```json
{
  "page_name": "Pages_xxx",
  "last_title": "更新选项",
  "dfs_override": {
    "package_name": "com.huawei.hmos.settings",
    "main_page_name": "com.huawei.hmos.settings.MainAbility",
    "page_description": "Mate X5_检查更新_更新选项",
    "path_snapshot": [
      {
        "type": "key",
        "value": "about_device",
        "key_description": "Mate X5",
        "step_prompt": "Mate X5"
      },
      {
        "type": "key",
        "value": "menu_grid",
        "key_description": "menu_grid",
        "step_prompt": "menu_grid"
      },
      {
        "type": "key",
        "value": "SettingMenu_MenuItem_0",
        "key_description": "更新选项",
        "step_prompt": "更新选项"
      }
    ]
  }
}
```

规则：

1. `dfs_override` 只覆盖最终 DFS 输出，不修改 transition、父子关系或页面识别。
2. `page_description` 和 `path_snapshot` 独立维护。
3. `path_snapshot` 每一步只允许紧凑定位字段：`type`、`value`、`key_description`、`step_prompt`。
4. `type` 当前只接受 `key` 或 `text`。
5. 清除 `dfs_override` 后立即恢复自动结果。
6. 保存和清除前由 `NavigationGraphRepository.backup()` 生成导航图备份。

`DFS.py` 中相关入口：

- `normalize_dfs_override()`：校验并压缩人工记录。
- `format_dfs_record()`：生成自动结果并应用覆盖。
- `build_page_dfs_record()`：生成指定可达页面的 DFS 记录。
- `build_page_dfs_preview()`：同时返回自动结果和当前生效结果。
- `format_dfs_records()`：批量导出最终固定字段。

## 7. 页面详情前端

`static/dfs_manual.js` 使用 `MutationObserver` 监听页面详情切换，并按当前 `page_name` 请求 `/api/dfs_record`。

板块提供三个动作：

- 保存人工数据：校验 JSON 后写入 `state.dfs_override`。
- 载入自动结果：只把自动结果放入编辑器，不立即保存。
- 清除人工覆盖：删除 `state.dfs_override`，恢复自动导出。

人工编辑器直接使用最终 DFS JSON 结构，避免再引入一套字段映射。

## 8. 页面目录和排序

页面目录的同级拖拽顺序通过 `reorder_children` 写入 outgoing transition 的 `priority`。页面目录和 DFS 都按：

```text
priority -> transition 原始记录顺序
```

排序。使用 transition ID 可以区分同一父页面下指向同一子页面的多条路径。

## 9. 删除规则

删除保持两阶段：

1. `dry_run=true` 返回完整删除计划和 `preview_token`。
2. 用户确认后使用同一令牌提交 `dry_run=false`。
3. 服务端重新规划并校验令牌，计划未变化时才备份和执行。

孤儿页面定义为从 `Pages_root` 不可达的 state，包括孤儿链后继和脱离 root 的循环子图。普通删除不得隐式删除计划外的孤儿页面。

## 10. 开发检查

检查 Python 语法：

```bash
python -m py_compile \
  settings_web_console.py web_nav_server.py settings_ui_manual_recorder.py DFS.py
```

运行回归测试：

```bash
python -m unittest -v \
  test_dfs_locator_format.py \
  test_dfs_manual_override.py \
  test_graph_maintenance.py \
  test_popup_operations.py
```

DFS 导出：

```bash
python DFS.py --work-dir settings_workspace
```

提交前使用真实导航图确认：

- 页面详情能显示当前自动 DFS 记录。
- 保存后 `settings_navigation_graph.json` 对应 state 出现 `dfs_override`。
- `menu_grid` 等中间步骤可以留在路径中，而 `page_description` 可独立修改。
- 清除覆盖后恢复自动结果。
- `DFS.py` 输出只包含四个固定字段。
- 导航图中不存在正式坐标字段。

## 11. 维护原则

- 一个功能只有一个业务入口。
- 录制图负责事实路径，`dfs_override` 只负责最终导出修正。
- 不用更多自动命名规则替代人工可控配置。
- 不生成模拟业务数据作为正式运行前提。
- 不保存 bounds 作为跨设备定位依据。
