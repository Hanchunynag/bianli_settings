# Settings Navigation Recorder 开发指南

本文只描述当前代码。项目目标是录制设置页面之间的稳定语义路径，并把导航图导出为 DFS 路径数据。

## 1. 结构

| 文件 | 职责 |
| --- | --- |
| `settings_ui_manual_recorder.py` | 设备动作、UI tree 解析、请求模型和导航图领域规则 |
| `web_nav_server.py` | FastAPI 接口及录制、维护、删除流程的编排 |
| `DFS.py` | 把导航图导出为紧凑 DFS 路径 JSON |
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
  -> capture_device()
  -> build_navigation_state() / extract_navigation_candidates()
  -> web_nav_server.py 的录制流程
  -> settings_navigation_graph.json
  -> DFS.py
  -> settings_navigation_paths.json
```

正式导航图只保存可跨设备复用的语义，例如 `key`、`text`、`step_prompt`。普通跳转 target 不保存组件
`type`；只有目标 Button 同时没有 key/text 时才使用 `type: "button"` 兜底。页面特殊操作才保存真实组件
`type`。坐标、屏幕尺寸和 bounds 仅用于当前点击，保存前由 `strip_coordinate_fields()` 清除。

## 3. 核心文件

### 3.1 录制领域层

`settings_ui_manual_recorder.py` 的主要入口：

- `capture_device(include_screen=...)`：统一拉取 UI tree，并按需拉取截图。
- `build_navigation_state()`：直接遍历 UI tree，提取标题、NavDestination 和页面签名。
- `extract_navigation_candidates()`：直接识别弹窗范围并提取可录制控件。
- `hit_test_full_ui_tree()`：按临时坐标命中节点。
- `build_semantic_target_from_node()`：把命中节点转换成稳定 target。
- `load_navigation_graph()` / `save_navigation_graph()`：读写并清理导航图。
- `NavigationGraph`：集中维护加边、同级排序、页面重命名及页面引用。
- `NavigationGraphRepository`：持有工作目录，统一完成 graph 读写、唯一备份及录制期 JSON 引用迁移。
- `execute_device_input()`：统一执行点击、返回和滑动等设备动作。
- `build_page_directory()`：构建页面目录。
- `GraphMaintenance`：继承 `NavigationGraph`，在同一图实例上完成可达性分析、删除计划和删除执行。
- `*Request`：Web API 的三个统一动作入口及重命名请求模型。

页面识别异常时直接检查 `build_navigation_state()`；候选缺失时检查 `extract_navigation_candidates()` 与 `is_recordable_clickable_area()`。这些流程已经并回主函数，不再经过单次转发辅助函数。

### 3.2 Web 后端

`web_nav_server.py` 只保留四组编排逻辑：

1. 当前页面状态与录制会话。
2. 页面跳转、同页变化、手势和弹窗的流程编排。
3. 页面查询、重命名和快速跳转 API。
4. 删除确认、孤儿页维护、备份和设备控制 API。

页面内录制统一经过：

```text
api_record_action()
  -> record_page_operation(mode=popup|same_page|gesture)
```

新增页面内操作模式时，优先扩展 `record_page_operation()` 的参数和差异分支，不要复制完整的采集与保存流程。

弹窗录制使用 `SheetWrapper`、`Dialog`、`MenuWrapper` 三种首批类型。前端选择“采集弹窗-类型”后只处理下一次截图点击，
请求完成（包括手动补充控件描述的重试）即自动退出采集状态。弹窗 `operationxx` 的 `operate` 保存真实动作，
`target.type` 和 `popup_type` 保存所选弹层组件类型；新增类型时同步扩展后端 `POPUP_TYPES` 与前端入口。

动作接口只保留三个聚合入口：

```text
/api/console_action
/api/record_action
/api/delete_action
```

不要再为同一个动作同时增加专用路由、专用请求模型和只转发一层的包装函数。

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
会改变状态的交互使用阻塞式 `api()` / `postJson()`；可并发的只读刷新使用 `queryJson()`。
目录刷新带请求代次校验，较早返回的响应不得覆盖较新的目录。

页面目录的同级拖拽顺序不保存在浏览器本地。拖拽完成后，前端通过 `reorder_children` 控制台动作把完整
同级 `transition_id` 顺序写入 graph 对应 outgoing transitions 的 `priority`；使用 transition ID
可以区分同一父页面下指向同一子页面的多条路径，同时会重排 graph 的 `transitions` 数组中这些边。
页面目录和 DFS 都按
`priority -> transition 原始记录顺序` 排序，因此重新生成 DFS 会沿用前端顺序。

### 3.4 DFS 导出

`DFS.py` 当前只导出路径数据，不连接设备、不执行点击。核心类是 `DfsPathExporter`：

- 从 `Pages_root` 开始；
- 每个可达页面只访问一次；
- 保留 transition 中的全部 steps；
- `page_description` 按到达的页面层级生成，不使用同一跳转中的中间菜单步骤；
- 页面 state 中存在 `dfs_manual` 时，四个 DFS 输出字段全部以人工记录为准；
- 输出页面的 `special_operate`；
- 报告不可达页面。

非法或缺失的 transition `priority` 按 `1000` 兼容处理；任一端 state 不存在的悬空边不会生成虚假页面。

页面节点使用入口上下文命名：根页面固定为 `Pages_root`，新页面按“父页面标题 + `to` + 当前标题”生成，例如从设置进入 WLAN 得到 `Pages_设置_toWLAN`。因此不同父页面下的同名页面会生成不同 `page_name`，不会仅因标题相同而合并。

Web 页面详情中的“DFS 人工维护”用于修正无法可靠识别的标题或路径。人工记录保存在对应 state 的
`dfs_manual` 中，字段严格为 `package_name`、`main_page_name`、`page_description` 和
`path_snapshot`。页面描述与路径互不推导：`menu_grid` 等临时菜单动作应保留在路径中，但不写入页面描述。
保存人工配置时同步更新 state 的 `page_description`；清除人工配置时恢复修改前的值。
当页面只有一个入边时，还会把人工路径最后一步的 `key_description` 和 `step_prompt`
同步到该 transition，避免 DFS 已显示“锁屏”而跳转详情仍显示旧的“7月24日”。
“生成 DFS 精简文件”写入完整的 `settings_navigation_paths.json`，“查看当前页面 DFS 分支”展示
当前页面及其所有可达后继页面的完整根路径。`a-b` 等包含连字符的页面名称属于合法描述，不得过滤。
保存或清除 `dfs_manual` 后必须立即重新导出并覆盖 `settings_navigation_paths.json`，保证导航图、
页面详情与后端精简结果始终一致。

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
  "target": {"key": "settings.wlan", "text": "WLAN"},
  "steps": [
    {
      "operate": "tap",
      "target": {
        "key": "settings.wlan",
        "text": "WLAN",
        "key_description": "WLAN",
        "step_prompt": "WLAN"
      }
    }
  ]
}
```

旧数据只有 `target` 时，页面目录读取时会兼容地转换成单步。

## 5. 常见改动

### 页面标题识别错误

用真实 `current_ui_tree.json` 复现，检查 `build_navigation_state()` 内的标题遍历顺序和稳定文本过滤。不要在 Web 层硬编码具体页面标题。

### 控件无法录制

依次检查：

1. 节点是否可见、可用、可点击；
2. bounds 是否有效；
3. key 或 text 是否稳定；
4. `extract_navigation_candidates()` 是否把节点过滤掉。

只有缺少稳定 key/text 时才要求用户填写 `manual_label`。

### 增加 Web 动作

若动作属于现有控制台或录制类别，直接在 `api_console_action()` 或 `api_record_action()` 中增加分派。只有设备 I/O、递归算法、跨流程复用或明确独立的领域规则才单独保留函数。

### 修改删除规则

删除必须保持两阶段：

1. `dry_run=true` 返回完整删除计划和 `preview_token`；
2. 用户确认后把该令牌随 `dry_run=false` 原样提交；
3. 服务端重新规划并校验令牌，计划未变化时才备份和执行。

预览和执行必须调用同一个 `GraphMaintenance.plan_delete()` 结果生成逻辑。删 state 必然带走的所有边也必须进入计划；普通删除不得隐式删除计划外的孤儿页面或坏引用。

孤儿页面定义为从 `Pages_root` 不可达的 state，包括孤儿链的后继页面和脱离 root 的循环子图。通过 `/api/orphan_pages` 查看，只能用 `target_type=orphan_pages` 显式删除。

## 6. 验证

检查 Python 语法：

```bash
conda run -n hcy-env python -m py_compile \
  web_nav_server.py settings_ui_manual_recorder.py DFS.py
```

运行删除与孤儿识别回归测试：

```bash
conda run -n hcy-env python -m unittest -v test_graph_maintenance.py
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

- `/api/state`、`/api/page_directory`、`/api/page_detail`、`/api/orphan_pages` 和 `/api/graph` 正常；
- 页面详情可保存及清除 `dfs_manual`，重新导出后人工记录优先生效；
- 页面详情可生成精简文件，并能查看当前页面及后继页面的 DFS 分支；
- 目录、详情、顶部状态和 DFS 分支只显示 `page_description` 的页面末级名称，
  不直接展示内部 `Pages_*` ID；
- 修改父页面人工 DFS 后，下级人工记录的路径前缀和 `page_description`
  前缀同步更新；
- DFS 定位器从 `key` 切换为 `text`（或反向切换）时，必须删除旧定位字段；
  下级历史路径即使保留旧类型，也应按共同祖先前缀完成修复；
- 页面目录以嵌套 DOM、缩进和树连接线表达父子层级，不能仅依赖颜色；
- 目录行 hover/focus 不得改变网格行列或高度；低频删除操作放入绝对定位的
  三点菜单，“详情”按钮位置保持不变；
- “展开所选页面”必须展开所选页面的祖先路径和完整子树；“全部收起”同时
  清除目录搜索，避免搜索态强制展开抵消收起操作；
- 页面详情以概览指标和折叠分组控制信息密度，DFS/跳转默认展开，操作、
  变体和续录默认折叠；运行标识属于低频高级字段；
- 采集、截图录制和系统返回属于当前页面变更动作，响应成功后必须清除旧的
  `selectedPage`，由目录刷新加载新的 `active_page` 详情；
- 页面跳转、同页变化、手势、弹窗四种记录仍可写入；
- 删除预览不修改文件，预览与执行计划一致，正式删除会生成备份；
- 普通删除不隐式删除孤儿页，孤儿页删除后列表会刷新；
- 导航图中不存在坐标字段；
- `DFS.py` 能导出有效 JSON。

## 7. 维护原则

- 一个功能只有一个业务入口；前端未使用的兼容路由不保留。
- 共用的采集、命中、差异计算和保存逻辑必须复用。
- 只转发一次且不增加语义的函数应直接合并；有稳定领域含义或被多处复用的函数保留。
- 同一批操作反复传递的状态优先放入对象，例如 `GraphMaintenance.graph`。
- 优先修改现有模块，不因小功能继续拆文件。
- 文档只描述已存在的行为，代码变更后同步更新本文。
