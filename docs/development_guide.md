# Settings Navigation Recorder 开发指南

本文只描述当前代码。项目目标是录制设置页面之间的稳定语义路径，并把导航图导出为 DFS 路径数据。

## 1. 结构

| 文件 | 职责 |
| --- | --- |
| `settings_ui_manual_recorder.py` | 设备动作、UI tree 解析、请求模型和导航图领域规则 |
| `web_nav_server.py` | FastAPI 接口及录制、维护、删除流程的编排 |
| `DFS.py` | 把导航图导出为紧凑 DFS 路径 JSON |
| `create_demo_settings.py` | 生成本地演示数据 |
| `templates/nav.html` | 页面结构 |
| `static/nav.js` | 前端事件入口 |
| `static/nav/render.js` | 页面渲染 |
| `static/nav/api.js` | HTTP 请求 |
| `static/nav/dom.js` | DOM 小工具 |
| `static/nav/state.js` | 前端状态 |

不要把 HTML、CSS 或 JavaScript 字符串嵌入 Python 函数。前端逻辑继续放在现有静态文件中；除非确有独立职责，不再增加脚本文件。

## 2. 数据流

```text
设备 UI
  -> capture_artifacts()
  -> build_navigation_state() / extract_navigation_candidates()
  -> web_nav_server.py 的录制流程
  -> settings_navigation_graph.json
  -> DFS.py
  -> settings_navigation_paths.json
```

正式导航图只保存可跨设备复用的语义，例如 `key`、`text`、`step_prompt`。坐标、屏幕尺寸和 bounds 仅用于当前点击，保存前由 `sanitize_navigation_graph_records()` 清除。

## 3. 核心文件

### 3.1 录制领域层

`settings_ui_manual_recorder.py` 的主要入口：

- `capture_artifacts()`：拉取 UI tree 和截图。
- `capture_ui_tree_only()`：只拉取 UI tree，用于快速导航。
- `build_navigation_state()`：识别普通页面或弹窗状态。
- `extract_navigation_candidates()`：提取可录制控件。
- `hit_test_full_ui_tree()`：按临时坐标命中节点。
- `build_semantic_target_from_node()`：把命中节点转换成稳定 target。
- `load_navigation_graph()` / `save_navigation_graph()`：读写并清理导航图。
- `execute_*()`：执行点击、返回和滑动等设备动作。
- `build_page_directory()`：构建页面目录。
- `plan_delete_transition()` / `apply_delete_plan()`：规划并应用图删除操作。
- `*Request`：Web API 共用的请求数据模型。

页面识别异常时，先检查 `find_page_title()`、`page_identity()` 和 `detect_dialog_root()`；候选缺失时检查 `is_recordable_clickable_area()` 与 `target_from_node()`。

### 3.2 Web 后端

`web_nav_server.py` 只保留四组编排逻辑：

1. 当前页面状态与录制会话。
2. 页面跳转、同页变化、手势和弹窗的流程编排。
3. 页面查询、重命名和快速跳转 API。
4. 删除确认、备份和设备控制 API。

页面内录制统一经过：

```text
prepare_operation()
  -> record_page_operation(mode=popup|same_page|gesture)
  -> record_*_operation() 薄入口
```

新增页面内操作模式时，优先扩展 `record_page_operation()` 的参数和差异分支，不要复制完整的采集与保存流程。

所有接口异常由 `api_error()` 转换成：

```json
{"ok": false, "error": "错误说明"}
```

业务接口只写正常流程，不再逐个复制 `try/except`。

### 3.3 前端

`static/nav.js` 只负责事件绑定；`static/nav/render.js` 只负责渲染和页面维护交互。新增按钮时：

1. 在 `templates/nav.html` 增加元素。
2. 在 `static/nav.js` 绑定事件。
3. 只有需要新展示结构时才修改 `static/nav/render.js`。

不要在事件回调中复制请求、错误展示或 loading 状态；使用 `static/nav/api.js` 的现有方法。

### 3.4 DFS 导出

`DFS.py` 当前只导出路径数据，不连接设备、不执行点击。核心类是 `DfsPathExporter`：

- 从 `Pages_root` 开始；
- 每个可达页面只访问一次；
- 保留 transition 中的全部 steps；
- 输出页面的 `special_operate`；
- 报告不可达页面。

页面节点使用入口上下文命名：根页面固定为 `Pages_root`，新页面按“父页面标题 + `to` + 当前标题”生成，例如从设置进入 WLAN 得到 `Pages_设置_toWLAN`。因此不同父页面下的同名页面会生成不同 `page_name`，不会仅因标题相同而合并。

## 4. 导航图格式

最小结构：

```json
{
  "package_name": "com.huawei.hmos.settings",
  "main_page_name": "com.huawei.hmos.settings.MainAbility",
  "traversal_config": {"strategy": "dfs", "root_page": "Pages_root"},
  "states": {
    "Pages_root": {"page_name": "Pages_root", "last_title": "设置"}
  },
  "transitions": []
}
```

跳转使用 `steps` 表示单步或多步路径：

```json
{
  "transition_id": "Pages_root__to__Pages_WLAN__tap_xxx",
  "from_page": "Pages_root",
  "to_page": "Pages_WLAN",
  "operate": "tap",
  "target": {"type": "key", "value": "settings.wlan"},
  "steps": [
    {
      "operate": "tap",
      "target": {
        "type": "key",
        "value": "settings.wlan",
        "key_description": "WLAN",
        "step_prompt": "WLAN"
      }
    }
  ]
}
```

旧数据只有 `target` 时，`transition_steps()` 会兼容地转换成单步。

## 5. 常见改动

### 页面标题识别错误

用真实 `current_ui_tree.json` 复现，修改 `find_page_title()` 或稳定文本过滤。不要在 Web 层硬编码具体页面标题。

### 控件无法录制

依次检查：

1. 节点是否可见、可用、可点击；
2. bounds 是否有效；
3. key 或 text 是否稳定；
4. `extract_navigation_candidates()` 是否把节点过滤掉。

只有缺少稳定 key/text 时才要求用户填写 `manual_label`。

### 增加 Web 动作

设备和图操作写成独立业务函数，接口仅做请求字段转换和响应包装。若动作属于现有控制台或录制类别，在 `api_console_action()` 或 `api_record_action()` 中增加分派即可。

### 修改删除规则

删除必须保持两阶段：

1. `dry_run=true` 返回删除计划；
2. 用户确认后备份导航图，再执行并清理引用。

不要绕过 `finalize_delete()`。

## 6. 验证

生成演示数据：

```bash
conda run -n hcy-env python create_demo_settings.py
```

检查 Python 语法：

```bash
conda run -n hcy-env python -m py_compile \
  web_nav_server.py settings_ui_manual_recorder.py DFS.py create_demo_settings.py
```

启动 Web：

```bash
conda run -n hcy-env python web_nav_server.py \
  --work-dir demo_settings \
  --output-dir demo_settings/outputs/latest \
  --host 127.0.0.1 \
  --port 8020
```

导出 DFS 路径：

```bash
conda run -n hcy-env python DFS.py --work-dir demo_settings
```

提交前至少确认：

- `/api/state`、`/api/page_directory`、`/api/page_detail` 和 `/api/graph` 正常；
- 页面跳转、同页变化、手势、弹窗四种记录仍可写入；
- 删除预览不修改文件，正式删除会生成备份；
- 导航图中不存在坐标字段；
- `DFS.py` 能导出有效 JSON。

## 7. 维护原则

- 一个功能只有一个业务实现，兼容路由只做薄包装。
- 共用的采集、命中、差异计算和保存逻辑必须复用。
- 函数只处理一个层级的职责，接口不嵌入设备脚本或前端脚本。
- 优先修改现有模块，不因小功能继续拆文件。
- 文档只描述已存在的行为，代码变更后同步更新本文。
