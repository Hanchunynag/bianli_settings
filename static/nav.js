let state = null;
let busy = false;
let highlighted = null;
let samePageMode = false;
let selectedPage = null;

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

async function refreshDirectory() {
  const data = await fetch('/api/page_directory').then((r) => r.json()).catch((e) => ({ ok: false, error: e.message }));
  if (data.ok) renderDirectory(data);
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
  renderCandidates(data.current_candidates || data.candidates || [], data.merged_candidates || []);
  el('screen').onload = () => renderOverlay(data.current_candidates || data.candidates || []);
  renderOverlay(data.current_candidates || data.candidates || []);
  refreshDirectory();
}

function candidateTitle(c) {
  return c.step_prompt || c.key_description || c.text || c.value || c.key || c.candidate_id || '';
}

function candidateLine(c, index) {
  const type = c.component_type || c.type || '';
  const source = c.source ? ` source=${c.source}` : '';
  const status = c.label ? ` status=${c.label}` : '';
  const center = c.bounds_center ? ` center=${JSON.stringify(c.bounds_center)}` : '';
  return `${index ? index + '. ' : ''}[${type}] ${candidateTitle(c)} key=${c.key || '(无 key)'}${source}${status}${center}`;
}

function button(label, onClick, className = '') {
  const b = document.createElement('button');
  b.textContent = label;
  if (className) b.className = className;
  b.addEventListener('click', onClick);
  return b;
}

function renderCandidates(currentCandidates, mergedCandidates) {
  const box = el('candidates');
  box.innerHTML = '';
  const currentTitle = document.createElement('h3');
  currentTitle.textContent = '当前屏幕';
  box.appendChild(currentTitle);
  if (!currentCandidates.length) box.insertAdjacentHTML('beforeend', '<div class="muted">暂无当前屏幕候选。请点击“重新采集”。</div>');
  currentCandidates.forEach((c, i) => {
    const row = document.createElement('div');
    row.className = 'candidate';
    row.dataset.index = c.index;
    row.innerHTML = `<div class="candidateText">${escapeHtml(candidateLine(c, c.index || i + 1))}</div>`;
    row.appendChild(button('点击并录制', async () => render(await api('/api/tap_candidate', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ index: c.index, expect: 'new_page', effect: '' })
    }))));
    row.addEventListener('mouseenter', () => highlight(c.index));
    box.appendChild(row);
  });
  const mergedTitle = document.createElement('h3');
  mergedTitle.textContent = '当前页面候选库';
  box.appendChild(mergedTitle);
  if (!mergedCandidates.length) box.insertAdjacentHTML('beforeend', '<div class="muted">暂无候选库。</div>');
  mergedCandidates.forEach((c) => {
    const row = document.createElement('div');
    row.className = 'candidate';
    row.innerHTML = `<div class="candidateText">${escapeHtml(candidateLine(c))}</div>`;
    row.appendChild(button('删除候选', () => dryRunDelete('/api/delete_candidate', { page_name: state.active_page || state.state?.page_name, candidate_id: c.candidate_id, dry_run: true })));
    row.appendChild(button('删候选及跳转', () => dryRunDelete('/api/delete_candidate', { page_name: state.active_page || state.state?.page_name, candidate_id: c.candidate_id, delete_linked_transitions: true, delete_linked_operations: true, dry_run: true })));
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
  renderOverlay(state?.current_candidates || state?.candidates || []);
}

function renderDirectory(data) {
  const box = el('pageDirectory');
  box.innerHTML = '';
  function addNode(node, depth = 0) {
    const row = document.createElement('div');
    row.className = 'dirNode';
    row.style.paddingLeft = `${depth * 14}px`;
    row.innerHTML = `<span>${escapeHtml(node.title || node.page_name)}</span> <code>${escapeHtml(node.page_name)}</code>`;
    row.appendChild(button('详情', () => loadPageDetail(node.page_name), 'secondary'));
    if (node.via?.transition_id) row.appendChild(button('删分支', () => dryRunDelete('/api/delete_branch', { transition_id: node.via.transition_id, delete_descendants: true, dry_run: true })));
    row.appendChild(button('删页面', () => dryRunDelete('/api/delete_page', { page_name: node.page_name, dry_run: true }), 'danger'));
    box.appendChild(row);
    (node.children || []).forEach((child) => addNode(child, depth + 1));
  }
  (data.items || []).forEach((n) => addNode(n));
}

async function loadPageDetail(pageName) {
  selectedPage = pageName;
  const data = await fetch(`/api/page_detail?page_name=${encodeURIComponent(pageName)}`).then((r) => r.json());
  if (!data.ok) return showError(data.error || '加载页面详情失败');
  renderPageDetail(data);
}

function renderPageDetail(data) {
  const box = el('pageDetail');
  const incoming = data.incoming_transitions || [];
  const outgoing = data.outgoing_transitions || [];
  const ops = data.page_operations || [];
  const caps = data.continued_captures || [];
  box.innerHTML = `<h3>${escapeHtml(data.state?.last_title || data.page_name)}</h3><p><code>${escapeHtml(data.page_name)}</code></p>`;
  box.appendChild(button('设为当前 active_page', async () => render(await api('/api/set_active_page', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ page_name: data.page_name }) }))));
  box.appendChild(button('从 root 执行跳转到此页', async () => {
    if (!confirm('请确认手机当前位于设置首页 Pages_root。继续执行跳转？')) return;
    render(await api('/api/navigate_to_page', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ page_name: data.page_name }) }));
  }));
  box.appendChild(button('查看 graph 原始 JSON', () => el('graphBtn').click(), 'secondary'));
  appendList(box, '入边', incoming, (t) => `${t.from_page} -> ${t.to_page} ${transitionLabel(t)}`, (t) => [button('删除这条跳转', () => dryRunDelete('/api/delete_transition', { transition_id: t.transition_id, dry_run: true }))]);
  appendList(box, '出边', outgoing, (t) => `${t.from_page} -> ${t.to_page} ${transitionLabel(t)}`, (t) => [button('删除这条跳转', () => dryRunDelete('/api/delete_transition', { transition_id: t.transition_id, dry_run: true }))]);
  appendList(box, '候选入口库', data.merged_candidates || [], (c) => `${candidateTitle(c)} ${c.label || ''}`, (c) => [button('删除候选', () => dryRunDelete('/api/delete_candidate', { page_name: data.page_name, candidate_id: c.candidate_id, dry_run: true })), button('删除候选及关联跳转', () => dryRunDelete('/api/delete_candidate', { page_name: data.page_name, candidate_id: c.candidate_id, delete_linked_transitions: true, delete_linked_operations: true, dry_run: true }))]);
  appendList(box, '页面内操作', ops, (o) => `${o.operation_id} ${candidateTitle(o.target || {})}`, (o) => [button('删除页面内操作', () => dryRunDelete('/api/delete_page_operation', { page_name: data.page_name, operation_id: o.operation_id, delete_revealed_candidates: false, dry_run: true })), button('删除页面内操作及其新增控件', () => dryRunDelete('/api/delete_page_operation', { page_name: data.page_name, operation_id: o.operation_id, delete_revealed_candidates: true, dry_run: true }))]);
  appendList(box, '继续录制', caps, (c) => `${c.capture_id} candidates=${c.candidate_count || 0}`, (c) => [button('删除该次续录', () => dryRunDelete('/api/delete_continued_capture', { page_name: data.page_name, capture_id: c.capture_id, delete_candidates_from_capture: false, dry_run: true })), button('删除该次续录及其候选控件', () => dryRunDelete('/api/delete_continued_capture', { page_name: data.page_name, capture_id: c.capture_id, delete_candidates_from_capture: true, dry_run: true }))]);
}

function appendList(box, title, rows, labelFn, buttonsFn) {
  const h = document.createElement('h4');
  h.textContent = title;
  box.appendChild(h);
  if (!rows.length) { box.insertAdjacentHTML('beforeend', '<div class="muted">无</div>'); return; }
  rows.forEach((r) => {
    const div = document.createElement('div');
    div.className = 'detailRow';
    div.innerHTML = `<span>${escapeHtml(labelFn(r))}</span>`;
    buttonsFn(r).forEach((b) => div.appendChild(b));
    box.appendChild(div);
  });
}

function transitionLabel(t) {
  const target = t.target || {};
  return target.step_prompt || target.key_description || target.value || '';
}

async function dryRunDelete(path, body) {
  const preview = await api(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...body, dry_run: true }) });
  if (!preview) return;
  const text = JSON.stringify(preview.delete_plan || preview, null, 2);
  if (!confirm(`删除预览：\n${text}\n\n确认执行删除？`)) return;
  const result = await api(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...body, dry_run: false }) });
  if (result) {
    await refreshDirectory();
    if (selectedPage) await loadPageDetail(selectedPage).catch(() => {});
    render(await api('/api/state'));
  }
}

function escapeHtml(text) {
  return String(text).replace(/[&<>\"]/g, (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[ch]));
}

el('captureBtn').onclick = async () => render(await api('/api/capture', { method: 'POST' }));
el('backBtn').onclick = async () => render(await api('/api/back', { method: 'POST' }));
el('clearPendingBtn').onclick = async () => { await api('/api/clear_pending', { method: 'POST' }); render(await api('/api/state')); };
el('markOverlayBtn').onclick = async () => render(await api('/api/mark_current_as_overlay', { method: 'POST' }));
el('continuePageBtn').onclick = async () => render(await api('/api/continue_current_page', { method: 'POST' }));
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
window.addEventListener('resize', () => renderOverlay(state?.current_candidates || state?.candidates || []));
api('/api/state').then(render);
