# Settings Navigation Recorder

一个收缩后的设置页面导航录制项目。当前只保留三件事：

1. Web 页面展示和录制设置页面跳转关系。
2. 轻量导航图 `settings_navigation_graph.json`。
3. `DFS.py` 按 DFS 生成/执行遍历。

## 文件结构

```text
web_nav_server.py                 Web 控制台服务
settings_ui_manual_recorder.py    UI tree、导航图、clickable 控件提取工具
DFS.py                            DFS 计划生成与设备执行脚本
create_demo_settings.py           生成模拟设置数据
templates/nav.html                Web 页面模板
static/nav.css                    页面样式
static/nav.js                     前端入口
static/nav/*.js                   前端模块
requirements.txt                  Python 依赖
demo_settings/                    模拟数据和输出
```

## 启动 Demo

```bash
conda run -n hcy-env python create_demo_settings.py
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

只生成遍历计划：

```bash
conda run -n hcy-env python DFS.py --work-dir demo_settings
```

模拟执行完整链路，不碰设备：

```bash
conda run -n hcy-env python DFS.py --work-dir demo_settings --execute --dry-run --step-delay 0
```

真实设备执行：

```bash
conda run -n hcy-env python DFS.py --work-dir demo_settings --execute --device-id 68Q0223918000004
```

默认不会执行页面内操作，例如删除、滑动删除主题等。需要显式加：

```bash
--execute-page-operations
```

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
