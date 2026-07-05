# Settings Recorder 后续开发指南
这份文档给初学者使用，目标是：知道新增功能应该改哪些文件、按什么顺序改、怎么验证。

## 1. 项目分层
本项目可以分成四层。

### 1.1 UI 采集与语义解析层
文件：`settings_ui_manual_recorder.py`
主要负责：
- 拉取 `current_ui_tree.json`
- 格式化 UI tree JSON
- 识别页面标题
- 生成 `Pages_xxx` 或 `Overlay_xxx`
- 判断当前界面是不是弹窗
- 提取可点击控件
- 读写 `settings_navigation_graph.json`
常见修改场景：
- 页面名识别错了，改 `find_page_title()` 或 `state_name_from_title()`。
- 弹窗识别不到，改 `detect_dialog_root()` 或 `detect_overlay_title()`。
- 按钮没出现在候选控件里，改 `extract_navigation_candidates()`。

### 1.2 Web 后端 API 层
文件：`web_nav_server.py`
主要负责：
- Web 按钮点击后执行什么动作
- 录制普通页面跳转
- 录制同页变化
- 标记弹窗
- 重命名页面
- 快速跳转到页面
- 返回前端需要的数据
常见修改场景：
- 新增一个 Web 功能按钮，通常要在这里新增后端动作。
- 新增一种录制逻辑，通常要在这里新增业务函数。

### 1.3 Web 前端层
文件：
- `templates/nav.html`
- `static/nav.js`
- `static/nav/render.js`
- `static/nav.css`
分工：
- `templates/nav.html`：放按钮和页面结构。
- `static/nav.js`：绑定按钮点击事件。
- `static/nav/render.js`：渲染页面目录、页面详情、提示信息。
- `static/nav.css`：控制布局和样式。

### 1.4 自动遍历层
文件：`DFS.py`
主要负责：
- 读取录制好的 `settings_navigation_graph.json`
- 生成 DFS 遍历计划
- 按计划点击页面
- 校验是否进入目标页面
- 返回父页面
- 执行页面内操作
常见修改场景：
- 想改变遍历顺序，改 `outgoing_map()` 或 `DfsPlanner`。
- 想改变页面校验方式，改 `PageMatcher`。
- 想增强控件匹配，改 `LocatorResolver`。

## 2. 导航图 JSON 基础
录制结果位于：
```text
<work-dir>/outputs/navigation/settings_navigation_graph.json
```
核心结构：
```json
{
  "states": {},
  "transitions": [],
  "traversal_config": {}
}
```
`states` 是页面表：
```json
{
  "Pages_root": {
    "page_name": "Pages_root",
    "last_title": "设置"
  }
}
```
`transitions` 是页面跳转边：
```json
{
  "from_page": "Pages_root",
  "to_page": "Pages_WLAN",
  "operate": "tap",
  "target": {
    "type": "key",
    "value": "settings.wlan",
    "step_prompt": "WLAN"
  }
}
```
`traversal_config` 是遍历配置：
```json
{
  "strategy": "dfs",
  "root_page": "Pages_root",
  "default_return_policy": {
    "type": "system_back"
  }
}
```
最重要原则：
> JSON 里保存稳定语义，不保存坐标；执行时再从当前 UI tree 临时解析坐标。

## 3. 例子一：开发“录制弹窗”
目标：当手机出现弹窗时，把它保存成 overlay state，并能录制弹窗按钮。
例子：
```text
是否允许打开 WLAN？
取消    确定
```
希望保存成：
```json
{
  "page_name": "Overlay_是否允许打开_WLAN",
  "state_type": "overlay",
  "is_overlay": true,
  "overlay_parent": "Pages_WLAN",
  "overlay_title": "是否允许打开 WLAN"
}
```

### 3.1 第一步：改弹窗识别
修改文件：`settings_ui_manual_recorder.py`
重点函数：
- `detect_dialog_root()`
- `detect_overlay_title()`
- `build_navigation_state()`
- `extract_navigation_candidates()`
`detect_dialog_root()` 用来判断有没有弹窗。
如果设备上的弹窗类型不是 `Dialog`，可以增加关键词：
```python
keywords = ["dialog", "popup", "modal", "alert"]
```
判断时可以使用：
```python
any(word in (get_type(n) + get_key(n)).lower() for word in keywords)
```
`detect_overlay_title()` 用来取弹窗标题。
如果标题取不到，可以兜底取弹窗里的第一个稳定文本。
`build_navigation_state()` 用来生成 state。
检测到弹窗时，state 应该包含：
```python
state.update({
    "state_type": "overlay",
    "is_overlay": True,
    "overlay_title": title,
})
```
`extract_navigation_candidates()` 用来提取弹窗按钮。
关键逻辑是：
```python
dialog_root = detect_dialog_root(root)
scope = dialog_root or root
```
检测到弹窗时，只从弹窗范围内提取按钮。
弹窗按钮的 target 建议带：
```json
{
  "scope": "dialog"
}
```

### 3.2 第二步：改 Web 后端
修改文件：`web_nav_server.py`
已有基础接口：
```text
api_mark_current_as_overlay()
```
如果要新增“录制当前弹窗”，可以加：
```python
def record_overlay_state():
    current = read_current_state(capture=True)
    state = current["state"]
    if not state.get("is_overlay"):
        raise ValueError("当前界面不是弹窗")
    graph = load_navigation_graph(config.work_dir)
    graph.setdefault("states", {})[state["page_name"]] = state
    save_navigation_graph(graph, config.work_dir)
    return read_current_state(capture=False)
```
然后在 `api_console_action()` 里加：
```python
if action == "record_overlay":
    return ok_response(**record_overlay_state())
```

### 3.3 第三步：改前端按钮
修改 `templates/nav.html`，在控制台里加按钮：
```html
<button id="recordOverlayBtn" class="secondary">录制当前弹窗</button>
```
修改 `static/nav.js`，绑定按钮：
```javascript
el('recordOverlayBtn').onclick = async () => {
  render(await consoleAction('record_overlay'));
};
```

### 3.4 第四步：验证弹窗录制
启动 Web：
```bash
conda run -n hcy-env python web_nav_server.py \
  --work-dir demo_settings \
  --output-dir demo_settings/outputs/latest \
  --host 127.0.0.1 \
  --port 49344
```
验证流程：
1. 手机打开会弹窗的设置项。
2. 点击“采集当前界面”。
3. 看顶部 `page_name` 是否是 `Overlay_xxx`。
4. 点击“录制当前弹窗”。
5. 打开 `settings_navigation_graph.json`。
6. 确认存在 `is_overlay: true`。
7. 确认弹窗按钮进入候选控件。
如果没识别成弹窗，回到 `detect_dialog_root()`。
如果识别了弹窗但没有按钮，回到 `extract_navigation_candidates()`。

## 4. 例子二：根据 JSON 自动遍历
目标：读取录制好的 JSON，自动从首页进入每个页面，再返回父页面。
主要修改文件：`DFS.py`

### 4.1 第一步：先生成计划
不要一上来真机执行，先生成 DFS 计划：
```bash
conda run -n hcy-env python DFS.py --work-dir demo_settings
```
输出文件：
```text
demo_settings/outputs/navigation/dfs_traversal_plan.json
```
计划里的常见事件：
- `visit_page`
- `enter_transition`
- `return_to_parent`
- `record_page_operations`
- `skip_transition`
- `already_seen_page`

### 4.2 第二步：理解遍历代码
重点类和函数：
- `DfsPlanner`
- `DfsPlanner.build()`
- `DfsPlanner._visit_page()`
- `outgoing_map()`
如果要改遍历顺序，优先改 `outgoing_map()`。
当前常见排序是：
```text
priority -> record_order -> transition_id
```

### 4.3 第三步：加跳过弹窗规则
如果暂时不想自动进入弹窗，可以在 `_visit_page()` 里加：
```python
to_state = self.graph.get("states", {}).get(to_page, {})
if to_state.get("is_overlay"):
    self._append({
        "event": "skip_transition",
        "reason": "skip_overlay",
        "transition_id": transition_id,
        "from_page": page,
        "to_page": to_page,
        "depth": depth,
        **self._snapshot_path(),
    })
    continue
```
也可以放进 JSON 配置：
```json
{
  "traversal_config": {
    "skip_overlay": true
  }
}
```

### 4.4 第四步：理解真实执行链路
真实执行时，不使用旧坐标。
执行链路：
```text
TraversalExecutor._execute_transition()
TraversalExecutor._execute_step()
DeviceDriver.capture_ui_tree()
LocatorResolver.resolve_center()
DeviceDriver.gesture()
```
每一步都会：
1. 拉当前 UI tree。
2. 用 JSON 里的 target 找控件。
3. 临时计算控件中心点。
4. 点击。
5. 校验目标页面。
如果报错 `cannot resolve target on current page`，重点看：
- `LocatorResolver.resolve_center()`
- `LocatorResolver._score_candidate()`
例如要让弹窗按钮优先匹配弹窗内控件，可以加分：
```python
if target.get("scope") == suggested.get("scope"):
    score += 20
```

### 4.5 第五步：dry-run
先模拟执行：
```bash
conda run -n hcy-env python DFS.py \
  --work-dir demo_settings \
  --execute \
  --dry-run \
  --step-delay 0
```

### 4.6 第六步：真机执行
确认计划没问题后：
```bash
conda run -n hcy-env python DFS.py \
  --work-dir demo_settings \
  --execute \
  --device-id 你的设备ID
```
可选参数：
- `--capture-screen`：每步也截图。
- `--execute-page-operations`：执行页面内操作。
默认不执行页面内操作，是为了避免误改系统设置。

## 5. 新功能开发顺序
建议按这个顺序：
1. 涉及 UI tree 识别，先改 `settings_ui_manual_recorder.py`。
2. 涉及 Web 后端动作，再改 `web_nav_server.py`。
3. 需要按钮，再改 `templates/nav.html`。
4. 需要绑定按钮，再改 `static/nav.js`。
5. 需要展示结果，再改 `static/nav/render.js`。
6. 涉及自动跑测试，再改 `DFS.py`。
不要一开始只改前端；前端只是入口，核心逻辑在后端和 UI tree 解析。

## 6. 检查命令
Python 检查：
```bash
python3 -m py_compile web_nav_server.py settings_ui_manual_recorder.py DFS.py create_demo_settings.py
```
前端检查：
```bash
node --check static/nav.js
node --check static/nav/render.js
```
补丁检查：
```bash
git diff --check
```
DFS 计划检查：
```bash
conda run -n hcy-env python DFS.py --work-dir demo_settings
```
DFS dry-run 检查：
```bash
conda run -n hcy-env python DFS.py --work-dir demo_settings --execute --dry-run --step-delay 0
```

## 7. 常见问题定位
页面名错了：
- `find_page_title()`
- `state_name_from_title()`
弹窗识别不到：
- `detect_dialog_root()`
- `detect_overlay_title()`
弹窗按钮没有候选：
- `extract_navigation_candidates()`
- `target_from_node()`
自动遍历找不到控件：
- `LocatorResolver.resolve_center()`
- `LocatorResolver._score_candidate()`
自动遍历进入错误页面：
- `PageMatcher.matches()`
最终原则：
> 录制阶段保存稳定语义，执行阶段重新拉 UI tree 解析坐标。

## 8. 更详细的开发流程：从需求到代码

这一节用更“新手向”的方式说明：当你想新增一个功能时，应该怎么拆。

不要一上来问“我要改哪个文件”。

先把功能拆成 4 个问题：

1. 这个功能是否需要重新理解 UI tree？
2. 这个功能是否需要保存或修改导航图 JSON？
3. 这个功能是否需要 Web 页面上新增按钮？
4. 这个功能是否需要 DFS 自动执行？

如果答案是“需要理解 UI tree”，先改：

```text
settings_ui_manual_recorder.py
```

如果答案是“需要保存 JSON 或执行设备动作”，再改：

```text
web_nav_server.py
```

如果答案是“需要 Web 按钮”，再改：

```text
templates/nav.html
static/nav.js
```

如果答案是“需要显示新数据”，再改：

```text
static/nav/render.js
static/nav.css
```

如果答案是“需要自动遍历时也支持”，最后改：

```text
DFS.py
```

这样做的好处是：

- 不会把前端按钮写好了，但后端没有逻辑。
- 不会把后端逻辑写好了，但 UI tree 根本识别不到目标。
- 不会把坐标写死到 JSON 里。
- 不会破坏已有 Web 录制流程。

## 9. 详细例子：新增“录制弹窗”功能

下面假设你要做一个更完整的弹窗录制功能。

目标不是只“标记当前页为弹窗”，而是做到：

1. 自动识别当前 UI tree 是否是弹窗。
2. 把弹窗保存成 `Overlay_xxx`。
3. 保存弹窗父页面，例如 `overlay_parent: Pages_WLAN`。
4. 提取弹窗里的按钮，比如“取消”“确定”。
5. 点击弹窗按钮后，可以记录弹窗关闭、跳转或同页变化。
6. 以后 DFS 自动遍历时，可以选择跳过弹窗或处理弹窗。

### 9.1 先观察 UI tree

第一步不要写代码，先采集一次弹窗 UI tree。

在 Web 页面点击：

```text
采集当前界面
```

然后打开：

```text
demo_settings/outputs/latest/current_ui_tree.json
```

搜索弹窗标题，例如：

```text
是否允许
确定
取消
```

你要观察三件事：

1. 弹窗根节点的 `type` 是什么。
2. 弹窗根节点或子节点的 `key` 里有没有稳定字段。
3. “确定”“取消”这些按钮是否有 `clickable: true`。

如果按钮没有 `clickable: true`，就要看它的父节点是否 clickable。

### 9.2 修改 detect_dialog_root

打开：

```text
settings_ui_manual_recorder.py
```

找到：

```text
detect_dialog_root()
```

这个函数的职责是：

> 从整棵 UI tree 中找到“最像弹窗根节点”的节点。

一个比较稳的思路是：

1. 优先找 type/key 中包含 `dialog` 的节点。
2. 再找包含 `popup`、`modal`、`alert` 的节点。
3. 如果多个候选都存在，选择面积最大的可见节点。
4. 不要把整个屏幕根节点当弹窗。

伪代码如下：

```python
def detect_dialog_root(root):
    keywords = ["dialog", "popup", "modal", "alert"]
    candidates = []
    for node, _, _ in walk(root):
        text = (get_type(node) + get_key(node)).lower()
        if not is_visible(node):
            continue
        if any(word in text for word in keywords):
            rect = parse_rect(get_attr(node, "bounds"))
            if rect["valid"] and rect["area"] > 0:
                candidates.append(node)
    if not candidates:
        return None
    return max(candidates, key=lambda n: parse_rect(get_attr(n, "bounds"))["area"])
```

注意：

- 右上角更多菜单不一定要当弹窗。
- Toast 一般不建议当页面，因为它很快消失。
- 权限确认框、删除确认框、选择方式弹窗，更适合当 overlay。

### 9.3 修改 detect_overlay_title

找到：

```text
detect_overlay_title()
```

这个函数的职责是：

> 从弹窗节点里取一个稳定标题。

优先级建议：

1. 弹窗标题节点。
2. 弹窗里第一条稳定文本。
3. 弹窗按钮以外的最长文本。
4. 实在没有，就返回 `未知弹窗`。

伪代码：

```python
def detect_overlay_title(root):
    dialog = detect_dialog_root(root)
    if not dialog:
        return ""
    title = nearest_label(dialog)
    if title:
        return title
    texts = [t for t in meaningful_texts(dialog) if is_stable_text_for_navigation(t)]
    button_words = {"确定", "取消", "允许", "不允许", "关闭"}
    texts = [t for t in texts if t not in button_words]
    return texts[0] if texts else "未知弹窗"
```

这里要注意：

如果标题取成了“确定”，页面名就可能变成：

```text
Overlay_确定
```

这通常不是你想要的。

### 9.4 修改 build_navigation_state

找到：

```text
build_navigation_state()
```

这个函数的职责是：

> 把当前 UI tree 转成一个页面状态 state。

普通页面应该生成：

```json
{
  "page_name": "Pages_WLAN",
  "page_description": "WLAN",
  "last_title": "WLAN"
}
```

弹窗页面应该生成：

```json
{
  "page_name": "Overlay_是否允许打开_WLAN",
  "page_description": "弹窗：是否允许打开 WLAN",
  "last_title": "是否允许打开 WLAN",
  "state_type": "overlay",
  "is_overlay": true,
  "overlay_title": "是否允许打开 WLAN"
}
```

关键逻辑是：

```python
dialog_root = detect_dialog_root(root)
scope = dialog_root or root
overlay_title = detect_overlay_title(root) if dialog_root else ""
title = overlay_title or page.get("title") or nearest_label(scope)
page_name = state_name_from_title(title, overlay=bool(dialog_root))
```

如果 `overlay=True`，`state_name_from_title()` 应该生成：

```text
Overlay_xxx
```

如果 `overlay=False`，则生成：

```text
Pages_xxx
```

### 9.5 修改 extract_navigation_candidates

找到：

```text
extract_navigation_candidates()
```

这个函数的职责是：

> 从当前页面中提取可录制的点击目标。

弹窗场景最重要的代码是：

```python
dialog_root = detect_dialog_root(root)
scope = dialog_root or root
```

这表示：

- 没有弹窗时，从整个页面提取候选控件。
- 有弹窗时，只从弹窗内部提取候选控件。

这样可以避免弹窗后面的页面按钮被误点。

然后看：

```text
target_from_node(node, dialog=bool(dialog_root))
```

如果是弹窗里的按钮，target 应该带：

```json
{
  "scope": "dialog"
}
```

这个字段后续在 DFS 自动匹配时很有用。

例如普通按钮：

```json
{
  "type": "text",
  "value": "确定",
  "step_prompt": "确定"
}
```

弹窗按钮：

```json
{
  "type": "text",
  "value": "确定",
  "step_prompt": "确定",
  "scope": "dialog"
}
```

### 9.6 后端新增 record_overlay_state

打开：

```text
web_nav_server.py
```

建议新增一个函数：

```python
def record_overlay_state() -> Dict[str, Any]:
    current = read_current_state(capture=True)
    state = current["state"]
    if not state.get("is_overlay"):
        raise ValueError("当前界面不是弹窗")
    graph = load_navigation_graph(config.work_dir)
    parent = current.get("active_page") or ""
    state["overlay_parent"] = parent
    graph.setdefault("states", {})[state["page_name"]] = state
    save_navigation_graph(graph, config.work_dir)
    return read_current_state(capture=False)
```

这个函数做 5 件事：

1. 重新采集当前界面。
2. 判断当前界面是不是弹窗。
3. 找到当前 active page 作为父页面。
4. 把弹窗 state 写入 graph。
5. 返回最新状态给前端。

注意：

`overlay_parent` 很重要。
没有它，后续你很难知道这个弹窗属于哪个页面。

### 9.7 在 console_action 中注册动作

仍然在：

```text
web_nav_server.py
```

找到：

```text
api_console_action()
```

加入：

```python
if action == "record_overlay":
    return ok_response(**record_overlay_state())
```

这样前端就可以统一调用：

```text
/api/console_action
```

不建议为每个新按钮都单独新建一个 API。
同类动作放在统一接口里更清楚。

### 9.8 前端新增按钮

打开：

```text
templates/nav.html
```

在操作控制台里加：

```html
<button id="recordOverlayBtn" class="secondary">录制当前弹窗</button>
```

再打开：

```text
static/nav.js
```

在 `bindCommandButtons()` 里加：

```javascript
el('recordOverlayBtn').onclick = async () => {
  render(await consoleAction('record_overlay'));
};
```

这里的 `consoleAction()` 本质上会调用：

```text
/api/console_action
```

payload 为空，因为录制当前弹窗不需要额外参数。

### 9.9 弹窗按钮点击后的三种结果

弹窗按钮点击后，通常有三种情况。

第一种：弹窗关闭，回到父页面。

例如点击：

```text
取消
```

结果：

```text
Overlay_xxx -> Pages_WLAN
```

这种可以记录成一个 transition：

```json
{
  "from_page": "Overlay_确认打开_WLAN",
  "to_page": "Pages_WLAN",
  "operate": "tap",
  "target": {
    "type": "text",
    "value": "取消",
    "scope": "dialog"
  }
}
```

第二种：弹窗关闭，并进入新页面。

例如点击：

```text
确定
```

结果：

```text
Overlay_xxx -> Pages_某个新页面
```

这也应该记录成 transition。

第三种：弹窗不关闭，只是内容变化。

例如点击：

```text
更多选项
```

弹窗内部出现更多按钮。

这种更像“同页状态变体”，可以参考当前项目里的：

```text
record_same_page_operation()
page_variants
```

不要把每一次弹窗内部内容变化都强行当成新页面。
如果弹窗标题没变、框架没变，只是内部控件变化，更适合记录成同页变体。

### 9.10 弹窗录制后的 JSON 检查

录制后打开：

```text
demo_settings/outputs/navigation/settings_navigation_graph.json
```

检查 `states`：

```json
{
  "Overlay_确认打开_WLAN": {
    "page_name": "Overlay_确认打开_WLAN",
    "state_type": "overlay",
    "is_overlay": true,
    "overlay_parent": "Pages_WLAN",
    "overlay_title": "确认打开 WLAN",
    "last_title": "确认打开 WLAN"
  }
}
```

检查 `transitions`：

```json
{
  "from_page": "Pages_WLAN",
  "to_page": "Overlay_确认打开_WLAN",
  "target": {
    "step_prompt": "WLAN 开关"
  }
}
```

如果你还记录了弹窗按钮，则应该还有：

```json
{
  "from_page": "Overlay_确认打开_WLAN",
  "to_page": "Pages_WLAN",
  "target": {
    "step_prompt": "取消",
    "scope": "dialog"
  }
}
```

### 9.11 弹窗录制常见错误

错误一：弹窗标题识别成“确定”。

解决：

```text
detect_overlay_title()
```

过滤按钮文本。

错误二：弹窗出现后，候选控件还是背景页面里的按钮。

解决：

```text
extract_navigation_candidates()
```

确认有弹窗时使用 `scope = dialog_root`。

错误三：弹窗被当成普通页面 `Pages_xxx`。

解决：

```text
state_name_from_title(title, overlay=True)
```

确认 `overlay=True` 时生成 `Overlay_xxx`。

## 10. 详细例子：根据 JSON 实现自动化遍历

现在讲第二个功能：根据录制好的 JSON 自动遍历。

目标：

> 从 `Pages_root` 开始，按照 `settings_navigation_graph.json` 自动点击每条 transition，进入子页面后再返回父页面。

主要文件：

```text
DFS.py
```

不要一开始就真机执行。
先生成计划，再 dry-run，最后真机执行。

### 10.1 先生成 DFS 计划

命令：

```bash
conda run -n hcy-env python DFS.py --work-dir demo_settings
```

输出：

```text
demo_settings/outputs/navigation/dfs_traversal_plan.json
```

计划文件里最重要的是：

```json
{
  "events": []
}
```

每个 event 表示一个动作。

常见 event：

```text
visit_page
enter_transition
return_to_parent
record_page_operations
skip_transition
already_seen_page
```

你可以先只看文本输出。
确认顺序合理后，再看 JSON 细节。

### 10.2 DfsPlanner 负责生成计划

打开：

```text
DFS.py
```

重点看：

```text
DfsPlanner
DfsPlanner.build()
DfsPlanner._visit_page()
outgoing_map()
```

它的逻辑是：

1. 从 root 页面开始。
2. 记录一个 `visit_page`。
3. 找到当前页面所有 outgoing transitions。
4. 对每条 transition 记录 `enter_transition`。
5. 递归访问子页面。
6. 子页面结束后记录 `return_to_parent`。

如果你想改变遍历顺序，先看：

```text
outgoing_map()
```

当前排序一般按：

```text
priority -> record_order -> transition_id
```

### 10.3 增加“跳过弹窗”规则

有些时候你不希望 DFS 自动进入弹窗。

比如权限弹窗、删除确认弹窗，可能会改变系统状态。

可以在 graph 里加配置：

```json
{
  "traversal_config": {
    "skip_overlay": true
  }
}
```

然后在 `DfsPlanner._visit_page()` 中读取：

```python
skip_overlay = bool(self.graph.get("traversal_config", {}).get("skip_overlay"))
```

遍历 transition 时加判断：

```python
to_state = self.graph.get("states", {}).get(to_page, {})
if skip_overlay and to_state.get("is_overlay"):
    self._append({
        "event": "skip_transition",
        "reason": "skip_overlay",
        "transition_id": transition_id,
        "from_page": page,
        "to_page": to_page,
        "depth": depth,
        **self._snapshot_path(),
    })
    continue
```

这样计划里会保留“我跳过了什么”，而不是静默忽略。

### 10.4 增加“最大深度”规则

如果设置页面很多，你可能只想先遍历前两层。

可以在 graph 中加：

```json
{
  "traversal_config": {
    "max_depth": 2
  }
}
```

然后在 `_visit_page()` 开头判断：

```python
max_depth = self.graph.get("traversal_config", {}).get("max_depth")
if max_depth is not None and depth > int(max_depth):
    return
```

更推荐的写法是在准备进入子页面前判断：

```python
if max_depth is not None and depth + 1 > int(max_depth):
    self._append({
        "event": "skip_transition",
        "reason": "max_depth",
        "transition_id": transition_id,
        "from_page": page,
        "to_page": to_page,
        "depth": depth,
        **self._snapshot_path(),
    })
    continue
```

这样计划更清楚。

### 10.5 真实执行链路

DFS 真机执行不是直接读坐标。

它每一步都会重新拉当前 UI tree。

执行链路：

```text
TraversalExecutor._execute_transition()
TraversalExecutor._execute_step()
DeviceDriver.capture_ui_tree()
LocatorResolver.resolve_center()
DeviceDriver.gesture()
```

含义：

1. `_execute_transition()` 准备执行一条页面跳转。
2. `_execute_step()` 执行 transition 里的一个 step。
3. `capture_ui_tree()` 重新拉当前 UI tree。
4. `resolve_center()` 用 target 匹配当前控件。
5. `gesture()` 临时点击解析出来的中心点。

注意：

> `settings_navigation_graph.json` 中不应该保存坐标。

坐标只能在执行时从当前 UI tree 算出来。

### 10.6 LocatorResolver 负责找控件

重点看：

```text
LocatorResolver.resolve_center()
LocatorResolver._score_candidate()
LocatorResolver._resolve_from_raw_nodes()
```

`resolve_center()` 的目标是：

> 把 JSON 里保存的 target，匹配到当前 UI tree 中的一个真实控件。

例如 JSON 里有：

```json
{
  "type": "key",
  "value": "settings.wlan",
  "key": "settings.wlan",
  "step_prompt": "WLAN"
}
```

当前 UI tree 中如果也有 key 为 `settings.wlan` 的控件，就能匹配。

如果没有 key，就会尝试用 text、key_description、step_prompt 匹配。

### 10.7 给弹窗控件加匹配分数

如果你给弹窗按钮加了：

```json
{
  "scope": "dialog"
}
```

那么 DFS 匹配时也应该利用这个字段。

可以在 `_score_candidate()` 中加：

```python
suggested = candidate.get("suggested_target") or {}
if target.get("scope") and target.get("scope") == suggested.get("scope"):
    score += 20
```

这样弹窗按钮会优先匹配弹窗里的候选。

如果不加这个逻辑，背景页面里同名按钮可能干扰匹配。

### 10.8 PageMatcher 负责校验页面

重点看：

```text
PageMatcher.matches()
```

它判断“当前页面是不是我期望的页面”。

当前常见校验方式：

1. `page_name` 相等。
2. 页面标题相等。
3. 当前文本里包含期望标题。
4. 页面 signature 文本有交集。

如果自动遍历经常进入错误页面，需要增强这里。

例如两个页面标题相同，但内容不同，就要增加 signature 校验。

可以在 state 中保存更多稳定文本：

```json
{
  "signature": {
    "title": "WLAN",
    "texts_any": ["WLAN", "已保存的网络", "更多设置"]
  }
}
```

然后在 `PageMatcher.matches()` 中要求至少命中两个文本。

### 10.9 dry-run 执行

真机执行前，先 dry-run。

命令：

```bash
conda run -n hcy-env python DFS.py \
  --work-dir demo_settings \
  --execute \
  --dry-run \
  --step-delay 0
```

dry-run 不会真的点手机。

它主要帮你检查：

- DFS 计划是否能正常生成。
- event 顺序是否合理。
- return_to_parent 是否存在。
- page_operations 是否被默认跳过。

如果 dry-run 输出就不合理，不要真机执行。

### 10.10 真机执行

确认 dry-run 没问题后，再执行：

```bash
conda run -n hcy-env python DFS.py \
  --work-dir demo_settings \
  --execute \
  --device-id 你的设备ID
```

常用参数：

```text
--capture-screen
```

表示每一步也保存截图。

```text
--execute-page-operations
```

表示执行页面内操作，例如开关、滑动、同页变化。

默认不执行页面内操作，是为了避免误改系统设置。

```text
--no-verify
```

表示不校验页面。

这个参数一般不建议用。
除非你正在调试点击动作，而页面识别逻辑暂时不稳定。

### 10.11 自动遍历失败时看哪里

运行失败后先看：

```text
demo_settings/outputs/navigation/dfs_runtime_session.json
```

这个文件会记录：

- 已完成哪些 event。
- 最后执行到哪个 event。
- 失败原因。
- 是否 dry-run。

如果报错是：

```text
cannot resolve target on current page
```

优先看：

```text
LocatorResolver.resolve_center()
LocatorResolver._score_candidate()
```

如果报错是：

```text
page verification failed
```

优先看：

```text
PageMatcher.matches()
build_navigation_state()
find_page_title()
```

如果报错是设备命令失败，优先检查：

```bash
hdc list targets
hdc version
```

## 11. 常见新功能模板

以后你要加新功能，可以按这个模板写需求。

```text
我要新增功能：xxx

目标：
1. Web 页面新增按钮 xxx
2. 点击后调用 /api/console_action action=xxx
3. 后端读取当前 UI tree
4. 后端修改 settings_navigation_graph.json
5. 前端显示执行结果
6. DFS 自动遍历时也支持这个字段

需要修改：
1. settings_ui_manual_recorder.py：新增识别逻辑
2. web_nav_server.py：新增后端动作
3. templates/nav.html：新增按钮
4. static/nav.js：绑定按钮事件
5. static/nav/render.js：展示结果
6. DFS.py：支持自动执行
```

如果功能不需要 UI tree 识别，就可以跳过第 1 步。

如果功能不需要自动遍历，就可以跳过第 6 步。

## 12. 修改后必须跑的检查

Python 语法检查：

```bash
python3 -m py_compile web_nav_server.py settings_ui_manual_recorder.py DFS.py create_demo_settings.py
```

前端 JS 检查：

```bash
node --check static/nav.js
node --check static/nav/render.js
```

补丁空白检查：

```bash
git diff --check
```

Web 启动检查：

```bash
conda run -n hcy-env python web_nav_server.py \
  --work-dir demo_settings \
  --output-dir demo_settings/outputs/latest \
  --host 127.0.0.1 \
  --port 49344
```

DFS 计划检查：

```bash
conda run -n hcy-env python DFS.py --work-dir demo_settings
```

DFS dry-run 检查：

```bash
conda run -n hcy-env python DFS.py \
  --work-dir demo_settings \
  --execute \
  --dry-run \
  --step-delay 0
```

## 13. 最终原则

第一，录制阶段保存稳定语义。

例如：

```json
{
  "type": "key",
  "value": "settings.wlan",
  "step_prompt": "WLAN"
}
```

第二，不要把坐标保存到正式导航图。

不要在 `settings_navigation_graph.json` 中长期保存：

```text
bounds
bounds_center
screen_size
normalized_center
coordinate_space
```

第三，执行阶段重新拉 UI tree。

每次点击前重新解析控件位置。

第四，页面识别错时先修识别，不要靠前端绕过去。

第五，自动遍历失败时先看计划，再看执行会话，再看 UI tree。

一句话总结：

> Web 负责录制，JSON 负责保存稳定语义，DFS 负责执行，坐标永远临时计算。

## 14. 源码地图：从页面按钮到 JSON 保存

这一节专门解释“点一下 Web 页面按钮，代码到底怎么走”。

如果你后续开发不知道该改哪里，先按这个调用链找。

### 14.1 点击“采集当前界面”的调用链

前端按钮在：

```text
templates/nav.html
```

按钮：

```html
<button id="captureBtn" class="primary">采集当前界面</button>
```

前端事件绑定在：

```text
static/nav.js
```

代码逻辑：

```javascript
el('captureBtn').onclick = async () => {
  render(await consoleAction('capture_current'));
};
```

这里的 `consoleAction('capture_current')` 会调用：

```text
POST /api/console_action
```

后端入口在：

```text
web_nav_server.py
api_console_action()
```

对应分支：

```python
if action == "capture_current":
    return api_capture()
```

`api_capture()` 会调用：

```python
read_current_state(capture=True)
```

`read_current_state(capture=True)` 做这些事：

1. 调用 `capture_artifacts()` 拉取截图和 UI tree。
2. 读取 `current_ui_tree.json`。
3. 调用 `annotate(root_json)` 给 UI tree 增加内部辅助字段。
4. 调用 `build_navigation_state(root_json)` 识别当前页面。
5. 调用 `extract_navigation_candidates(root_json)` 提取可点击候选。
6. 把 state 和候选控件写入 navigation graph。
7. 返回前端需要的页面状态、候选控件、截图 URL。

底层采集函数在：

```text
settings_ui_manual_recorder.py
capture_artifacts()
```

它负责：

- `hdc shell uitest dumpLayout`
- `hdc file recv current_ui_tree.json`
- 格式化 JSON
- `hdc shell uitest screenCap`
- `hdc file recv current_screen.png`

所以如果“采集当前界面”失败，你按这个顺序查：

1. `hdc version` 是否正常。
2. 设备是否授权。
3. `outputs/latest/current_ui_tree.json` 是否生成。
4. `outputs/latest/current_screen.png` 是否生成。
5. JSON 是否能被 Python 正常读取。

### 14.2 点击截图录制跳转的调用链

截图点击事件在：

```text
static/nav.js
bindScreenRecorder()
```

核心逻辑：

```javascript
el('screen').addEventListener('click', async (event) => {
  const x = ...
  const y = ...
  const action = ...
  let data = await recordAction(action, payload);
  render(data);
});
```

这里会把浏览器里的点击位置换算成手机屏幕坐标。

根据当前模式不同，action 可能是：

```text
tap_point
same_page_tap
same_page_gesture
```

这些 action 统一发到：

```text
POST /api/record_action
```

后端入口：

```text
web_nav_server.py
api_record_action()
```

普通页面跳转走：

```python
record_tap_at_point()
```

同页点击变化走：

```python
record_same_page_operation()
```

同页手势走：

```python
record_page_gesture_operation()
```

### 14.3 record_tap_at_point 做了什么

这个函数是普通跳转录制的核心。

位置：

```text
web_nav_server.py
record_tap_at_point()
```

它的流程：

1. 采集点击前页面。
2. 用 `hit_test_full_ui_tree()` 找到你点中的 UI 节点。
3. 用 `build_semantic_target_from_node()` 把节点转成稳定 target。
4. 如果节点没有稳定 key/text，就要求用户输入 manual label。
5. 真机点击这个位置。
6. 等待页面变化。
7. 采集点击后页面。
8. 判断点击前后是不是同一个页面。
9. 如果进入新页面，就写入一条 transition。
10. 如果仍在同页面，就进入 pending chain 或提示使用同页变化模式。

最关键的点：

```python
target = build_semantic_target_from_node(hit, manual_label=manual_label.strip())
```

target 是真正写进 JSON 的东西。

坐标不会写进正式 graph。

### 14.4 transition 是怎么保存的

当点击后进入新页面，会生成：

```python
transition = {
    "transition_id": tid,
    "from_page": from_page,
    "to_page": to_page,
    "operate": "tap",
    "target": ...,
    "steps": steps,
}
```

然后调用：

```python
add_transition(graph, transition)
save_navigation_graph(graph, config.work_dir)
```

`save_navigation_graph()` 在：

```text
settings_ui_manual_recorder.py
```

它会写入：

```text
outputs/navigation/settings_navigation_graph.json
```

如果你要给 transition 增加新字段，比如：

```json
{
  "risk_level": "safe",
  "test_tags": ["network", "basic"]
}
```

应该在 `record_tap_at_point()` 生成 transition 时加。

如果 DFS 也要使用这个字段，再去 `DFS.py` 读取。

## 15. 录制弹窗：完整实现清单

这一节把“录制弹窗”拆成可执行任务单。

你可以按顺序一项一项做。

### 15.1 任务 A：确认弹窗 UI tree 特征

先不要写代码。

操作：

1. 手机打开会弹窗的页面。
2. 点击 Web 控制台“采集当前界面”。
3. 打开 `outputs/latest/current_ui_tree.json`。
4. 搜索弹窗标题、确定按钮、取消按钮。

你要记录这些信息：

```text
弹窗标题文本：
弹窗根节点 type：
弹窗根节点 key：
确定按钮 type：
确定按钮 key：
确定按钮 clickable：
取消按钮 type：
取消按钮 key：
取消按钮 clickable：
```

如果按钮本身不是 clickable，继续看父节点。

有些 UI tree 是这样的：

```text
Text("确定") 不可点击
父节点 Button 可点击
```

这时 hit-test 和候选提取要能找到父节点。

### 15.2 任务 B：增强 detect_dialog_root

文件：

```text
settings_ui_manual_recorder.py
```

目标：

> 能从 UI tree 中找到弹窗根节点。

建议实现规则：

1. 只考虑 visible 节点。
2. type/key 命中弹窗关键词。
3. bounds 面积有效。
4. 面积不能接近整屏。
5. 多个候选时选面积最大的。

示例代码骨架：

```python
def detect_dialog_root(root: Node) -> Optional[Node]:
    keywords = ["dialog", "popup", "modal", "alert"]
    candidates = []
    screen = screen_metrics_from_root(root).get("screen_size") or [0, 0]
    screen_area = int(screen[0] or 0) * int(screen[1] or 0)
    for node, _, _ in walk(root):
        if not is_visible(node):
            continue
        marker = (get_type(node) + get_key(node)).lower()
        if not any(word in marker for word in keywords):
            continue
        rect = parse_rect(get_attr(node, "bounds"))
        if not rect["valid"] or rect["area"] <= 0:
            continue
        if screen_area and rect["area"] > screen_area * 0.9:
            continue
        candidates.append(node)
    return max(candidates, key=lambda n: parse_rect(get_attr(n, "bounds"))["area"]) if candidates else None
```

注意：

这个只是示例骨架。
实际写入时要和当前文件已有函数风格保持一致。

### 15.3 任务 C：增强 detect_overlay_title

文件：

```text
settings_ui_manual_recorder.py
```

目标：

> 弹窗标题应该是“确认打开 WLAN”，而不是“确定”。

建议过滤这些按钮词：

```python
button_words = {
    "确定", "取消", "允许", "不允许", "关闭",
    "完成", "保存", "删除", "继续", "返回"
}
```

示例代码骨架：

```python
def detect_overlay_title(root: Node) -> str:
    dialog = detect_dialog_root(root)
    if not dialog:
        return ""
    title = nearest_label(dialog)
    if title and title not in button_words:
        return title
    texts = [t for t in meaningful_texts(dialog) if is_stable_text_for_navigation(t)]
    texts = [t for t in texts if t not in button_words]
    if texts:
        return max(texts, key=len)
    return "未知弹窗"
```

为什么选最长文本？

因为弹窗标题或正文通常比“确定”“取消”更长。

但不是所有场景都适用。
如果最长文本是大段说明，也可以改成第一条稳定文本。

### 15.4 任务 D：保存 overlay_parent

文件：

```text
web_nav_server.py
```

目标：

> 保存弹窗属于哪个父页面。

建议在 `record_overlay_state()` 或 `api_mark_current_as_overlay()` 中写：

```python
parent = current.get("active_page") or pending.get("from_page") or ""
state_entry.update({
    "overlay_parent": parent,
})
```

如果当前 active_page 本身就是 overlay，父页面可以从已有 state 里取：

```python
parent = state_entry.get("overlay_parent") or current.get("active_page") or ""
```

这里要小心：

不要把弹窗自己的 page_name 当成父页面。

### 15.5 任务 E：弹窗按钮录制

弹窗按钮本质上还是 target。

如果点击弹窗按钮后进入新页面，就保存 transition。

如果点击后回到父页面，也保存 transition。

如果点击后仍在弹窗内，只是内容变化，就保存 same-page operation 或 page variant。

建议先支持前两种：

```text
Overlay_xxx -> Pages_parent
Overlay_xxx -> Pages_new
```

第三种后面再做。

### 15.6 任务 F：前端显示弹窗信息

文件：

```text
static/nav/render.js
```

在页面详情里，如果 state 是 overlay，可以显示：

```text
类型：弹窗
父页面：Pages_WLAN
弹窗标题：确认打开 WLAN
```

伪代码：

```javascript
if (data.state?.is_overlay) {
  box.insertAdjacentHTML('beforeend', `
    <div class="muted">类型：弹窗</div>
    <div class="muted">父页面：${escapeHtml(data.state.overlay_parent || '-')}</div>
  `);
}
```

这样你调试时会更清楚。

## 16. 自动遍历：完整实现清单

这一节把“根据录制好的 JSON 自动遍历”拆成可执行任务。

### 16.1 任务 A：确认 graph 是否可遍历

先打开：

```text
outputs/navigation/settings_navigation_graph.json
```

检查三件事：

第一，是否有 root：

```json
{
  "Pages_root": {
    "page_name": "Pages_root"
  }
}
```

第二，是否有 transitions：

```json
{
  "from_page": "Pages_root",
  "to_page": "Pages_WLAN"
}
```

第三，每条 transition 是否有稳定 target：

```json
{
  "target": {
    "type": "key",
    "value": "settings.wlan",
    "step_prompt": "WLAN"
  }
}
```

如果 target 只有人工描述，没有 key/text，自动执行成功率会下降。

### 16.2 任务 B：生成 plan

运行：

```bash
conda run -n hcy-env python DFS.py --work-dir demo_settings
```

打开：

```text
outputs/navigation/dfs_traversal_plan.json
```

检查：

```json
{
  "summary": {
    "visited_page_count": 10,
    "page_count": 10
  }
}
```

如果 `unreachable_pages` 里有页面，说明这些页面没有从 root 连通。

常见原因：

- 手动进入页面后直接采集，没有录制父页面入口。
- 删除 transition 后页面变成孤儿。
- 页面名改了，但 transition 没同步改。

### 16.3 任务 C：看 enter_transition 是否完整

每个进入页面的动作应该长这样：

```json
{
  "event": "enter_transition",
  "from_page": "Pages_root",
  "to_page": "Pages_WLAN",
  "steps": [
    {
      "operate": "tap",
      "target": {
        "type": "key",
        "value": "settings.wlan"
      }
    }
  ]
}
```

如果 `steps` 为空，DFS 不知道该点什么。

这时要回到录制阶段重新录 transition。

### 16.4 任务 D：确认 return_to_parent

进入子页面后，应该有返回事件：

```json
{
  "event": "return_to_parent",
  "from_page": "Pages_WLAN",
  "to_page": "Pages_root",
  "return_policy": {
    "type": "system_back"
  }
}
```

默认返回策略来自：

```json
{
  "traversal_config": {
    "default_return_policy": {
      "type": "system_back"
    }
  }
}
```

如果某个页面不能用系统返回键返回，可以给这个 state 单独加：

```json
{
  "return_policy": {
    "type": "tap",
    "target": {
      "type": "text",
      "value": "返回"
    }
  }
}
```

或者多步骤返回：

```json
{
  "return_policy": {
    "type": "steps",
    "steps": [
      {
        "operate": "tap",
        "target": {
          "type": "text",
          "value": "完成"
        }
      },
      {
        "operate": "tap",
        "target": {
          "type": "text",
          "value": "返回"
        }
      }
    ]
  }
}
```

### 16.5 任务 E：dry-run 验证计划

运行：

```bash
conda run -n hcy-env python DFS.py \
  --work-dir demo_settings \
  --execute \
  --dry-run \
  --step-delay 0
```

dry-run 主要看输出顺序。

你要确认：

1. 先访问 root。
2. 再进入子页面。
3. 子页面结束后返回 root。
4. 不会反复进入同一个 transition。
5. 页面内操作默认被跳过。

dry-run 不是完整真机验证。
它不保证控件一定能在真实设备上找到。

### 16.6 任务 F：真机执行前准备

先确认设备：

```bash
hdc list targets
```

确认能拉 UI tree：

```bash
hdc shell uitest dumpLayout -p /data/local/tmp/current_ui_tree.json
```

确认能点击：

```bash
hdc shell uitest uiInput click 100 100
```

如果这些命令失败，先不要跑 DFS。

DFS 不是设备连接修复工具。

### 16.7 任务 G：真机执行

运行：

```bash
conda run -n hcy-env python DFS.py \
  --work-dir demo_settings \
  --execute \
  --device-id 你的设备ID
```

如果你想慢一点，便于观察：

```bash
--step-delay 1.5
```

如果你想保存每步截图：

```bash
--capture-screen
```

如果你想跳过页面校验：

```bash
--no-verify
```

不建议长期使用 `--no-verify`。
它会掩盖页面识别问题。

## 17. 新增 JSON 字段时怎么设计

后续开发经常需要给 graph 增加字段。

不要随便把字段塞进去。

先问三个问题：

1. 这个字段是稳定语义吗？
2. DFS 执行时是否需要它？
3. Web 页面是否需要展示它？

### 17.1 可以长期保存的字段

推荐保存：

```text
page_name
page_description
last_title
state_type
is_overlay
overlay_parent
overlay_title
signature
from_page
to_page
operate
target
steps
return_policy
page_operations
page_variants
traversal_config
```

这些字段和设备分辨率无关，比较稳定。

### 17.2 不建议长期保存的字段

不推荐保存：

```text
bounds
bounds_center
screen_size
normalized_center
coordinate_space
pixel_ratio
```

原因：

这些字段依赖设备、分辨率、字体大小、系统版本。

换一台手机就可能失效。

如果为了调试临时保存，可以放到 debug 日志，不要放进正式 graph。

### 17.3 新字段应该放在哪里

如果字段描述页面，放到 state：

```json
{
  "states": {
    "Pages_WLAN": {
      "risk_level": "safe"
    }
  }
}
```

如果字段描述页面跳转，放到 transition：

```json
{
  "transitions": [
    {
      "transition_id": "root_to_wlan",
      "test_tags": ["network"]
    }
  ]
}
```

如果字段描述遍历策略，放到 traversal_config：

```json
{
  "traversal_config": {
    "skip_overlay": true,
    "max_depth": 3
  }
}
```

如果字段描述同页操作，放到 page_operations 或 page_variants。

### 17.4 新字段要不要写兼容逻辑

要。

因为旧 JSON 里没有你的新字段。

读取时要用：

```python
value = state.get("new_field") or default_value
```

不要直接写：

```python
value = state["new_field"]
```

否则旧数据会直接报错。

### 17.5 保存前要不要清理字段

要看字段类型。

本项目已经有：

```text
sanitize_navigation_graph_records()
strip_coordinate_fields()
```

它们用于避免坐标类字段进入正式 graph。

如果你新增了新的临时字段，比如：

```text
debug_bounds
runtime_center
matched_node
```

也应该加入清理逻辑。

## 18. 不破坏已有功能的开发原则

这个项目已经有几条重要功能：

1. Web 录制页面跳转。
2. 同页变化录制。
3. 页面目录展示。
4. 页面重命名。
5. 导航图 JSON 保存。
6. DFS 自动遍历。

新增功能时不要破坏它们。

### 18.1 不要改已有接口返回结构的核心字段

例如 `/api/state` 返回：

```text
state
active_state
active_page
current_candidates
candidates
merged_candidates
pending
pending_action_chain
warning
screenshot_url
screen_metrics
```

前端依赖这些字段。

可以新增字段，但不要随便删除或改名。

### 18.2 不要拆散统一动作接口

现在前端控制台动作主要走：

```text
/api/console_action
```

录制动作主要走：

```text
/api/record_action
```

删除动作主要走：

```text
/api/delete_action
```

新增同类功能时，优先加 action 分支。

不要每个按钮都新增一个完全独立接口。

### 18.3 不要在前端判断太多业务逻辑

前端可以做：

- 显示按钮
- 收集输入
- 调用 API
- 渲染结果

前端不应该负责：

- 判断页面是否是弹窗
- 修改 graph
- 推断 transition
- 决定 DFS 策略

这些应该放在后端或 DFS。

### 18.4 不要为了通过一次测试写死页面名

错误例子：

```python
if title == "WLAN":
    page_name = "Pages_WLAN"
```

这种只能解决一个页面。

更好的做法是改通用规则：

```text
find_page_title()
state_name_from_title()
```

只有 `设置 -> Pages_root` 这种根页面特殊规则可以保留。

## 19. 提交前代码审查清单

每次提交前，问自己这些问题。

第一，是否保存了坐标到 graph？

如果有，删除。

第二，是否兼容旧 JSON？

如果用了新字段，读取时必须有默认值。

第三，是否影响 Web 录制普通页面？

至少手动测试：

```text
Pages_root -> Pages_WLAN
```

第四，是否影响页面重命名？

如果新增字段里保存了 page_name，也要考虑重命名同步。

第五，是否影响 DFS？

至少跑：

```bash
conda run -n hcy-env python DFS.py --work-dir demo_settings
```

第六，是否影响前端布局？

页面要保持：

- 三栏并列。
- 页面级不滚动。
- 内部面板自己滚动。
- 截图在左侧。
- 目录能容纳大量页面。

第七，是否给用户明确错误提示？

不要只返回：

```text
failed
```

应该说清楚：

```text
当前界面不是弹窗
当前页面找不到路径控件 WLAN
找不到 Pages_root 到 Pages_xxx 的路径
```

