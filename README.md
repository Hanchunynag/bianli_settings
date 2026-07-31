# Settings Navigation Recorder

一个收缩后的设置页面导航录制项目。当前只保留三件事：

1. Web 页面展示和录制设置页面跳转关系。
2. 轻量导航图 `settings_navigation_graph.json`。
3. `DFS.py` 按 DFS 导出紧凑路径数据。

## 文件结构

```text
web_nav_server.py                 Web 控制台服务
settings_ui_manual_recorder.py    UI tree、导航图、clickable 控件提取工具
DFS.py                            DFS 路径导出脚本
templates/nav.html                Web 页面模板
static/nav.css                    页面样式
static/nav.js                     前端入口
static/nav/*.js                   前端模块
requirements.txt                  Python 依赖
demo_settings/                    现有采集数据和输出
```

## 后续开发指南

如果要继续开发新功能，例如录制弹窗、根据录制好的 JSON 自动遍历，请先阅读：

```text
docs/development_guide.md
```

## 启动

先连接设备并确认 `hdc` 可用，然后启动 Web 控制台：

```bash
conda run -n hcy-env python web_nav_server.py \
  --work-dir demo_settings \
  --output-dir demo_settings/outputs/latest \
  --host 127.0.0.1 \
  --port 8020
```

打开：

```text
http://127.0.0.1:8020/
```

## DFS

导出遍历路径：

```bash
conda run -n hcy-env python DFS.py --work-dir demo_settings
```

默认输出到 `demo_settings/outputs/navigation/settings_navigation_paths.json`。
当前脚本只生成 JSON，不连接设备、不执行页面操作。

在 Web 控制台的“页面详情 / 可达页面”中可以维护单页 DFS 数据。
人工配置会独立保存 `page_description` 与完整 `path_snapshot`：例如
`menu_grid` 这类中间点击仍保留在路径中，但不会被自动拼进页面描述。
页面详情还提供“生成 DFS 精简文件”和“查看当前页面 DFS 分支”按钮；
精简结果写入 `demo_settings/outputs/navigation/settings_navigation_paths.json`。
保存或清除人工 DFS 配置时也会自动重新生成并覆盖该文件，无需再手动点击精简按钮。
连字符属于合法页面名称字符，例如 `a-b` 会被完整保留。

Web 前端只展示一个页面名称：取 `page_description` 的最后一个页面段。
例如内部 `page_name=Pages_设置_to关于本机`、完整
`page_description=设置_关于本机`，目录、详情和 DFS 分支统一显示“关于本机”。
`page_name` 仅作为后端状态 ID；“修改内部页面 ID”按钮只修改该 ID，
页面显示名称统一在 DFS 维护区修改。
人工修改某级 DFS 定位器时可以在 `key` 与 `text` 之间切换；保存会删除
旧类型字段，并同步更新所有下级人工路径前缀和自动导出路径。

页面目录使用嵌套树结构展示：父子页面之间有缩进、竖向主干线和横向分支线，
同级页面保持在同一缩进列中，不依赖额外颜色区分层级。
目录行保持固定高度，“详情”始终位于最右侧；删除分支和删除页面收纳在
左侧三点菜单中，打开菜单不会挤压或改变目录布局。
目录标题区提供“展开所选页面”和“全部收起”：前者会展开当前详情页面的
祖先路径及其全部后继分支，并滚动到该页面；后者会清除搜索和所有展开状态。

页面详情使用分组折叠结构。顶部集中展示进入、离开、操作、变体和续录数量；
DFS 与页面跳转默认展开，低频的页面内操作、同页变体和续录默认折叠。
`package_name` 与 `main_page_name` 收纳在“运行标识”高级项中。
采集当前界面、点击截图录制以及系统返回完成后，页面详情会自动切换到后端
返回的当前页面；手动浏览历史页面详情时则不会被普通目录刷新打断。

## 数据原则

正式导航图只保存稳定语义：

```text
from_page
to_page
transition.steps
target.key / target.text / target.description
page_operations
return_policy
```

正式记录不保存设备相关字段：

```text
bounds
bounds_center
screen_size
normalized_center
coordinate_space
```

坐标只允许在执行时从当前 UI tree 临时计算，用完即丢。
