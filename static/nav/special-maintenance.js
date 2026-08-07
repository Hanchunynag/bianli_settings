import { api, postJson } from './api.js';
import { escapeHtml } from './dom.js';
import { store } from './state.js';

const SPECIAL_PREFIX = 'special_capture::';
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
  const type = String(step.type || '').trim();
  return {
    type: type === 'text' ? 'text' : 'key',
    value: step.value ?? '',
    key_description: String(step.key_description || '').trim(),
    step_prompt: String(step.step_prompt || '').trim(),
  };
}

function locatorFromOperation(operation = {}) {
  const target = operation.target || {};
  const key = String(target.key || '').trim();
  const text = String(target.text || '').trim();
  const rawType = String(target.type || '').trim();
  const rawValue = target.value;
  let type = 'key';
  let value = '';
  if (key) {
    type = 'key';
    value = key;
  } else if (text) {
    type = 'text';
    value = text;
  } else if ((rawType === 'key' || rawType === 'text') && rawValue !== undefined && rawValue !== null) {
    type = rawType;
    value = rawValue;
  } else {
    // Legacy popup data may have target.type=Dialog. That is metadata, not a
    // locator type. Keep the stored value only as a text fallback.
    type = 'text';
    value = rawValue ?? '';
  }
  return cloneStep({
    type,
    value,
    key_description: target.key_description || target.step_prompt || text || value,
    step_prompt: target.step_prompt || target.key_description || text || value,
  });
}

function specialSession(effect) {
  const value = String(effect || '').trim();
  if (!value.startsWith(SPECIAL_PREFIX)) return null;
  const parts = value.split('::');
  if (parts.length !== 3 || !parts[1]) return null;
  const matched = /^step(\d+)$/.exec(parts[2]);
  if (!matched) return null;
  return { sessionId: parts[1], stepIndex: Number(matched[1]) };
}

function groupsFromOperations(operations) {
  const result = [];
  const sessions = new Map();
  (operations || []).forEach((operation, index) => {
    if (!operation || typeof operation !== 'object') return;
    const session = specialSession(operation.effect);
    const popupType = String(operation.popup_type || '').trim();
    const isPopup = Boolean(popupType || String(operation.effect || '').trim() === 'open_popup');
    const isSpecial = String(operation.operation_kind || '').trim() === 'special_operate';
    if (!session && !isPopup && !isSpecial) return;

    const step = locatorFromOperation(operation);
    if (session) {
      let group = sessions.get(session.sessionId);
      if (!group) {
        group = { firstIndex: index, popupType: '', indexedSteps: [] };
        sessions.set(session.sessionId, group);
        result.push(group);
      }
      group.indexedSteps.push([session.stepIndex, step]);
      return;
    }

    result.push({
      firstIndex: index,
      popupType: popupType || (isPopup ? 'popup' : ''),
      indexedSteps: [[1, step]],
    });
  });

  result.sort((a, b) => a.firstIndex - b.firstIndex);
  return result.map((group) => ({
    popupType: group.popupType,
    steps: group.indexedSteps
      .sort((a, b) => a[0] - b[0])
      .map(([, step]) => step),
  }));
}

function groupsToStructure() {
  return Object.fromEntries(
    groups.map((group, index) => [
      `operation${index + 1}`,
      group.steps.map((step) => ({
        type: String(step.type || '').trim() === 'text' ? 'text' : 'key',
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

function groupsToServerStructure() {
  // Compatibility adapter for the current backend storage format. Popup type
  // is sent only as internal metadata; the user-facing/exported locator type
  // stays key/text.
  const structure = groupsToStructure();
  groups.forEach((group, index) => {
    if (!group.popupType || group.popupType === 'popup') return;
    const steps = structure[`operation${index + 1}`];
    if (Array.isArray(steps) && steps[0]) {
      steps[0] = { ...steps[0], type: group.popupType };
    }
  });
  return structure;
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
            <span>type（定位方式）</span>
            <select data-special-field="type">
              <option value="key" ${step.type === 'key' ? 'selected' : ''}>key</option>
              <option value="text" ${step.type === 'text' ? 'selected' : ''}>text</option>
            </select>
          </label>
          <label>
            <span>value</span>
            <input data-special-field="value" value="${escapeHtml(String(step.value ?? ''))}" placeholder="key 或 text 的定位值" />
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
        <button class="secondary compact" type="button" data-special-action="step-down" data-operation-index="${operationIndex}" data-step-index="${stepIndex}" ${stepIndex === groups[operationIndex].steps.length - 1 ? 'disabled' : ''}>下移</button>
        <button class="danger compact" type="button" data-special-action="step-delete" data-operation-index="${operationIndex}" data-step-index="${stepIndex}">删除</button>
      </div>
    </div>`;
}

function renderOperation(group, operationIndex) {
  const hint = group.popupType
    ? `<span class="statusBadge">点击后出现弹窗${group.popupType === 'popup' ? '' : ` · ${escapeHtml(group.popupType)}`}</span>`
    : '<span class="statusBadge isManual">special operate</span>';
  return `
    <article class="dfsRecordCard specialMaintainCard" data-special-operation="${operationIndex}">
      <div class="dfsRecordHeading">
        <div>
          <strong>operation${operationIndex + 1}</strong>
          ${hint}
          <span class="statusBadge isManual">${group.steps.length} 项数组</span>
        </div>
        <div class="dfsEditorActions">
          <button class="secondary compact" type="button" data-special-action="operation-up" data-operation-index="${operationIndex}" ${operationIndex === 0 ? 'disabled' : ''}>operation 上移</button>
          <button class="secondary compact" type="button" data-special-action="operation-down" data-operation-index="${operationIndex}" ${operationIndex === groups.length - 1 ? 'disabled' : ''}>operation 下移</button>
          <button class="danger compact" type="button" data-special-action="operation-delete" data-operation-index="${operationIndex}">删除 operation</button>
        </div>
      </div>
      <p class="muted">这一整个数组就是 <code>special_opearte.operation${operationIndex + 1}</code>。弹窗信息只是提示；数组内每一项的 type 始终是 key/text。</p>
      <div class="specialStepList">
        ${group.steps.map((step, stepIndex) => renderStep(step, operationIndex, stepIndex)).join('')}
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
        持久化导出结构为 <code>special_opearte.operationN = [ {...}, {...} ]</code>。
        <strong>type 永远只表示定位方式：key 或 text。</strong>
        Dialog / SheetWrapper / MenuWrapper 仅作为“点击后会出现弹窗”的录制提示，不参与 locator。
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
        <summary>查看当前导出结构 JSON</summary>
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
  const step = groups[operationIndex]?.steps?.[stepIndex];
  if (!step || !field) return;
  step[field] = input.value;
}

async function saveCurrentStructure(pageName) {
  const result = await postJson('/api/console_action', {
    action: 'maintain_special_opearte',
    payload: {
      page_name: pageName,
      special_opearte: groupsToServerStructure(),
    },
  });
  if (!result?.ok) return;
  await loadStructure(pageName);
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
  groups = groupsFromOperations(data.page_operations || []);
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
    section.onchange = section.oninput;

    section.onclick = async (event) => {
      const button = event.target.closest('button[data-special-action]');
      if (!button) return;
      const action = button.dataset.specialAction;
      const operationIndex = Number(button.dataset.operationIndex);
      const stepIndex = Number(button.dataset.stepIndex);

      if (action === 'operation-add') {
        groups.push({ popupType: '', steps: [blankStep()] });
      } else if (action === 'operation-up') {
        moveItem(groups, operationIndex, -1);
      } else if (action === 'operation-down') {
        moveItem(groups, operationIndex, 1);
      } else if (action === 'operation-delete') {
        if (!window.confirm(`确认删除 operation${operationIndex + 1}？`)) return;
        groups.splice(operationIndex, 1);
      } else if (action === 'step-add') {
        groups[operationIndex]?.steps.push(blankStep());
      } else if (action === 'step-up') {
        moveItem(groups[operationIndex]?.steps || [], stepIndex, -1);
      } else if (action === 'step-down') {
        moveItem(groups[operationIndex]?.steps || [], stepIndex, 1);
      } else if (action === 'step-delete') {
        const operation = groups[operationIndex];
        if (!operation) return;
        if (operation.steps.length === 1) {
          if (!window.confirm('这是该 operation 的最后一步，删除后整个 operation 也会删除。继续？')) return;
          groups.splice(operationIndex, 1);
        } else {
          operation.steps.splice(stepIndex, 1);
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
