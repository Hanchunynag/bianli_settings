import { api, postJson, requestJson } from './nav/api.js';
import { el } from './nav/dom.js';
import { store } from './nav/state.js';
import { render, renderOverlay } from './nav/render.js';

function bindCommandButtons() {
  el('captureBtn').onclick = async () => render(await api('/api/capture', { method: 'POST' }));
  el('backBtn').onclick = async () => render(await api('/api/back', { method: 'POST' }));
  el('clearPendingBtn').onclick = async () => {
    await api('/api/clear_pending', { method: 'POST' });
    render(await api('/api/state'));
  };
  el('markOverlayBtn').onclick = async () => render(await api('/api/mark_current_as_overlay', { method: 'POST' }));
  el('continuePageBtn').onclick = async () => render(await api('/api/continue_current_page', { method: 'POST' }));
  el('swipeLeftBtn').onclick = async () => render(await postJson('/api/swipe_horizontal', { direction: 'left' }));
  el('swipeRightBtn').onclick = async () => render(await postJson('/api/swipe_horizontal', { direction: 'right' }));
  el('samePageModeBtn').onclick = () => {
    store.samePageMode = !store.samePageMode;
    if (store.samePageMode) store.pageOperationMode = false;
    el('samePageModeBtn').textContent = store.samePageMode ? '退出页面内变化模式' : '录制页面内变化';
    el('pageOperationModeBtn').textContent = '录制页面内操作';
    render(store.data);
  };
  el('pageOperationModeBtn').onclick = () => {
    store.pageOperationMode = !store.pageOperationMode;
    if (store.pageOperationMode) store.samePageMode = false;
    el('pageOperationModeBtn').textContent = store.pageOperationMode ? '退出页面内操作模式' : '录制页面内操作';
    el('samePageModeBtn').textContent = '录制页面内变化';
    el('moreActions').open = false;
    render(store.data);
  };
  el('graphBtn').onclick = async () => {
    const data = await requestJson('/api/graph').catch((err) => ({ error: err.message }));
    const box = el('graphBox');
    box.textContent = JSON.stringify(data, null, 2);
    box.classList.toggle('hidden');
  };
  el('moreActions').addEventListener('click', (event) => {
    if (event.target.tagName === 'BUTTON') {
      el('moreActions').open = false;
    }
  });
}

function bindScreenRecorder() {
  el('screen').addEventListener('click', async (event) => {
    if (!store.data?.screen_metrics?.screen_size || store.busy) return;
    const rect = el('screen').getBoundingClientRect();
    const [screenWidth, screenHeight] = store.data.screen_metrics.screen_size;
    const x = Math.round((event.clientX - rect.left) / rect.width * screenWidth);
    const y = Math.round((event.clientY - rect.top) / rect.height * screenHeight);
    const endpoint = store.pageOperationMode
      ? '/api/page_gesture_operation'
      : store.samePageMode
        ? '/api/tap_same_page_operation'
        : '/api/tap_point';
    const operationBody = {
      x,
      y,
      operate: el('operationGesture').value,
      effect: el('operationEffect').value.trim(),
      manual_label: '',
    };
    let data = await postJson(endpoint, store.pageOperationMode ? operationBody : store.samePageMode ? { x, y, manual_label: '' } : { x, y, expect: 'new_page', effect: '' });
    if (data?.needs_manual_label) {
      const label = window.prompt(data.message || '请填写该控件的稳定描述');
      if (label) {
        data = await postJson(endpoint, store.samePageMode
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
