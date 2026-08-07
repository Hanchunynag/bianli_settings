import { api, postJson } from './api.js';
import { escapeHtml } from './dom.js';
import { store } from './state.js';

let observer = null;
let renderGeneration = 0;
let renderScheduled = false;
let internalMutation = false;
let editorPage = '';
let groups = [];

function currentDetailPage() {
  return String(
    store.selectedPage
    || store.data?.active_page
    || store.data?.active_state?.page_name
    || store.data?.state?.page_name
    || '',
  ).trim();
}

function cloneStep(step = {}) {
  return {
    type: String(step.type || '').trim(),
    value: step.value ?? '',
    key_description: String(step.key_description || '').trim(),
    step_prompt: String(step.step_prompt || '').trim(),
  };
}

function structureToGroups(structure) {
  if (!structure || typeof structure !== 'object' || Array.isArray(structure)) return [];
  return Object.entries(structure)
    .map(([key, steps]) => {
      const match = /^operation(\d+)$/.exec(key);
      return match ? [Number(match[1]), steps] : null;
    })
    .filter(Boolean)
    .sort((left, right) => left[0] - right[0])
    .map(([, steps]) => (Array.isArray(steps) ? steps.map(cloneStep) : []))
    .filter((steps) => steps.length);
}

function groupsToStructure() {
  return Object.fromEntries(
    groups.map((steps, index) => [
      `operation${index + 1}`,
      steps.map((step) => ({
        type: String(step.type || '').trim(),
        value: step.value,
        ...(String(step.key_description || '').trim()
          ? { key_description: String(step.key_description).trim() }
          : {}),
        ...(String(step.step_prompt || '').trim()
          ? { step_prompt: String(step.step_prompt).trim() }
          : {}),
      })),
    ]),
  );
}

function blankStep() {
  return {
    type: 'key',
    value: '',
    key_description: '',
    step_prompt: '',
  };
}

function moveItem(list, index, delta) {
  const target = index + delta;
  if (index < 0 || index >= list.length || target < 0 || target >= list.length) return false;
  [list[index], list[target]] = [list[target], list[index]];
  return true;
}

function stepLabel(step) {
  return String(
    step.step_prompt
    || step.key_description
    || step.value
    || step.type
    || '未维护步骤',
  ).trim();
}

function renderStep(step, operationIndex, stepIndex) {
  return `
    <div class="operationRow specialStepEditor" data-special-step="${stepIndex}">
      <div class="operationMain specialStepFields">
        <strong>数组第 ${stepIndex + 1} 项 · ${escapeHtml(stepLabel(step))}</strong>
        <div class="dfsAdvancedBody">
          <label>
            <span>type</span>
            <input data-special-field="type" value="${escapeHtml(step.type || '')}" placeholder="key / text / Dialog ..." />
          </label>
          <label>
            <span>value</span>
            <input data-special-field="value" value="${escapeHtml(String(step.value ?? ''))}" placeholder="定位值" />
          </label>
          <label>
            <span>key_description</span>
            <input data-special-field="key_description" value="${escapeHtml(step.key_description || '')}" placeholder="操作说明" />
          </label>
          <label>
            <span>step_prompt</span>
            <input data-special-field="step_prompt" value="${escapeHtml(step.step_prompt || '')}" placeholder="执行提示" />
          </label>
        </div>
      </div>
      <div class="dfsEditorActions specialStepActions">
        <button class="secondary compact" type="button" data-special-action="step-up" data-operation-index="${operationIndex}" data-step-index="${stepIndex}" ${stepIndex === 0 ? 'disabled' : ''}>上移</button>
        <button class="secondary compact" type="button" data-special-action="step-down" data-operation-index="${operationIndex}" data-step-index="${stepIndex}" ${stepIndex === groups[operationIndex].length - 1 ? 'disabled' : ''}>下移</button>
        <button class="danger compact" type="button" data-special-action="step-delete" data-operation-index="${operationIndex}" data-step-index="${stepIndex}">删除</button>
      </div>
    </div>`;
}

function renderOperation(steps, operationIndex) {
  return `
    <article class="dfsRecordCard specialMaintainCard" data-special-operation="${operationIndex}">
      <div class="dfsRecordHeading">
        <div>
          <strong>operation${operationIndex + 1}</strong>
          <span class="statusBadge isManual">${steps.length} 项数组</span>
        </div>
        <div class="dfsEditorActions">
          <button class="secondary compact" type="button" data-special-action="operation-up" data-operation-index="${operationIndex}" ${operationIndex === 0 ? 'disabled' : ''}>operation 上移</button>
          <button class="secondary compact" type="button" data-special-action="operation-down" data-operation-index="${operationIndex}" ${operationIndex === groups.length - 1 ? 'disabled' : ''}>operation 下移</button>
          <button class="danger compact" type="button" data-special-action="operation-delete" data-operation-index="${operationIndex}">删除 operation</button>
        </div>
      </div>
      <p class="muted">这一整个数组就是 <code>special_opearte.operation${operationIndex + 1}</code>；多步操作只是在数组里继续追加对象。</p>
      <div class="specialStepList">
        ${steps.map((step, stepIndex) => renderStep(step, operationIndex, stepIndex)).join('')}
      </div>
      <button class="secondary compact" type="button" data-special-action="step-add" data-operation-index="${operationIndex}">+ 向 operation${operationIndex + 1} 追加一步</button>
    </article>`;
}

function renderEditor(section) {
  section.innerHTML = `
    <summary>
      <span>Special Operate 维护</span>
      <small>${groups.length} 组</small>
    </summary>
    <div class="detailSectionBody">
      <p class="muted">
        持久化结构为 <code>special_opearte.operationN = [ {...}, {...} ]</code>。
        operation 顺序就是执行顺序；一个 operation 内有几步，就在对应数组里放几个对象。
        popup 仍放在这个序列里，例如 type 可直接维护为 Dialog / SheetWrapper / MenuWrapper。
      </p>
      <div class="dfsEditorActions">
        <button class="secondary" type="button" data-special-action="operation-add">+ 新增 operation</button>
        <button class="primary" type="button" data-special-action="save">保存 special_opearte</button>
        <button class="secondary" type="button" data-special-action="reload">放弃未保存修改</button>
      </div>
      <div class="specialOperationList">
        ${groups.length
          ? groups.map(renderOperation).join('')
          : '<div class="muted">当前页面没有 special_opearte。点击“新增 operation”可人工维护。</div>'}
      </div>
      <details class="dfsAdvanced">
        <summary>查看当前结构 JSON</summary>
        <pre class="graphBox">${escapeHtml(JSON.stringify({ special_opearte: groupsToStructure() }, null, 2))}</pre>
      </details>
    </div>`;
}

function updateFieldFromInput(input) {
  const operationCard = input.closest('[data-special-operation]');
  const stepRow = input.closest('[data-special-step]');
  if (!operationCard || !stepRow) return;
  const operationIndex = Number(operationCard.dataset.specialOperation);
  const stepIndex = Number(stepRow.dataset.specialStep);
  const field = input.dataset.specialField;
  if (!groups[operationIndex]?.[stepIndex] || !field) return;
  groups[operationIndex][stepIndex][field] = input.value;
}

async function saveCurrentStructure(pageName) {
  const result = await postJson('/api/console_action', {
    action: 'maintain_special_opearte',
    payload: {
      page_name: pageName,
      special_opearte: groupsToStructure(),
    },
  });
  if (!result?.ok) return;
  groups = structureToGroups(result.special_opearte || {});
  const status = document.getElementById('overlayStatus');
  if (status) {
    status.textContent = `${result.message} 已同步更新 DFS：${result.output_path}`;
    status.classList.remove('hidden');
  }
  const section = document.querySelector('[data-special-maintenance]');
  if (section) renderEditor(section);
}

async function loadStructure(pageName) {
  const data = await api(`/api/page_detail?page_name=${encodeURIComponent(pageName)}`);
  if (!data?.ok) return false;
  editorPage = pageName;
  groups = structureToGroups(
    data.special_opearte
    || data.dfs_record?.special_opearte
    || {},
  );
  return true;
}

async function renderMaintenance(force = false) {
  const box = document.getElementById('pageDetail');
  if (!box || store.showingOrphans) return;
  const pageName = currentDetailPage();
  if (!pageName) return;

  const generation = ++renderGeneration;
  if (force || editorPage !== pageName) {
    if (!await loadStructure(pageName)) return;
  }
  if (generation !== renderGeneration) return;

  internalMutation = true;
  try {
    box.querySelector('[data-special-maintenance]')?.remove();
    const section = document.createElement('details');
    section.className = 'detailSection';
    section.open = true;
    section.dataset.specialMaintenance = 'true';
    renderEditor(section);
    box.appendChild(section);

    section.oninput = (event) => {
      const input = event.target.closest('[data-special-field]');
      if (input) updateFieldFromInput(input);
    };

    section.onclick = async (event) => {
      const button = event.target.closest('button[data-special-action]');
      if (!button) return;
      const action = button.dataset.specialAction;
      const operationIndex = Number(button.dataset.operationIndex);
      const stepIndex = Number(button.dataset.stepIndex);

      if (action === 'operation-add') {
        groups.push([blankStep()]);
      } else if (action === 'operation-up') {
        moveItem(groups, operationIndex, -1);
      } else if (action === 'operation-down') {
        moveItem(groups, operationIndex, 1);
      } else if (action === 'operation-delete') {
        if (!window.confirm(`确认删除 operation${operationIndex + 1}？`)) return;
        groups.splice(operationIndex, 1);
      } else if (action === 'step-add') {
        groups[operationIndex]?.push(blankStep());
      } else if (action === 'step-up') {
        moveItem(groups[operationIndex] || [], stepIndex, -1);
      } else if (action === 'step-down') {
        moveItem(groups[operationIndex] || [], stepIndex, 1);
      } else if (action === 'step-delete') {
        const operation = groups[operationIndex];
        if (!operation) return;
        if (operation.length === 1) {
          if (!window.confirm('这是该 operation 的最后一步，删除后整个 operation 也会删除。继续？')) return;
          groups.splice(operationIndex, 1);
        } else {
          operation.splice(stepIndex, 1);
        }
      } else if (action === 'reload') {
        await loadStructure(pageName);
      } else if (action === 'save') {
        await saveCurrentStructure(pageName);
        return;
      } else {
        return;
      }
      renderEditor(section);
    };
  } finally {
    internalMutation = false;
  }
}

function scheduleRender(force = false) {
  if (renderScheduled) return;
  renderScheduled = true;
  window.setTimeout(() => {
    renderScheduled = false;
    renderMaintenance(force).catch(() => {});
  }, force ? 0 : 80);
}

export function initSpecialMaintenance() {
  const box = document.getElementById('pageDetail');
  if (!box || observer) return;
  observer = new MutationObserver(() => {
    if (!internalMutation) scheduleRender(false);
  });
  observer.observe(box, { childList: true, subtree: true });
  scheduleRender(true);
}
