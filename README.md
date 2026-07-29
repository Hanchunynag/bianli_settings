# Settings Navigation Recorder

设置页面导航录制项目，当前只保留四件事：

1. Web 页面展示和录制设置页面跳转关系。
2. 轻量导航图 `settings_navigation_graph.json`。
3. 页面级 DFS 数据人工维护。
4. `DFS.py` 按 DFS 导出紧凑路径数据。

## 文件结构

```text
web_nav_server.py                  Web 控制台服务，包含 DFS 人工维护接口
settings_ui_manual_recorder.py     UI tree、导航图、clickable 控件提取工具
DFS.py                             DFS 路径导出与页面级覆盖规则
static/dfs_manual.js               页面详情中的 DFS 人工维护面板
templates/nav.html                 Web 页面模板
static/nav.css                     页面样式
static/nav.js                      前端入口
static/nav/*.js                    前端模块
requirements.txt                   Python 依赖
```

项目不再生成或提交模拟设置数据。工作目录应指向真实录制数据目录。

## 后续开发指南

继续开发录制、弹窗或自动遍历功能前，请阅读：

```text
docs/development_guide.md
```

## 启动 Web 控制台

```bash
conda run -n hcy-env python web_nav_server.py \
  --work-dir settings_workspace \
  --output-dir settings_workspace/outputs/latest \
  --host 127.0.0.1 \
  --port 8020
```

打开：

```text
http://127.0.0.1:8020/
```

工作目录中需要存在真实采集产生的：

```text
outputs/latest/current_ui_tree.json
outputs/latest/current_screen.png
outputs/navigation/settings_navigation_graph.json
```

## DFS 人工维护

在“页面详情 / 可达页面”中选择页面后，页面底部会出现“DFS 人工维护”板块。编辑内容与最终导出结构一致：

```json
{
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
```

`page_description` 与 `path_snapshot` 独立。`menu_grid` 等中间操作可以留在实际 DFS 点击路径中，但不需要出现在页面描述中。人工数据保存到对应 state 的 `dfs_override`；“清除人工覆盖”会恢复自动生成结果。

保存和清除人工覆盖都会先备份导航图。人工数据只影响最终 DFS 导出，不修改页面父子关系、transition 或实际录制路径。

## DFS 导出

```bash
conda run -n hcy-env python DFS.py --work-dir settings_workspace
```

默认输出到：

```text
settings_workspace/outputs/navigation/settings_navigation_paths.json
```

脚本只生成 JSON，不连接设备、不执行页面操作。

## 数据原则

正式导航图只保存稳定语义：

```text
from_page
to_page
transition.steps
target.key / target.text / target.description
page_operations
return_policy
state.dfs_override
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
