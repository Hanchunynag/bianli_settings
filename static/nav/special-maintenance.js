import { postJson, queryJson } from './api.js?v=special-array-16';
import { escapeHtml } from './dom.js?v=special-profile-15';
import { store } from './state.js?v=special-profile-15';

let renderQueued = false;
let requestGeneration = 0;
let editorPage = '';
let editorProfileId = '';
let operations = [];

function currentDetailPage() {
  return String(
    store.selectedPage
    || store.data?.active_page
    || store.data?.active_state?.page_name
    || store.data?.state?.page_name
    || '',
  ).trim();
}

function cloneItem(item = {}) {
  return {
    type: String(item.type || '').trim() === 'text' ? 'text' : 'key',
    value: item.value ?? '',
    key_description: String(item.key_description || '').trim(),
    step_prompt: String(item.step_prompt || '').trim(),
  };
}

function groupsFromStructure(structure = {}) {
  return Object.entries(structure || {})
    .map(([name, items], index) => {
      const matched = /^operation(\d+)$/.exec(name);
      return {
        order: matched ? Number(matched[1]) : index + 1,
        items: Array.isArray(items) ? items.map(cloneItem) : [],
      };
    })
    .filter((group) => group.items.length)
    .sort((a, b) => a.order - b.order)
    .map((group) => group.items);
}

function structureFromGroups() {
  return Object.fromEntries(
    operations.map((items, index) => [
      `operation${index + 1}`,
      items.map((item) => {
        const normalized = {
          type: String(item.type || '').trim() === 'text' ? 'text' : 'key',
          value: item.value,
        };
        const description = String(item.key_description || '').trim();
        const prompt = String(item.step_prompt || '').trim();
        if (description) normalized.key_description = description;
        if (prompt) normalized.step_prompt = prompt;
        return normalized;
      }),
    ]),
  );
}

function blankItem() {
  return {
    type: 'key',
    value: '',
    key_description: '',
    step_prompt: '',
  };
}

function moveItem(list, index, delta) {
  const target = index + delta;
  if (index < 0 || index >= list.length || target < 0 || target >= list.length) return;
  [list[index], list[target]] = [list[target], list[index]];
}

function itemLabel(item) {
  return String(
    item.step_prompt
    || item.key_description
    || item.value
    || '未维护定位项',
  ).trim();
}

function renderArrayItem(item, operationIndex, itemIndex) {
  return `
    <div class="operationRow specialStepEditor" data-special-array-item="${itemIndex}">
      <div class="operationMain specialStepFields">
        <strong>数组第 ${itemIndex + 1} 项 · ${escapeHtml(itemLabel(item))}</strong>
        <div class="dfsAdvancedBody">
          <label>
            <span>type（定位方式）</span>
            <select data-special-array-field="type">
              <option value="key" ${item.type === 'key' ? 'selected' : ''}>key</option>
              <option value="text" ${item.type === 'text' ? 'selected' : ''}>text</option>
            </select>
          </label>
          <label>
            <span>value</span>
            <input data-special-array-field="value" value="${escapeHtml(String(item.value ?? ''))}" placeholder="key 或 text 的定位值" />
          </label>
          <label>
            <span>key_description</span>
            <input data-special-array-field="key_description" value="${escapeHtml(item.key_description || '')}" placeholder="操作说明" />
          </label>
          <label>
            <span>step_prompt</span>
            <input data-special-array-field="step_prompt" value="${escapeHtml(item.step_prompt || '')}" placeholder="执行提示" />
          </label>
        </div>
      </div>
      <div class="dfsEditorActions specialStepActions">
        <button class="secondary compact" type="button" data-special-array-action="item-up" data-operation-index="${operationIndex}" data-item-index="${itemIndex}" ${itemIndex === 0 ? 'disabled' : ''}>上移</button>
        <button class="secondary compact" type="button" data-special-array-action="item-down" data-operation-index="${operationIndex}" data-item-index="${itemIndex}" ${itemIndex === operations[operationIndex].length - 1 ? 'disabled' : ''}>下移</button>
        <button class="danger compact" type="button" data-special-array-action="item-delete" data-operation-index="${operationIndex}" data-item-index="${itemIndex}">删除</button>
      </div>
    </div>`;
}

function renderOperation(items, operationIndex) {
  return `
    <article class="dfsRecordCard specialMaintainCard" data-special-array-operation="${operationIndex}">
      <div class="dfsRecordHeading">
        <div>
          <strong>operation${operationIndex + 1}</strong>
          <span class="statusBadge isManual">${items.length} 个数组项</span>
        </div>
        <div class="dfsEditorActions">
          <button class="secondary compact" type="button" data-special-array-action="operation-up" data-operation-index="${operationIndex}" ${operationIndex === 0 ? 'disabled' : ''}>operation 上移</button>
          <button class="secondary compact" type="button" data-special-array-action="operation-down" data-operation-index="${operationIndex}" ${operationIndex === operations.length - 1 ? 'disabled' : ''}>operation 下移</button>
          <button class="danger compact" type="button" data-special-array-action="operation-delete" data-operation-index="${operationIndex}">删除 operation</button>
        </div>
      </div>
      <p class="muted">这一整个数组就是 <code>special_opearte.operation${operationIndex + 1}</code>。数组从上到下就是实际执行顺序。</p>
      <div class="specialStepList">
        ${items.map((item, itemIndex) => renderArrayItem(item, operationIndex, itemIndex)).join('')}
      </div>
      <button class="secondary compact" type="button" data-special-array-action="item-add" data-operation-index="${operationIndex}">+ 追加一个数组项</button>
    </article>`;
}

function renderEditor(section) {
  section.innerHTML = `
    <summary>
      <span>Special Operate 数组维护</span>
      <small>${operations.length} 个 operation</small>
    </summary>
    <div class="detailSectionBody">
      <p class="muted">
        固定导出结构：<code>special_opearte.operationN = [ {...}, {...} ]</code>。
        同一个特殊操作有多次点击时，直接按顺序追加到同一个数组；不使用额外的步骤编号字段。
        每个数组项的 <code>type</code> 只允许 <code>key</code> 或 <code>text</code>。
      </p>
      <div class="dfsEditorActions">
        <button class="secondary" type="button" data-special-array-action="operation-add">+ 新增 operation</button>
        <button class="primary" type="button" data-special-array-action="save">保存 special_opearte</button>
        <button class="secondary" type="button" data-special-array-action="reload">放弃未保存修改</button>
        <button class="secondary" type="button" data-special-array-action="reset">恢复自动生成</button>
      </div>
      <div class="specialOperationList">
        ${operations.length
          ? operations.map(renderOperation).join('')
          : '<div class="muted">当前页面没有 special_opearte。点击“新增 operation”可人工维护。</div>'}
      </div>
      <details class="dfsAdvanced">
        <summary>查看当前导出结构 JSON</summary>
        <pre class="graphBox">${escapeHtml(JSON.stringify({ special_opearte: structureFromGroups() }, null, 2))}</pre>
      </details>
    </div>`;
}

function updateFromInput(input) {
  const operationCard = input.closest('[data-special-array-operation]');
  const itemRow = input.closest('[data-special-array-item]');
  if (!operationCard || !itemRow) return;
  const operationIndex = Number(operationCard.dataset.specialArrayOperation);
  const itemIndex = Number(itemRow.dataset.specialArrayItem);
  const field = input.dataset.specialArrayField;
  const item = operations[operationIndex]?.[itemIndex];
  if (!item || !field) return;
  item[field] = input.value;
}

async function loadStructure(pageName) {
  const generation = ++requestGeneration;
  const data = await queryJson(`/api/page_detail?page_name=${encodeURIComponent(pageName)}`);
  if (generation !== requestGeneration || !data?.ok) return false;
  editorPage = pageName;
  editorProfileId = store.activeSettingsProfileId || 'default';
  operations = groupsFromStructure(data.special_record || {});
  return true;
}

async function saveStructure(pageName) {
  for (let operationIndex = 0; operationIndex < operations.length; operationIndex += 1) {
    const items = operations[operationIndex];
    if (!items.length) {
      window.alert(`operation${operationIndex + 1} 不能为空。`);
      return;
    }
    for (let itemIndex = 0; itemIndex < items.length; itemIndex += 1) {
      const item = items[itemIndex];
      if (!String(item.value ?? '').trim()) {
        window.alert(`operation${operationIndex + 1} 数组第 ${itemIndex + 1} 项的 value 不能为空。`);
        return;
      }
    }
  }
  const result = await postJson('/api/console_action', {
    action: 'maintain_special_dfs',
    payload: {
      page_name: pageName,
      special: structureFromGroups(),
    },
  });
  if (!result) return;
  await loadStructure(pageName);
  const status = document.getElementById('overlayStatus');
  if (status) {
    status.textContent = result.message;
    status.classList.remove('hidden');
  }
}

async function resetStructure(pageName) {
  if (!window.confirm('确认删除本页人工 special_opearte 并恢复自动生成？')) return;
  const result = await postJson('/api/console_action', {
    action: 'maintain_special_dfs',
    payload: { page_name: pageName, clear: true },
  });
  if (!result) return;
  await loadStructure(pageName);
  const status = document.getElementById('overlayStatus');
  if (status) {
    status.textContent = result.message;
    status.classList.remove('hidden');
  }
}

async function mountEditor(force = false) {
  const box = document.getElementById('pageDetail');
  if (!box || store.showingOrphans) return;
  const pageName = currentDetailPage();
  if (!pageName) return;

  const oldForm = box.querySelector('#specialManualForm');
  if (!oldForm) return;
  oldForm.hidden = true;
  const oldDivider = oldForm.previousElementSibling;
  if (oldDivider?.classList.contains('dfsEditorDivider')) oldDivider.hidden = true;

  const activeProfileId = store.activeSettingsProfileId || 'default';
  const existing = box.querySelector('[data-special-array-maintenance]');
  if (
    existing
    && editorPage === pageName
    && editorProfileId === activeProfileId
    && !force
  ) {
    return;
  }

  if (
    force
    || editorPage !== pageName
    || editorProfileId !== activeProfileId
  ) {
    if (!await loadStructure(pageName)) return;
  }

  existing?.remove();
  const section = document.createElement('details');
  section.className = 'detailSection';
  section.open = true;
  section.dataset.specialArrayMaintenance = 'true';
  renderEditor(section);
  oldForm.insertAdjacentElement('afterend', section);

  section.oninput = (event) => {
    const input = event.target.closest('[data-special-array-field]');
    if (input) updateFromInput(input);
  };
  section.onchange = section.oninput;
  section.onclick = async (event) => {
    const button = event.target.closest('button[data-special-array-action]');
    if (!button) return;
    const action = button.dataset.specialArrayAction;
    const operationIndex = Number(button.dataset.operationIndex);
    const itemIndex = Number(button.dataset.itemIndex);

    if (action === 'operation-add') {
      operations.push([blankItem()]);
    } else if (action === 'operation-up') {
      moveItem(operations, operationIndex, -1);
    } else if (action === 'operation-down') {
      moveItem(operations, operationIndex, 1);
    } else if (action === 'operation-delete') {
      if (!window.confirm(`确认删除 operation${operationIndex + 1}？`)) return;
      operations.splice(operationIndex, 1);
    } else if (action === 'item-add') {
      operations[operationIndex]?.push(blankItem());
    } else if (action === 'item-up') {
      moveItem(operations[operationIndex] || [], itemIndex, -1);
    } else if (action === 'item-down') {
      moveItem(operations[operationIndex] || [], itemIndex, 1);
    } else if (action === 'item-delete') {
      const items = operations[operationIndex];
      if (!items) return;
      if (items.length === 1) {
        if (!window.confirm('这是该 operation 的最后一个数组项，删除后整个 operation 也会删除。继续？')) return;
        operations.splice(operationIndex, 1);
      } else {
        items.splice(itemIndex, 1);
      }
    } else if (action === 'reload') {
      await loadStructure(pageName);
    } else if (action === 'save') {
      await saveStructure(pageName);
    } else if (action === 'reset') {
      await resetStructure(pageName);
    } else {
      return;
    }
    renderEditor(section);
  };
}

function scheduleMount(force = false) {
  if (renderQueued) return;
  renderQueued = true;
  queueMicrotask(async () => {
    renderQueued = false;
    await mountEditor(force);
  });
}

const pageDetail = document.getElementById('pageDetail');
if (pageDetail) {
  new MutationObserver(() => scheduleMount(false)).observe(pageDetail, {
    childList: true,
    subtree: true,
  });
}

scheduleMount(true);
