import { api, postJson, requestJson } from './nav/api.js';
import { el } from './nav/dom.js';
import { store } from './nav/state.js?v=tree-directory-5';
import { render, renderOverlay } from './nav/render.js?v=tree-directory-5';

const consoleAction = (action, payload = {}) => postJson('/api/console_action', { action, payload });
const recordAction = (action, payload = {}) => postJson('/api/record_action', { action, payload });
const recorderModes = {
  samePageMode: ['samePageModeBtn', '开始记录同页点击变化', '停止记录同页点击变化'],
  popupMode: ['popupModeBtn', '开始记录弹窗操作', '停止记录弹窗操作'],
  pageOperationMode: ['pageOperationModeBtn', '开始记录同页手势', '停止记录同页手势'],
};

function toggleRecorderMode(selectedMode) {
  const enabled = !store[selectedMode];
  Object.entries(recorderModes).forEach(([mode, [buttonId, startText, stopText]]) => {
    store[mode] = mode === selectedMode && enabled;
    el(buttonId).textContent = store[mode] ? stopText : startText;
  });
  render(store.data);
}

function screenRecordRequest(x, y) {
  const point = { x, y, manual_label: '' };
  if (store.popupMode) return ['popup_tap', point];
  if (store.pageOperationMode) return ['same_page_gesture', {
    ...point,
    operate: el('operationGesture').value,
    effect: el('operationEffect').value.trim(),
  }];
  if (store.samePageMode) return ['same_page_tap', point];
  return ['tap_point', { ...point, expect: 'new_page', effect: '' }];
}

function bindCommandButtons() {
  el('captureBtn').onclick = async () => render(await consoleAction('capture_current'));
  el('backBtn').onclick = async () => render(await consoleAction('system_back'));
  el('clearPendingBtn').onclick = async () => render(await consoleAction('clear_pending'));
  el('markOverlayBtn').onclick = async () => render(await consoleAction('mark_overlay'));
  el('continuePageBtn').onclick = async () => render(await consoleAction('continue_current_page'));
  el('swipeLeftBtn').onclick = async () => render(await consoleAction('swipe_horizontal', { direction: 'left' }));
  el('swipeRightBtn').onclick = async () => render(await consoleAction('swipe_horizontal', { direction: 'right' }));
  Object.entries(recorderModes).forEach(([mode, [buttonId]]) => {
    el(buttonId).onclick = () => toggleRecorderMode(mode);
  });
  el('graphBtn').onclick = async () => {
    const data = await requestJson('/api/graph').catch((err) => ({ error: err.message }));
    const box = el('graphBox');
    box.textContent = JSON.stringify(data, null, 2);
    box.classList.toggle('hidden');
  };
}

function bindScreenRecorder() {
  el('screen').addEventListener('click', async (event) => {
    if (!store.data?.screen_metrics?.screen_size || store.busy) return;
    const rect = el('screen').getBoundingClientRect();
    const [screenWidth, screenHeight] = store.data.screen_metrics.screen_size;
    const x = Math.round((event.clientX - rect.left) / rect.width * screenWidth);
    const y = Math.round((event.clientY - rect.top) / rect.height * screenHeight);
    const [action, payload] = screenRecordRequest(x, y);
    let data = await recordAction(action, payload);
    if (data?.needs_manual_label) {
      const label = window.prompt(data.message || '请填写该控件的稳定描述');
      if (label) data = await recordAction(action, { ...payload, manual_label: label });
    }
    render(data);
  });

  window.addEventListener('resize', () => renderOverlay([]));
}

bindCommandButtons();
bindScreenRecorder();
api('/api/state').then(render);
