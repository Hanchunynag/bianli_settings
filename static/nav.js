let state = null;
let busy = false;
let highlighted = null;
let mode = 'tap';
let lastClickPoint = null;

const el = (id) => document.getElementById(id);

function setBusy(value) {
  busy = value;
  el('loading').classList.toggle('hidden', !value);
  document.querySelectorAll('button').forEach((btn) => btn.disabled = value);
}

function setMode(nextMode) {
  mode = nextMode;
  el('modeLabel').textContent = nextMode;
  document.querySelectorAll('button.mode').forEach((btn) => btn.classList.remove('active'));
  const id = nextMode === 'tap' ? 'modeTapBtn' : nextMode === 'swipe_left' ? 'modeSwipeLeftBtn' : 'modeSwipeRightBtn';
  el(id).classList.add('active');
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
    row.innerHTML = `<div class="candidateText">${escapeHtml(candidateLine(c))}</div><button>按中心点录制</button>`;
    row.addEventListener('mouseenter', () => highlight(c.index));
    row.querySelector('button').addEventListener('click', async () => {
      const center = c.bounds_center || [];
      if (center.length !== 2) return showError('该候选入口没有有效 center');
      lastClickPoint = { x: Number(center[0]), y: Number(center[1]) };
      const data = await recordPoint(lastClickPoint.x, lastClickPoint.y, normalizedPoint(lastClickPoint.x, lastClickPoint.y));
      render(data);
    });
    box.appendChild(row);
  });
}

function renderOverlay(candidates) {
  const img = el('screen');
  const overlay = el('overlay');
  overlay.innerHTML = '';
  if (!img.complete || !img.naturalWidth) return;
  const rect = img.getBoundingClientRect();
  const wrapRect = el('screenWrap').getBoundingClientRect();
  overlay.style.left = `${rect.left - wrapRect.left + el('screenWrap').scrollLeft}px`;
  overlay.style.top = `${rect.top - wrapRect.top + el('screenWrap').scrollTop}px`;
  overlay.style.width = `${rect.width}px`;
  overlay.style.height = `${rect.height}px`;
  const size = state?.screen_size || state?.screen_metrics?.screen_size || [img.naturalWidth, img.naturalHeight];
  const [sw, sh] = size;
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
  if (lastClickPoint) {
    const p = document.createElement('div');
    p.className = 'clickPoint';
    p.style.left = `${lastClickPoint.x / img.naturalWidth * rect.width}px`;
    p.style.top = `${lastClickPoint.y / img.naturalHeight * rect.height}px`;
    overlay.appendChild(p);
  }
}

function highlight(index) {
  highlighted = index;
  document.querySelectorAll('.candidate').forEach((row) => row.classList.toggle('active', Number(row.dataset.index) === index));
  renderOverlay(state?.candidates || []);
}

function escapeHtml(text) {
  return String(text).replace(/[&<>"]/g, (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[ch]));
}

function pointFromMouse(evt) {
  const img = el('screen');
  const rect = img.getBoundingClientRect();
  if (!img.naturalWidth || !rect.width || evt.clientX < rect.left || evt.clientX > rect.right || evt.clientY < rect.top || evt.clientY > rect.bottom) {
    return null;
  }
  const x = Math.round((evt.clientX - rect.left) / rect.width * img.naturalWidth);
  const y = Math.round((evt.clientY - rect.top) / rect.height * img.naturalHeight);
  return { x, y, normalized_point: normalizedPoint(x, y) };
}

function normalizedPoint(x, y) {
  const img = el('screen');
  const w = img.naturalWidth || state?.screen_size?.[0] || 1;
  const h = img.naturalHeight || state?.screen_size?.[1] || 1;
  return [Number((x / w).toFixed(6)), Number((y / h).toFixed(6))];
}

async function recordPoint(x, y, normalized_point) {
  if (mode === 'tap') {
    return api('/api/tap_point', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ x, y, normalized_point, expect: 'new_page', effect: '' })
    });
  }
  return api('/api/swipe_point', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ x, y, normalized_point, direction: mode === 'swipe_left' ? 'left' : 'right' })
  });
}

el('screen').addEventListener('mousemove', (evt) => {
  const point = pointFromMouse(evt);
  el('coordLabel').textContent = point ? `坐标: [${point.x}, ${point.y}] normalized=${JSON.stringify(point.normalized_point)}` : '坐标: -';
});

el('screen').addEventListener('click', async (evt) => {
  if (busy) return;
  const point = pointFromMouse(evt);
  if (!point) return;
  lastClickPoint = { x: point.x, y: point.y };
  renderOverlay(state?.candidates || []);
  const data = await recordPoint(point.x, point.y, point.normalized_point);
  render(data);
});

el('modeTapBtn').onclick = () => setMode('tap');
el('modeSwipeLeftBtn').onclick = () => setMode('swipe_left');
el('modeSwipeRightBtn').onclick = () => setMode('swipe_right');
el('captureBtn').onclick = async () => render(await api('/api/capture', { method: 'POST' }));
el('backBtn').onclick = async () => render(await api('/api/back', { method: 'POST' }));
el('clearPendingBtn').onclick = async () => { await api('/api/clear_pending', { method: 'POST' }); render(await api('/api/state')); };
el('undoBtn').onclick = async () => render(await api('/api/undo_last', { method: 'POST' }));
el('graphBtn').onclick = async () => {
  const data = await fetch('/api/graph').then((r) => r.json()).catch((e) => ({ error: e.message }));
  const box = el('graphBox');
  box.textContent = JSON.stringify(data, null, 2);
  box.classList.toggle('hidden');
};
window.addEventListener('resize', () => renderOverlay(state?.candidates || []));
setMode('tap');
api('/api/state').then(render);
