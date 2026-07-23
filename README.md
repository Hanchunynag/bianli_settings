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
create_demo_settings.py           生成模拟设置数据
templates/nav.html                Web 页面模板
static/nav.css                    页面样式
static/nav.js                     前端入口
static/nav/*.js                   前端模块
requirements.txt                  Python 依赖
demo_settings/                    模拟数据和输出
```

## 后续开发指南

如果要继续开发新功能，例如录制弹窗、根据录制好的 JSON 自动遍历，请先阅读：

```text
docs/development_guide.md
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

导出遍历路径：

```bash
conda run -n hcy-env python DFS.py --work-dir demo_settings
```

默认输出到 `demo_settings/outputs/navigation/settings_navigation_paths.json`。
当前脚本只生成 JSON，不连接设备、不执行页面操作。

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
