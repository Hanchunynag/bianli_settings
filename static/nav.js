import { api, postJson, requestJson } from './nav/api.js';
import { el } from './nav/dom.js';
import { store } from './nav/state.js';
import { refreshDirectory, render, renderOverlay } from './nav/render.js';

const consoleAction = (action, payload = {}) => postJson('/api/console_action', { action, payload });
const recordAction = (action, payload = {}) => postJson('/api/record_action', { action, payload });

function bindCommandButtons() {
  el('captureBtn').onclick = async () => render(await consoleAction('capture_current'));
  el('backBtn').onclick = async () => render(await consoleAction('system_back'));
  el('clearPendingBtn').onclick = async () => render(await consoleAction('clear_pending'));
  el('markOverlayBtn').onclick = async () => render(await consoleAction('mark_overlay'));
  el('continuePageBtn').onclick = async () => render(await consoleAction('continue_current_page'));
  el('swipeLeftBtn').onclick = async () => render(await consoleAction('swipe_horizontal', { direction: 'left' }));
  el('swipeRightBtn').onclick = async () => render(await consoleAction('swipe_horizontal', { direction: 'right' }));
  el('samePageModeBtn').onclick = () => {
    store.samePageMode = !store.samePageMode;
    if (store.samePageMode) store.pageOperationMode = false;
    el('samePageModeBtn').textContent = store.samePageMode ? '停止记录同页点击变化' : '开始记录同页点击变化';
    el('pageOperationModeBtn').textContent = '开始记录同页手势';
    render(store.data);
  };
  el('pageOperationModeBtn').onclick = () => {
    store.pageOperationMode = !store.pageOperationMode;
    if (store.pageOperationMode) store.samePageMode = false;
    el('pageOperationModeBtn').textContent = store.pageOperationMode ? '停止记录同页手势' : '开始记录同页手势';
    el('samePageModeBtn').textContent = '开始记录同页点击变化';
    render(store.data);
  };
  el('graphBtn').onclick = async () => {
    const data = await requestJson('/api/graph').catch((err) => ({ error: err.message }));
    const box = el('graphBox');
    box.textContent = JSON.stringify(data, null, 2);
    box.classList.toggle('hidden');
  };
  el('pageSearch').addEventListener('input', (event) => {
    store.directoryQuery = event.target.value.trim().toLowerCase();
    refreshDirectory();
  });
}

function bindScreenRecorder() {
  el('screen').addEventListener('click', async (event) => {
    if (!store.data?.screen_metrics?.screen_size || store.busy) return;
    const rect = el('screen').getBoundingClientRect();
    const [screenWidth, screenHeight] = store.data.screen_metrics.screen_size;
    const x = Math.round((event.clientX - rect.left) / rect.width * screenWidth);
    const y = Math.round((event.clientY - rect.top) / rect.height * screenHeight);
    const action = store.pageOperationMode
      ? 'same_page_gesture'
      : store.samePageMode
        ? 'same_page_tap'
        : 'tap_point';
    const operationBody = {
      x,
      y,
      operate: el('operationGesture').value,
      effect: el('operationEffect').value.trim(),
      manual_label: '',
    };
    let data = await recordAction(action, store.pageOperationMode ? operationBody : store.samePageMode ? { x, y, manual_label: '' } : { x, y, expect: 'new_page', effect: '' });
    if (data?.needs_manual_label) {
      const label = window.prompt(data.message || '请填写该控件的稳定描述');
      if (label) {
        data = await recordAction(action, store.samePageMode
          ? { x, y, manual_label: label }
          : store.pageOperationMode
            ? { ...operationBody, manual_label: label }
          : { x, y, expect: 'new_page', effect: '', manual_label: label });
      }
    }
    render(data);
  });

  window.addEventListener('resize', () => renderOverlay([]));
}

bindCommandButtons();
bindScreenRecorder();
api('/api/state').then(render);
