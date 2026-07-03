import { api, postJson, requestJson, showError } from './api.js';
import { actionButton, clear, el, escapeHtml } from './dom.js';
import { store } from './state.js';

export async function refreshDirectory() {
  const data = await requestJson('/api/page_directory').catch((err) => ({ ok: false, error: err.message }));
  if (data.ok) {
    renderDirectory(data);
    const activePage = store.data?.active_page || store.data?.state?.page_name;
    if (activePage && !store.selectedPage) await loadPageDetail(activePage).catch(() => {});
  }
}

export function render(data) {
  if (!data) return;
  store.data = data;
  store.highlighted = null;

  el('pageName').textContent = data.state?.page_name || '-';
  el('activePage').textContent = data.active_page || data.active_state?.page_name || '-';
  el('title').textContent = data.state?.last_title || '-';
  el('pending').textContent = data.pending_action_chain?.steps?.length
    ? `${data.pending_action_chain.from_page} 已记录 ${data.pending_action_chain.steps.length} 步`
    : data.pending
      ? `${data.pending.from_page} -> ${(data.pending.target || {}).step_prompt || (data.pending.target || {}).value || ''}`
      : '无';

  el('warning').textContent = data.warning || '';
  el('warning').classList.toggle('hidden', !data.warning);
  const modeMsg = store.samePageMode ? '当前模式：记录同页点击变化。点击截图中的控件后，会补采并合并当前页面内容。' : '';
  const operationModeMsg = store.pageOperationMode ? '当前模式：记录同页手势。选择手势和变化结果后，点击截图中的操作区域。' : '';
  const overlayMsg = data.message || modeMsg || operationModeMsg || (data.state?.is_overlay ? '当前页面已标记为弹窗页面' : '');
  el('overlayStatus').textContent = overlayMsg;
  el('overlayStatus').classList.toggle('hidden', !overlayMsg);

  if (data.screenshot_url) el('screen').src = data.screenshot_url;
  renderActionChain(data.pending_action_chain);
  el('screen').onload = () => renderOverlay([]);
  renderOverlay([]);
  refreshDirectory();
}

function candidateTitle(candidate) {
  return candidate.step_prompt || candidate.key_description || candidate.text || candidate.value || candidate.key || candidate.candidate_id || '';
}

function renderActionChain(chain) {
  const box = el('chainStatus');
  clear(box);
  if (!chain?.steps?.length) {
    box.classList.add('hidden');
    return;
  }
  box.classList.remove('hidden');
  box.innerHTML = `
    <div class="chainTitle">正在录制多步骤跳转</div>
    <div class="chainRoute">${escapeHtml(chain.from_page)} <span>...</span> 目标页面待确定</div>
    <ol>${chain.steps.map((step) => `<li>${escapeHtml(stepLabel(step))}</li>`).join('')}</ol>
    <div class="muted">继续点击临时菜单或弹层里的目标控件；进入新页面后会保存为一条页面跳转。</div>
  `;
}

function appendReachablePages(box, transitions) {
  const header = document.createElement('h4');
  header.textContent = '可达页面';
  box.appendChild(header);
  if (!transitions.length) {
    box.insertAdjacentHTML('beforeend', '<div class="muted">选择左侧页面后查看可到达页面。</div>');
    return;
  }
  transitions.forEach((transition) => {
    const row = document.createElement('div');
    row.className = 'reachableRow';
    row.innerHTML = `
      <div class="reachableMain">
        <strong>${escapeHtml(transition.to_title || transition.to_page)}</strong>
        <code>${escapeHtml(transition.to_page)}</code>
        <small>${transitionSteps(transition).length} 步可达</small>
      </div>
    `;
    row.appendChild(actionButton('详情', () => loadPageDetail(transition.to_page), 'secondary'));
    box.appendChild(row);
  });
}

export function renderOverlay(candidates) {
  const img = el('screen');
  const overlay = el('overlay');
  clear(overlay);
  if (!img.complete || !img.naturalWidth || !store.data?.screen_metrics?.screen_size) return;

  const rect = img.getBoundingClientRect();
  const wrap = el('screenWrap');
  const wrapRect = wrap.getBoundingClientRect();
  overlay.style.left = `${rect.left - wrapRect.left + wrap.scrollLeft}px`;
  overlay.style.top = `${rect.top - wrapRect.top + wrap.scrollTop}px`;
  overlay.style.width = `${rect.width}px`;
  overlay.style.height = `${rect.height}px`;

  const [screenWidth, screenHeight] = store.data.screen_metrics.screen_size;
  candidates.forEach((candidate) => {
    const center = candidate.bounds_center;
    if (!Array.isArray(center) || center.length !== 2) return;
    const marker = document.createElement('div');
    marker.className = `marker ${store.highlighted === candidate.index ? 'active' : ''}`;
    marker.textContent = candidate.index;
    marker.style.left = `${center[0] / screenWidth * rect.width}px`;
    marker.style.top = `${center[1] / screenHeight * rect.height}px`;
    overlay.appendChild(marker);
  });
}

function renderDirectory(data) {
  const box = el('pageDirectory');
  clear(box);
  const query = (store.directoryQuery || '').trim().toLowerCase();
  const nodeText = (node) => [
    node.title,
    node.page_name,
    node.via?.target_label,
  ].filter(Boolean).join(' ').toLowerCase();
  const totalCount = (node) => 1 + (node.children || []).reduce((sum, child) => sum + totalCount(child), 0);
  const matchesQuery = (node) => !query || nodeText(node).includes(query) || (node.children || []).some(matchesQuery);
  let shown = 0;
  const total = (data.items || []).reduce((sum, node) => sum + totalCount(node), 0);
  const addNode = (node, depth = 0) => {
    if (!matchesQuery(node)) return;
    shown += 1;
    const row = document.createElement('div');
    row.className = 'dirNode';
    row.style.setProperty('--depth', String(Math.min(depth, 8)));
    const title = node.title || node.page_name;
    const viaLabel = node.via?.target_label || '';
    const showVia = node.via && (node.via.step_count > 1 || normalizeText(viaLabel) !== normalizeText(title));
    const via = showVia ? escapeHtml(node.via.step_count > 1 ? `${node.via.step_count} 步` : viaLabel) : '';
    row.innerHTML = `
      <div class="dirMain">
        <div class="dirTitle">
          <strong>${escapeHtml(title)}</strong>
          ${via ? `<span class="dirVia">${via}</span>` : ''}
        </div>
        <code>${escapeHtml(node.page_name)}</code>
      </div>
      <div class="dirActions"></div>
    `;
    const actions = row.querySelector('.dirActions');
    actions.appendChild(actionButton('详情', () => loadPageDetail(node.page_name), 'secondary compact'));
    if (node.via?.transition_id) {
      actions.appendChild(actionButton('删分支', () => dryRunDelete('branch', {
        transition_id: node.via.transition_id,
        delete_descendants: true,
      }), 'danger compact'));
    }
    actions.appendChild(actionButton('删页', () => dryRunDelete('page', { page_name: node.page_name }), 'danger compact'));
    box.appendChild(row);
    (node.children || []).forEach((child) => addNode(child, depth + 1));
  };
  (data.items || []).forEach((node) => addNode(node));
  el('directoryCount').textContent = query ? `${shown}/${total}` : `${total}`;
  if (!shown) box.insertAdjacentHTML('beforeend', '<div class="muted">没有匹配页面。</div>');
}

function normalizeText(value) {
  return String(value || '').replace(/\s+/g, '').toLowerCase();
}

export async function loadPageDetail(pageName) {
  store.selectedPage = pageName;
  const data = await requestJson(`/api/page_detail?page_name=${encodeURIComponent(pageName)}`);
  if (!data.ok) return showError(data.error || '加载页面详情失败');
  renderPageDetail(data);
}

function renderPageDetail(data) {
  const box = el('pageDetail');
  clear(box);
  hideGraphBox();
  box.innerHTML = `<h3>${escapeHtml(data.state?.last_title || data.page_name)}</h3><p><code>${escapeHtml(data.page_name)}</code></p>`;
  box.appendChild(actionButton('从 root 执行跳转到此页', async () => {
    if (!confirm('请确认手机当前位于设置首页 Pages_root。继续执行跳转？')) return;
    render(await postJson('/api/console_action', { action: 'navigate_to_page', payload: { page_name: data.page_name } }));
  }));
  box.appendChild(actionButton('重命名页面', () => renamePage(data), 'secondary'));
  box.appendChild(actionButton('查看本页 JSON', () => showPageJson(data), 'secondary'));
  appendTransitionList(box, '从哪些页面可以进来', data.incoming_transitions || [], false);
  appendTransitionList(box, '从当前页面可以去哪里', data.outgoing_transitions || [], true);
  appendReachablePages(box, data.outgoing_transitions || []);
  appendOperationList(box, '页面内操作', data.page_operations || [], data.page_name);
  appendVariantList(box, '同页状态变体', data.page_variants || []);
  appendList(box, '继续录制', data.continued_captures || [], (cap) => `${cap.capture_id} candidates=${cap.candidate_count || 0}`, (cap) => [
    actionButton('删除该次续录', () => dryRunDelete('continued_capture', {
      page_name: data.page_name,
      capture_id: cap.capture_id,
      delete_candidates_from_capture: false,
    }), 'secondary'),
    actionButton('删除该次续录及其候选控件', () => dryRunDelete('continued_capture', {
      page_name: data.page_name,
      capture_id: cap.capture_id,
      delete_candidates_from_capture: true,
    }), 'danger'),
  ]);
}

function hideGraphBox() {
  const box = el('graphBox');
  box.textContent = '';
  box.classList.add('hidden');
}

function showPageJson(data) {
  const box = el('graphBox');
  const scoped = {
    page_name: data.page_name,
    path_from_root: data.path_from_root || [],
    state: data.state || {},
    incoming_transitions: data.incoming_transitions || [],
    outgoing_transitions: data.outgoing_transitions || [],
    merged_candidates: data.merged_candidates || [],
    page_operations: data.page_operations || [],
    page_variants: data.page_variants || [],
    continued_captures: data.continued_captures || [],
  };
  box.textContent = JSON.stringify(scoped, null, 2);
  box.classList.remove('hidden');
}

async function renamePage(data) {
  const currentName = data.page_name || '';
  const currentTitle = data.state?.last_title || data.state?.page_description || currentName;
  const newName = window.prompt('新的 page_name（必须以 Pages_ 开头）', currentName);
  if (newName === null) return;
  const newTitle = window.prompt('新的页面显示标题', currentTitle);
  if (newTitle === null) return;
  const result = await postJson('/api/rename_page', {
    old_page_name: currentName,
    new_page_name: newName.trim(),
    new_title: newTitle.trim(),
  });
  if (!result) return;
  if (store.data?.state?.page_name === currentName) {
    store.data.state.page_name = result.page_name;
    store.data.state.last_title = result.new_title || newTitle.trim() || result.page_name;
    el('pageName').textContent = store.data.state.page_name;
    el('title').textContent = store.data.state.last_title;
  }
  if (store.data?.active_page === currentName) {
    store.data.active_page = result.page_name;
    el('activePage').textContent = result.page_name;
  }
  await refreshDirectory();
  await loadPageDetail(result.page_name);
  el('overlayStatus').textContent = result.message || '页面已重命名';
  el('overlayStatus').classList.remove('hidden');
}

function appendVariantList(box, title, variants) {
  const header = document.createElement('h4');
  header.textContent = title;
  box.appendChild(header);
  if (!variants.length) {
    box.insertAdjacentHTML('beforeend', '<div class="muted">无</div>');
    return;
  }
  variants.forEach((variant) => {
    const row = document.createElement('div');
    row.className = 'operationRow';
    const trigger = variant.trigger?.step_prompt || variant.trigger?.key_description || variant.trigger?.text || variant.trigger?.value || variant.trigger_operation_id || '同页操作';
    const shown = (variant.revealed_candidates || []).length;
    const hidden = (variant.hidden_candidates || []).length;
    row.innerHTML = `
      <div class="operationMain">
        <strong>${escapeHtml(trigger)}</strong>
        <span>${escapeHtml(variant.effect || 'same_page_state_changed')} · 新增 ${shown} · 消失 ${hidden}${variant.is_mutually_exclusive ? ' · 互斥场景' : ''}</span>
        <code>${escapeHtml(variant.variant_id || '')}</code>
      </div>
    `;
    box.appendChild(row);
  });
}

function appendOperationList(box, title, operations, pageName) {
  const header = document.createElement('h4');
  header.textContent = title;
  box.appendChild(header);
  if (!operations.length) {
    box.insertAdjacentHTML('beforeend', '<div class="muted">无</div>');
    return;
  }
  operations.forEach((operation) => {
    const row = document.createElement('div');
    row.className = 'operationRow';
    row.innerHTML = `
      <div class="operationMain">
        <strong>${escapeHtml(operationLabel(operation))}</strong>
        <span>${escapeHtml(operation.effect || 'same_page_state_changed')}</span>
        <code>${escapeHtml(operation.operation_id || '')}</code>
      </div>
    `;
    row.appendChild(actionButton('删除操作', () => dryRunDelete('page_operation', {
      page_name: pageName,
      operation_id: operation.operation_id,
      delete_revealed_candidates: false,
    }), 'danger'));
    box.appendChild(row);
  });
}

function appendTransitionList(box, title, transitions, outgoing) {
  const header = document.createElement('h4');
  header.textContent = title;
  box.appendChild(header);
  if (!transitions.length) {
    box.insertAdjacentHTML('beforeend', '<div class="muted">无</div>');
    return;
  }
  transitions.forEach((transition) => {
    const row = document.createElement('div');
    row.className = 'transitionRow';
    const route = outgoing
      ? `${transition.from_page} -> ${transition.to_page}`
      : `${transition.from_page} -> ${transition.to_page}`;
    row.innerHTML = `
      <div class="transitionMain">
        <strong>${escapeHtml(route)}</strong>
        <ol>${transitionSteps(transition).map((step) => `<li>${escapeHtml(stepLabel(step))}</li>`).join('')}</ol>
      </div>
    `;
    row.appendChild(actionButton('删除跳转', () => dryRunDelete('transition', {
      transition_id: transition.transition_id,
    }), 'danger'));
    box.appendChild(row);
  });
}

function appendList(box, title, rows, labelFn, buttonsFn) {
  const header = document.createElement('h4');
  header.textContent = title;
  box.appendChild(header);
  if (!rows.length) {
    box.insertAdjacentHTML('beforeend', '<div class="muted">无</div>');
    return;
  }
  rows.forEach((item) => {
    const row = document.createElement('div');
    row.className = 'detailRow';
    row.innerHTML = `<span>${escapeHtml(labelFn(item))}</span>`;
    buttonsFn(item).forEach((button) => row.appendChild(button));
    box.appendChild(row);
  });
}

function transitionLabel(transition) {
  const steps = transitionSteps(transition);
  if (steps.length) return steps.map(stepLabel).join(' -> ');
  const target = transition.target || {};
  return target.step_prompt || target.key_description || target.value || '';
}

function transitionSteps(transition) {
  if (Array.isArray(transition.steps) && transition.steps.length) return transition.steps;
  return transition.target ? [{ operate: transition.operate || 'tap', target: transition.target }] : [];
}

function stepLabel(step) {
  const target = step.target || {};
  const name = target.step_prompt || target.key_description || target.text || target.value || target.key || '未知控件';
  const locator = target.key ? ` key=${target.key}` : '';
  return `${step.operate || 'tap'} ${name}${locator}`;
}

function operationLabel(operation) {
  const target = operation.target || {};
  const targetName = target.step_prompt || target.key_description || target.text || target.value || target.key || '当前区域';
  return `${operation.operate || 'tap'} ${targetName}`;
}

export async function dryRunDelete(targetType, body) {
  const preview = await postJson('/api/delete_action', { target_type: targetType, payload: body, dry_run: true });
  if (!preview) return;
  const text = JSON.stringify(preview.delete_plan || preview, null, 2);
  if (!confirm(`删除预览：\n${text}\n\n确认执行删除？`)) return;
  const result = await postJson('/api/delete_action', { target_type: targetType, payload: body, dry_run: false });
  if (!result) return;
  await refreshDirectory();
  if (store.selectedPage) await loadPageDetail(store.selectedPage).catch(() => {});
  render(await api('/api/state'));
}
