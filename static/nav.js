let state = null;
let busy = false;
let highlighted = null;
let samePageMode = false;

const el = (id) => document.getElementById(id);

function setBusy(value) {
  busy = value;
  el('loading').classList.toggle('hidden', !value);
  document.querySelectorAll('button').forEach((btn) => btn.disabled = value);
}

function showError(message) {
  el('error').textContent = message || '';
  el('error').classList.toggle('hidden', !message);
}

async function api(path, options = {}) {
  if (busy) return;
  setBusy(true);
  showError('');
  try {
    const res = await fetch(path, options);
    const data = await res.json();
    if (!data.ok && data.error) throw new Error(data.error);
    return data;
  } catch (err) {
    showError(err.message || String(err));
  } finally {
    setBusy(false);
  }
}

function render(data) {
  if (!data) return;
  state = data;
  highlighted = null;
  el('pageName').textContent = data.state?.page_name || '-';
  el('activePage').textContent = data.active_page || data.active_state?.page_name || '-';
  el('title').textContent = data.state?.last_title || '-';
  el('pending').textContent = data.pending ? `${data.pending.from_page} -> ${(data.pending.target || {}).step_prompt || (data.pending.target || {}).value || ''}` : '无';
  el('warning').textContent = data.warning || '';
  el('warning').classList.toggle('hidden', !data.warning);
  const modeMsg = samePageMode ? '当前模式：录制页面内变化。点击截图后将刷新并合并当前页面内容。' : '';
  const overlayMsg = data.message || modeMsg || (data.state?.is_overlay ? '当前页面已标记为弹窗页面' : '');
  el('overlayStatus').textContent = overlayMsg;
  el('overlayStatus').classList.toggle('hidden', !overlayMsg);
  if (data.screenshot_url) el('screen').src = data.screenshot_url;
  renderCandidates(data.candidates || []);
  el('screen').onload = () => renderOverlay(data.candidates || []);
  renderOverlay(data.candidates || []);
}

function candidateLine(c) {
  return `${c.index}. [${c.type || ''}] ${c.text || ''} key=${c.key || '(无 key)'} center=${JSON.stringify(c.bounds_center || [])}`;
}

function renderCandidates(candidates) {
  const box = el('candidates');
  box.innerHTML = '';
  if (!candidates.length) {
    box.innerHTML = '<div class="muted">暂无候选入口。请点击“重新采集”。</div>';
    return;
  }
  candidates.forEach((c) => {
    const row = document.createElement('div');
    row.className = 'candidate';
    row.dataset.index = c.index;
    row.innerHTML = `<div class="candidateText">${escapeHtml(candidateLine(c))}</div><button>点击并录制</button>`;
    row.addEventListener('mouseenter', () => highlight(c.index));
    row.querySelector('button').addEventListener('click', async () => {
      const data = await api('/api/tap_candidate', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ index: c.index, expect: 'new_page', effect: '' })
      });
      render(data);
    });
    box.appendChild(row);
  });
}

function renderOverlay(candidates) {
  const img = el('screen');
  const overlay = el('overlay');
  overlay.innerHTML = '';
  if (!img.complete || !img.naturalWidth || !state?.screen_metrics?.screen_size) return;
  const rect = img.getBoundingClientRect();
  const wrapRect = el('screenWrap').getBoundingClientRect();
  overlay.style.left = `${rect.left - wrapRect.left + el('screenWrap').scrollLeft}px`;
  overlay.style.top = `${rect.top - wrapRect.top + el('screenWrap').scrollTop}px`;
  overlay.style.width = `${rect.width}px`;
  overlay.style.height = `${rect.height}px`;
  const [sw, sh] = state.screen_metrics.screen_size;
  candidates.forEach((c) => {
    const center = c.bounds_center;
    if (!Array.isArray(center) || center.length !== 2) return;
    const m = document.createElement('div');
    m.className = `marker ${highlighted === c.index ? 'active' : ''}`;
    m.textContent = c.index;
    m.style.left = `${center[0] / sw * rect.width}px`;
    m.style.top = `${center[1] / sh * rect.height}px`;
    overlay.appendChild(m);
  });
}

function highlight(index) {
  highlighted = index;
  document.querySelectorAll('.candidate').forEach((row) => row.classList.toggle('active', Number(row.dataset.index) === index));
  renderOverlay(state?.candidates || []);
}

function escapeHtml(text) {
  return String(text).replace(/[&<>"]/g, (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[ch]));
}

el('captureBtn').onclick = async () => render(await api('/api/capture', { method: 'POST' }));
el('backBtn').onclick = async () => render(await api('/api/back', { method: 'POST' }));
el('clearPendingBtn').onclick = async () => { await api('/api/clear_pending', { method: 'POST' }); render(await api('/api/state')); };
el('markOverlayBtn').onclick = async () => render(await api('/api/mark_current_as_overlay', { method: 'POST' }));
el('samePageModeBtn').onclick = () => {
  samePageMode = !samePageMode;
  el('samePageModeBtn').textContent = samePageMode ? '退出页面内变化模式' : '录制页面内变化';
  render(state);
};
el('swipeLeftBtn').onclick = async () => render(await api('/api/swipe_horizontal', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ direction: 'left' }) }));
el('swipeRightBtn').onclick = async () => render(await api('/api/swipe_horizontal', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ direction: 'right' }) }));
el('graphBtn').onclick = async () => {
  const data = await fetch('/api/graph').then((r) => r.json()).catch((e) => ({ error: e.message }));
  const box = el('graphBox');
  box.textContent = JSON.stringify(data, null, 2);
  box.classList.toggle('hidden');
};
el('screen').addEventListener('click', async (ev) => {
  if (!state?.screen_metrics?.screen_size || busy) return;
  const rect = el('screen').getBoundingClientRect();
  const [sw, sh] = state.screen_metrics.screen_size;
  const x = Math.round((ev.clientX - rect.left) / rect.width * sw);
  const y = Math.round((ev.clientY - rect.top) / rect.height * sh);
  const endpoint = samePageMode ? '/api/tap_same_page_operation' : '/api/tap_point';
  let data = await api(endpoint, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(samePageMode ? { x, y, manual_label: '' } : { x, y, expect: 'new_page', effect: '' })
  });
  if (data?.needs_manual_label) {
    const label = window.prompt(data.message || '请填写该控件的稳定描述');
    if (label) {
      data = await api(endpoint, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(samePageMode ? { x, y, manual_label: label } : { x, y, expect: 'new_page', effect: '', manual_label: label })
      });
    }
  }
  render(data);
});
window.addEventListener('resize', () => renderOverlay(state?.candidates || []));
api('/api/state').then(render);
