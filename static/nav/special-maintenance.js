import { api, postJson } from './api.js';
import { escapeHtml } from './dom.js';
import { store } from './state.js';

const SPECIAL_PREFIX = 'special_capture::';
let observer = null;
let renderGeneration = 0;
let renderScheduled = false;
let internalMutation = false;

function currentDetailPage() {
  return String(
    store.selectedPage
    || store.data?.active_page
    || store.data?.active_state?.page_name
    || store.data?.state?.page_name
    || '',
  ).trim();
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

function targetLabel(target = {}) {
  return String(
    target.step_prompt
    || target.key_description
    || target.text
    || target.value
    || target.key
    || '当前区域',
  ).trim();
}

function targetLocator(target = {}) {
  const key = String(target.key || '').trim();
  if (key) return `key=${key}`;
  const text = String(target.text || '').trim();
  if (text) return `text=${text}`;
  const type = String(target.type || target.component_type || '').trim();
  const value = target.value;
  if (type || value !== undefined) return `${type || 'value'}=${String(value ?? '')}`;
  return '未维护 locator';
}

function buildGroups(operations) {
  const groups = [];
  const sessions = new Map();
  (operations || []).forEach((operation, index) => {
    if (!operation || typeof operation !== 'object') return;
    const session = specialSession(operation.effect);
    if (session) {
      let group = sessions.get(session.sessionId);
      if (!group) {
        group = {
          key: `special:${session.sessionId}`,
          kind: 'special_operate',
          firstIndex: index,
          steps: [],
        };
        sessions.set(session.sessionId, group);
        groups.push(group);
      }
      group.steps.push({
        operation,
        order: session.stepIndex,
      });
      return;
    }

    const popupType = String(operation.popup_type || '').trim();
    if (popupType || String(operation.effect || '') === 'open_popup') {
      groups.push({
        key: `popup:${operation.operation_id || index}`,
        kind: 'popup',
        popupType,
        firstIndex: index,
        steps: [{ operation, order: 1 }],
      });
    }
  });

  groups.sort((left, right) => left.firstIndex - right.firstIndex);
  groups.forEach((group) => group.steps.sort((left, right) => left.order - right.order));
  return groups;
}

async function executeDelete(pageName, operationId) {
  const body = {
    page_name: pageName,
    operation_id: operationId,
    delete_revealed_candidates: false,
  };
  const preview = await postJson('/api/delete_action', {
    target_type: 'page_operation',
    payload: body,
    dry_run: true,
  });
  if (!preview?.preview_token) return false;
  const result = await postJson('/api/delete_action', {
    target_type: 'page_operation',
    payload: body,
    dry_run: false,
    preview_token: preview.preview_token,
  });
  return Boolean(result?.ok);
}

async function deleteOperations(pageName, operationIds, description) {
  const ids = [...new Set((operationIds || []).filter(Boolean))];
  if (!ids.length) return;
  if (!window.confirm(`确认删除${description}？\n将删除 ${ids.length} 个底层 page_operation。`)) return;
  for (const operationId of ids) {
    await executeDelete(pageName, operationId);
  }
  const status = document.getElementById('overlayStatus');
  if (status) {
    status.textContent = `已删除${description}，共 ${ids.length} 个步骤。`;
    status.classList.remove('hidden');
  }
  scheduleRender(true);
}

function renderGroup(group, index) {
  const operationIds = group.steps
    .map(({ operation }) => String(operation.operation_id || '').trim())
    .filter(Boolean)
    .join(',');
  const badge = group.kind === 'popup'
    ? `popup${group.popupType ? ` · ${group.popupType}` : ''}`
    : 'special_operate';
  return `
    <article class="dfsRecordCard specialMaintainCard" data-special-group="${escapeHtml(group.key)}">
      <div class="dfsRecordHeading">
        <div>
          <strong>operate${index + 1}</strong>
          <span class="statusBadge ${group.kind === 'special_operate' ? 'isManual' : ''}">${escapeHtml(badge)}</span>
        </div>
        <button class="danger compact" type="button" data-special-delete-group="${escapeHtml(operationIds)}">删除整组</button>
      </div>
      <div class="muted">该组按当前页面内的实际录制顺序导出；special 多步会保持在同一个 operateN 内。</div>
      <div class="specialStepList">
        ${group.steps.map(({ operation }, stepIndex) => `
          <div class="operationRow specialStepRow">
            <div class="operationMain">
              <strong>step${stepIndex + 1} · ${escapeHtml(operation.operate || 'tap')} · ${escapeHtml(targetLabel(operation.target))}</strong>
              <span>${escapeHtml(targetLocator(operation.target))}</span>
              <code>${escapeHtml(operation.operation_id || '')}</code>
            </div>
            <button class="danger compact" type="button" data-special-delete-step="${escapeHtml(operation.operation_id || '')}">删除 step</button>
          </div>
        `).join('')}
      </div>
    </article>`;
}

async function renderMaintenance(force = false) {
  const box = document.getElementById('pageDetail');
  if (!box || store.showingOrphans) return;
  const pageName = currentDetailPage();
  if (!pageName) return;

  const generation = ++renderGeneration;
  const data = await api(`/api/page_detail?page_name=${encodeURIComponent(pageName)}`);
  if (generation !== renderGeneration || !data?.ok) return;
  const groups = buildGroups(data.page_operations || []);

  internalMutation = true;
  try {
    box.querySelector('[data-special-maintenance]')?.remove();
    const section = document.createElement('details');
    section.className = 'detailSection';
    section.open = true;
    section.dataset.specialMaintenance = 'true';
    section.innerHTML = `
      <summary>
        <span>Special / Popup 维护</span>
        <small>${groups.length} 组</small>
      </summary>
      <div class="detailSectionBody">
        <p class="muted">
          这里显示最终 DFS <code>special.operate1 / operate2 / ...</code> 的顺序。
          special 的同一采集 session 会聚合为一个 operateN，并按 step1 / step2 / ... 展示；popup 单独占一个 operateN。
        </p>
        ${groups.length
          ? groups.map(renderGroup).join('')
          : '<div class="muted">当前页面还没有 special_operate 或 popup 记录。</div>'}
      </div>`;
    box.appendChild(section);

    section.onclick = async (event) => {
      const groupButton = event.target.closest('[data-special-delete-group]');
      if (groupButton) {
        const ids = String(groupButton.dataset.specialDeleteGroup || '').split(',').filter(Boolean);
        await deleteOperations(pageName, ids, ` operate 组`);
        return;
      }
      const stepButton = event.target.closest('[data-special-delete-step]');
      if (stepButton) {
        const id = String(stepButton.dataset.specialDeleteStep || '').trim();
        await deleteOperations(pageName, [id], ` step ${id}`);
      }
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
