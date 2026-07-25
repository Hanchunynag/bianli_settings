import { api, postJson, queryJson } from './api.js';
import { el, escapeHtml } from './dom.js';
import { store } from './state.js';

let directorySearchTimer;
let directoryDrag = null;
let directoryClickBlocked = false;
let directoryRequestGeneration = 0;

function finishDirectoryDrag() {
  if (directoryDrag?.row) directoryDrag.row.style.opacity = '';
  directoryDrag = null;
  setTimeout(() => { directoryClickBlocked = false; }, 0);
}

function enableDirectoryDrag(main, row, node, parentPage, siblings, rerender) {
  const transitionId = node.via?.transition_id;
  if (!transitionId) return;
  main.draggable = true;
  main.style.cursor = 'grab';
  main.ondragstart = (event) => {
    directoryDrag = { transitionId, parentPage, row };
    directoryClickBlocked = true;
    row.style.opacity = '0.45';
    if (event.dataTransfer) {
      event.dataTransfer.effectAllowed = 'move';
      event.dataTransfer.setData('text/plain', transitionId);
    }
  };
  main.ondragend = finishDirectoryDrag;
  row.ondragover = (event) => {
    if (!directoryDrag || directoryDrag.parentPage !== parentPage || directoryDrag.transitionId === transitionId) return;
    event.preventDefault();
    const after = event.clientY > row.getBoundingClientRect().top + row.offsetHeight / 2;
    row.style.boxShadow = `inset 0 ${after ? '-2px' : '2px'} 0 currentColor`;
  };
  row.ondragleave = () => { row.style.boxShadow = ''; };
  row.ondrop = async (event) => {
    event.preventDefault();
    row.style.boxShadow = '';
    if (!directoryDrag || directoryDrag.parentPage !== parentPage || directoryDrag.transitionId === transitionId) return;
    const orderedNodes = [...siblings];
    const sourceIndex = orderedNodes.findIndex((item) => item.via?.transition_id === directoryDrag.transitionId);
    if (sourceIndex < 0) return finishDirectoryDrag();
    const [movingNode] = orderedNodes.splice(sourceIndex, 1);
    let targetIndex = orderedNodes.findIndex((item) => item.via?.transition_id === transitionId);
    if (targetIndex < 0) return finishDirectoryDrag();
    if (event.clientY > row.getBoundingClientRect().top + row.offsetHeight / 2) targetIndex += 1;
    orderedNodes.splice(targetIndex, 0, movingNode);
    const saved = await postJson('/api/console_action', {
      action: 'reorder_children',
      payload: {
        parent_page: parentPage,
        ordered_transition_ids: orderedNodes.map((sibling) => sibling.via.transition_id),
      },
    });
    finishDirectoryDrag();
    if (saved) await refreshDirectory();
    else rerender();
  };
}

async function refreshDirectory() {
  const generation = ++directoryRequestGeneration;
  const data = await queryJson('/api/page_directory');
  if (generation !== directoryRequestGeneration) return;
  if (data?.ok) {
    renderDirectory(data);
    const activePage = store.data?.active_page || store.data?.state?.page_name;
    if (activePage && !store.selectedPage && !store.showingOrphans) {
      await loadPageDetail(activePage).catch(() => {});
    }
  }
}

export function render(data) {
  if (!data) return;
  store.data = data;

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
  const popupCaptureMsg = store.popupType
    ? `单次采集：弹窗-${store.popupType}。点击截图中的控件后自动退出采集状态。`
    : '';
  const overlayMsg = popupCaptureMsg || data.message;
  el('overlayStatus').textContent = overlayMsg;
  el('overlayStatus').classList.toggle('hidden', !overlayMsg);

  if (data.screenshot_url) el('screen').src = data.screenshot_url;
  const chainBox = el('chainStatus');
  chainBox.replaceChildren();
  if (data.pending_action_chain?.steps?.length) {
    const chain = data.pending_action_chain;
    chainBox.classList.remove('hidden');
    chainBox.innerHTML = `
      <div class="chainTitle">正在录制多步骤跳转</div>
      <div class="chainRoute">${escapeHtml(chain.from_page)} <span>...</span> 目标页面待确定</div>
      <ol>${chain.steps.map((step) => `<li>${escapeHtml(stepLabel(step))}</li>`).join('')}</ol>
      <div class="muted">继续点击临时菜单或弹层里的目标控件；进入新页面后会保存为一条页面跳转。</div>
    `;
  } else {
    chainBox.classList.add('hidden');
  }
  el('screen').onload = renderOverlay;
  renderOverlay();
  refreshDirectory();
}

export function renderOverlay() {
  const img = el('screen');
  const overlay = el('overlay');
  overlay.replaceChildren();
  if (!img.complete || !img.naturalWidth || !store.data?.screen_metrics?.screen_size) return;

  const rect = img.getBoundingClientRect();
  const wrap = el('screenWrap');
  const wrapRect = wrap.getBoundingClientRect();
  overlay.style.left = `${rect.left - wrapRect.left + wrap.scrollLeft}px`;
  overlay.style.top = `${rect.top - wrapRect.top + wrap.scrollTop}px`;
  overlay.style.width = `${rect.width}px`;
  overlay.style.height = `${rect.height}px`;

}

function renderDirectory(data) {
  const box = el('pageDirectory');
  const search = el('pageSearch');
  const scheduleSearch = () => {
    clearTimeout(directorySearchTimer);
    directorySearchTimer = setTimeout(() => {
      const query = search.value.trim().toLowerCase();
      if (query === store.directoryQuery) return;
      store.directoryQuery = query;
      renderDirectory(data);
    }, 80);
  };
  search.oninput = scheduleSearch;
  search.onkeyup = scheduleSearch;
  search.onkeydown = scheduleSearch;
  box.replaceChildren();
  const query = (store.directoryQuery || '').trim().toLowerCase();
  const nodeText = (node) => [node.title, node.page_name, node.via?.target_label].filter(Boolean).join(' ').toLowerCase();
  const totalCount = (node) => 1 + (node.children || []).reduce((sum, child) => sum + totalCount(child), 0);
  const matchesQuery = (node) => !query || nodeText(node).includes(query) || (node.children || []).some(matchesQuery);
  const roots = data.items || [];
  let shown = 0;
  const total = roots.reduce((sum, node) => sum + totalCount(node), 0);

  const addNode = (node, depth = 0, parentPage = '__root__', siblings = roots) => {
    if (!matchesQuery(node)) return;
    shown += 1;
    const rawChildren = node.children || [];
    const children = [...rawChildren].filter(matchesQuery);
    const expandable = children.length > 0;
    const expanded = expandable && (Boolean(query) || store.expandedPages.has(node.page_name));
    const row = document.createElement('div');
    row.className = 'dirNode';
    row.style.setProperty('--depth', String(Math.min(depth, 8)));
    const title = node.title || node.page_name;
    const viaLabel = node.via?.target_label || '';
    const normalizedVia = String(viaLabel).replace(/\s+/g, '').toLowerCase();
    const normalizedTitle = String(title).replace(/\s+/g, '').toLowerCase();
    const showVia = node.via && (node.via.step_count > 1 || normalizedVia !== normalizedTitle);
    const via = showVia ? escapeHtml(node.via.step_count > 1 ? `${node.via.step_count} 步` : viaLabel) : '';
    row.innerHTML = `
      <div class="dirMain${expandable ? ' isExpandable' : ''}" ${expandable ? `role="button" tabindex="0" aria-expanded="${expanded}"` : ''}>
        <span class="dirCaret${expandable ? '' : ' isLeaf'}" aria-hidden="true">${expandable ? (expanded ? '−' : '+') : ''}</span>
        <div class="dirContent">
          <div class="dirTitle">
            <strong>${escapeHtml(title)}</strong>
            ${via ? `<span class="dirVia">${via}</span>` : ''}
          </div>
          <code>${escapeHtml(node.page_name)}</code>
        </div>
      </div>
      <div class="dirActions"></div>
    `;

    const main = row.querySelector('.dirMain');
    if (!query) enableDirectoryDrag(main, row, node, parentPage, siblings, () => renderDirectory(data));
    if (expandable) {
      const toggle = () => {
        if (directoryClickBlocked) return;
        if (store.expandedPages.has(node.page_name)) store.expandedPages.delete(node.page_name);
        else store.expandedPages.add(node.page_name);
        renderDirectory(data);
      };
      main.onclick = toggle;
      main.onkeydown = (event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          toggle();
        }
      };
    }

    const actions = row.querySelector('.dirActions');
    actions.innerHTML = `
      <button class="secondary compact" data-action="detail">详情</button>
      ${node.via?.transition_id ? '<button class="danger compact" data-action="branch">删分支</button>' : ''}
      ${node.page_name !== 'Pages_root' ? '<button class="danger compact" data-action="page">删页</button>' : ''}`;
    actions.onclick = (event) => {
      const action = event.target.dataset.action;
      if (action === 'detail') loadPageDetail(node.page_name);
      else if (action === 'branch') dryRunDelete('branch', { transition_id: node.via.transition_id, delete_descendants: true });
      else if (action === 'page') dryRunDelete('page', { page_name: node.page_name });
    };
    box.appendChild(row);
    if (expanded) children.forEach((child) => addNode(child, depth + 1, node.page_name, rawChildren));
  };

  [...roots].forEach((node) => addNode(node));
  el('directoryCount').textContent = shown === total ? `${total}` : `${shown}/${total}`;
  if (!shown) box.insertAdjacentHTML('beforeend', '<div class="muted">没有匹配页面。</div>');
}

async function loadPageDetail(pageName) {
  store.selectedPage = pageName;
  store.showingOrphans = false;
  const data = await api(`/api/page_detail?page_name=${encodeURIComponent(pageName)}`);
  if (!data?.ok) return;
  const box = el('pageDetail');
  el('graphBox').classList.add('hidden');
  const operations = data.page_operations || [];
  const variants = data.page_variants || [];
  const captures = data.continued_captures || [];
  const detailJson = { ...data };
  delete detailJson.ok;
  const renderTransitions = (title, transitions) => `
    <h4>${title}</h4>${transitions.length ? transitions.map((transition) => `
      <div class="transitionRow">
        <div class="transitionMain">
          <strong>${escapeHtml(`${transition.from_page} -> ${transition.to_page}`)}</strong>
          <ol>${transitionSteps(transition).map((step) => `<li>${escapeHtml(stepLabel(step))}</li>`).join('')}</ol>
        </div>
        <button class="danger" data-action="delete-transition" data-id="${escapeHtml(transition.transition_id || '')}">删除跳转</button>
      </div>`).join('') : '<div class="muted">无</div>'}`;
  box.innerHTML = `
    <h3>${escapeHtml(data.state?.last_title || data.page_name)}</h3>
    <p><code>${escapeHtml(data.page_name)}</code></p>
    <button class="secondary" data-action="rename">重命名页面</button>
    <button class="secondary" data-action="json">查看本页 JSON</button>
    ${renderTransitions('从哪些页面可以进来', data.incoming_transitions || [])}
    ${renderTransitions('从当前页面可以去哪里', data.outgoing_transitions || [])}
    <h4>页面内操作</h4>
    ${operations.length ? operations.map((operation) => `
      <div class="operationRow">
      <div class="operationMain">
        <strong>${escapeHtml(`${operation.operate || 'tap'} ${operation.target?.step_prompt || operation.target?.key_description || operation.target?.text || operation.target?.value || operation.target?.key || '当前区域'}`)}</strong>
        <span>${escapeHtml(operation.effect || 'same_page_state_changed')}</span>
        <code>${escapeHtml(operation.operation_id || '')}</code>
      </div>
      <button class="danger" data-action="delete-operation" data-id="${escapeHtml(operation.operation_id || '')}">删除操作</button>
      </div>`).join('') : '<div class="muted">无</div>'}
    <h4>同页状态变体</h4>
    ${variants.length ? variants.map((variant) => `
      <div class="operationRow"><div class="operationMain">
        <strong>${escapeHtml(variant.trigger?.step_prompt || variant.trigger?.key_description || variant.trigger?.text || variant.trigger?.value || variant.trigger_operation_id || '同页操作')}</strong>
        <span>${escapeHtml(variant.effect || 'same_page_state_changed')} · 新增 ${(variant.revealed_candidates || []).length} · 消失 ${(variant.hidden_candidates || []).length}${variant.is_mutually_exclusive ? ' · 互斥场景' : ''}</span>
        <code>${escapeHtml(variant.variant_id || '')}</code>
      </div></div>`).join('') : '<div class="muted">无</div>'}
    <h4>继续录制</h4>
    ${captures.length ? captures.map((capture) => `
      <div class="detailRow">
        <span>${escapeHtml(`${capture.capture_id} candidates=${capture.candidate_count || 0}`)}</span>
        <button class="secondary" data-action="delete-capture" data-id="${escapeHtml(capture.capture_id || '')}">删除该次续录</button>
        <button class="danger" data-action="delete-capture-candidates" data-id="${escapeHtml(capture.capture_id || '')}">删除该次续录及其候选控件</button>
      </div>`).join('') : '<div class="muted">无</div>'}`;
  box.onclick = async (event) => {
    const button = event.target.closest('button[data-action]');
    if (!button) return;
    const action = button.dataset.action;
    if (action === 'rename') {
      renamePage(data);
    } else if (action === 'json') {
      const graphBox = el('graphBox');
      graphBox.textContent = JSON.stringify(detailJson, null, 2);
      graphBox.classList.remove('hidden');
    } else if (action === 'delete-transition') {
      dryRunDelete('transition', { transition_id: button.dataset.id });
    } else if (action === 'delete-operation') {
      dryRunDelete('page_operation', { page_name: data.page_name, operation_id: button.dataset.id, delete_revealed_candidates: false });
    } else {
      dryRunDelete('continued_capture', {
        page_name: data.page_name,
        capture_id: button.dataset.id,
        delete_candidates_from_capture: action === 'delete-capture-candidates',
      });
    }
  };
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

function transitionSteps(transition) {
  if (Array.isArray(transition.steps) && transition.steps.length) return transition.steps;
  return transition.target ? [{ operate: transition.operate || 'tap', target: transition.target }] : [];
}

function stepLabel(step) {
  const target = step.target || {};
  const operate = step.operate || 'tap';

  const key =
    target.key ||
    (target.type === 'key' ? target.value : '');

  const name =
    target.step_prompt ||
    target.key_description ||
    target.text ||
    '';

  if (key && name && name !== key) {
    return `${operate} ${name} [key=${key}]`;
  }

  if (key) {
    return `${operate} key=${key}`;
  }

  return `${operate} ${name || target.value || '未知控件'}`;
}

async function dryRunDelete(targetType, body) {
  const preview = await postJson('/api/delete_action', { target_type: targetType, payload: body, dry_run: true });
  if (!preview) return;
  const text = JSON.stringify(preview.delete_plan || preview, null, 2);
  if (!confirm(`删除预览：\n${text}\n\n确认执行删除？`)) return;
  const result = await postJson('/api/delete_action', {
    target_type: targetType,
    payload: body,
    dry_run: false,
    preview_token: preview.preview_token,
  });
  if (!result) return;
  const deletedPages = new Set(result.delete_plan?.states || []);
  if (store.selectedPage && deletedPages.has(store.selectedPage)) store.selectedPage = null;
  render(await api('/api/state'));
  if (store.showingOrphans) await refreshOrphans();
}

export async function refreshOrphans() {
  const data = await api('/api/orphan_pages');
  if (!data?.ok) return;
  store.selectedPage = null;
  store.showingOrphans = true;
  const box = el('pageDetail');
  el('graphBox').classList.add('hidden');
  const pages = data.orphan_pages || [];
  box.innerHTML = `
    <h3>孤儿页面 <span class="countBadge">${data.count || 0}</span></h3>
    <p class="muted">以下页面无法从 Pages_root 到达。删除仍会先展示完整预览。</p>
    ${pages.length ? `
      <button class="danger" data-orphan="*">删除全部孤儿页面</button>
      ${pages.map((page) => `<div class="detailRow">
        <span><strong>${escapeHtml(page.title || page.page_name)}</strong><br>
        <code>${escapeHtml(page.page_name)}</code><br>
        <small>入 ${page.incoming_count} · 出 ${page.outgoing_count} · 候选 ${page.candidate_count}${page.is_active ? ' · 当前页面' : ''}</small></span>
        <button class="danger" data-orphan="${escapeHtml(page.page_name)}">删除</button>
      </div>`).join('')}` : '<div class="muted">当前没有孤儿页面。</div>'}`;
  box.onclick = async (event) => {
    const button = event.target.closest('button[data-orphan]');
    if (button) await dryRunDelete('orphan_pages', {
      page_names: button.dataset.orphan === '*' ? pages.map((page) => page.page_name) : [button.dataset.orphan],
    });
  };
}
