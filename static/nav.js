import { api, postJson } from './nav/api.js';
import { el } from './nav/dom.js';
import { store } from './nav/state.js';
import { refreshOrphans, render, renderOverlay } from './nav/render.js?v=follow-active-page-12';

const popupTypeButtons = [...document.querySelectorAll('[data-popup-type]')];
const popupTypes = new Set(popupTypeButtons.map((button) => button.dataset.popupType));
const specialOperateButton = el('special_opeartebutton');
let specialOperateMode = false;

function renderSpecialOperateHint() {
  if (!specialOperateMode) return;
  const status = el('overlayStatus');
  status.textContent = '单次采集：当前页面操作。点击截图中的控件后只允许保存同页操作；若发生页面跳转则拒绝保存。';
  status.classList.remove('hidden');
}

function selectSpecialOperate(enabled, rerender = true) {
  specialOperateMode = Boolean(enabled);
  if (specialOperateMode && store.popupType) selectPopupType(null, false);
  specialOperateButton.classList.toggle('isArmed', specialOperateMode);
  specialOperateButton.setAttribute('aria-pressed', String(specialOperateMode));
  if (rerender) {
    render(store.data);
    renderSpecialOperateHint();
  }
}

function selectPopupType(popupType = null, rerender = true) {
  store.popupType = popupType && popupTypes.has(popupType) ? popupType : null;
  if (store.popupType && specialOperateMode) selectSpecialOperate(false, false);
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
specialOperateButton.onclick = () => selectSpecialOperate(!specialOperateMode);
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
  const samePageMode = specialOperateMode;
  const action = popupType ? 'popup_tap' : samePageMode ? 'same_page_tap' : 'tap_point';
  const payload = popupType
    ? { ...point, popup_type: popupType }
    : samePageMode
      ? point
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
    if (samePageMode) selectSpecialOperate(false, false);
  }
  renderFollowingActivePage(data);
});

window.addEventListener('resize', renderOverlay);
api('/api/state').then(render);
