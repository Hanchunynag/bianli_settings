import { api, postJson } from './nav/api.js';
import { el } from './nav/dom.js';
import { store } from './nav/state.js';
import { refreshOrphans, render, renderOverlay } from './nav/render.js?v=page-names-8';

const popupTypeButtons = [...document.querySelectorAll('[data-popup-type]')];
const popupTypes = new Set(popupTypeButtons.map((button) => button.dataset.popupType));

function selectPopupType(popupType = null, rerender = true) {
  store.popupType = popupType && popupTypes.has(popupType) ? popupType : null;
  popupTypeButtons.forEach((button) => {
    const selected = button.dataset.popupType === store.popupType;
    button.classList.toggle('isArmed', selected);
    button.setAttribute('aria-pressed', String(selected));
  });
  if (rerender) render(store.data);
}

el('captureBtn').onclick = async () => render(await postJson('/api/console_action', { action: 'capture_current', payload: {} }));
el('backBtn').onclick = async () => render(await postJson('/api/console_action', { action: 'system_back', payload: {} }));
el('clearPendingBtn').onclick = async () => render(await postJson('/api/console_action', { action: 'clear_pending', payload: {} }));
el('orphanBtn').onclick = refreshOrphans;
popupTypeButtons.forEach((button) => {
  button.onclick = () => selectPopupType(button.dataset.popupType);
});
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
  const action = popupType ? 'popup_tap' : 'tap_point';
  const payload = popupType
    ? { ...point, popup_type: popupType }
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
  render(data);
});

window.addEventListener('resize', renderOverlay);
api('/api/state').then(render);
