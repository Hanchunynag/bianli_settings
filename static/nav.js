import { api, postJson } from './nav/api.js';
import { el } from './nav/dom.js';
import { store } from './nav/state.js';
import { refreshOrphans, render, renderOverlay } from './nav/render.js?v=follow-active-page-12';

const popupTypeButtons = [...document.querySelectorAll('[data-popup-type]')];
const popupTypes = new Set(popupTypeButtons.map((button) => button.dataset.popupType));
const specialOperateButton = el('special_opeartebutton');
const finishSpecialButton = el('finishSpecialOperateBtn');
const cancelSpecialButton = el('cancelSpecialOperateBtn');
let specialCapture = null;

function currentPageName() {
  return String(
    store.data?.active_page
    || store.data?.active_state?.page_name
    || store.data?.state?.page_name
    || '',
  ).trim();
}

function specialEffect(sessionId, stepIndex) {
  return `special_capture::${sessionId}::step${stepIndex}`;
}

function pageOperations(data) {
  return data?.active_state?.page_operations
    || data?.state?.page_operations
    || [];
}

function syncSpecialOperationIds(data) {
  if (!specialCapture) return;
  const prefix = `special_capture::${specialCapture.id}::`;
  specialCapture.operationIds = pageOperations(data)
    .filter((operation) => String(operation?.effect || '').startsWith(prefix))
    .map((operation) => String(operation?.operation_id || '').trim())
    .filter(Boolean);
}

function renderSpecialOperateHint(message = '') {
  const status = el('overlayStatus');
  if (!specialCapture) return;
  status.textContent = message || (
    `正在录制 special_operate：${specialCapture.pageName}，已保存 ${specialCapture.stepCount} 步。`
    + '继续点击截图录制下一步；完成后点“完成特殊操作”，放弃本组请点“取消特殊操作”。'
  );
  status.classList.remove('hidden');
}

function refreshSpecialButtons() {
  const armed = Boolean(specialCapture);
  specialOperateButton.classList.toggle('isArmed', armed);
  specialOperateButton.setAttribute('aria-pressed', String(armed));
  specialOperateButton.disabled = armed;
  finishSpecialButton.disabled = !armed;
  cancelSpecialButton.disabled = !armed;
}

function startSpecialOperate() {
  const pageName = currentPageName();
  if (!pageName) {
    window.alert('当前页面尚未确定，请先采集或绑定当前页面。');
    return;
  }
  if (store.popupType) selectPopupType(null, false);
  const sessionId = `${Date.now()}_${Math.random().toString(16).slice(2, 8)}`;
  specialCapture = {
    id: sessionId,
    pageName,
    stepCount: 0,
    operationIds: [],
  };
  refreshSpecialButtons();
  render(store.data);
  renderSpecialOperateHint();
}

function finishSpecialOperate() {
  if (!specialCapture) return;
  if (!specialCapture.stepCount) {
    window.alert('当前 special_operate 还没有录制任何步骤。');
    return;
  }
  const count = specialCapture.stepCount;
  specialCapture = null;
  refreshSpecialButtons();
  render(store.data);
  const status = el('overlayStatus');
  status.textContent = `已完成一组 special_operate，共 ${count} 步；DFS 导出时会作为同一个 operateN 输出。`;
  status.classList.remove('hidden');
}

async function rollbackSpecialOperate() {
  if (!specialCapture) return;
  const capture = specialCapture;
  specialCapture = null;
  refreshSpecialButtons();
  for (const operationId of capture.operationIds) {
    const body = {
      page_name: capture.pageName,
      operation_id: operationId,
      delete_revealed_candidates: false,
    };
    const preview = await postJson('/api/delete_action', {
      target_type: 'page_operation',
      payload: body,
      dry_run: true,
    });
    if (!preview?.preview_token) continue;
    await postJson('/api/delete_action', {
      target_type: 'page_operation',
      payload: body,
      dry_run: false,
      preview_token: preview.preview_token,
    });
  }
  const data = await api('/api/state');
  renderFollowingActivePage(data);
  const status = el('overlayStatus');
  status.textContent = `已取消本组 special_operate，并回滚 ${capture.operationIds.length} 个已录步骤。`;
  status.classList.remove('hidden');
}

function selectPopupType(popupType = null, rerender = true) {
  if (popupType && specialCapture) {
    window.alert('当前正在录制多步 special_operate，请先完成或取消本组采集，再录制弹窗。');
    return;
  }
  store.popupType = popupType && popupTypes.has(popupType) ? popupType : null;
  popupTypeButtons.forEach((button) => {
    const selected = button.dataset.popupType === store.popupType;
    button.classList.toggle('isArmed', selected);
    button.setAttribute('aria-pressed', String(selected));
  });
  if (rerender) render(store.data);
}

function renderFollowingActivePage(data) {
  if (!data) return;
  store.selectedPage = null;
  store.showingOrphans = false;
  render(data);
  refreshSpecialButtons();
  if (specialCapture) renderSpecialOperateHint();
}

el('captureBtn').onclick = async () => renderFollowingActivePage(
  await postJson('/api/console_action', { action: 'capture_current', payload: {} }),
);
el('backBtn').onclick = async () => renderFollowingActivePage(
  await postJson('/api/console_action', { action: 'system_back', payload: {} }),
);
el('clearPendingBtn').onclick = async () => render(await postJson('/api/console_action', { action: 'clear_pending', payload: {} }));
el('orphanBtn').onclick = refreshOrphans;
popupTypeButtons.forEach((button) => {
  button.onclick = () => selectPopupType(button.dataset.popupType);
});
specialOperateButton.onclick = startSpecialOperate;
finishSpecialButton.onclick = finishSpecialOperate;
cancelSpecialButton.onclick = rollbackSpecialOperate;

el('bindCurrentPageBtn').onclick = async () => {
  const pageName = String(store.selectedPage || '').trim();
  if (!pageName) {
    window.alert('请先在页面目录中点击“详情”选择一个已经维护/命名的页面，再执行“指定所选页面为当前页面”。');
    return;
  }
  const data = await postJson('/api/console_action', {
    action: 'bind_current_page',
    payload: { page_name: pageName },
  });
  renderFollowingActivePage(data);
};

el('addAndBindCurrentPageBtn').onclick = async () => {
  let pageName = window.prompt('请输入新的内部页面 ID，例如 Pages_高级设置：', 'Pages_');
  if (!pageName) return;
  pageName = pageName.trim();
  if (!pageName.startsWith('Pages_')) pageName = `Pages_${pageName}`;
  const pageDescription = window.prompt('请输入页面显示名称（无标题页必须人工填写）：', pageName.replace(/^Pages_/, ''));
  if (!pageDescription?.trim()) return;

  const created = await postJson('/api/console_action', {
    action: 'create_manual_page',
    payload: {
      page_name: pageName,
      page_description: pageDescription.trim(),
    },
  });
  if (!created) return;
  renderFollowingActivePage(created);
  const status = el('overlayStatus');
  status.textContent = `已新增并绑定人工页面 ${pageName}。下一步请在页面详情中人工维护该页面 DFS path_snapshot。`;
  status.classList.remove('hidden');
};

el('graphBtn').onclick = async () => {
  const data = await api('/api/graph');
  if (!data) return;
  const box = el('graphBox');
  box.textContent = JSON.stringify(data, null, 2);
  box.classList.toggle('hidden');
};

el('screen').addEventListener('click', async (event) => {
  if (!store.data?.screen_metrics?.screen_size || store.busy) return;
  const rect = el('screen').getBoundingClientRect();
  const [screenWidth, screenHeight] = store.data.screen_metrics.screen_size;
  const point = {
    x: Math.round((event.clientX - rect.left) / rect.width * screenWidth),
    y: Math.round((event.clientY - rect.top) / rect.height * screenHeight),
    manual_label: '',
  };
  const popupType = store.popupType;
  const special = specialCapture;
  const nextSpecialStep = special ? special.stepCount + 1 : 0;
  const action = popupType ? 'popup_tap' : special ? 'special_tap' : 'tap_point';
  const payload = popupType
    ? { ...point, popup_type: popupType }
    : special
      ? {
          ...point,
          operate: 'tap',
          effect: specialEffect(special.id, nextSpecialStep),
        }
      : { ...point, expect: 'new_page', effect: '' };
  let data = null;
  try {
    data = await postJson('/api/record_action', { action, payload });
    if (data?.needs_manual_label) {
      const label = window.prompt(data.message || '请填写该控件的稳定描述');
      if (label) data = await postJson('/api/record_action', { action, payload: { ...payload, manual_label: label } });
    }
  } finally {
    if (popupType) selectPopupType(null, false);
  }
  if (!data) {
    if (specialCapture) renderSpecialOperateHint('本步没有保存；仍处于 special_operate 采集状态，可继续操作或取消。');
    return;
  }
  if (specialCapture) {
    specialCapture.stepCount = nextSpecialStep;
    syncSpecialOperationIds(data);
    render(data);
    refreshSpecialButtons();
    renderSpecialOperateHint();
    return;
  }
  renderFollowingActivePage(data);
});

window.addEventListener('resize', renderOverlay);
api('/api/state').then((data) => {
  render(data);
  refreshSpecialButtons();
});
