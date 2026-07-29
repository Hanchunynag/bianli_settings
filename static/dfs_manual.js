import { postJson, queryJson } from './nav/api.js';

const detailBox = document.getElementById('pageDetail');
let requestGeneration = 0;

function currentPageName() {
  return [...detailBox.querySelectorAll('code')]
    .map((node) => node.textContent.trim())
    .find((value) => value.startsWith('Pages_')) || '';
}

function setEditorData(panel, data) {
  const textarea = panel.querySelector('[data-dfs-editor]');
  const mode = panel.querySelector('[data-dfs-mode]');
  textarea.value = JSON.stringify(data.record, null, 2);
  mode.textContent = data.manual_override
    ? '当前使用人工维护数据；重新生成 DFS 时优先使用本记录。'
    : '当前使用自动生成数据。保存后将仅覆盖本页面的 DFS 输出。';
  panel.dataset.manualOverride = String(Boolean(data.manual_override));
}

function createPanel(pageName, data) {
  const panel = document.createElement('section');
  panel.dataset.dfsMaintenance = pageName;
  panel.className = 'dfsMaintenance';
  panel.innerHTML = `
    <h4>DFS 人工维护</h4>
    <p class="muted">
      <code>page_description</code> 与 <code>path_snapshot</code> 独立保存。
      中间点击步骤可以保留在路径中，不必出现在页面描述里。
    </p>
    <div class="muted" data-dfs-mode></div>
    <textarea data-dfs-editor spellcheck="false" style="width:100%;min-height:320px;box-sizing:border-box;font-family:ui-monospace,SFMono-Regular,Consolas,monospace;margin-top:10px;"></textarea>
    <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:10px;">
      <button class="primary" data-dfs-action="save">保存人工数据</button>
      <button class="secondary" data-dfs-action="automatic">载入自动结果</button>
      <button class="secondary" data-dfs-action="reset">清除人工覆盖</button>
    </div>
    <pre data-dfs-auto class="graphBox hidden"></pre>
  `;
  setEditorData(panel, data);

  panel.onclick = async (event) => {
    const button = event.target.closest('button[data-dfs-action]');
    if (!button) return;
    const action = button.dataset.dfsAction;
    const textarea = panel.querySelector('[data-dfs-editor]');

    if (action === 'automatic') {
      textarea.value = JSON.stringify(data.automatic_record, null, 2);
      const autoBox = panel.querySelector('[data-dfs-auto]');
      autoBox.textContent = JSON.stringify(data.automatic_record, null, 2);
      autoBox.classList.toggle('hidden');
      return;
    }

    if (action === 'reset') {
      if (!window.confirm(`确认清除 ${pageName} 的 DFS 人工覆盖？`)) return;
      const result = await postJson('/api/console_action', {
        action: 'reset_dfs_override',
        payload: { page_name: pageName },
      });
      if (!result) return;
      Object.assign(data, result);
      setEditorData(panel, data);
      return;
    }

    let record;
    try {
      record = JSON.parse(textarea.value);
    } catch (error) {
      const errorBox = document.getElementById('error');
      errorBox.textContent = `DFS JSON 格式错误：${error.message}`;
      errorBox.classList.remove('hidden');
      return;
    }
    const result = await postJson('/api/console_action', {
      action: 'save_dfs_override',
      payload: { page_name: pageName, record },
    });
    if (!result) return;
    Object.assign(data, result);
    setEditorData(panel, data);
  };

  return panel;
}

async function renderDfsMaintenance() {
  const pageName = currentPageName();
  const existingPanel = detailBox.querySelector('[data-dfs-maintenance]');
  if (!pageName || existingPanel?.dataset.dfsMaintenance === pageName) return;

  const generation = ++requestGeneration;
  const data = await queryJson(`/api/dfs_record?page_name=${encodeURIComponent(pageName)}`);
  if (!data || generation !== requestGeneration || currentPageName() !== pageName) return;

  if (existingPanel) existingPanel.remove();
  detailBox.appendChild(createPanel(pageName, data));
}

const observer = new MutationObserver(() => {
  window.queueMicrotask(renderDfsMaintenance);
});
observer.observe(detailBox, { childList: true, subtree: true });
renderDfsMaintenance();
