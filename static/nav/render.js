import { api, postJson, queryJson } from './api.js';
import { el, escapeHtml } from './dom.js';
import { store } from './state.js';

let directorySearchTimer;
let directoryDrag = null;
let directoryClickBlocked = false;
let directoryRequestGeneration = 0;

function localDescription(value) {
  let label = String(value || '').trim().replace(/^Pages_/, '');
  if (label.includes('_to')) label = label.split('_to').at(-1).trim();
  if (label.includes(' to')) label = label.split(' to').at(-1).trim();
  if (label.includes('_')) label = label.split('_').at(-1).trim();
  return label;
}

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

  el('title').textContent = localDescription(
    data.active_state?.page_description
    || data.active_state?.last_title
    || data.state?.page_description
    || data.state?.last_title
    || data.active_page
    || data.state?.page_name,
  ) || '-';
  el('pending').textContent = data.pending_action_chain?.steps?.length
    ? `${localDescription(data.pending_action_chain.from_page)} 已记录 ${data.pending_action_chain.steps.length} 步`
    : data.pending
      ? `${localDescription(data.pending.from_page)} → ${(data.pending.target || {}).step_prompt || (data.pending.target || {}).value || ''}`
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

  const addNode = (
    node,
    depth = 0,
    parentPage = '__root__',
    siblings = roots,
    container = box,
  ) => {
    if (!matchesQuery(node)) return;
    shown += 1;
    const rawChildren = node.children || [];
    const children = [...rawChildren].filter(matchesQuery);
    const expandable = children.length > 0;
    const expanded = expandable && (Boolean(query) || store.expandedPages.has(node.page_name));
    const treeItem = document.createElement('div');
    treeItem.className = `dirTreeItem${depth === 0 ? ' isRoot' : ''}`;
    const row = document.createElement('div');
    row.className = 'dirNode';
    const title = node.title || node.page_name;
    row.innerHTML = `
      <div class="dirMain${expandable ? ' isExpandable' : ''}" ${expandable ? `role="button" tabindex="0" aria-expanded="${expanded}"` : ''}>
        <span class="dirCaret${expandable ? '' : ' isLeaf'}" aria-hidden="true">${expandable ? (expanded ? '▾' : '▸') : ''}</span>
        <div class="dirContent">
          <div class="dirTitle">
            <strong>${escapeHtml(title)}</strong>
          </div>
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
    const hasMoreActions = Boolean(
      node.via?.transition_id || node.page_name !== 'Pages_root',
    );
    actions.innerHTML = `
      ${hasMoreActions ? `
        <details class="dirMore">
          <summary title="更多操作" aria-label="更多操作">⋯</summary>
          <div class="dirActionMenu">
            ${node.via?.transition_id ? '<button class="danger" data-action="branch">删除分支</button>' : ''}
            ${node.page_name !== 'Pages_root' ? '<button class="danger" data-action="page">删除页面</button>' : ''}
          </div>
        </details>` : ''}
      <button class="secondary compact" data-action="detail">详情</button>`;
    const more = actions.querySelector('.dirMore');
    if (more) {
      more.ontoggle = () => {
        if (!more.open) return;
        box.querySelectorAll('.dirMore[open]').forEach((other) => {
          if (other !== more) other.removeAttribute('open');
        });
      };
    }
    actions.onclick = (event) => {
      const action = event.target.closest('[data-action]')?.dataset.action;
      if (!action) return;
      more?.removeAttribute('open');
      if (action === 'detail') loadPageDetail(node.page_name);
      else if (action === 'branch') dryRunDelete('branch', { transition_id: node.via.transition_id, delete_descendants: true });
      else if (action === 'page') dryRunDelete('page', { page_name: node.page_name });
    };
    treeItem.appendChild(row);
    container.appendChild(treeItem);
    if (expanded) {
      const childContainer = document.createElement('div');
      childContainer.className = 'dirChildren';
      treeItem.appendChild(childContainer);
      children.forEach((child) => addNode(
        child,
        depth + 1,
        node.page_name,
        rawChildren,
        childContainer,
      ));
    }
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
  const dfsRecord = data.dfs_manual || data.dfs_record || {
    package_name: '',
    main_page_name: '',
    page_description: '',
    path_snapshot: [],
  };
  const dfsIsManual = Boolean(data.dfs_manual);
  const recordDisplayName = (record) => {
    const description = localDescription(record?.page_description);
    if (description) return description;
    const targets = [...(record?.path_snapshot || [])].reverse();
    for (const target of targets) {
      const label = target.step_prompt || target.key_description || target.text || target.value || target.key;
      if (String(label || '').trim()) return String(label).trim();
    }
    return localDescription(record?.page_name);
  };
  const currentDescription = data.display_name
    || recordDisplayName(dfsRecord)
    || localDescription(data.state?.page_description)
    || localDescription(data.state?.last_title)
    || data.page_name;
  const detailJson = { ...data };
  delete detailJson.ok;
  const showDfsDetail = (detail) => {
    const detailBox = box.querySelector('#dfsBranchDetail');
    const records = detail?.branch_records || [];
    const currentPage = detail?.page_name || data.page_name;
    detailBox.innerHTML = `
      <div class="dfsBranchHeader">
        <strong>当前页面：${escapeHtml(detail?.display_name || currentDescription)}</strong>
        <span>${records.length} 个 DFS 分支页面</span>
      </div>
      ${records.length ? records.map((record) => `
        <article class="dfsRecordCard${record.page_name === currentPage ? ' isCurrent' : ''}">
          <div class="dfsRecordHeading">
            <strong>${escapeHtml(record.display_name || recordDisplayName(record))}</strong>
            ${record.page_name === currentPage ? '<span class="statusBadge isManual">当前页面</span>' : ''}
          </div>
          <ol>
            ${(record.path_snapshot || []).map((target) => `
              <li>
                <span>${escapeHtml(target.step_prompt || target.key_description || target.value || '')}</span>
                <code>${escapeHtml(`${target.type || ''}=${target.value || ''}`)}</code>
              </li>`).join('') || '<li class="muted">根页面，无需点击</li>'}
          </ol>
        </article>`).join('') : '<div class="muted">当前页面没有可展示的 DFS 记录。</div>'}
    `;
    detailBox.classList.remove('hidden');
  };
  const renderTransitions = (title, transitions) => `
    <h4>${title}</h4>${transitions.length ? transitions.map((transition) => `
      <div class="transitionRow">
        <div class="transitionMain">
          <strong>${escapeHtml(`${transition.from_page_description || transition.from_page} → ${transition.to_page_description || transition.to_page}`)}</strong>
          <ol>${transitionSteps(transition).map((step) => `<li>${escapeHtml(stepLabel(step))}</li>`).join('')}</ol>
        </div>
        <button class="danger" data-action="delete-transition" data-id="${escapeHtml(transition.transition_id || '')}">删除跳转</button>
      </div>`).join('') : '<div class="muted">无</div>'}`;
  box.innerHTML = `
    <h3>${escapeHtml(currentDescription)}</h3>
    ${data.page_name !== 'Pages_root' ? '<button class="secondary" data-action="rename">修改内部页面 ID</button>' : ''}
    <button class="secondary" data-action="json">查看本页 JSON</button>
    <section class="dfsEditor">
      <div class="dfsEditorTitle">
        <h4>当前页面 DFS 维护</h4>
        <span class="statusBadge ${dfsIsManual ? 'isManual' : ''}">${dfsIsManual ? '人工配置' : '自动生成'}</span>
      </div>
      <p class="muted">page_description 保存完整 DFS 路径描述，前端只显示最后一个页面名称；path_snapshot 保存实际点击步骤。menu_grid 等中间操作不会作为页面名称展示。</p>
      <form id="dfsManualForm">
        <label>
          <span>package_name</span>
          <input name="package_name" value="${escapeHtml(dfsRecord.package_name || '')}" required />
        </label>
        <label>
          <span>main_page_name</span>
          <input name="main_page_name" value="${escapeHtml(dfsRecord.main_page_name || '')}" required />
        </label>
        <label>
          <span>page_description（可直接修改）</span>
          <input name="page_description" value="${escapeHtml(dfsRecord.page_description || '')}" required />
        </label>
        <label>
          <span>path_snapshot（JSON 数组）</span>
          <textarea name="path_snapshot" rows="12" spellcheck="false">${escapeHtml(JSON.stringify(dfsRecord.path_snapshot || [], null, 2))}</textarea>
        </label>
        <div class="dfsEditorActions">
          <button class="primary" type="submit">保存 page_description / DFS 数据</button>
          <button class="secondary" type="button" data-action="export-dfs">生成 DFS 精简文件</button>
          <button class="secondary" type="button" data-action="view-dfs">查看当前页面 DFS 分支</button>
          ${dfsIsManual ? '<button class="secondary" type="button" data-action="reset-dfs">恢复自动生成</button>' : ''}
        </div>
      </form>
      <div id="dfsBranchDetail" class="dfsBranchDetail hidden"></div>
    </section>
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
  const dfsForm = box.querySelector('#dfsManualForm');
  dfsForm.onsubmit = async (event) => {
    event.preventDefault();
    let pathSnapshot;
    try {
      pathSnapshot = JSON.parse(dfsForm.elements.path_snapshot.value || '[]');
      if (!Array.isArray(pathSnapshot)) throw new Error('必须是 JSON 数组');
    } catch (error) {
      el('error').textContent = `path_snapshot 格式错误：${error.message}`;
      el('error').classList.remove('hidden');
      return;
    }
    const result = await postJson('/api/console_action', {
      action: 'maintain_page_dfs',
      payload: {
        page_name: data.page_name,
        package_name: dfsForm.elements.package_name.value,
        main_page_name: dfsForm.elements.main_page_name.value,
        page_description: dfsForm.elements.page_description.value,
        path_snapshot: pathSnapshot,
      },
    });
    if (!result) return;
    await refreshDirectory();
    await loadPageDetail(data.page_name);
    el('overlayStatus').textContent = result.message;
    el('overlayStatus').classList.remove('hidden');
  };
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
    } else if (action === 'view-dfs') {
      const detail = await api(`/api/page_dfs_detail?page_name=${encodeURIComponent(data.page_name)}`);
      if (detail?.ok) showDfsDetail(detail);
    } else if (action === 'export-dfs') {
      const result = await postJson('/api/console_action', {
        action: 'export_dfs_compact',
        payload: { page_name: data.page_name },
      });
      if (!result) return;
      if (result.dfs_detail) showDfsDetail(result.dfs_detail);
      el('overlayStatus').textContent = `${result.message} 保存位置：${result.output_path}`;
      el('overlayStatus').classList.remove('hidden');
    } else if (action === 'reset-dfs') {
      if (!confirm('确认删除本页人工 DFS 配置并恢复自动生成？')) return;
      const result = await postJson('/api/console_action', {
        action: 'maintain_page_dfs',
        payload: {
          page_name: data.page_name,
          clear: true,
        },
      });
      if (!result) return;
      await refreshDirectory();
      await loadPageDetail(data.page_name);
      el('overlayStatus').textContent = result.message;
      el('overlayStatus').classList.remove('hidden');
    } else if (action === 'delete-capture' || action === 'delete-capture-candidates') {
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
  const newName = window.prompt(
    '修改后端内部 page_name（必须以 Pages_ 开头，不会改变前端显示名称）',
    currentName,
  );
  if (newName === null) return;
  const result = await postJson('/api/rename_page', {
    old_page_name: currentName,
    new_page_name: newName.trim(),
    new_title: '',
  });
  if (!result) return;
  if (store.data?.state?.page_name === currentName) {
    store.data.state.page_name = result.page_name;
  }
  if (store.data?.active_page === currentName) {
    store.data.active_page = result.page_name;
  }
  await refreshDirectory();
  await loadPageDetail(result.page_name);
  el('overlayStatus').textContent = result.message || '内部页面 ID 已修改';
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
